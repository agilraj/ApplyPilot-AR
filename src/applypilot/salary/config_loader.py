"""Load and validate salary.json and scoring_weights.json."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parents[4] / "config"


@lru_cache(maxsize=1)
def load_salary_config() -> dict:
    """Return the parsed salary.json config."""
    path = _CONFIG_DIR / "salary.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_scoring_weights() -> dict:
    """Return the parsed scoring_weights.json config."""
    path = _CONFIG_DIR / "scoring_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_work_auth() -> dict:
    """Return the parsed work_auth.json config."""
    path = _CONFIG_DIR / "work_auth.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_region_config(country: str | None, is_remote: bool = False) -> dict:
    """Return the salary config block for a given country/remote flag.

    Matching priority:
      1. Remote → "remote" region
      2. Exact country name in "regions" dict
      3. Europe (if country in priority_countries or ppp_europe_adjustments)
      4. SEA_other (if country in ppp_sea_adjustments)
      5. Fallback: "Malaysia" (base)
    """
    cfg = load_salary_config()
    regions = cfg.get("regions", {})

    if is_remote:
        return regions.get("remote", regions.get("Malaysia", {}))

    if not country:
        return regions.get("Malaysia", {})

    # Exact match
    if country in regions:
        return regions[country]

    # Europe PPP countries
    europe_countries = set(cfg.get("ppp_europe_adjustments", {}).keys())
    europe_priority = set(regions.get("Europe", {}).get("priority_countries", []))
    if country in europe_countries or country in europe_priority:
        return regions.get("Europe", {})

    # SEA other
    sea_countries = set(cfg.get("ppp_sea_adjustments", {}).keys())
    if country in sea_countries:
        return regions.get("SEA_other", {})

    # Fallback
    log.debug("No region config for country '%s', using Malaysia base.", country)
    return regions.get("Malaysia", {})


def get_floor_and_target(country: str | None, is_remote: bool = False) -> tuple[int, int]:
    """Return (floor_in_local_currency, target_in_local_currency) for a location."""
    region = get_region_config(country, is_remote)
    floor = region.get("minimum", load_salary_config().get("base_minimum", 10000))
    target = region.get("target", load_salary_config().get("base_target", 12000))
    return int(floor), int(target)


def get_salary_fit_scores() -> dict:
    """Return the salary_fit_scores mapping from scoring_weights.json."""
    return load_scoring_weights().get("salary_fit_scores", {})


def get_undisclosed_policy() -> dict:
    return load_salary_config().get("undisclosed_salary", {})
