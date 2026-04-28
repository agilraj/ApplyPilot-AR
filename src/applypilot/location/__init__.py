"""Location classification and scoring for ApplyPilot."""

from applypilot.location.classifier import classify_job, classify_all_discovered
from applypilot.location.scorer import location_fit_score

__all__ = ["classify_job", "classify_all_discovered", "location_fit_score"]
