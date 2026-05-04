"""Salary negotiation anchor calculation from config/salary.json.

Pure config arithmetic — no LLM calls. Reads floor and target for the
application's location, then derives:

  anchor = target * (1 + open_anchor_above_target_pct / 100)  ← open with this
  target = from config                                          ← acceptable close
  floor  = from config                                          ← NEVER disclose

Also generates word-for-word negotiation scripts for the two most common
scenarios: interviewer asks first vs candidate brings it up.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SALARY_CFG = _PROJECT_ROOT / "config" / "salary.json"
_WORK_AUTH_CFG = _PROJECT_ROOT / "config" / "work_auth.json"


def _load_salary_config() -> dict:
    with open(_SALARY_CFG) as f:
        return json.load(f)


def _load_work_auth() -> dict:
    try:
        with open(_WORK_AUTH_CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_region_cfg(salary_cfg: dict, country: Optional[str], is_remote: bool) -> dict:
    regions = salary_cfg.get("regions", {})
    if is_remote:
        return regions.get("remote", regions.get("Malaysia", {}))
    if not country:
        return regions.get("Malaysia", {})
    if country in regions:
        return regions[country]
    europe_adj = salary_cfg.get("ppp_europe_adjustments", {})
    sea_adj = salary_cfg.get("ppp_sea_adjustments", {})
    if country in europe_adj:
        return regions.get("Europe", {})
    if country in sea_adj:
        return regions.get("SEA_other", {})
    return regions.get("Malaysia", {})


def _fmt(amount: int, currency: str) -> str:
    """Format salary as currency string, e.g. 'MYR 13,800'."""
    return f"{currency} {amount:,}"


def build_salary_anchor(
    country: Optional[str],
    is_remote: bool,
    currency: str,
    prep_cfg: dict,
) -> dict:
    """Compute floor, target, anchor and negotiation scripts for a location.

    Args:
        country:    Job location country (None → use Malaysia base)
        is_remote:  True if job is remote
        currency:   Display currency string (e.g. 'MYR', 'SGD')
        prep_cfg:   Parsed config/prep.json dict

    Returns dict with:
        floor, target, anchor (all int, monthly in local currency)
        currency, floor_str, target_str, anchor_str (formatted)
        negotiation (dict): open_anchor_pct, current_salary_response,
                            if_pushed, scripts
        relocation_required (bool)
        total_comp_notes (str)
    """
    salary_cfg = _load_salary_config()
    work_auth = _load_work_auth()
    sal_neg = prep_cfg.get("salary_negotiation", {})
    total_comp = salary_cfg.get("total_comp", {})
    form_fill = salary_cfg.get("form_fill_rules", {})

    region = _get_region_cfg(salary_cfg, country, is_remote)
    base = salary_cfg.get("base_minimum", 10000)
    floor = int(region.get("minimum", base))
    target = int(region.get("target", salary_cfg.get("base_target", 12000)))

    open_pct = sal_neg.get("open_anchor_above_target_pct", 15)
    anchor = int(target * (1 + open_pct / 100))

    # PPP note for Europe
    ppp_note = ""
    if region.get("method") == "ppp_adjusted_per_country" and country:
        rate = salary_cfg.get("ppp_europe_adjustments", {}).get(country, 1.0)
        adj_floor = int(floor * rate)
        adj_target = int(target * rate)
        adj_anchor = int(anchor * rate)
        ppp_note = (
            f"PPP-adjusted for {country} (×{rate}): "
            f"floor {_fmt(adj_floor, currency)} / "
            f"target {_fmt(adj_target, currency)} / "
            f"anchor {_fmt(adj_anchor, currency)}"
        )

    # Work auth note
    auth_note = ""
    auth = work_auth.get(country or "Malaysia", {})
    if auth.get("requires_sponsorship"):
        auth_note = f"Sponsorship required in {country}. Confirm employer will sponsor before discussing salary."

    # Negotiation scripts
    current_sal_resp = sal_neg.get(
        "current_salary_response", "prefer to discuss at offer stage"
    )
    if_pushed = sal_neg.get("if_pushed_for_number", "anchor at target, not floor")

    script_they_ask = (
        f'Script — if they ask your expected salary:\n'
        f'"Based on market rates for this role in {country or "this market"} and the scope of '
        f'responsibilities, I\'m targeting {_fmt(anchor, currency)} per month. '
        f'I\'m flexible and open to discussing the full package including benefits."'
    )
    script_you_ask = (
        f'Script — if you bring it up:\n'
        f'"Before we go too far, I want to make sure we\'re aligned on compensation. '
        f'For a role at this level I\'d be looking at around {_fmt(anchor, currency)} monthly — '
        f'does that fit within the budgeted range?"'
    )
    script_current_sal = (
        f'Script — if asked current salary:\n'
        f'"I {current_sal_resp}. I\'m more focused on the total package and market rate '
        f'for the scope of this role."'
    )
    script_pushed = (
        f'Script — if pushed hard for a number:\n'
        f'"If I had to give a number, I\'d say {_fmt(target, currency)} is where I\'d need to land — '
        f'though I\'m really thinking about the overall package."'
    )

    # Total comp notes
    tc_notes_parts: list[str] = []
    if total_comp.get("include_bonus"):
        bonus_pct = total_comp.get("target_bonus_pct", 15)
        tc_notes_parts.append(f"Target bonus: {bonus_pct}% of base")
    if total_comp.get("include_allowances"):
        types = ", ".join(total_comp.get("allowance_types", []))
        tc_notes_parts.append(f"Allowances to ask about: {types}")
        car = total_comp.get("car_allowance_myr")
        if car:
            tc_notes_parts.append(f"Car allowance benchmark: MYR {car:,}/month")
    total_comp_notes = " | ".join(tc_notes_parts) if tc_notes_parts else "See total_comp config"

    return {
        "floor": floor,
        "target": target,
        "anchor": anchor,
        "currency": currency,
        "floor_str": _fmt(floor, currency),
        "target_str": _fmt(target, currency),
        "anchor_str": _fmt(anchor, currency),
        "open_anchor_pct": open_pct,
        "ppp_note": ppp_note,
        "auth_note": auth_note,
        "relocation_required": bool(region.get("relocation_required", False)),
        "relocation_condition": region.get("relocation_condition", ""),
        "scripts": {
            "they_ask": script_they_ask,
            "you_ask": script_you_ask,
            "current_salary": script_current_sal,
            "if_pushed": script_pushed,
        },
        "current_salary_response": current_sal_resp,
        "total_comp_notes": total_comp_notes,
        "range_buffer_pct": form_fill.get("range_buffer_pct", 20),
        "form_fill_anchor": _fmt(anchor, currency),
        "form_fill_range_top": _fmt(int(anchor * (1 + form_fill.get("range_buffer_pct", 20) / 100)), currency),
    }
