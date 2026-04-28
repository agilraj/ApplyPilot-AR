"""Dataclasses for tracker entities: Application, EmailEvent, StatusHistory.

These mirror the DB schema exactly. Fields are Optional where the column
allows NULL. Dataclasses are used for type hints and convenience — they are
not ORM models; queries.py handles all DB I/O directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Application:
    """One row in the applications table — full lifecycle of a job application."""

    id: Optional[int] = None
    job_title: str = ""
    company_name: str = ""
    company_domain: Optional[str] = None
    company_type: Optional[str] = None
    job_url: Optional[str] = None
    job_board: Optional[str] = None
    fit_score: Optional[float] = None
    resume_score: Optional[float] = None
    role_score: Optional[float] = None
    location_score: Optional[float] = None
    salary_score: Optional[float] = None
    resume_version_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    applied_email: Optional[str] = None
    applied_date: Optional[str] = None
    status: str = "discovered"

    # Location fields
    job_location_raw: Optional[str] = None
    job_location_city: Optional[str] = None
    job_location_country: Optional[str] = None
    job_is_remote: bool = False
    job_is_hybrid: bool = False
    hybrid_days_mentioned: Optional[int] = None
    location_tier: Optional[str] = None
    location_score_value: Optional[int] = None
    relocation_required: bool = False
    relocation_flag: Optional[str] = None

    # Salary fields
    salary_disclosed: bool = False
    salary_currency: Optional[str] = None
    salary_min_posted: Optional[int] = None
    salary_max_posted: Optional[int] = None
    salary_floor_applied: Optional[int] = None
    salary_target_applied: Optional[int] = None
    salary_gate_result: Optional[str] = None
    salary_inference_confidence: Optional[int] = None
    salary_estimated_min: Optional[int] = None
    salary_estimated_max: Optional[int] = None
    salary_form_submitted: Optional[int] = None
    ppp_rate_used: Optional[float] = None

    # Work auth
    sponsorship_required: bool = False
    sponsorship_confirmed: Optional[bool] = None

    # Email linker — Signal 1
    gmail_thread_id: Optional[str] = None
    thread_linked_date: Optional[str] = None

    # Email linker — Signal 2
    tag_matched: bool = False
    tag_match_date: Optional[str] = None

    # Email linker — Signal 3
    llm_extracted_company: Optional[str] = None
    llm_extracted_title: Optional[str] = None
    llm_match_confidence: Optional[int] = None

    # Fusion output
    link_confidence: Optional[int] = None
    link_method: Optional[str] = None

    # Interview fields
    interview_datetime: Optional[str] = None
    interview_type: Optional[str] = None
    interviewer_name: Optional[str] = None
    interviewer_email: Optional[str] = None
    interview_platform: Optional[str] = None
    prep_package_path: Optional[str] = None
    prep_generated_date: Optional[str] = None

    # Outcome
    outcome: Optional[str] = None
    outcome_date: Optional[str] = None
    offer_amount: Optional[int] = None
    offer_currency: Optional[str] = None
    notes: Optional[str] = None

    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class EmailEvent:
    """One row in email_events — a single inbound email processed by the linker."""

    id: Optional[int] = None
    application_id: Optional[int] = None
    gmail_message_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    received_date: Optional[str] = None
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    body_snippet: Optional[str] = None

    # Signal results
    signal1_fired: bool = False
    signal2_fired: bool = False
    signal2_tag: Optional[str] = None
    signal3_fired: bool = False
    signal3_company: Optional[str] = None
    signal3_title: Optional[str] = None
    signal3_intent: Optional[str] = None
    signal3_datetime: Optional[str] = None
    signal3_confidence: Optional[int] = None

    link_confidence: Optional[int] = None
    link_method: Optional[str] = None
    action_taken: Optional[str] = None
    requires_review: bool = False

    created_at: Optional[str] = None


@dataclass
class StatusHistory:
    """One row in status_history — an audit trail entry for a status transition."""

    id: Optional[int] = None
    application_id: Optional[int] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    triggered_by: Optional[str] = None
    email_event_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
