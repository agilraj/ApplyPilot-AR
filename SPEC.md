# ApplyPilot Enhancement Spec
**Owner:** Ray | **Role:** C&I Solar / Energy PM | **Base Location:** Kuala Lumpur, Malaysia  
**Version:** 1.0 | **Last Updated:** April 2026

---

## Overview

This spec extends ApplyPilot's existing 6-stage pipeline with five new modules:

1. **Application Tracker** — SQLite CRM logging every application end-to-end
2. **3-Signal Email Linker** — Gmail-based interview detection with confidence scoring
3. **Salary & Location Engine** — Global benchmarks with PPP adjustment and inference for undisclosed salaries
4. **Interview Prep Engine** — Auto-triggered Stage 7 prep package on interview confirmation
5. **Dashboard** — Unified CLI and HTML view across all regions and statuses

**Golden rule for Claude Code:** Only create files listed in Section 9 (File Structure). Do not modify existing ApplyPilot pipeline files except to append tracker write calls at their very end. All personal preferences live in config files — never hardcoded in Python.

---

## 1. Guiding Principles

- **Config-first:** All user preferences (salary, location, weights) live in `/config/*.json`. Python only reads them — never contains preference values directly.
- **Non-destructive:** New modules hook into existing stages via write calls appended at stage ends. Core pipeline logic is never modified.
- **Fail-safe:** When uncertain (low confidence email link, undisclosed salary, ambiguous location), the system flags for human review rather than acting or dropping silently.
- **Audit trail:** Every action — application, email event, status change — is logged with timestamp and source.

---

## 2. Salary Configuration

**File:** `config/salary.json`

```json
{
  "base_currency": "MYR",
  "base_minimum": 10000,
  "base_target": 12000,

  "regions": {
    "Malaysia": {
      "currency": "MYR",
      "minimum": 10000,
      "target": 12000,
      "method": "fixed",
      "hard_floor": true,
      "relocation_required": false
    },
    "Singapore": {
      "currency": "SGD",
      "minimum": 5000,
      "target": 7000,
      "method": "fixed",
      "hard_floor": true,
      "relocation_required": true,
      "relocation_condition": "package dependent"
    },
    "Australia": {
      "currency": "AUD",
      "minimum": 10000,
      "target": 12000,
      "method": "fixed",
      "hard_floor": true,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level",
      "note": "AUD 10k = ~MYR 28-30k, premium expected for relocation"
    },
    "Europe": {
      "currency": "EUR",
      "minimum": 5000,
      "target": 7000,
      "method": "ppp_adjusted_per_country",
      "base_eur_minimum": 5000,
      "base_eur_target": 7000,
      "hard_floor": true,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level",
      "priority_countries": ["Germany", "Netherlands", "UK", "Sweden"]
    },
    "SEA_other": {
      "method": "ppp_vs_malaysia",
      "base_reference": "Malaysia",
      "hard_floor": true,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    "remote": {
      "currency": "MYR",
      "minimum": 10000,
      "target": 12000,
      "method": "fixed",
      "note": "Remote always benchmarked against Malaysia regardless of company HQ"
    }
  },

  "ppp_europe_adjustments": {
    "Switzerland": 1.50,
    "Norway": 1.35,
    "Denmark": 1.20,
    "Sweden": 1.10,
    "Netherlands": 1.10,
    "Germany": 1.00,
    "France": 1.00,
    "UK": 0.95,
    "Belgium": 0.95,
    "Austria": 1.00,
    "Spain": 0.80,
    "Portugal": 0.75,
    "Poland": 0.55,
    "Czech Republic": 0.60,
    "Hungary": 0.55
  },

  "ppp_sea_adjustments": {
    "Thailand": 0.85,
    "Indonesia": 0.60,
    "Philippines": 0.65,
    "Vietnam": 0.55,
    "UAE": 0.95
  },

  "undisclosed_salary": {
    "action": "infer",
    "min_confidence_to_proceed": 40,
    "relevance_override_resume_score": 9.0,
    "relevance_override_min_confidence": 40
  },

  "total_comp": {
    "include_bonus": true,
    "target_bonus_pct": 15,
    "include_allowances": true,
    "allowance_types": ["travel", "phone", "housing", "car"],
    "car_allowance_myr": 1500,
    "include_equity": false
  },

  "form_fill_rules": {
    "always_anchor_at_target": true,
    "never_disclose_floor": true,
    "current_salary_response": "prefer to discuss",
    "range_buffer_pct": 20
  }
}
```

---

## 3. Location Configuration

**File:** `config/locations.json`

```json
{
  "remote": {
    "accept": true,
    "priority": 1
  },

  "preferred_locations": [
    {
      "city": "Kuala Lumpur",
      "country": "Malaysia",
      "priority": 2,
      "max_commute_minutes": 45,
      "relocation_required": false
    }
  ],

  "acceptable_locations": [
    {
      "country": "Malaysia",
      "note": "Anywhere in Malaysia",
      "priority": 3,
      "relocation_required": true,
      "relocation_condition": "package dependent"
    },
    {
      "country": "Singapore",
      "priority": 4,
      "relocation_required": true,
      "relocation_condition": "package dependent"
    },
    {
      "country": "Australia",
      "priority": 5,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    {
      "country": "Germany",
      "priority": 6,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    {
      "country": "Netherlands",
      "priority": 6,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    {
      "country": "UK",
      "priority": 6,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    {
      "country": "Sweden",
      "priority": 7,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    },
    {
      "country": "Thailand",
      "priority": 8,
      "relocation_required": true,
      "relocation_condition": "right package regardless of level"
    }
  ],

  "excluded_locations": [],

  "hybrid_policy": {
    "accept": true,
    "min_remote_days": 2
  },

  "scoring_location_weights": {
    "SEA": 0.15,
    "Australia": 0.20,
    "Europe": 0.25,
    "remote": 0.05
  },

  "location_scores": {
    "remote": 10,
    "preferred_city_within_commute": 10,
    "preferred_city_outside_commute": 7,
    "acceptable_country_no_relocation": 8,
    "acceptable_country_relocation": 5,
    "unknown_ambiguous": 3,
    "excluded": 0
  }
}
```

---

## 4. Scoring Weights Configuration

**File:** `config/scoring_weights.json`

```json
{
  "resume_fit": 0.45,
  "role_fit": 0.25,
  "location_fit": 0.15,
  "salary_fit": 0.15,

  "salary_fit_scores": {
    "disclosed_above_target": 10,
    "disclosed_at_target": 8,
    "disclosed_above_floor_below_target": 6,
    "disclosed_borderline": 3,
    "disclosed_below_floor": 0,
    "undisclosed_confidence_80_plus": 7,
    "undisclosed_confidence_60_to_79": 5,
    "undisclosed_confidence_40_to_59": 3,
    "undisclosed_confidence_below_40": 1,
    "undisclosed_no_inference": 2
  },

  "min_overall_score_to_apply": 6.0,
  "relevance_override_score_threshold": 9.0
}
```

---

## 5. Gmail Configuration

**File:** `config/gmail.json`

```json
{
  "poll_interval_minutes": 30,
  "tag_prefix": "AP",
  "base_email": "YOUR_GMAIL@gmail.com",
  "scopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify"
  ],

  "signal_weights": {
    "signal1_thread_match": 60,
    "signal2_tag_match": 70,
    "signal3_llm_match_strong": 40,
    "signal3_llm_match_moderate": 25,
    "signal3_llm_match_weak": 10,
    "agreement_bonus": 20
  },

  "confidence_thresholds": {
    "auto_link_and_act": 60,
    "link_flag_for_review": 30,
    "hold_unlinked": 0
  },

  "interview_keywords": [
    "interview", "schedule a call", "next steps",
    "shortlisted", "pleased to invite", "available for",
    "speak with you", "meet with you", "calendly",
    "book a time", "video call", "phone screen"
  ],

  "rejection_keywords": [
    "unfortunately", "not moving forward", "other candidates",
    "position has been filled", "not successful", "regret to inform"
  ]
}
```

---

## 6. Interview Prep Configuration

**File:** `config/prep.json`

```json
{
  "output_format": "markdown",
  "output_dir": "prep_packages",
  "auto_trigger_on_status": "interview_scheduled",

  "sections": {
    "company_brief": true,
    "role_analysis": true,
    "talking_points": true,
    "predicted_questions": true,
    "questions_to_ask": true,
    "salary_anchor": true,
    "interviewer_research": true,
    "compensation_intelligence": true,
    "relocation_intel": true
  },

  "salary_negotiation": {
    "never_disclose_floor": true,
    "open_anchor_above_target_pct": 15,
    "current_salary_response": "prefer to discuss at offer stage",
    "if_pushed_for_number": "anchor at target, not floor"
  },

  "web_search_sources": [
    "company about page",
    "linkedin company page",
    "recent news last 90 days",
    "glassdoor reviews",
    "linkedin salary insights"
  ]
}
```

---

## 7. Work Authorization Configuration

**File:** `config/work_auth.json`

```json
{
  "Malaysia": {
    "status": "citizen_or_PR",
    "requires_sponsorship": false
  },
  "Singapore": {
    "status": "requires_EP",
    "requires_sponsorship": true,
    "acceptable": true
  },
  "Australia": {
    "status": "requires_sponsorship",
    "visa_type": "482_TSS_or_186",
    "requires_sponsorship": true,
    "acceptable": true,
    "condition": "employer must sponsor"
  },
  "Europe": {
    "status": "requires_work_permit",
    "requires_sponsorship": true,
    "acceptable": true,
    "condition": "employer must sponsor",
    "eu_blue_card_eligible": true
  }
}
```

---

## 8. Database Schema

**File location:** `tracker.db` in project root (auto-created on first run)

### Table: applications

```sql
CREATE TABLE IF NOT EXISTS applications (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  job_title                 TEXT NOT NULL,
  company_name              TEXT NOT NULL,
  company_domain            TEXT,
  company_type              TEXT,
  job_url                   TEXT UNIQUE,
  job_board                 TEXT,
  fit_score                 REAL,
  resume_score              REAL,
  role_score                REAL,
  location_score            REAL,
  salary_score              REAL,
  resume_version_path       TEXT,
  cover_letter_path         TEXT,
  applied_email             TEXT,
  applied_date              DATETIME,
  status                    TEXT DEFAULT 'discovered',

  -- Location fields
  job_location_raw          TEXT,
  job_location_city         TEXT,
  job_location_country      TEXT,
  job_is_remote             BOOLEAN DEFAULT FALSE,
  job_is_hybrid             BOOLEAN DEFAULT FALSE,
  hybrid_days_mentioned     INTEGER,
  location_tier             TEXT,
  location_score_value      INTEGER,
  relocation_required       BOOLEAN DEFAULT FALSE,
  relocation_flag           TEXT,

  -- Salary fields
  salary_disclosed          BOOLEAN DEFAULT FALSE,
  salary_currency           TEXT,
  salary_min_posted         INTEGER,
  salary_max_posted         INTEGER,
  salary_floor_applied      INTEGER,
  salary_target_applied     INTEGER,
  salary_gate_result        TEXT,
  salary_inference_confidence INTEGER,
  salary_estimated_min      INTEGER,
  salary_estimated_max      INTEGER,
  salary_form_submitted     INTEGER,
  ppp_rate_used             REAL,

  -- Work auth fields
  sponsorship_required      BOOLEAN DEFAULT FALSE,
  sponsorship_confirmed     BOOLEAN,

  -- Signal 1 (thread match)
  gmail_thread_id           TEXT,
  thread_linked_date        DATETIME,

  -- Signal 2 (tagged address)
  tag_matched               BOOLEAN DEFAULT FALSE,
  tag_match_date            DATETIME,

  -- Signal 3 (LLM extraction)
  llm_extracted_company     TEXT,
  llm_extracted_title       TEXT,
  llm_match_confidence      INTEGER,

  -- Fusion output
  link_confidence           INTEGER,
  link_method               TEXT,

  -- Interview fields
  interview_datetime        DATETIME,
  interview_type            TEXT,
  interviewer_name          TEXT,
  interviewer_email         TEXT,
  interview_platform        TEXT,
  prep_package_path         TEXT,
  prep_generated_date       DATETIME,

  -- Outcome
  outcome                   TEXT,
  outcome_date              DATETIME,
  offer_amount              INTEGER,
  offer_currency            TEXT,
  notes                     TEXT,

  -- Timestamps
  created_at                DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at                DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Table: email_events

```sql
CREATE TABLE IF NOT EXISTS email_events (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id        INTEGER REFERENCES applications(id),
  gmail_message_id      TEXT UNIQUE,
  gmail_thread_id       TEXT,
  received_date         DATETIME,
  sender_email          TEXT,
  sender_name           TEXT,
  subject               TEXT,
  body_snippet          TEXT,

  -- Signal results
  signal1_fired         BOOLEAN DEFAULT FALSE,
  signal2_fired         BOOLEAN DEFAULT FALSE,
  signal2_tag           TEXT,
  signal3_fired         BOOLEAN DEFAULT FALSE,
  signal3_company       TEXT,
  signal3_title         TEXT,
  signal3_intent        TEXT,
  signal3_datetime      TEXT,
  signal3_confidence    INTEGER,

  link_confidence       INTEGER,
  link_method           TEXT,
  action_taken          TEXT,
  requires_review       BOOLEAN DEFAULT FALSE,

  created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Table: status_history

```sql
CREATE TABLE IF NOT EXISTS status_history (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id        INTEGER REFERENCES applications(id),
  from_status           TEXT,
  to_status             TEXT,
  triggered_by          TEXT,
  email_event_id        INTEGER REFERENCES email_events(id),
  notes                 TEXT,
  created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Status Flow

```
discovered → scored → tailored → applied → responded
  → interview_scheduled → interviewing
  → offer_received → accepted / declined
  → rejected / ghosted / withdrawn
```

---

## 9. Feature Descriptions

### Feature A: Application Tracker

**Purpose:** Write a record to the DB at the end of each pipeline stage.

**Stage hooks (append to end of existing stage files — do not modify core logic):**
- End of `score.py` → write application record with fit scores and salary/location gate results
- End of `tailor.py` → update record with resume_version_path
- End of `cover_letter.py` → update record with cover_letter_path
- End of `apply.py` → update status to `applied`, set applied_date and applied_email

**New CLI commands:**
- `applypilot status` → terminal table of all applications grouped by region and status
- `applypilot dashboard` → open HTML dashboard in browser

---

### Feature B: 3-Signal Email Linker

**Purpose:** Monitor Gmail inbox, link replies to application records, detect interview signals.

**Signal 1 — Thread Match:**
- Check if incoming email thread_id exists in `applications.gmail_thread_id`
- If yes: application_id is known, confidence += 60

**Signal 2 — Tagged Address Match:**
- Parse `To:` field for pattern `+AP{id}@` (e.g. `+AP1042@`)
- Extract numeric id, look up in applications table
- If found: confidence += 70

**Signal 3 — LLM Extraction:**
- Always runs on every new email regardless of Signal 1/2 results
- LLM prompt extracts: company name, job title, intent (interview/rejection/schedule/other), date/time if mentioned, confidence score
- Fuzzy match extracted company+title against applications table
- Confidence contribution: strong match +40, moderate +25, weak +10

**Fusion logic:**
- If Signal 2 fires: use its application_id as primary
- Else if Signal 1 fires: use its application_id as primary
- If Signal 3 also fires and agrees: add agreement bonus (+20)
- If Signal 1 and Signal 3 disagree: set confidence to 0, flag for manual review
- Final confidence >= 60: auto-link and act
- Final confidence 30-59: link but flag for review
- Final confidence < 30: hold unlinked, notify user

**On interview detected:**
- Update application status to `interview_scheduled`
- Extract and store interview_datetime, interview_type, interviewer_name if present
- Trigger prep engine

---

### Feature C: Salary & Location Engine

**Purpose:** Gate and score applications based on salary and location preferences from config files.

**Location filter (hook into Stage 1 - discover.py end):**
- Classify each job into: remote / preferred / acceptable / unknown / excluded
- Excluded jobs are dropped immediately
- Unknown locations are flagged for review (not dropped)
- Store location_tier and location_score_value in DB

**Salary gate (hook into Stage 3 - score.py end):**
- If salary disclosed: compare to floor for that location/currency
  - Below floor → set salary_gate_result = `below_floor`, drop from pipeline
  - Borderline (within 10% below floor) → flag, proceed
  - Above floor → proceed
- If salary not disclosed: run inference engine (5 signals below)
- If sponsorship required but job says no sponsorship → drop

**Salary inference engine (for undisclosed salaries):**

Signal 1 - Title benchmark: look up title in salary benchmarks dict for that country
Signal 2 - Company type multiplier: MNC 1.3x, mid-size 1.0x, SME 0.8x, GLC 0.9x, startup 0.7-1.1x
Signal 3 - JD complexity: LLM reads JD and scores seniority signals (team size, P&L, regional scope, years exp required)
Signal 4 - Historical DB: query applications table for similar title+location+company_type with disclosed salary
Signal 5 - External data: attempt Glassdoor/LinkedIn salary lookup via web search

Combine signals into estimated range and confidence score (0-100).
Map confidence to salary_score using scoring_weights.json table.

**Relevance override:**
- If resume_score >= relevance_override_score_threshold AND job domain matches C&I solar/energy/CaaS AND salary inference confidence >= 40
- Then proceed regardless of salary uncertainty, flag as "salary TBC at interview"

**Form fill:**
- For salary form fields: always use target not floor
- For range fields: target to target * (1 + range_buffer_pct/100)
- For current salary: use current_salary_response value from config
- For sponsorship questions: answer from work_auth.json

---

### Feature D: Interview Prep Engine

**Purpose:** Auto-generate a comprehensive prep package when interview_scheduled status is set.

**Trigger:** Status change to `interview_scheduled` (from email linker or manual CLI update)

**Output file:** `prep_packages/{application_id}_{company}_{YYYY-MM-DD}.md`

**Prep package sections (controlled by config/prep.json):**

1. **Company Brief** — web search: company name + "about" + "recent news" last 90 days. Extract: business model, key clients, headcount, funding stage, recent announcements.

2. **Role Analysis** — LLM reads stored JD. Extract: must-have skills, nice-to-haves, inferred pain points, likely "success in 90 days" definition, seniority signals.

3. **Talking Points** — LLM maps profile.json experience to JD requirements. For each key requirement, surface the best matching experience bullet. Never fabricate — only use resume_facts.

4. **Predicted Questions** — LLM generates: 5 behavioral, 3 technical, 3 situational questions based on JD and role type. For each: suggested answer framework using candidate's background.

5. **Questions to Ask** — LLM generates 5 specific questions based on company context and role. Must reference actual company details — not generic.

6. **Salary Anchor** — calculate from salary.json for that location:
   - Your floor: from config (displayed as reminder, labeled "never disclose")
   - Your anchor: target * 1.15 (open with this)
   - Your target: from config (acceptable close)
   - Script for when they ask first vs when to bring it up

7. **Interviewer Research** — if interviewer_name stored: web search LinkedIn profile, tenure, background summary.

8. **Compensation Intelligence** — estimated market range from Signal 4/5 of inference engine. Cost of living note if relocation required.

9. **Relocation Intel** — only if relocation_required = true: cost of living comparison vs KL, visa/permit requirements, housing market note.

---

### Feature E: Dashboard

**Purpose:** Unified view of all applications across regions and statuses.

**CLI view (`applypilot status`):**
- Table grouped by: region → status → score descending
- Columns: ID, Company, Title, Location, Score, Salary Gate, Status, Last Activity
- Summary line: total applied, response rate, interview rate

**HTML dashboard (`applypilot dashboard`):**
- Kanban-style: Applied | Responded | Interview | Offer | Closed
- Each card shows: company, title, location flag emoji, score badge, salary status, days since applied
- Color coded: green = above target salary, yellow = borderline/undisclosed, red = below floor (should not appear — these are dropped)
- Flagged items (review needed) shown in separate "Needs Attention" section at top

---

## 10. File Structure

### New files to create — do not create anything outside this list

```
config/
├── salary.json                   ← Section 2 of this spec
├── locations.json                ← Section 3 of this spec
├── scoring_weights.json          ← Section 4 of this spec
├── gmail.json                    ← Section 5 of this spec
├── prep.json                     ← Section 6 of this spec
└── work_auth.json                ← Section 7 of this spec

src/applypilot/
├── tracker/
│   ├── __init__.py
│   ├── db.py                     ← SQLite connection, schema init, migrations
│   ├── models.py                 ← Application, EmailEvent, StatusHistory dataclasses
│   └── queries.py                ← All DB read/write/update functions
│
├── email_linker/
│   ├── __init__.py
│   ├── gmail_client.py           ← Gmail API OAuth, polling loop
│   ├── signal1_thread.py         ← Thread ID matching logic
│   ├── signal2_tag.py            ← Tagged address parsing and lookup
│   ├── signal3_llm.py            ← LLM extraction and fuzzy matching
│   ├── fusion.py                 ← Confidence scoring and action router
│   └── test_inbox.py             ← Standalone test: process last 10 emails
│
├── salary/
│   ├── __init__.py
│   ├── config_loader.py          ← Load and validate salary.json
│   ├── ppp.py                    ← PPP rate lookup and calculation
│   ├── inference.py              ← 5-signal salary estimation engine
│   └── gate.py                   ← Salary gate decision logic
│
├── location/
│   ├── __init__.py
│   ├── config_loader.py          ← Load and validate locations.json
│   ├── classifier.py             ← Classify job location into tier
│   └── scorer.py                 ← Location fit sub-score
│
├── prep/
│   ├── __init__.py
│   ├── engine.py                 ← Stage 7 orchestrator
│   ├── company_research.py       ← Web search company brief
│   ├── role_analysis.py          ← JD analysis and talking points
│   ├── questions.py              ← Predicted questions and answers
│   ├── salary_anchor.py          ← Negotiation prep from salary config
│   └── renderer.py               ← Markdown file output
│
└── dashboard/
    ├── __init__.py
    ├── cli_view.py               ← Terminal table for applypilot status
    └── html_view.py              ← HTML dashboard for applypilot dashboard

prep_packages/                    ← Auto-created, stores generated prep .md files
tracker.db                        ← Auto-created SQLite database
```

---

## 11. Do Not Touch — Existing ApplyPilot Files

Claude Code must NOT modify the core logic of any of these files. Only append tracker/salary/location hook calls at their very end:

```
src/applypilot/discover.py      ← append: location_classifier.classify(job) + tracker write
src/applypilot/enrich.py        ← do not touch
src/applypilot/score.py         ← append: salary_gate.evaluate(job) + tracker write with scores
src/applypilot/tailor.py        ← append: tracker update with resume_version_path
src/applypilot/cover_letter.py  ← append: tracker update with cover_letter_path
src/applypilot/apply.py         ← append: tracker update status=applied, applied_email, applied_date
```

---

## 12. CLI Commands Reference (New + Extended)

```bash
# Tracker
applypilot status                           # terminal table of all applications
applypilot dashboard                        # open HTML dashboard in browser
applypilot update --id 1042 --status interviewing --date "2026-04-28 10:00"
applypilot update --id 1042 --outcome rejected
applypilot update --id 1042 --outcome offer --amount 13000 --currency MYR

# Email linker
applypilot email --watch                    # start polling daemon
applypilot email --test                     # process last 10 emails, show signal results
applypilot email --link --id 1042 --thread THREAD_ID   # manual link

# Interview prep
applypilot prep --id 1042                   # generate prep package for application ID
applypilot prep --id 1042 --interviewer "John Smith"   # with known interviewer

# Salary / Location
applypilot salary --estimate --id 1042      # show salary inference result for an application
applypilot location --check "Bangkok, Thailand"         # show location tier and PPP floor
```

---

## 13. Session Plan for Claude Code

Execute in this order. Each session is independent — test before proceeding to next.

### Session 1 — Foundation (Tracker)
**Goal:** Build tracker module and hook into pipeline stages.
**Instruction to Claude Code:**
```
Read SPEC.md. Build Session 1 only.
Create all files under src/applypilot/tracker/ and 
config/ files from Sections 2-7 of the spec.
Then append tracker write calls to the end of 
discover.py, score.py, tailor.py, cover_letter.py, 
and apply.py — do not modify their core logic.
After each file, confirm what was created.
Run a dry-run to verify DB writes work.
```
**Test:** `applypilot run --dry-run` then `applypilot status`

### Session 2 — Salary + Location Engine
**Goal:** Build salary and location modules, hook into scoring.
**Instruction to Claude Code:**
```
Read SPEC.md. Build Session 2 only: 
src/applypilot/salary/ and src/applypilot/location/.
Hook location classifier into discover.py end.
Hook salary gate + inference into score.py end.
Do not modify any other files.
```
**Test:** `applypilot run --dry-run --min-score 1` and check DB for location_tier and salary_gate_result

### Session 3 — Email Linker
**Goal:** Build Gmail watcher with 3-signal fusion.
**Instruction to Claude Code:**
```
Read SPEC.md. Build Session 3 only:
src/applypilot/email_linker/.
Set up Gmail API OAuth flow using credentials from .env.
Implement all 3 signals and fusion layer.
Build test_inbox.py to process last 10 emails 
against tracker DB and print signal results.
Do not modify any other files.
```
**Test:** `python src/applypilot/email_linker/test_inbox.py`

### Session 4 — Interview Prep Engine
**Goal:** Build prep package generator triggered by status change.
**Instruction to Claude Code:**
```
Read SPEC.md. Build Session 4 only:
src/applypilot/prep/.
Trigger: application status changes to interview_scheduled.
Output: markdown file in prep_packages/ directory.
Use Gemini API (already configured) for LLM calls.
Use web search for company research sections.
Do not modify any other files.
```
**Test:** `applypilot update --id 1 --status interview_scheduled` then check prep_packages/

### Session 5 — Dashboard
**Goal:** Build CLI status view and HTML dashboard.
**Instruction to Claude Code:**
```
Read SPEC.md. Build Session 5 only:
src/applypilot/dashboard/.
Extend existing applypilot status command with new fields.
Build HTML dashboard for applypilot dashboard command.
Group by region and status. Include flagged items section.
Do not modify any other files.
```
**Test:** `applypilot status` and `applypilot dashboard`

---

## 14. Environment Variables Required

Add to `.env` file:

```
# Existing (from applypilot init)
GEMINI_API_KEY=your_key_here
LLM_MODEL=gemini-1.5-flash
CAPSOLVER_API_KEY=optional

# New (add these)
GMAIL_CLIENT_ID=your_gmail_oauth_client_id
GMAIL_CLIENT_SECRET=your_gmail_oauth_client_secret
GMAIL_BASE_EMAIL=your_gmail@gmail.com
PPP_REFRESH_INTERVAL_DAYS=30
```

---

## 15. Notes for Claude Code

- Always load config files using `json.load()` — never hardcode salary numbers or location names in Python
- SQLite DB path: always relative to project root as `tracker.db`
- For LLM calls in new modules: use the same Gemini client pattern already used in existing pipeline stages — check score.py or tailor.py for the pattern
- PPP rates: fetch from World Bank API or Numbeo on first run, cache locally in `config/ppp_cache.json`, refresh per PPP_REFRESH_INTERVAL_DAYS
- Email linker daemon: write a PID file to prevent multiple instances running simultaneously
- All datetime values: store as ISO 8601 strings in SQLite (Python's datetime.isoformat())
- Fuzzy matching for company names: use `rapidfuzz` library (pip install rapidfuzz) — threshold 85 for strong match, 70 for moderate
- Never log or store email body content beyond the 500-char snippet for privacy
