"""ApplyPilot Interview Prep Engine — Stage 7.

Triggered when an application's status changes to 'interview_scheduled'.
Generates a comprehensive markdown prep package in prep_packages/.

Public API:
    generate_prep(application_id, interviewer_name=None) -> str
        Build prep package for one application. Returns the output file path.

    check_and_trigger_all() -> list[str]
        Scan tracker DB for interview_scheduled applications with no prep package
        yet and generate one for each. Returns list of generated file paths.
"""

from applypilot.prep.engine import check_and_trigger_all, generate_prep

__all__ = ["generate_prep", "check_and_trigger_all"]
