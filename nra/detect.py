"""Find customer questions/objections the bot did not answer.

Rules work on the parsed bot log (see botlog.py). Each customer turn is checked:
1. Is it a candidate question or objection (Q-* rules)?
2. What did the bot do next (R-* rules)?
Only candidates whose response looks like a non-answer are flagged.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .botlog import CallLog, Turn

FILLER = {
    "uh", "um", "umm", "uhm", "hmm", "ah", "oh", "yeah", "yes", "yep", "ok", "okay",
    "well", "so", "hello", "hi", "hey", "sorry", "excuse", "me", "wait", "and", "but",
    "like", "listen", "ma'am", "sir", "huh",
}
WH_OPENERS = (
    "what", "what's", "whats", "who", "who's", "whos", "why", "where", "where's", "when",
    "how", "how's", "which", "whose", "is this", "is it", "is that", "are you", "are they",
    "can you", "could you", "can i", "do you", "does", "did you", "will", "would you",
    "should i", "am i",
)
STRONG_PHRASES = (
    "how much", "who is this", "who's this", "whos this", "what is this", "what's this",
    "whats this", "where are you calling from", "where you calling from", "what company",
    "which company", "how did you get my number", "who gave you my number", "is this a scam",
    "what's this about", "what is this about", "who are you", "what do you want",
    "is it free", "how much does it cost", "are you a robot", "are you real", "is this a recording",
    "is this a real person", "what are you selling", "what plan", "what kind of",
    "who am i talking to", "who am i speaking", "what's your name", "what is your name",
    "why are you calling", "what are you calling", "what is it", "what for", "reaching out about",
    "calling me about", "what's it about", "what is it about",
)
QUESTION_INTENTS = {"CALLING_ABOUT", "WHO_ARE_YOU"}
OBJECTION_INTENTS = {"ALREADY_HAVE", "HA"}
# Phatic questions: line checks, not real questions.
PHATIC = ("can you hear me", "do you hear me", "how are you", "how you doing", "how are you doing",
          "are you there", "you there", "hear me")

# Production bot states (log `state=`). Script states move the call on.
SCRIPT_STATES = {"HELLO", "PITCH", "AGAIN_PITCH", "ASK_AGE"}
TRANSFER_STATES = {"TRANSFER"}
# What each production rebuttal state answers (topic keywords on normalized text).
REBUTTAL_STATE_TOPICS = {
    "WHO_ARE_YOU_REBUTTAL": ("who", "company", "calling from", "your name", "calling about",
                             "why are you calling", "what is this", "what's this", "whats this",
                             "how did you get", "my number", "where"),
    "ALREADY_HAVE_REBUTTAL": ("already have", "have insurance", "have medicare", "covered",
                              "got medicare", "have a plan", "have coverage", "got insurance", "i have it",
                              "have it", "i got it", "i have one", "have that", "got that"),
    "NOT_INTERESTED": ("not interested", "no thanks", "no thank you", "don't want", "dont want",
                       "not looking", "don't need", "dont need"),
}
# Calling-about questions are answered by the pitch itself.
PITCH_ANSWERS = ("what is this", "what's this", "whats this", "calling about", "why are you calling",
                 "what are you calling", "what for", "what is it", "what do you want", "reaching out about",
                 "what's it about", "can i help you", "what can i do for you", "what you want",
                 "what do you need", "is this medic", "is this about medicare", "what you need")
SKIP_DISPOSITIONS = {"AM"}           # answering machine: no real customer
RESPECTED_DISPOSITIONS = {"DNC", "XFER"}


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_filler(norm: str) -> str:
    words = norm.split()
    while words and words[0] in FILLER:
        words.pop(0)
    return " ".join(words)


def _has(norm: str, phrases) -> str | None:
    padded = f" {norm} "
    for p in phrases:
        if f" {p} " in padded:
            return p
    return None


@dataclass(frozen=True)
class Rebuttal:
    name: str
    keywords: tuple[str, ...]


def load_rebuttals(manifest: Path) -> list[Rebuttal]:
    seen: dict[str, Rebuttal] = {}
    with manifest.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["clip_type"] != "rebuttal":
                continue
            name = re.sub(r"_V\d+$", "", row["clip_name"])
            if name not in seen:
                kws = tuple(normalize(k) for k in row["topic_keywords"].split(",") if k.strip())
                seen[name] = Rebuttal(name, kws)
    return list(seen.values())


def suggest_rebuttal(norm: str, rebuttals: list[Rebuttal]) -> str:
    best, best_hits = "", 0
    for r in rebuttals:
        hits = sum(1 for k in r.keywords if _has(norm, (k,)))
        if hits > best_hits:
            best, best_hits = r.name, hits
    return best


@dataclass
class Candidate:
    turn_no: int
    bot_state: str
    bot_audio: str
    customer_text: str
    intent: str
    q_rule: str
    response: str        # next state or "dispose:<disp>"
    r_rule: str
    confidence: float
    speech_start: float
    question_key: str
    suggested_rebuttal: str
    played: bool = True
    repeats: int = 1
    llm: dict = field(default_factory=dict)


def question_rule(turn: Turn) -> str | None:
    norm = normalize(turn.customer_text)
    if not norm:
        return None
    core = strip_filler(norm)
    if _has(norm, PHATIC) and len(core.split()) <= len(_has(norm, PHATIC).split()) + 3:
        return None
    if _has(norm, STRONG_PHRASES):
        return "Q-WH"
    if turn.intent in QUESTION_INTENTS:
        return "Q-INTENT"
    if len(core.split()) < 3:
        return None
    if any(core == o or core.startswith(o + " ") for o in WH_OPENERS):
        return "Q-WH"
    if turn.intent in OBJECTION_INTENTS:
        return "Q-OBJ"
    return None


def response_rule(turn: Turn, norm: str, q_rule: str = "Q-WH") -> tuple[str, float] | None:
    """Return (rule, confidence) if the bot's response looks like a non-answer."""
    if turn.action == "dispose":
        if q_rule == "Q-OBJ":
            return None  # ending the call on an objection is a normal outcome
        if turn.disposition in RESPECTED_DISPOSITIONS or turn.disposition in SKIP_DISPOSITIONS:
            return None
        return ("R-NR-HANGUP", 0.6 if turn.disposition == "DNQ" else 0.7)
    nxt = turn.next_state
    if not nxt or nxt in TRANSFER_STATES:
        return None
    if nxt in SCRIPT_STATES:
        if nxt in {"PITCH", "AGAIN_PITCH"} and _has(norm, PITCH_ANSWERS):
            return None
        if turn.state == "HELLO" and nxt == "PITCH":
            return ("R-NR-IGNORED", 0.5)  # pitch often covers early questions; lower confidence
        return ("R-NR-IGNORED", 0.8)
    topics = REBUTTAL_STATE_TOPICS.get(nxt)
    if topics is not None:
        return None if _has(norm, topics) else ("R-NR-OFFTOPIC", 0.5)
    return ("R-NR-UNKNOWN-STATE", 0.4)


def question_key(campaign: str, text: str) -> str:
    core = " ".join(strip_filler(normalize(text)).split()[:12])
    return hashlib.sha1(f"{campaign}|{core}".encode()).hexdigest()[:16]


def detect(campaign: str, log: CallLog, rebuttals: list[Rebuttal]) -> list[Candidate]:
    if log.end_disposition in SKIP_DISPOSITIONS:
        return []
    found: dict[str, Candidate] = {}
    turn_nos = {t.no for t in log.turns}
    for turn in log.turns:
        q = question_rule(turn)
        if q is None:
            continue
        norm = normalize(turn.customer_text)
        r = response_rule(turn, norm, q)
        if r is None:
            continue
        rule, conf = r
        played = turn.action == "dispose" or (turn.no + 1) in turn_nos
        if not played:
            conf -= 0.2  # customer hung up before the bot's reply started
        key = question_key(campaign, turn.customer_text)
        if key in found:
            first = found[key]
            first.repeats += 1
            first.confidence = min(1.0, first.confidence + 0.1)  # A3: asked again
            continue
        resp = f"dispose:{turn.disposition}" if turn.action == "dispose" else turn.next_state
        found[key] = Candidate(
            turn_no=turn.no, bot_state=turn.state, bot_audio=turn.audio,
            customer_text=turn.customer_text, intent=turn.intent, q_rule=q,
            response=resp, r_rule=rule, confidence=round(conf, 2),
            speech_start=round(turn.speech_start, 1), question_key=key,
            suggested_rebuttal=suggest_rebuttal(norm, rebuttals), played=played,
        )
    return list(found.values())
