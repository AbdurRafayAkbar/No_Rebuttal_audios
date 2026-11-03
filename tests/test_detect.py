from pathlib import Path

from nra import botlog, detect

ROOT = Path(__file__).resolve().parent.parent
REBUTTALS = detect.load_rebuttals(ROOT / "campaigns" / "medicare" / "clips_manifest.csv")

U = "[abc-123]"


def _log(*turns: tuple[str, str, str, str, str], end: str = "CALLER_HANGUP") -> str:
    """turns: (state, audio, customer_text, intent, logic) -> synthetic bot log."""
    lines = [f"[2026-11-03T20:00:00] [INFO] {U} Call started | campaign=001camp"]
    sec = 1
    for no, (state, audio, text, intent, logic) in enumerate(turns, 1):
        ts = lambda: f"[2026-11-03T20:00:{sec:02d}]"  # noqa: E731
        lines.append(f"{ts()} [INFO] {U} Turn {no} | state={state} | audio={audio}")
        sec += 4
        q = '"' if "'" in text else "'"
        lines.append(f"{ts()} [INFO] {U} STT result | text={q}{text}{q} | confidence=0.90 | stt_duration=3.00s")
        lines.append(f"{ts()} [DEBUG] [server] {U} deepgram intent='GREETING' state={state}")
        lines.append(f"{ts()} [INFO] [server] {U} intent='{intent}' state={state}")
        lines.append(f"{ts()} [INFO] {U} call_logic response | {logic} | sdc={intent} | confidence=x")
        sec += 1
    lines.append(f"[2026-11-03T20:00:{sec:02d}] [INFO] {U} Call ended | disposition={end} | turns={len(turns)}"
                 f" | duration={sec}.0s | final_state=X")
    return "\n".join(lines)


def _nxt(state):
    return f"action=next_state | next_state={state} | disposition="


def _dispose(disp):
    return f"action=dispose | next_state= | disposition={disp}"


def test_parse_basic():
    log = botlog.parse_log(_log(("HELLO", "Hello.wav", "Hello", "GREETING", _nxt("PITCH")),
                                ("PITCH", "Pitch.wav", "what's this about", "CALLING_ABOUT", _nxt("TRANSFER"))))
    assert len(log.turns) == 2
    t1, t2 = log.turns
    assert t1.intent == "GREETING"  # INFO line wins over DEBUG
    assert t1.next_state == "PITCH"
    assert t2.customer_text == "what's this about"
    assert t2.t_stt == 10 and t2.stt_duration == 3.0
    assert log.ended and log.end_disposition == "CALLER_HANGUP"


def test_question_ignored_by_script_state_is_flagged():
    log = botlog.parse_log(_log(("PITCH", "Pitch.wav", "how much does it cost", "OTHER", _nxt("ASK_AGE")),
                                ("ASK_AGE", "Ask_age.wav", "", "NoAnswer", _nxt("AGAIN_PITCH"))))
    cands = detect.detect("MEDICARE", log, REBUTTALS)
    assert len(cands) == 1
    c = cands[0]
    assert c.r_rule == "R-NR-IGNORED" and c.q_rule == "Q-WH"
    assert c.suggested_rebuttal == "Rebuttal_Is_It_Free_Answer"


def test_question_answered_by_matching_rebuttal_not_flagged():
    log = botlog.parse_log(_log(("PITCH", "Pitch.wav", "who is this calling", "WHO_ARE_YOU",
                                 _nxt("WHO_ARE_YOU_REBUTTAL")),
                                ("WHO_ARE_YOU_REBUTTAL", "Who_are_you.wav", "", "NoAnswer", _nxt("ASK_AGE"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_offtopic_rebuttal_flagged():
    log = botlog.parse_log(_log(("PITCH", "Pitch.wav", "how did you get my number", "OTHER",
                                 _nxt("NOT_INTERESTED")),
                                ("NOT_INTERESTED", "Not_Interested_Rebuttal.wav", "", "NoAnswer", _nxt("ASK_AGE"))))
    cands = detect.detect("MEDICARE", log, REBUTTALS)
    assert [c.r_rule for c in cands] == ["R-NR-OFFTOPIC"]
    assert cands[0].suggested_rebuttal == "Rebuttal_How_Did_You_Get_My_Number_Answer"


def test_calling_about_answered_by_pitch():
    log = botlog.parse_log(_log(("HELLO", "Hello.wav", "what is this about", "CALLING_ABOUT", _nxt("PITCH")),
                                ("PITCH", "Pitch.wav", "", "NoAnswer", _nxt("AGAIN_PITCH"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_transfer_and_dnc_not_flagged():
    log = botlog.parse_log(_log(("PITCH", "Pitch.wav", "can you tell me more about it", "MED_positive",
                                 _nxt("TRANSFER")),
                                ("TRANSFER", "Transfer.wav", "stop calling me who are you", "DNC",
                                 _dispose("DNC"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_hangup_on_question_flagged():
    log = botlog.parse_log(_log(("ASK_AGE", "Ask_age.wav", "why do you need my age", "OTHER", _dispose("DNQ"))))
    cands = detect.detect("MEDICARE", log, REBUTTALS)
    assert [(c.r_rule, c.confidence) for c in cands] == [("R-NR-HANGUP", 0.6)]


def test_short_and_greeting_turns_ignored():
    log = botlog.parse_log(_log(("HELLO", "Hello.wav", "hello hello", "GREETING", _nxt("PITCH")),
                                ("PITCH", "Pitch.wav", "yes", "MED_positive", _nxt("ASK_AGE")),
                                ("ASK_AGE", "Ask_age.wav", "", "NoAnswer", _nxt("AGAIN_PITCH"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_answering_machine_call_skipped():
    log = botlog.parse_log(_log(("HELLO", "Hello.wav", "what is the best time to reach you", "AM",
                                 _nxt("PITCH")), end="AM"))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_repeat_question_kept_once_with_boost():
    log = botlog.parse_log(_log(("PITCH", "Pitch.wav", "um how much is it", "OTHER", _nxt("AGAIN_PITCH")),
                                ("AGAIN_PITCH", "A_Pitch.wav", "how much is it", "OTHER", _nxt("ASK_AGE")),
                                ("ASK_AGE", "Ask_age.wav", "", "NoAnswer", _nxt("AGAIN_PITCH"))))
    cands = detect.detect("MEDICARE", log, REBUTTALS)
    assert len(cands) == 1 and cands[0].repeats == 2 and cands[0].confidence == 0.9


def test_no_speech_text_discarded():
    text = _log(("PITCH", "Pitch.wav", "what company is this", "OTHER", _nxt("ASK_AGE")))
    text = text.replace("STT result", "No speech detected by VAD | x\n[2026-11-03T20:00:05] [INFO] [abc-123] STT result", 1)
    log = botlog.parse_log(text)
    assert log.turns[0].customer_text == ""
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_day_range_utc_new_york():
    from datetime import date, datetime
    from nra.pipeline import day_range_utc
    s, e = day_range_utc(date(2026, 11, 3), "America/New_York")
    assert s == datetime(2026, 11, 3, 5, 0) and e == datetime(2026, 11, 4, 5, 0)


def test_phatic_questions_ignored():
    log = botlog.parse_log(_log(("HELLO", "Hello.wav", "hello hi can you hear me okay yeah", "GREETING",
                                 _nxt("PITCH")),
                                ("PITCH", "Pitch.wav", "", "NoAnswer", _nxt("AGAIN_PITCH"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []


def test_age_answer_before_dnq_not_flagged():
    log = botlog.parse_log(_log(("ASK_AGE", "Ask_age.wav", "no I'm older than that", "MED_Negative",
                                 _dispose("DNQ"))))
    assert detect.detect("MEDICARE", log, REBUTTALS) == []
