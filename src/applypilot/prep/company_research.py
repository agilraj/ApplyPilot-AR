"""Company, interviewer, and relocation research via DuckDuckGo + LLM.

Web search uses DuckDuckGo HTML endpoint (no API key required).
LLM is used to synthesise raw search snippets into structured output.
All functions degrade gracefully: if web search fails, the LLM uses its
own training knowledge and clearly labels results as 'knowledge cutoff only'.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from applypilot.llm import get_client

log = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Search helper
# ---------------------------------------------------------------------------

def _ddg_search(query: str, max_results: int = 5) -> str:
    """Run a DuckDuckGo HTML search and return a plain-text summary of snippets."""
    try:
        resp = httpx.get(
            _DDG_URL,
            params={"q": query, "kl": "en-us", "ia": "web"},
            headers=_HEADERS,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        snippets: list[str] = []
        for result in soup.select(".result__body")[:max_results]:
            title_el = result.select_one(".result__a")
            snippet_el = result.select_one(".result__snippet")
            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if snippet:
                snippets.append(f"• {title}: {snippet}" if title else f"• {snippet}")

        return "\n".join(snippets)

    except Exception as exc:
        log.warning("DDG search failed for query '%s': %s", query[:60], exc)
        return ""


def _search_multi(queries: list[str], max_per: int = 4) -> str:
    """Run multiple queries and concatenate non-empty results."""
    parts: list[str] = []
    for q in queries:
        result = _ddg_search(q, max_results=max_per)
        if result:
            parts.append(result)
        time.sleep(0.5)   # be a polite bot
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Company brief
# ---------------------------------------------------------------------------

_COMPANY_PROMPT = """\
You are a business research assistant helping a job candidate prepare for an interview.

Using the web search results below, write a company brief with EXACTLY these sections:

**Overview** (2-3 sentences): What the company does, its core business model, primary market.
**Key Clients / Customers**: Notable clients, customer segments, or industries served.
**Scale**: Headcount estimate, number of offices, geographic footprint.
**Funding / Ownership**: Public/private, funding stage, notable investors, or parent company.
**Recent News** (last 90 days): Up to 3 notable announcements, launches, or developments.
**Interview Angle**: One sentence on how to position yourself given this company context.

If information is not available in the search results, say "Not publicly available" — do not fabricate.

Company: {company}
Location context: {location}

Web search results:
{search_results}
"""


def research_company(company: str, location: str = "") -> dict:
    """Research a company using web search + LLM synthesis.

    Returns a dict with keys: overview, key_clients, scale, funding,
    recent_news, interview_angle, raw_search (for audit), source.
    """
    queries = [
        f'"{company}" company overview business model',
        f'"{company}" {location} recent news 2025 2026',
        f'"{company}" employees headquarters funding',
    ]
    raw = _search_multi(queries)
    source = "web+llm" if raw else "llm_only"

    prompt = _COMPANY_PROMPT.format(
        company=company,
        location=location or "not specified",
        search_results=raw or "(no web results — use training knowledge, label as approximate)",
    )

    try:
        client = get_client()
        brief_text = client.ask(prompt, temperature=0.2, max_tokens=8192)
    except Exception as exc:
        log.warning("Company research LLM call failed: %s", exc)
        brief_text = f"Could not generate company brief: {exc}"

    return {
        "company": company,
        "location": location,
        "brief_text": brief_text,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Interviewer research
# ---------------------------------------------------------------------------

_INTERVIEWER_PROMPT = """\
You are helping a job candidate research an interviewer before their interview.

Using the web search results below, provide a brief profile of {name} at {company}:

**Role & Tenure**: Their current title, how long they've been at the company (if known).
**Background**: Prior companies, career trajectory in 2-3 sentences.
**LinkedIn / Public Presence**: Any notable articles, talks, or posts they've made.
**Suggested Rapport**: One talking point to build rapport based on their background.

If information is not found, say "Could not find public profile" — do not fabricate.

Web search results:
{search_results}
"""


def research_interviewer(name: str, company: str) -> dict:
    """Research an interviewer by name + company using web search + LLM.

    Returns a dict with keys: name, company, profile_text, source.
    """
    queries = [
        f'"{name}" "{company}" LinkedIn',
        f'"{name}" {company} career background',
    ]
    raw = _search_multi(queries, max_per=3)
    source = "web+llm" if raw else "llm_only"

    prompt = _INTERVIEWER_PROMPT.format(
        name=name,
        company=company,
        search_results=raw or "(no web results found)",
    )

    try:
        client = get_client()
        profile_text = client.ask(prompt, temperature=0.2, max_tokens=8192)
    except Exception as exc:
        log.warning("Interviewer research LLM call failed: %s", exc)
        profile_text = f"Could not generate interviewer profile: {exc}"

    return {
        "name": name,
        "company": company,
        "profile_text": profile_text,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Relocation intel
# ---------------------------------------------------------------------------

_RELOCATION_PROMPT = """\
You are helping a Malaysian professional (based in Kuala Lumpur) understand what
relocation to {city}, {country} would involve.

Using the search results and your knowledge, cover:

**Cost of Living vs KL**: Housing, transport, food index comparison (be specific with numbers where possible).
**Visa / Work Permit**: What permit type is typically required for Malaysian nationals. Employer sponsorship process.
**Housing Market**: Typical monthly rent for a 1-bedroom apartment in a good area (local currency).
**Practical Notes**: Healthcare, language, expat community size, commute culture.

Web search results:
{search_results}
"""


def research_relocation(country: str, city: str = "") -> dict:
    """Research relocation intel for a given country/city vs KL baseline.

    Returns a dict with keys: country, city, intel_text, source.
    """
    location = f"{city} {country}".strip()
    queries = [
        f"cost of living {location} vs Kuala Lumpur 2025",
        f"work visa {country} Malaysian nationals 2025",
        f"expat living {location} housing rent 2025",
    ]
    raw = _search_multi(queries, max_per=3)
    source = "web+llm" if raw else "llm_only"

    prompt = _RELOCATION_PROMPT.format(
        city=city or country,
        country=country,
        search_results=raw or "(no web results — use training knowledge)",
    )

    try:
        client = get_client()
        intel_text = client.ask(prompt, temperature=0.2, max_tokens=8192)
    except Exception as exc:
        log.warning("Relocation research LLM call failed: %s", exc)
        intel_text = f"Could not generate relocation intel: {exc}"

    return {
        "country": country,
        "city": city,
        "intel_text": intel_text,
        "source": source,
    }
