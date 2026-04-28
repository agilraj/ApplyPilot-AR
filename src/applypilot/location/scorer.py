"""Location fit sub-score (0-10) from a classification tier.

Used by the scoring stage to contribute location_fit into the composite score.
"""

from __future__ import annotations

from applypilot.location.config_loader import get_location_scores, load_scoring_weights


def location_fit_score(tier: str, location_score_value: int | None = None) -> float:
    """Return a normalised 0-10 fit score for a given location tier.

    If location_score_value is provided (already computed by classifier), it is
    returned directly. Otherwise the tier is mapped via location_scores config.
    """
    if location_score_value is not None:
        return float(location_score_value)

    scores = get_location_scores()
    mapping = {
        "remote":     scores.get("remote", 10),
        "preferred":  scores.get("preferred_city_within_commute", 10),
        "acceptable": scores.get("acceptable_country_relocation", 5),
        "unknown":    scores.get("unknown_ambiguous", 3),
        "excluded":   scores.get("excluded", 0),
    }
    return float(mapping.get(tier, 3))


def weighted_location_contribution(tier: str, location_score_value: int | None = None) -> float:
    """Return the location component's weighted contribution to the overall score.

    Multiplies the 0-10 sub-score by the location_fit weight from scoring_weights.json.
    """
    weights = load_scoring_weights()
    weight = weights.get("location_fit", 0.15)
    raw = location_fit_score(tier, location_score_value)
    return round(raw * weight, 4)
