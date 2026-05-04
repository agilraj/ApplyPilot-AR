"""Predicted interview questions and candidate-specific questions to ask.

Two LLM outputs per prep package:
  1. predicted_questions — 5 behavioral, 3 technical, 3 situational with
     suggested answer frameworks tied to the candidate's real background.
  2. questions_to_ask — 5 specific questions the candidate should ask the
     interviewer, referencing actual company/role details (not generic).
"""

from __future__ import annotations

import json
import logging
import re

from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Predicted questions
# ---------------------------------------------------------------------------

_QUESTIONS_PROMPT = """\
You are a senior career coach building an interview prep kit for a candidate.

Candidate profile:
Name: {name}
Current title: {current_title}
Years of experience: {years_exp}
Companies: {companies}
Real achievements/metrics: {metrics}
Target role: {target_role}

Role context:
Role type: {role_type}
Key themes: {themes}
Must-have requirements: {must_haves}
Inferred pain points: {pain_points}

Company: {company}

Generate interview questions in EXACTLY this JSON format:
{{
  "behavioral": [
    {{
      "question": "Tell me about a time when...",
      "why_asked": "one sentence on what they're testing",
      "answer_framework": "2-3 sentence suggested answer using candidate's real background"
    }}
  ],
  "technical": [
    {{
      "question": "...",
      "why_asked": "...",
      "answer_framework": "..."
    }}
  ],
  "situational": [
    {{
      "question": "If you were faced with...",
      "why_asked": "...",
      "answer_framework": "..."
    }}
  ]
}}

Rules:
- 5 behavioral questions, 3 technical, 3 situational (11 total)
- Answer frameworks must reference ONLY the candidate's real background provided above
- Make questions specific to this role type and company context — not generic
- Return ONLY the JSON object, no commentary
"""


def _generate_predicted(
    jd_text: str,
    company_brief: dict,
    role_analysis: dict,
    profile: dict,
) -> dict:
    """Generate predicted interview questions with answer frameworks."""
    personal = profile.get("personal", {})
    experience = profile.get("experience", {})
    resume_facts = profile.get("resume_facts", {})
    analysis = role_analysis if role_analysis else {}

    prompt = _QUESTIONS_PROMPT.format(
        name=personal.get("full_name", "Candidate"),
        current_title=experience.get("current_title", "not specified"),
        years_exp=experience.get("years_of_experience_total", "not specified"),
        companies=", ".join(resume_facts.get("preserved_companies", [])) or "not listed",
        metrics="\n".join(f"- {m}" for m in resume_facts.get("real_metrics", [])) or "not listed",
        target_role=experience.get("target_role", "the role"),
        role_type=analysis.get("role_type", "unknown"),
        themes=", ".join(analysis.get("key_themes", [])) or "not specified",
        must_haves="\n".join(f"- {r}" for r in analysis.get("must_haves", [])) or "see JD",
        pain_points="\n".join(f"- {p}" for p in analysis.get("inferred_pain_points", [])) or "not identified",
        company=company_brief.get("company", "the company"),
    )

    client = get_client()
    try:
        raw = client.ask(prompt, temperature=0.3, max_tokens=8192)
        raw = raw.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return parsed
    except Exception as exc:
        log.warning("Predicted questions LLM error: %s", exc)

    return {"behavioral": [], "technical": [], "situational": []}


# ---------------------------------------------------------------------------
# Questions to ask the interviewer
# ---------------------------------------------------------------------------

_QUESTIONS_TO_ASK_PROMPT = """\
You are helping a job candidate prepare 5 sharp, specific questions to ask at the end of their interview.

Company context:
{company_brief}

Role context:
Role type: {role_type}
Key themes: {themes}
Success in 90 days: {success_90_days}
Inferred pain points: {pain_points}

Rules for the questions:
- Each question must reference a SPECIFIC detail from the company or role context above
- No generic questions like "What does a typical day look like?"
- Questions should signal strategic thinking and genuine preparation
- Cover a mix of: team/culture, success metrics, challenges, company direction, growth

Return ONLY a JSON array of 5 strings (the questions themselves):
["Question 1", "Question 2", "Question 3", "Question 4", "Question 5"]
"""


def _generate_questions_to_ask(
    company_brief: dict,
    role_analysis: dict,
) -> list[str]:
    """Generate 5 specific, well-researched questions for the interviewer."""
    analysis = role_analysis if role_analysis else {}
    brief_text = company_brief.get("brief_text", company_brief.get("company", ""))

    prompt = _QUESTIONS_TO_ASK_PROMPT.format(
        company_brief=brief_text[:2000] if brief_text else "No company brief available",
        role_type=analysis.get("role_type", "unknown"),
        themes=", ".join(analysis.get("key_themes", [])) or "not specified",
        success_90_days=analysis.get("success_90_days", "not specified"),
        pain_points="\n".join(f"- {p}" for p in analysis.get("inferred_pain_points", [])) or "not identified",
    )

    client = get_client()
    try:
        raw = client.ask(prompt, temperature=0.4, max_tokens=8192)
        raw = raw.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return [str(q) for q in result[:5]]
    except Exception as exc:
        log.warning("Questions to ask LLM error: %s", exc)

    return ["What does success look like in the first 90 days for this role?"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_questions(
    jd_text: str,
    company_brief: dict,
    role_analysis: dict,
    profile: dict,
) -> dict:
    """Generate all interview questions (predicted + questions to ask).

    Returns:
      {
        "predicted": {"behavioral": [...], "technical": [...], "situational": [...]},
        "questions_to_ask": ["...", ...]
      }
    """
    predicted = _generate_predicted(jd_text, company_brief, role_analysis, profile)
    questions_to_ask = _generate_questions_to_ask(company_brief, role_analysis)

    log.info(
        "Questions: %d behavioral, %d technical, %d situational, %d to-ask",
        len(predicted.get("behavioral", [])),
        len(predicted.get("technical", [])),
        len(predicted.get("situational", [])),
        len(questions_to_ask),
    )

    return {
        "predicted": predicted,
        "questions_to_ask": questions_to_ask,
    }
