"""Load and validate locations.json and scoring_weights.json."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parents[3] / "config"


@lru_cache(maxsize=1)
def load_locations() -> dict:
    """Return the parsed locations.json config."""
    path = _CONFIG_DIR / "locations.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_scoring_weights() -> dict:
    """Return the parsed scoring_weights.json config."""
    path = _CONFIG_DIR / "scoring_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_location_scores() -> dict:
    """Return the location_scores mapping from locations.json."""
    return load_locations().get("location_scores", {})


def get_preferred_locations() -> list[dict]:
    return load_locations().get("preferred_locations", [])


def get_acceptable_locations() -> list[dict]:
    return load_locations().get("acceptable_locations", [])


def get_excluded_locations() -> list[str]:
    return load_locations().get("excluded_locations", [])


def is_remote_accepted() -> bool:
    return load_locations().get("remote", {}).get("accept", True)


def get_hybrid_policy() -> dict:
    return load_locations().get("hybrid_policy", {"accept": True, "min_remote_days": 2})
