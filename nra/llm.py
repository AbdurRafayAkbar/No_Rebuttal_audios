"""Optional LLM judge (gpt-oss-120b on SGLang, OpenAI-compatible, D22).

Used only when LLM_ENDPOINT is set. Verdicts are cached in the app DB by
(prompt version, question_key, response), so each distinct case is judged once.
"""
from __future__ import annotations

import json
import re
import urllib.request

from .config import ROOT, Settings

PROMPT_VERSION = "judge_v1"
_PROMPT = (ROOT / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")


def judge(settings: Settings, customer_text: str, bot_state: str, response: str,
          rebuttal_names: list[str], timeout: float = 60.0) -> dict:
    prompt = _PROMPT.format(
        rebuttals="\n".join(f"- {n}" for n in rebuttal_names),
        customer_text=customer_text.replace('"', "'"),
        bot_state=bot_state,
        response=response,
    )
    body = json.dumps({
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 800,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    req = urllib.request.Request(f"{settings.llm_endpoint}/chat/completions", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = json.load(resp)["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("LLM reply has no JSON object")
    verdict = json.loads(m.group(0))
    return {
        "is_question": bool(verdict.get("is_question")),
        "answers_question": bool(verdict.get("answers_question")),
        "confidence": float(verdict.get("confidence") or 0),
        "reason": str(verdict.get("reason") or "")[:300],
        "suggested_rebuttal": str(verdict.get("suggested_rebuttal") or ""),
    }
