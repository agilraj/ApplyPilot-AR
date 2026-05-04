"""Markdown renderer for interview prep packages.

Assembles all section dicts produced by the other prep modules into a
single well-formatted markdown file and writes it to prep_packages/.

Output filename: {app_id}_{company_slug}_{YYYY-MM-DD}.md
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _slug(text: str, max_len: int = 30) -> str:
    """Convert company name to a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len].strip("_") or "company"


def _section(title: str, body: str, number: Optional[int] = None) -> str:
    prefix = f"{number}. " if number else ""
    return f"\n## {prefix}{title}\n\n{body.strip()}\n"


def _subsection(title: str, body: str) -> str:
    return f"\n### {title}\n\n{body.strip()}\n"


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "_None identified_"


def _confidential_banner() -> str:
    return (
        "> ⚠️  **CONFIDENTIAL — FOR PERSONAL USE ONLY**  \n"
        "> This document contains salary floors and negotiation scripts.  \n"
        "> Do not share with the interviewer or third parties.\n"
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_company_brief(data: dict) -> str:
    brief = data.get("brief_text", "")
    source = data.get("source", "unknown")
    source_note = "_Source: web search + LLM synthesis_" if "web" in source else "_Source: LLM training knowledge only (web search unavailable)_"
    return f"{source_note}\n\n{brief}"


def _render_role_analysis(analysis: dict) -> str:
    parts: list[str] = []

    must_haves = analysis.get("must_haves", [])
    parts.append("**Must-Have Requirements**\n" + _bullet_list(must_haves))

    nice_to_haves = analysis.get("nice_to_haves", [])
    parts.append("**Nice-to-Haves**\n" + _bullet_list(nice_to_haves))

    pain_points = analysis.get("inferred_pain_points", [])
    parts.append("**Inferred Pain Points** _(why this role exists)_\n" + _bullet_list(pain_points))

    success = analysis.get("success_90_days", "")
    if success:
        parts.append(f"**Success in 90 Days**\n{success}")

    seniority = analysis.get("seniority_signals", [])
    if seniority:
        parts.append("**Seniority Signals**\n" + _bullet_list(seniority))

    themes = analysis.get("key_themes", [])
    if themes:
        parts.append("**Key Themes**\n" + _bullet_list(themes))

    return "\n\n".join(parts)


def _render_talking_points(talking_points: list[dict]) -> str:
    if not talking_points:
        return "_No talking points generated (JD may not be available)._"

    lines: list[str] = []
    gaps: list[dict] = []

    for i, tp in enumerate(talking_points, start=1):
        req = tp.get("requirement", "Requirement")
        point = tp.get("talking_point", "")
        is_gap = tp.get("is_gap", False)

        if is_gap or point.startswith("Gap:"):
            gaps.append(tp)
            continue

        lines.append(f"**{i}. {req}**\n\n{point}")

    if gaps:
        lines.append("---\n**⚠️ Gaps to Address**")
        for g in gaps:
            lines.append(f"- {g.get('talking_point', g.get('requirement', ''))}")

    return "\n\n".join(lines)


def _render_questions(predicted: dict) -> str:
    parts: list[str] = []

    behavioral = predicted.get("behavioral", [])
    if behavioral:
        parts.append("### Behavioral (5)\n")
        for i, q in enumerate(behavioral, start=1):
            parts.append(
                f"**Q{i}. {q.get('question', '')}**\n\n"
                f"_Why asked:_ {q.get('why_asked', '')}\n\n"
                f"_Answer framework:_ {q.get('answer_framework', '')}"
            )

    technical = predicted.get("technical", [])
    if technical:
        parts.append("### Technical (3)\n")
        for i, q in enumerate(technical, start=1):
            parts.append(
                f"**Q{i}. {q.get('question', '')}**\n\n"
                f"_Why asked:_ {q.get('why_asked', '')}\n\n"
                f"_Answer framework:_ {q.get('answer_framework', '')}"
            )

    situational = predicted.get("situational", [])
    if situational:
        parts.append("### Situational (3)\n")
        for i, q in enumerate(situational, start=1):
            parts.append(
                f"**Q{i}. {q.get('question', '')}**\n\n"
                f"_Why asked:_ {q.get('why_asked', '')}\n\n"
                f"_Answer framework:_ {q.get('answer_framework', '')}"
            )

    return "\n\n".join(parts) if parts else "_No questions generated._"


def _render_questions_to_ask(questions: list[str]) -> str:
    if not questions:
        return "_No questions generated._"
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))


def _render_salary_anchor(data: dict) -> str:
    lines: list[str] = [
        "> 🔒 **NEVER disclose your floor figure in any conversation.**\n",
        "| | Amount | Note |",
        "|---|---|---|",
        f"| 🔒 Floor _(never disclose)_ | **{data['floor_str']}**/mo | Hard minimum |",
        f"| ✅ Target _(acceptable close)_ | **{data['target_str']}**/mo | Acceptable outcome |",
        f"| 🎯 Anchor _(open with this)_ | **{data['anchor_str']}**/mo | {data['open_anchor_pct']}% above target |",
        "",
    ]

    if data.get("ppp_note"):
        lines.append(f"_{data['ppp_note']}_\n")

    if data.get("auth_note"):
        lines.append(f"> ⚠️ {data['auth_note']}\n")

    # Form-fill guidance
    lines += [
        "**Form Fill Rules**",
        f"- Salary field: enter **{data['form_fill_anchor']}**",
        f"- Range field: **{data['form_fill_anchor']}** – **{data['form_fill_range_top']}**",
        f"- Current salary: _{data['current_salary_response']}_",
        "",
    ]

    # Total comp
    if data.get("total_comp_notes"):
        lines.append(f"**Total Comp**\n{data['total_comp_notes']}\n")

    # Scripts
    lines.append("---\n**Negotiation Scripts**\n")
    scripts = data.get("scripts", {})
    for script_text in scripts.values():
        lines.append(f"{script_text}\n")

    return "\n".join(lines)


def _render_interviewer(data: dict) -> str:
    source = data.get("source", "")
    source_note = "_Source: web search + LLM_" if "web" in source else "_Source: LLM training knowledge only_"
    profile = data.get("profile_text", "No profile found.")
    return f"**Interviewer:** {data.get('name', 'Unknown')} @ {data.get('company', '')}\n\n{source_note}\n\n{profile}"


def _render_comp_intel(data: dict) -> str:
    est_min = data.get("estimated_min")
    est_max = data.get("estimated_max")
    confidence = data.get("inference_confidence")
    currency = data.get("currency", "MYR")

    if est_min and est_max:
        range_str = f"{currency} {est_min:,} – {currency} {est_max:,} /month"
        conf_str = f" _(inference confidence: {confidence}%)_" if confidence else ""
        return f"Estimated market range: **{range_str}**{conf_str}"
    return "_No salary estimate available (job may have disclosed salary or inference was not run)._"


def _render_relocation(data: dict) -> str:
    source = data.get("source", "")
    source_note = "_Source: web search + LLM_" if "web" in source else "_Source: LLM training knowledge only_"
    intel = data.get("intel_text", "No relocation intel generated.")
    location = f"{data.get('city', '')} {data.get('country', '')}".strip()
    return f"**Destination:** {location}\n\n{source_note}\n\n{intel}"


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(
    app_id: int,
    company: str,
    date_str: str,
    app: dict,
    jd_text: str,
    sections: dict,
    output_dir: Path,
) -> str:
    """Assemble all section dicts into a markdown file.

    Args:
        app_id:     Application ID (used in filename and header)
        company:    Company name (used in filename and header)
        date_str:   ISO date string YYYY-MM-DD (used in filename)
        app:        Full application dict from tracker DB
        jd_text:    Raw job description text (included as appendix if available)
        sections:   Dict of section data from prep sub-modules
        output_dir: Directory to write the file into

    Returns:
        Absolute file path as string.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    company_slug = _slug(company)
    filename = f"{app_id}_{company_slug}_{date_str}.md"
    file_path = output_dir / filename

    job_title = app.get("job_title", "Unknown Role")
    location = " ".join(filter(None, [
        app.get("job_location_city"),
        app.get("job_location_country"),
    ])) or "Location TBC"
    if app.get("job_is_remote"):
        location = "Remote" + (f" / {location}" if location != "Location TBC" else "")
    fit_score = app.get("fit_score")
    score_str = f"{fit_score:.1f}/10" if fit_score else "N/A"
    interview_dt = app.get("interview_datetime") or "TBC"
    interview_type = app.get("interview_type") or "Not specified"

    doc_parts: list[str] = []

    # Header
    doc_parts.append(
        f"# Interview Prep Package\n\n"
        f"**Company:** {company}  \n"
        f"**Role:** {job_title}  \n"
        f"**Location:** {location}  \n"
        f"**Interview:** {interview_dt} ({interview_type})  \n"
        f"**Fit Score:** {score_str}  \n"
        f"**Generated:** {date_str}  \n"
        f"**Application ID:** #{app_id}\n"
    )

    doc_parts.append(_confidential_banner())
    doc_parts.append("---\n")

    n = 1

    # Section 1: Company Brief
    if "company_brief" in sections:
        doc_parts.append(_section("Company Brief", _render_company_brief(sections["company_brief"]), n))
        n += 1

    # Section 2: Role Analysis
    if "role_analysis" in sections:
        doc_parts.append(_section("Role Analysis", _render_role_analysis(sections["role_analysis"]), n))
        n += 1

    # Section 3: Talking Points
    if "talking_points" in sections:
        doc_parts.append(_section("Talking Points", _render_talking_points(sections["talking_points"]), n))
        n += 1

    # Section 4: Predicted Questions
    if "predicted_questions" in sections:
        doc_parts.append(_section("Predicted Interview Questions", _render_questions(sections["predicted_questions"]), n))
        n += 1

    # Section 5: Questions to Ask
    if "questions_to_ask" in sections:
        doc_parts.append(_section("Questions to Ask", _render_questions_to_ask(sections["questions_to_ask"]), n))
        n += 1

    # Section 6: Salary Anchor
    if "salary_anchor" in sections:
        doc_parts.append(_section("Salary Anchor & Negotiation", _render_salary_anchor(sections["salary_anchor"]), n))
        n += 1

    # Section 7: Interviewer Research
    if "interviewer_research" in sections:
        doc_parts.append(_section("Interviewer Research", _render_interviewer(sections["interviewer_research"]), n))
        n += 1

    # Section 8: Compensation Intelligence
    if "compensation_intel" in sections:
        doc_parts.append(_section("Compensation Intelligence", _render_comp_intel(sections["compensation_intel"]), n))
        n += 1

    # Section 9: Relocation Intel
    if "relocation_intel" in sections:
        doc_parts.append(_section("Relocation Intel", _render_relocation(sections["relocation_intel"]), n))
        n += 1

    # Appendix: Raw JD
    if jd_text:
        doc_parts.append(
            "\n---\n\n## Appendix: Job Description\n\n"
            f"```\n{jd_text[:5000].strip()}\n"
            + ("...[truncated]\n" if len(jd_text) > 5000 else "")
            + "```\n"
        )

    content = "\n".join(doc_parts)
    file_path.write_text(content, encoding="utf-8")

    log.info("Prep package written: %s", file_path)
    return str(file_path)
