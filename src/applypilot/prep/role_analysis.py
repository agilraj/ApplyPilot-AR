"""JD analysis and talking point generation.

Two LLM calls per prep package:
  1. analyse_jd()     — extracts must-haves, pain points, seniority signals, etc.
  2. build_talking_points() — maps profile.resume_facts to each key requirement.

Never fabricates experience. Only uses bullets from profile.resume_facts.real_metrics
and preserved_companies/projects.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JD analysis
# ---------------------------------------------------------------------------

_JD_ANALYSIS_PROMPT = """\
You are a senior career coach analysing a job description for an interview candidate.

Analyse the JD below and return ONLY a JSON object with exactly these keys:
{{
  "must_haves": ["list of 5-7 non-negotiable requirements from the JD"],
  "nice_to_haves": ["list of 3-5 preferred but not mandatory skills"],
  "inferred_pain_points": ["list of 2-3 problems this role was created to solve"],
  "success_90_days": "one sentence describing what success looks like in the first 90 days",
  "seniority_signals": ["list of 3-4 signals that indicate seniority level: team size, P&L, scope"],
  "role_type": "one of: IC, manager, director, executive, specialist, consultant",
  "key_themes": ["2-3 overarching themes of what this role is really about"]
}}

Return ONLY the JSON. No commentary.

Job Description:
{jd_text}
"""


def _analyse_jd(jd_text: str) -> dict:
    """Call LLM to extract structured analysis from JD text."""
    if not jd_text:
        return {
            "must_haves": [],
            "nice_to_haves": [],
            "inferred_pain_points": [],
            "success_90_days": "JD not available",
            "seniority_signals": [],
            "role_type": "unknown",
            "key_themes": [],
        }

    prompt = _JD_ANALYSIS_PROMPT.format(jd_text=jd_text[:7000])
    client = get_client()

    try:
        raw = client.ask(prompt, temperature=0.1, max_tokens=8192)
        raw = raw.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as exc:
        log.warning("JD analysis LLM error: %s", exc)

    return {
        "must_haves": ["Could not parse JD analysis"],
        "nice_to_haves": [],
        "inferred_pain_points": [],
        "success_90_days": "See job description",
        "seniority_signals": [],
        "role_type": "unknown",
        "key_themes": [],
    }


# ---------------------------------------------------------------------------
# Talking points
# ---------------------------------------------------------------------------

_TALKING_POINTS_PROMPT = """\
You are a career coach helping a candidate prepare talking points for an interview.

Candidate background (from their resume — these are real facts, do NOT fabricate):
Name: {name}
Current title: {current_title}
Years of experience: {years_exp}
Companies: {companies}
Projects: {projects}
Real metrics / achievements: {metrics}
Target role: {target_role}

Key JD requirements (must-haves):
{must_haves}

For EACH must-have requirement, write one talking point that:
1. Maps the closest matching real experience from the candidate's background
2. Uses the STAR framework briefly (Situation/Task → Action → Result)
3. References ONLY facts listed above — never invent experience or metrics
4. Is 2-4 sentences max

If there is genuinely no matching experience for a requirement, write:
"Gap: [requirement] — suggest acknowledging this and highlighting your fast-learning track record."

Return ONLY a JSON array:
[
  {{"requirement": "...", "talking_point": "...", "is_gap": false}},
  ...
]
"""


def _build_talking_points(analysis: dict, profile: dict) -> list[dict]:
    """Map profile resume_facts to each JD must-have requirement."""
    must_haves = analysis.get("must_haves", [])
    if not must_haves:
        return []

    personal = profile.get("personal", {})
    experience = profile.get("experience", {})
    resume_facts = profile.get("resume_facts", {})

    prompt = _TALKING_POINTS_PROMPT.format(
        name=personal.get("full_name", "Candidate"),
        current_title=experience.get("current_title", "not specified"),
        years_exp=experience.get("years_of_experience_total", "not specified"),
        companies=", ".join(resume_facts.get("preserved_companies", [])) or "not listed",
        projects=", ".join(resume_facts.get("preserved_projects", [])) or "not listed",
        metrics="\n".join(f"- {m}" for m in resume_facts.get("real_metrics", [])) or "not listed",
        target_role=experience.get("target_role", "the role"),
        must_haves="\n".join(f"{i+1}. {r}" for i, r in enumerate(must_haves)),
    )

    client = get_client()
    try:
        raw = client.ask(prompt, temperature=0.2, max_tokens=8192)
        raw = raw.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as exc:
        log.warning("Talking points LLM error: %s", exc)

    return [{"requirement": r, "talking_point": "Could not generate", "is_gap": False}
            for r in must_haves]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyse_role(jd_text: str, profile: dict) -> dict:
    """Full role analysis: JD extraction + mapped talking points.

    Returns:
      {
        "analysis": {must_haves, nice_to_haves, pain_points, ...},
        "talking_points": [{"requirement", "talking_point", "is_gap"}, ...]
      }
    """
    analysis = _analyse_jd(jd_text)
    talking_points = _build_talking_points(analysis, profile)

    log.info(
        "Role analysis: %d must-haves, %d talking points",
        len(analysis.get("must_haves", [])),
        len(talking_points),
    )

    return {
        "analysis": analysis,
        "talking_points": talking_points,
    }
