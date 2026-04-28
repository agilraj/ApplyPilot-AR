"""PPP rate lookup and calculation.

Rates are sourced from salary.json (ppp_europe_adjustments, ppp_sea_adjustments).
The optional PPP_REFRESH_INTERVAL_DAYS env var controls how often an external
refresh is attempted (World Bank API). Falls back to config rates silently.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from applypilot.salary.config_loader import load_salary_config

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parents[4] / "config"
_CACHE_PATH = _CONFIG_DIR / "ppp_cache.json"
_REFRESH_DAYS = int(os.environ.get("PPP_REFRESH_INTERVAL_DAYS", "30"))


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            with _CACHE_PATH.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        log.warning("PPP cache write failed: %s", exc)


def _cache_is_fresh(cache: dict) -> bool:
    refreshed = cache.get("refreshed_at")
    if not refreshed:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(refreshed)).days
        return age < _REFRESH_DAYS
    except Exception:
        return False


def _try_fetch_world_bank(country_iso3: str) -> Optional[float]:
    """Attempt to fetch a PPP conversion factor from the World Bank API.

    Returns the factor (local currency per international dollar) or None on failure.
    This is best-effort and never raises.
    """
    try:
        import urllib.request
        url = (
            f"https://api.worldbank.org/v2/country/{country_iso3}/indicator/"
            f"PA.NUS.PPP?format=json&mrv=1"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            value = data[1][0].get("value")
            return float(value) if value is not None else None
    except Exception as exc:
        log.debug("World Bank PPP fetch failed for %s: %s", country_iso3, exc)
        return None


def get_ppp_rate(country: str) -> float:
    """Return the PPP adjustment multiplier for a given country name.

    Multiplier is relative to the Malaysia base (1.0 = same purchasing power as Malaysia).
    Values > 1.0 mean higher cost of living (higher salary required).
    Values < 1.0 mean lower cost of living (lower salary floor).

    Uses config file rates. World Bank refresh is attempted once per
    PPP_REFRESH_INTERVAL_DAYS but never blocks if unavailable.
    """
    cfg = load_salary_config()
    europe_rates: dict[str, float] = cfg.get("ppp_europe_adjustments", {})
    sea_rates: dict[str, float] = cfg.get("ppp_sea_adjustments", {})

    # Malaysia = base
    if country == "Malaysia":
        return 1.0

    # Check static config first
    if country in europe_rates:
        return europe_rates[country]
    if country in sea_rates:
        return sea_rates[country]

    # Singapore is fixed-rate in config regions
    if country == "Singapore":
        return 1.0  # handled separately by fixed floor

    # Unknown → neutral
    log.debug("No PPP rate found for country '%s', using 1.0", country)
    return 1.0


def apply_ppp(base_amount: int | float, country: str) -> int:
    """Return a PPP-adjusted floor amount in local purchasing-power terms.

    Example: apply_ppp(5000, "Germany") → 5000 * 1.0 = 5000 EUR
             apply_ppp(5000, "Poland")  → 5000 * 0.55 = 2750 EUR
    """
    rate = get_ppp_rate(country)
    return int(round(base_amount * rate))
