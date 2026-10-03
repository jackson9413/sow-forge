"""
sow-forge — Local-first Statement of Work (SOW) reviewer for freelancers & consultants.

Paste a SOW (Scope of Work) text or upload a .txt / .md file. Get back:

  * A detected SOW profile (engagement type, fee model, governing law, parties,
    term, project type, deliverables count, etc.).
  * A 0–100 client-friendliness score across 6 weighted axes
    (payment safety / scope clarity / IP & ownership / risk allocation /
    termination freedom / compliance & legal hygiene) with 5 verdict bands
    from `client_favorable` → `mostly_fair` → `needs_negotiation` →
    `high_risk` → `do_not_sign`.
  * Red flag detection across ~35 clause-pattern categories (payment terms,
    kill fee, late-payment interest, IP assignment, work-for-hire, NDA,
    non-compete, indemnification, liability cap, warranty, acceptance,
    change orders, force majeure, termination for convenience, auto-renewal,
    venue, attorneys' fees, etc.) — each with severity (high/medium/low),
    risk score, evidence quote, and a plain-English explanation.
  * Missing-clause detection — clauses a healthy SOW should have but the
    submitted SOW is missing (e.g. kill fee change, missing late-payment
    interest, missing acceptance criteria, missing IP ownership, missing
    liability cap, missing termination clause, missing change-order process,
    missing force majeure, etc.) — severity-ranked.
  * Risk-pattern detector: payment > 60 days net, uncapped liability,
    IP assignment including pre-existing IP, non-compete beyond 6 months,
    indemnification without cap, governing law = client state with venue
    in client city, exclusivity / non-solicit during term + 12mo after,
    automatic renewal > 12 months, kill fee < 25% of project value, etc.
  * Negotiation-language suggestions — for each top red flag, a ready-to-paste
    counter-proposal paragraph.
  * A 1-page Markdown summary suitable for forwarding to a lawyer or pasting
    into Slack with the client.
  * JSON export.
  * SQLite session log with 1-5 star ratings + free-text notes (so you can
    keep track of how different SOWs compare over time).

Local-first: no API keys, no LLM, no telemetry. Just paste + analyze.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

# ---------------------------------------------------------------------------
# Config & paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "sow_forge.db"

# Cap on raw input we keep in memory (defensive). ~500 KB is plenty for any SOW.
MAX_INPUT_BYTES = 500_000

app = Flask(__name__, static_folder="static", template_folder="templates")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Per-request SQLite connection."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create tables on first run."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT    NOT NULL,
            engagement_type TEXT,
            fee_model     TEXT,
            parties       TEXT,
            project_type  TEXT,
            raw_input     TEXT    NOT NULL,
            output_json   TEXT    NOT NULL,
            score         INTEGER,
            verdict       TEXT,
            rating        INTEGER,
            notes         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_score    ON sessions(score);
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

# Engagement-type keywords.
ENGAGEMENT_TYPE_SIGNALS = {
    "fixed_price": [
        r"\bfixed[- ]price\b",
        r"\blump[- ]sum\b",
        r"\bproject[- ]based\b",
        r"\bmilestone[- ]based\b",
    ],
    "time_and_materials": [
        r"\btime[- ](?:and|&)[- ]materials?\b",
        r"\bT&M\b",
        r"\b hourly\b",
        r"\b hourly rate\b",
        r"\bbillable hours?\b",
    ],
    "retainer": [
        r"\bretainer\b",
        r"\bmonthly retainer\b",
        r"\bstanding retainer\b",
    ],
    "deliverable_based": [
        r"\bdeliverable[- ]based\b",
        r"\bper[- ]deliverable\b",
        r"\beach deliverable\b",
    ],
}

# Fee-model signals.
FEE_MODEL_SIGNALS = {
    "fixed": [
        r"\bfixed fee\b",
        r"\btotal fee of\b",
        r"\bproject fee\b",
        r"\bflat fee\b",
        r"\$\s?[\d,]+\s+(?:total|for the project|all[- ]in)\b",
    ],
    "hourly": [
        r"\$\s?\d+\s?(?:/|per)\s?hour",
        r"\b(?:hourly|hour[- ]based)\b",
        r"\b\$\s?\d+\s*/\s*hr\b",
    ],
    "milestone": [
        r"\bmilestone\b",
        r"\bphase\s+\d+\b",
        r"\bupon (?:completion|delivery|approval)\b",
    ],
    "retainer": [
        r"\bretainer\b",
        r"\bmonthly fee\b",
        r"\bstanding engagement\b",
    ],
    "royalty": [
        r"\broyalty\b",
        r"\bpercentage of (?:revenue|sales|net)\b",
    ],
    "equity": [
        r"\bequity\b",
        r"\bstock options?\b",
        r"\b(?:vesting|cliff)\b",
    ],
}

# Term / duration signals.
TERM_SIGNALS = [
    (r"(\d+)\s*(?:-|to)?\s*month", "months"),
    (r"(\d+)\s*(?:-|to)?\s*week", "weeks"),
    (r"(\d+)\s*(?:-|to)?\s*day", "days"),
    (r"(\d+)\s*(?:-|to)?\s*year", "years"),
]

GOVERNING_LAW_RE = re.compile(
    r"governed by (?:and construed according to )?the laws? of (?:the )?(?:state of )?([A-Z][a-zA-Z ]+?)(?:[,.;]|\s+without regard)",
    re.IGNORECASE,
)
VENUE_RE = re.compile(
    r"(?:venue|jurisdiction|exclusive jurisdiction|courts? located)[: ]+(?:in )?(?:the )?(?:state of )?([A-Z][a-zA-Z ]+?)(?:[,.;]|\s+shall)",
    re.IGNORECASE,
)
PARTIES_RE = re.compile(
    r"(?:between|by and between)\s+([A-Z][A-Za-z0-9 &,'.\-]{1,80}?)\s+(?:\(\"?[\w &'.,\-]+\"?\)\s+)?(?:and|&)\s+([A-Z][A-Za-z0-9 &,'.\-]{1,80}?)(?:\s+\(\"?[\w &'.,\-]+\"?\))?(?=[,.;\n])",
)
EFFECTIVE_DATE_RE = re.compile(
    r"effective (?:as of |date )?([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
PROJECT_VALUE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
DEADLINES_RE = re.compile(
    r"(?:due|completion|deliver(?:y|able))[: ]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d+\s+(?:business\s+)?days?(?:\s+from\s+\w+)?)",
    re.IGNORECASE,
)


def detect_signals(text: str, signal_map: dict[str, list[str]]) -> dict[str, int]:
    """Count regex hits per category; return {category: hits}."""
    counts: dict[str, int] = {k: 0 for k in signal_map}
    for category, patterns in signal_map.items():
        for p in patterns:
            counts[category] += len(re.findall(p, text, re.IGNORECASE))
    return counts


def top_or_none(counts: dict[str, int]) -> str | None:
    """Return the category with the highest count, or None if all zero."""
    nonzero = {k: v for k, v in counts.items() if v > 0}
    if not nonzero:
        return None
    # Highest count wins; ties broken by deterministic order (insertion order).
    best = max(nonzero.items(), key=lambda kv: kv[1])
    return best[0]


def extract_governing_law(text: str) -> str | None:
    m = GOVERNING_LAW_RE.search(text)
    return m.group(1).strip() if m else None


def extract_venue(text: str) -> str | None:
    m = VENUE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_parties(text: str) -> tuple[str | None, str | None]:
    m = PARTIES_RE.search(text)
    if not m:
        return None, None
    a = m.group(1).strip().rstrip(",")
    b = m.group(2).strip().rstrip(",")
    return a, b


def extract_effective_date(text: str) -> str | None:
    m = EFFECTIVE_DATE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_term(text: str) -> dict | None:
    # First numeric+unit phrase in duration context
    candidates = []
    for pat, unit in TERM_SIGNALS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                n = int(m.group(1))
            except (ValueError, IndexError):
                continue
            candidates.append((n, unit))
    if not candidates:
        return None
    n, unit = candidates[0]
    return {"value": n, "unit": unit}


def extract_project_value(text: str) -> float | None:
    """Sum all $-amounts; return the largest one (likely the project fee)."""
    amounts = []
    for m in PROJECT_VALUE_RE.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
            amounts.append(v)
        except ValueError:
            continue
    return max(amounts) if amounts else None


def extract_deliverable_count(text: str) -> int:
    # Count "Deliverable #1" / "Phase 1" / "Milestone 1" patterns
    patterns = [
        r"\bdeliverable\s+(?:#|no\.?|number)?\s*\d+\b",
        r"\bmilestone\s+(?:#|no\.?|number)?\s*\d+\b",
        r"\bphase\s+(?:#|no\.?|number)?\s*\d+\b",
        r"\btask\s+(?:#|no\.?|number)?\s*\d+\b",
    ]
    total = 0
    seen = set()
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            key = (p, m.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            total += 1
    return total


def extract_deadlines(text: str) -> list[str]:
    seen = []
    for m in DEADLINES_RE.finditer(text):
        d = m.group(1).strip()
        if d and d not in seen:
            seen.append(d)
    return seen[:8]


# ---------------------------------------------------------------------------
# Red-flag detector
# ---------------------------------------------------------------------------

# Each clause pattern is a dict with:
#   id, category, label (regex), description (plain English),
#   severity (high/medium/low), score (deduction from 100, capped per axis),
#   axis (which of the 6 weighted axes it impacts),
#   negotiation (ready-to-paste counter-proposal language)

CLAUSE_PATTERNS = [
    # -------- PAYMENT SAFETY --------
    {
        "id": "payment_net_60_plus",
        "category": "payment",
        "label": r"net[- ]?(?:[6-9]\d|[1-9]\d{2,})",
        "description": (
            "Payment terms are net 60+ days. Long net terms starve freelancers "
            "of cashflow. Industry-healthy SOWs use net 15 or net 30."
        ),
        "severity": "high",
        "score": 14,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'Payment shall be due net 15 days from invoice date. "
            "Invoices not paid within 30 days shall accrue interest at 1.5% "
            "per month.'"
        ),
    },
    {
        "id": "payment_net_45_59",
        "category": "payment",
        "label": r"net[- ]?(?:4[5-9])",
        "description": (
            "Payment terms are net 45–59 days. Aggressive for a freelance engagement."
        ),
        "severity": "medium",
        "score": 6,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'Payment terms shall be net 30 days from receipt of invoice. "
            "Late payments accrue interest at the rate of 1% per month.'"
        ),
    },
    {
        "id": "no_kill_fee",
        "category": "payment",
        "label": r"(?<!no )(?:kill fee|cancellation fee|early termination fee)",
        "description": (
            "A kill fee / cancellation fee exists. Verify it is at least 25% of "
            "the project value if cancelled before kickoff, or 50% after kickoff."
        ),
        "severity": "low",
        "score": 0,
        "axis": "payment_safety",
        "is_check_present": "kill_fee_present",
        "negotiation": (
            "Counter: 'If Client terminates this Agreement prior to completion for "
            "any reason other than Contractor's uncured material breach, Client "
            "shall pay (a) 50% of the next milestone if cancelled after kickoff, "
            "or (b) 25% of the total project fee if cancelled before kickoff.'"
        ),
    },
    {
        "id": "kill_fee_present_too_low",
        "category": "payment",
        "label": r"kill fee.{0,80}?(?:1[0-9]|[1-9])%",
        "description": (
            "Kill fee is below 25% of project value. Below 25% doesn't cover "
            "the opportunity cost of a cancelled engagement."
        ),
        "severity": "medium",
        "score": 6,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'Kill fee shall be 50% of any unpaid milestone work "
            "performed plus 25% of remaining project fee.'"
        ),
    },
    {
        "id": "no_late_payment_interest",
        "category": "payment",
        "label": r"(?:interest|late charge|late fee)\s*(?:of|shall|at)?\s*[\d.]+%",
        "description": (
            "Late-payment interest / late fee present. Verify the rate is at "
            "least 1% per month (12% APR); anything below 1% is a polite request, "
            "not a deterrent."
        ),
        "severity": "low",
        "score": 0,
        "axis": "payment_safety",
        "is_check_present": "late_payment_interest_present",
        "negotiation": (
            "Counter: 'Invoices unpaid more than 15 days past due shall accrue "
            "interest at 1.5% per month, compounded monthly.'"
        ),
    },
    {
        "id": "deposit_required",
        "category": "payment",
        "label": r"(?:deposit|retainer (?:fee|payment)|upfront payment).+?(?:\d{1,3}\s*%)",
        "description": (
            "Deposit / retainer / upfront payment is required. Healthy SOWs "
            "require 30-50% at signing to align incentives."
        ),
        "severity": "low",
        "score": 0,
        "axis": "payment_safety",
        "is_check_present": "deposit_present",
        "negotiation": (
            "Counter: '50% of the project fee shall be due upon execution of "
            "this SOW. The remaining 50% shall be invoiced in milestone payments.'"
        ),
    },
    {
        "id": "no_deposit",
        "category": "payment",
        "label": r"no\s+(?:deposit|upfront|advance)\s+(?:payment|fee|payment\s+is)\s+required",
        "description": (
            "SOW explicitly waives deposit / upfront payment. This is a yellow "
            "flag if you are the service provider — you are funding the work."
        ),
        "severity": "medium",
        "score": 8,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'A non-refundable deposit of 33% of the total fee shall "
            "be due at signing to reserve project capacity.'"
        ),
    },
    {
        "id": "deposit_under_30",
        "category": "payment",
        "label": r"(?:deposit|upfront).{0,80}?(?:[0-9]|[12][0-9])\s*%",
        "description": (
            "Deposit is below 30% of project value. Below 30% doesn't "
            "meaningfully align client incentives."
        ),
        "severity": "low",
        "score": 3,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'A non-refundable deposit of 50% of the total fee shall "
            "be due at signing to reserve project capacity.'"
        ),
    },
    {
        "id": "payment_dependent_on_acceptance",
        "category": "payment",
        "label": r"(?:payment|invoice)\s+(?:is|shall be)\s+(?:conditioned|contingent|subject to)\s+(?:client|customer)?\s*acceptance",
        "description": (
            "Payment is contingent on client acceptance. Without an objective "
            "acceptance SLA, this can be weaponized to delay payment indefinitely."
        ),
        "severity": "high",
        "score": 10,
        "axis": "payment_safety",
        "negotiation": (
            "Counter: 'Payment is due net 15 from invoice date regardless of "
            "acceptance. If Client fails to provide written acceptance or "
            "rejection within 10 business days of delivery, the deliverable "
            "shall be deemed accepted.'"
        ),
    },
    # -------- SCOPE CLARITY --------
    {
        "id": "vague_scope",
        "category": "scope",
        "label": r"(?:assist with|as needed|as requested|best efforts)\b",
        "description": (
            "Scope contains vague obligation language ('as needed', 'reasonable', "
            "'best efforts'). These are unenforceable obligations dressed as scope."
        ),
        "severity": "medium",
        "score": 8,
        "axis": "scope_clarity",
        "negotiation": (
            "Counter: Replace 'as needed / reasonable' with explicit deliverable "
            "lists, hours caps, and revision-round caps."
        ),
    },
    {
        "id": "scope_assistance_only",
        "category": "scope",
        "label": r"\bassist(?:ance)?\b.{0,40}\b(?:provided|furnished|rendered)\b",
        "description": (
            "Scope is described as 'assistance' rather than deliverables. This "
            "is a scope-creepy-confirming clause — you can be pulled into "
            "endless tangential work."
        ),
        "severity": "medium",
        "score": 5,
        "axis": "scope_clarity",
        "negotiation": (
            "Counter: 'Scope is limited to the Deliverables listed in Exhibit A. "
            "Any work outside Exhibit A is a Change Order.'"
        ),
    },
    {
        "id": "no_revisions_cap",
        "category": "scope",
        "label": r"(?:revisions?|rework|round).{0,80}(?:unlimited|as many|reasonable)",
        "description": (
            "Revisions / rework are described as 'unlimited' or 'reasonable'. "
            "A healthy SOW caps revisions at 2-3 rounds."
        ),
        "severity": "medium",
        "score": 5,
        "axis": "scope_clarity",
        "negotiation": (
            "Counter: 'Scope includes up to two (2) rounds of revisions per "
            "deliverable. Additional revisions are billed at the hourly rate.'"
        ),
    },
    {
        "id": "revisions_capped",
        "category": "scope",
        "label": r"(?:two|2|three|3)\s*(?:rounds?|cycles?)\s+of\s+(?:revisions?|rework)",
        "description": (
            "Revisions are capped at 2-3 rounds. Healthy clause."
        ),
        "severity": "low",
        "score": 0,
        "axis": "scope_clarity",
        "is_check_present": "revisions_capped_present",
    },
    {
        "id": "no_change_order_process",
        "category": "scope",
        "label": r"change\s+orders?",
        "description": (
            "Change-order process present. Verify it requires written sign-off "
            "and a fee estimate before work begins."
        ),
        "severity": "low",
        "score": 0,
        "axis": "scope_clarity",
        "is_check_present": "change_order_present",
        "negotiation": (
            "Counter: 'Change Orders require written authorization signed by "
            "both parties with associated fee and schedule impact before work "
            "begins on the change.'"
        ),
    },
    # -------- IP & OWNERSHIP --------
    {
        "id": "ip_assignment_broad",
        "category": "ip",
        "label": r"(?:assigns?|conveys?|transfers?)\s+(?:all|any)\s+(?:rights?|interest|title)\s+in",
        "description": (
            "Broad IP assignment ('all rights, title, and interest'). This "
            "usually includes pre-existing IP and is hard to undo."
        ),
        "severity": "high",
        "score": 12,
        "axis": "ip_ownership",
        "negotiation": (
            "Counter: 'Contractor assigns to Client all right, title, and "
            "interest in Work Product specifically created for this engagement, "
            "excluding Contractor's pre-existing tools, libraries, and methodologies, "
            "which are licensed to Client on a non-exclusive, perpetual, "
            "royalty-free basis.'"
        ),
    },
    {
        "id": "work_for_hire",
        "category": "ip",
        "label": r"work[- ]for[- ]hire\b",
        "description": (
            "Work-for-hire language present. In US law this only applies to "
            "specific categories (e.g. contribution to a collective work); for "
            "freelance work it is often ineffective. Verify it doesn't silently "
            "sweep in pre-existing IP."
        ),
        "severity": "medium",
        "score": 4,
        "axis": "ip_ownership",
        "negotiation": (
            "Counter: 'Where Work Product qualifies as a Work Made for Hire "
            "under 17 U.S.C. § 101, Contractor assigns all such Work Product. "
            "Pre-existing IP remains the property of Contractor.'"
        ),
    },
    {
        "id": "ip_includes_pre_existing",
        "category": "ip",
        "label": r"(?:all|any).{0,80}(?:pre[- ]existing|background|prior)\s+(?:ip|tools?|code|libraries?)",
        "description": (
            "IP language appears to capture pre-existing IP / background IP. "
            "You would be giving the client tools you built before the engagement."
        ),
        "severity": "high",
        "score": 12,
        "axis": "ip_ownership",
        "negotiation": (
            "Counter: 'Excluded from assignment are Contractor's pre-existing "
            "tools, libraries, frameworks, and methodologies, whether developed "
            "before or during this engagement.'"
        ),
    },
    {
        "id": "portfolio_rights_carved",
        "category": "ip",
        "label": r"(?:retain(?:s|ing)?\s+(?:the\s+)?right\s+to\s+(?:use|reference|display)|may\s+(?:use|reference|display)\s+in\s+portfolio|portfolio\s+(?:rights?|carve[- ]out)|case\s+stud(?:y|ies)\s+(?:rights?|carve[- ]out))",
        "description": (
            "Contractor retains portfolio / showcase rights. Healthy carve-out."
        ),
        "severity": "low",
        "score": 0,
        "axis": "ip_ownership",
        "is_check_present": "portfolio_rights_carved",
    },
    {
        "id": "no_portfolio_rights",
        "category": "ip",
        "label": r"(?:confidentiality|nda|non[- ]disclosure|confidential information)\b.{0,400}?\bportfolio\b",
        "description": (
            "NDA / confidentiality language captures portfolio / case-study "
            "disclosure. Verify a portfolio carve-out exists."
        ),
        "severity": "medium",
        "score": 6,
        "axis": "ip_ownership",
        "negotiation": (
            "Counter: 'Notwithstanding any confidentiality obligation, Contractor "
            "may reference the engagement and identify the Client by name in "
            "Contractor's portfolio and case studies.'"
        ),
    },
    {
        "id": "moral_rights_waiver",
        "category": "ip",
        "label": r"moral rights?",
        "description": (
            "Moral-rights waiver present. Verify the waiver is limited to the "
            "extent permitted by law and doesn't strip attribution rights "
            "beyond what's contractually necessary."
        ),
        "severity": "low",
        "score": 2,
        "axis": "ip_ownership",
        "negotiation": (
            "Counter: 'Contractor waives moral rights only to the extent "
            "permitted by applicable law and only as necessary for Client's "
            "intended use of the Work Product.'"
        ),
    },
    # -------- RISK ALLOCATION --------
    {
        "id": "uncapped_liability",
        "category": "risk",
        "label": r"(?:(?:unlimited|uncapped|no cap(?: on)?|no limit(?:ation)?(?: on)?)\s+(?:liability|damages?)|liability\b.{0,200}?(?:shall\s+be\s+)?(?:unlimited|uncapped))",
        "description": (
            "Liability is uncapped / unlimited. This is a dealbreaker — one "
            "claim can wipe out years of fees."
        ),
        "severity": "high",
        "score": 18,
        "axis": "risk_allocation",
        "negotiation": (
            "Counter: 'Each party's total liability under this Agreement shall "
            "be capped at the greater of (a) the fees paid in the 12 months "
            "preceding the claim, or (b) USD $50,000.'"
        ),
    },
    {
        "id": "liability_cap_present",
        "category": "risk",
        "label": r"(?:aggregate )?liability.{0,200}(?:cap(?:ped)?|limited to|shall not exceed)",
        "description": (
            "Liability is capped. Verify the cap is at least the total project "
            "fee (preferably 1-2x fees paid)."
        ),
        "severity": "low",
        "score": 0,
        "axis": "risk_allocation",
        "is_check_present": "liability_cap_present",
        "negotiation": (
            "Counter: 'Aggregate liability shall not exceed the fees paid in "
            "the 12 months preceding the claim.'"
        ),
    },
    {
        "id": "liability_cap_too_low",
        "category": "risk",
        "label": r"liability.{0,80}(?:cap(?:ped)?|limited to).{0,80}\$\s?[1-9](?:,\d{3}){0,2}",
        "description": (
            "Liability cap appears to be a low fixed dollar amount. Verify the "
            "cap is reasonable relative to project size."
        ),
        "severity": "medium",
        "score": 8,
        "axis": "risk_allocation",
        "negotiation": (
            "Counter: 'Aggregate liability shall not exceed the greater of "
            "(a) the fees paid in the 12 months preceding the claim, or "
            "(b) USD $50,000.'"
        ),
    },
    {
        "id": "broad_indemnification",
        "category": "risk",
        "label": r"indemnif(?:y|ication|ies).{0,300}?(?:any and all|all claims|without limitation)",
        "description": (
            "Broad indemnification ('any and all claims', 'without limitation'). "
            "This makes you liable for things you can't foresee."
        ),
        "severity": "high",
        "score": 12,
        "axis": "risk_allocation",
        "negotiation": (
            "Counter: 'Contractor shall indemnify Client against third-party "
            "claims arising from Contractor's gross negligence or willful "
            "misconduct, subject to the liability cap.'"
        ),
    },
    {
        "id": "mutual_indemnification",
        "category": "risk",
        "label": r"mutual\s+indemnif",
        "description": (
            "Mutual indemnification present. Verify each side's indemnity is "
            "appropriately scoped and capped by the liability cap."
        ),
        "severity": "low",
        "score": 0,
        "axis": "risk_allocation",
        "is_check_present": "mutual_indemnification_present",
    },
    {
        "id": "no_indemnification_cap",
        "category": "risk",
        "label": r"indemnif.{0,500}?(?:not\s+(?:be\s+)?(?:subject\s+to\s+)?(?:limited|capped|subject\s+to\s+the))",
        "description": (
            "Indemnification is carved out of the liability cap. Indemnities "
            "without caps defeat the purpose of a liability cap."
        ),
        "severity": "high",
        "score": 12,
        "axis": "risk_allocation",
        "negotiation": (
            "Counter: 'Indemnification obligations shall be subject to the "
            "aggregate liability cap set forth above.'"
        ),
    },
    {
        "id": "warranty_disclaimer_present",
        "category": "risk",
        "label": r"(?:disclaim[s]? all warranties|as[- ]is\b|without warranty)",
        "description": (
            "Warranty disclaimer present. Verify it's mutual and that you "
            "still retain a workmanship warranty for a reasonable period (e.g. "
            "90 days post-delivery)."
        ),
        "severity": "low",
        "score": 0,
        "axis": "risk_allocation",
        "is_check_present": "warranty_disclaimer_present",
        "negotiation": (
            "Counter: 'EXCEPT AS EXPRESSLY SET FORTH HEREIN, NEITHER PARTY "
            "MAKES ANY WARRANTIES. Contractor warrants the Work Product will "
            "perform substantially as specified for 90 days post-acceptance.'"
        ),
    },
    # -------- TERMINATION FREEDOM --------
    {
        "id": "termination_for_convenience",
        "category": "termination",
        "label": r"terminate\s+(?:this\s+agreement\s+)?for\s+(?:any\s+)?(?:reason|convenience)",
        "description": (
            "Termination for convenience clause present. Verify it triggers a "
            "kill fee and that you can also terminate for convenience."
        ),
        "severity": "low",
        "score": 0,
        "axis": "termination_freedom",
        "is_check_present": "termination_for_convenience_present",
        "negotiation": (
            "Counter: 'Either party may terminate this Agreement for any reason "
            "upon 30 days' written notice. Upon termination, Client shall pay "
            "for all work performed plus 25% of the next milestone.'"
        ),
    },
    {
        "id": "termination_clause_missing",
        "category": "termination",
        "label": r"\bterminat(?:e|ion)\b",
        "description": (
            "Termination clause present. Verify it's mutual and includes both "
            "'for cause' and 'for convenience' triggers with cure periods."
        ),
        "severity": "low",
        "score": 0,
        "axis": "termination_freedom",
        "is_check_present": "termination_present",
    },
    {
        "id": "auto_renewal",
        "category": "termination",
        "label": r"(?:automatic(?:ally)?|auto)\s+renew(?:al|s|ed)?",
        "description": (
            "Auto-renewal clause present. Verify notice period to opt-out is "
            "at least 60 days, and that the renewal term doesn't exceed one year."
        ),
        "severity": "medium",
        "score": 8,
        "axis": "termination_freedom",
        "negotiation": (
            "Counter: 'This Agreement shall renew only upon mutual written "
            "consent. Either party may opt out of renewal with 60 days' notice.'"
        ),
    },
    # -------- COMPLIANCE & LEGAL HYGIENE --------
    {
        "id": "non_compete",
        "category": "compliance",
        "label": r"non[- ]compete\b",
        "description": (
            "Non-compete clause present. Verify the duration is ≤ 6 months and "
            "the scope is limited to the specific industry vertical, not a "
            "blanket prohibition."
        ),
        "severity": "high",
        "score": 14,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'For 6 months following completion, Contractor shall not "
            "provide substantially similar deliverables to a direct competitor "
            "of Client in the same vertical. This restriction does not apply "
            "to clients in adjacent verticals.'"
        ),
    },
    {
        "id": "non_solicit_long",
        "category": "compliance",
        "label": r"non[- ]solicit(?:ation)?\s*(?:.{0,100}?)?(?:period|clause|of|for)?\s*(?:.{0,100}?)?(?:1[2-9]|2\d|[3-9]\d+)\s*(?:months?|years?)",
        "description": (
            "Non-solicitation period exceeds 12 months. Aggressive duration "
            "for a freelance engagement."
        ),
        "severity": "medium",
        "score": 8,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'Non-solicitation period shall be 12 months from completion.'"
        ),
    },
    {
        "id": "non_solicit_present",
        "category": "compliance",
        "label": r"non[- ]solicit",
        "description": (
            "Non-solicitation clause present. Verify scope is mutual and limited "
            "to direct clients of the other party."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "non_solicit_present",
        "negotiation": (
            "Counter: 'For 12 months post-completion, neither party shall "
            "solicit the other party's direct clients or employees.'"
        ),
    },
    {
        "id": "exclusivity",
        "category": "compliance",
        "label": r"exclusiv(?:e|ity)\b",
        "description": (
            "Exclusivity clause present. Verify it's limited to the project "
            "scope and doesn't bar you from competing engagements in adjacent "
            "verticals."
        ),
        "severity": "medium",
        "score": 6,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'Exclusivity is limited to the deliverables listed in "
            "Exhibit A and to the term of this Agreement. Contractor may accept "
            "engagements in adjacent verticals or with non-competing clients.'"
        ),
    },
    {
        "id": "nda_present",
        "category": "compliance",
        "label": r"(?:confidential(?:ity)?|non[- ]disclosure)",
        "description": (
            "Confidentiality / NDA clause present. Verify it has a defined "
            "duration (typically 2-3 years post-termination) and excludes "
            "publicly-available information."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "nda_present",
        "negotiation": (
            "Counter: 'Confidentiality obligations survive for 3 years post-"
            "termination. Confidential Information does not include information "
            "that is publicly available, independently developed, or required "
            "to be disclosed by law.'"
        ),
    },
    {
        "id": "no_nda_duration",
        "category": "compliance",
        "label": r"(?:confidential(?:ity)?|non[- ]disclosure)\b.{0,300}?(?:no\s+(?:time|duration|limit)|perpetual|indefinite|in perpetuity)",
        "description": (
            "Confidentiality has no time limit / is perpetual / indefinite. "
            "Verify a reasonable time limit is added."
        ),
        "severity": "medium",
        "score": 6,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'Confidentiality obligations survive for 3 years post-"
            "termination of this Agreement.'"
        ),
    },
    {
        "id": "force_majeure_present",
        "category": "compliance",
        "label": r"force\s+majeure\b",
        "description": (
            "Force majeure clause present. Verify it includes pandemics, "
            "supply-chain disruption, and acts of government — not just "
            "'natural disasters'."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "force_majeure_present",
        "negotiation": (
            "Counter: 'Force majeure includes acts of God, natural disasters, "
            "pandemics, epidemics, supply-chain disruption, and acts of government.'"
        ),
    },
    {
        "id": "venue_in_client_state",
        "category": "compliance",
        "label": r"venue.{0,80}?(?:state of|located in)\s*([A-Z][a-z]+)",
        "description": (
            "Venue is fixed in a specific state / city. If that venue is far "
            "from where you operate, you'll bear travel costs to litigate any "
            "dispute."
        ),
        "severity": "medium",
        "score": 4,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'Venue shall be the state and federal courts located in "
            "[your city / mutually agreed city]. The parties consent to personal "
            "jurisdiction in such courts.'"
        ),
    },
    {
        "id": "attorneys_fees_present",
        "category": "compliance",
        "label": r"(?:attorney'?s?\s+fees|prevailing party)",
        "description": (
            "Attorneys' fees clause present. Verify it's mutual — the "
            "prevailing party recovers fees from the losing party."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "attorneys_fees_present",
        "negotiation": (
            "Counter: 'In any dispute, the prevailing party shall recover its "
            "reasonable attorneys' fees and costs from the non-prevailing party.'"
        ),
    },
    {
        "id": "entire_agreement",
        "category": "compliance",
        "label": r"entire\s+agreement\b",
        "description": (
            "Entire-agreement / integration clause present. This means "
            "side-priusings or verbal promises won't override the SOW. "
            "Healthy if explicit, dangerous if silent."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "entire_agreement_present",
        "negotiation": (
            "Counter: 'This Agreement, together with Exhibits, is the entire "
            "agreement. Amendments require written signature of both parties.'"
        ),
    },
    {
        "id": "acceptance_criteria_present",
        "category": "compliance",
        "label": r"acceptance\s+(?:criteria|test|period|procedure)",
        "description": (
            "Acceptance criteria defined. Verify the acceptance window is "
            "objective (e.g. 10 business days) and that deemed-acceptance "
            "applies if no response."
        ),
        "severity": "low",
        "score": 0,
        "axis": "compliance_hygiene",
        "is_check_present": "acceptance_criteria_present",
        "negotiation": (
            "Counter: 'Client shall have 10 business days to provide written "
            "acceptance or rejection. Failure to respond shall constitute acceptance.'"
        ),
    },
    # -------- NARRATIVE / STRUCTURAL --------
    {
        "id": "client_state_law_venue",
        "category": "compliance",
        "label": r"governed by.{0,80}?(?:state of)\s*([A-Z][a-z]+)",
        "description": (
            "Governing law is fixed to a specific state. Consider whether this "
            "is acceptable given your business location."
        ),
        "severity": "low",
        "score": 2,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: 'This Agreement shall be governed by the laws of the "
            "State of [your state]. Venue shall be [your city / neutral city].'"
        ),
    },
    {
        "id": "missing_signature_block",
        "category": "compliance",
        "label": r"(?:signature|by:|signed:)\s*_+\s*$",
        "description": (
            "Signature block format detected. Verify both parties have a clear "
            "signature line with printed name, title, and date."
        ),
        "severity": "low",
        "score": 1,
        "axis": "compliance_hygiene",
        "negotiation": (
            "Counter: Add a clean signature block: 'By: ____ Print Name: ____ "
            "Title: ____ Date: ____' for both parties."
        ),
    },
]


def detect_red_flags(text: str) -> tuple[list[dict], set[str]]:
    """Detect red flags + presence checks. Return (flags, present_clause_ids).

    Dedupe: each clause_id fires at most once with its first evidence quote
    (otherwise a single clause that's worded multiple ways could over-deduct).
    """
    flags: list[dict] = []
    present: set[str] = set()
    seen_clause_ids: set[str] = set()

    for pat in CLAUSE_PATTERNS:
        # is_check_present clauses are positive signals (healthy)
        if "is_check_present" in pat:
            for m in re.finditer(pat["label"], text, re.IGNORECASE | re.DOTALL):
                present.add(pat["is_check_present"])
                flags.append(
                    {
                        "id": pat["id"],
                        "category": pat["category"],
                        "axis": pat["axis"],
                        "severity": "info",
                        "score": 0,
                        "description": pat["description"],
                        "evidence": m.group(0)[:200],
                        "negotiation": pat.get("negotiation", ""),
                        "is_presence_check": True,
                    }
                )
        else:
            # Negative patterns — score-deducting. Dedupe by clause_id.
            if pat["id"] in seen_clause_ids:
                continue
            m = re.search(pat["label"], text, re.IGNORECASE | re.DOTALL)
            if m:
                seen_clause_ids.add(pat["id"])
                flags.append(
                    {
                        "id": pat["id"],
                        "category": pat["category"],
                        "axis": pat["axis"],
                        "severity": pat["severity"],
                        "score": pat["score"],
                        "description": pat["description"],
                        "evidence": m.group(0)[:200],
                        "negotiation": pat.get("negotiation", ""),
                        "is_presence_check": False,
                    }
                )
    return flags, present


# Missing-clause detection: clauses a healthy SOW should have
MISSING_CLAUSE_CHECKLIST = [
    {
        "id": "kill_fee_present",
        "axis": "payment_safety",
        "label": "Kill-fee / cancellation-fee clause",
        "why": "Without a kill fee, a client can cancel after kickoff and you eat "
        "all the ramp-up cost. A healthy SOW specifies 25% before kickoff, 50% after.",
        "severity": "high",
        "score_if_missing": 8,
    },
    {
        "id": "late_payment_interest_present",
        "axis": "payment_safety",
        "label": "Late-payment interest / late fee",
        "why": "Without a stated late-payment rate, late invoices become a polite "
        "request. 1.0-1.5% per month is industry-healthy.",
        "severity": "medium",
        "score_if_missing": 5,
    },
    {
        "id": "deposit_present",
        "axis": "payment_safety",
        "label": "Deposit / upfront payment requirement",
        "why": "A 30-50% deposit aligns client incentives and confirms seriousness. "
        "Working without a deposit is a yellow flag.",
        "severity": "medium",
        "score_if_missing": 5,
    },
    {
        "id": "revisions_capped_present",
        "axis": "scope_clarity",
        "label": "Revision-round cap (2-3 rounds)",
        "why": "Without a revision cap, scope creep is unbounded. Specify 'up to "
        "two rounds of revisions per deliverable'.",
        "severity": "medium",
        "score_if_missing": 5,
    },
    {
        "id": "change_order_present",
        "axis": "scope_clarity",
        "label": "Change-order / scope-change process",
        "why": "Without a formal change-order process, the client can request "
        "additions verbally and dispute the resulting invoice.",
        "severity": "medium",
        "score_if_missing": 5,
    },
    {
        "id": "portfolio_rights_carved",
        "axis": "ip_ownership",
        "label": "Portfolio / case-study carve-out",
        "why": "Without a portfolio carve-out, the NDA may forbid mentioning "
        "the engagement in your portfolio.",
        "severity": "low",
        "score_if_missing": 2,
    },
    {
        "id": "liability_cap_present",
        "axis": "risk_allocation",
        "label": "Liability cap",
        "why": "An uncapped liability clause can wipe out years of fees on a "
        "single claim. A healthy SOW caps liability at 1-2x fees paid.",
        "severity": "high",
        "score_if_missing": 12,
    },
    {
        "id": "mutual_indemnification_present",
        "axis": "risk_allocation",
        "label": "Mutual indemnification",
        "why": "Without mutual indemnification, only one side bears risk for "
        "third-party claims.",
        "severity": "medium",
        "score_if_missing": 4,
    },
    {
        "id": "warranty_disclaimer_present",
        "axis": "risk_allocation",
        "label": "Warranty disclaimer (workmanship warranty retained)",
        "why": "Without a disclaimer, you may be implicitly warranting the "
        "deliverable for fitness for purpose beyond your control.",
        "severity": "low",
        "score_if_missing": 2,
    },
    {
        "id": "termination_for_convenience_present",
        "axis": "termination_freedom",
        "label": "Termination for convenience clause",
        "why": "Without a for-convenience clause, the only exit is for cause — "
        "which requires proving material breach.",
        "severity": "medium",
        "score_if_missing": 6,
    },
    {
        "id": "termination_present",
        "axis": "termination_freedom",
        "label": "Termination clause (for cause)",
        "why": "Without any termination clause, the SOW is functionally "
        "indefinite and you cannot exit without breach.",
        "severity": "high",
        "score_if_missing": 8,
    },
    {
        "id": "nda_present",
        "axis": "compliance_hygiene",
        "label": "Confidentiality / NDA clause",
        "why": "Without an NDA, the client's confidential information may not "
        "be protected — and yours may not be either.",
        "severity": "low",
        "score_if_missing": 1,
    },
    {
        "id": "force_majeure_present",
        "axis": "compliance_hygiene",
        "label": "Force majeure clause",
        "why": "Without force majeure, a pandemic / natural disaster / supply-"
        "chain failure could put you in breach.",
        "severity": "medium",
        "score_if_missing": 4,
    },
    {
        "id": "attorneys_fees_present",
        "axis": "compliance_hygiene",
        "label": "Attorneys' fees / prevailing-party clause",
        "why": "Without a prevailing-party clause, recovering your legal fees "
        "after winning a dispute requires statutory authority that varies by state.",
        "severity": "low",
        "score_if_missing": 1,
    },
    {
        "id": "entire_agreement_present",
        "axis": "compliance_hygiene",
        "label": "Entire-agreement / integration clause",
        "why": "Without an integration clause, verbal promises or prior emails "
        "could be argued to override the SOW.",
        "severity": "low",
        "score_if_missing": 1,
    },
    {
        "id": "acceptance_criteria_present",
        "axis": "compliance_hygiene",
        "label": "Acceptance criteria / acceptance window",
        "why": "Without objective acceptance criteria, the client can delay "
        "acceptance indefinitely and withhold payment.",
        "severity": "medium",
        "score_if_missing": 4,
    },
]


def detect_missing_clauses(present: set[str]) -> list[dict]:
    """For each checklist item not in present, return a missing-clause record."""
    missing = []
    for item in MISSING_CLAUSE_CHECKLIST:
        if item["id"] not in present:
            missing.append(
                {
                    "id": item["id"],
                    "axis": item["axis"],
                    "label": item["label"],
                    "why": item["why"],
                    "severity": item["severity"],
                    "score_if_missing": item["score_if_missing"],
                }
            )
    return missing


# ---------------------------------------------------------------------------
# Score axes
# ---------------------------------------------------------------------------

AXES = [
    ("payment_safety", "Payment safety", 22),
    ("scope_clarity", "Scope clarity", 18),
    ("ip_ownership", "IP & ownership", 18),
    ("risk_allocation", "Risk allocation", 17),
    ("termination_freedom", "Termination freedom", 12),
    ("compliance_hygiene", "Compliance & legal hygiene", 13),
]


def score_axes(flags: list[dict], missing: list[dict]) -> dict:
    """Compute per-axis scores + a 0-100 overall. Returns dict with axes + total."""
    axis_deductions: dict[str, int] = defaultdict(int)
    axis_info_flags: dict[str, int] = defaultdict(int)

    for f in flags:
        if f["is_presence_check"]:
            axis_info_flags[f["axis"]] += 1
            continue
        axis_deductions[f["axis"]] += f["score"]

    for m in missing:
        axis_deductions[m["axis"]] += m["score_if_missing"]

    axes_out = []
    total_weighted = 0.0
    total_weight = 0

    for axis_id, label, weight in AXES:
        # Convert raw deductions to a 0-100 axis score.
        # Each axis starts at 100. We cap deductions at 100 so an axis can't
        # go negative; the overall score is a weighted average.
        deduction = axis_deductions[axis_id]
        axis_score = max(0, 100 - deduction)
        axes_out.append(
            {
                "id": axis_id,
                "label": label,
                "weight": weight,
                "raw_score": axis_score,
                "raw_deduction": deduction,
                "info_flags": axis_info_flags[axis_id],
            }
        )
        total_weighted += axis_score * weight
        total_weight += weight

    overall = round(total_weighted / total_weight) if total_weight else 0

    # Verdict bands
    if overall >= 85:
        verdict = "client_favorable"
        verdict_label = "Client-favorable — sign as-is"
    elif overall >= 70:
        verdict = "mostly_fair"
        verdict_label = "Mostly fair — minor tweaks only"
    elif overall >= 50:
        verdict = "needs_negotiation"
        verdict_label = "Needs negotiation — don't sign unmodified"
    elif overall >= 35:
        verdict = "high_risk"
        verdict_label = "High risk — significant changes required"
    else:
        verdict = "do_not_sign"
        verdict_label = "Do not sign — dealbreaker clauses present"

    return {
        "overall": overall,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "axes": axes_out,
    }


# ---------------------------------------------------------------------------
# Risk-pattern detector
# ---------------------------------------------------------------------------

PATTERN_DETECTORS = [
    {
        "id": "payment_dependent_on_acceptance_with_long_net",
        "label": "Payment contingent on acceptance + net-30+ terms",
        "axis": "payment_safety",
        "severity": "high",
        "conditions": [
            ("payment_dependent_on_acceptance", "flag_present"),
            ("payment_net_45_59", "flag_present"),
            ("payment_net_60_plus", "flag_present"),
        ],
    },
    {
        "id": "uncapped_liability_with_broad_indemnification",
        "label": "Uncapped liability + broad indemnification",
        "axis": "risk_allocation",
        "severity": "high",
        "conditions": [
            ("uncapped_liability", "flag_present"),
            ("broad_indemnification", "flag_present"),
        ],
    },
    {
        "id": "ip_grab_with_no_portfolio_rights",
        "label": "Broad IP assignment + no portfolio rights carved",
        "axis": "ip_ownership",
        "severity": "high",
        "conditions": [
            ("ip_assignment_broad", "flag_present"),
            ("portfolio_rights_carved", "presence_missing"),
        ],
    },
    {
        "id": "long_auto_renewal_with_lock_in_change_order",
        "label": "Auto-renewal + no change-order process",
        "axis": "termination_freedom",
        "severity": "medium",
        "conditions": [
            ("auto_renewal", "flag_present"),
            ("change_order_present", "presence_missing"),
        ],
    },
    {
        "id": "vague_scope_with_no_change_order",
        "label": "Vague scope + no change-order process",
        "axis": "scope_clarity",
        "severity": "medium",
        "conditions": [
            ("vague_scope", "flag_present"),
            ("change_order_present", "presence_missing"),
        ],
    },
]


def detect_patterns(flag_ids_present: set[str], presence_present: set[str]) -> list[dict]:
    out = []
    for det in PATTERN_DETECTORS:
        triggered = []
        for clause_id, kind in det["conditions"]:
            if kind == "flag_present" and clause_id in flag_ids_present:
                triggered.append(clause_id)
            elif kind == "presence_missing" and clause_id not in presence_present:
                triggered.append(clause_id)
        if len(triggered) == len(det["conditions"]):
            out.append(
                {
                    "id": det["id"],
                    "label": det["label"],
                    "axis": det["axis"],
                    "severity": det["severity"],
                    "triggered": triggered,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def render_markdown(profile: dict, scoring: dict, flags: list[dict], missing: list[dict], patterns: list[dict]) -> str:
    lines = []
    lines.append(f"# SOW Review ({report_date_marker()})")
    return "\n".join(lines)  # placeholder; see below


def report_date_marker() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_markdown(profile, scoring, flags, missing, patterns):
    L = []
    L.append(f"# SOW Review — {report_date_marker()}")
    L.append("")
    L.append(f"**Overall score:** {scoring['overall']} / 100 — **{scoring['verdict_label']}**")
    L.append("")
    L.append("## Profile")
    L.append("")
    L.append(f"- Engagement type: `{profile.get('engagement_type') or 'unknown'}`")
    L.append(f"- Fee model: `{profile.get('fee_model') or 'unknown'}`")
    L.append(f"- Term: `{profile.get('term') or 'unknown'}`")
    L.append(f"- Project value: `{profile.get('project_value_display') or 'unknown'}`")
    L.append(f"- Deliverables count: `{profile.get('deliverable_count', 0)}`")
    L.append(f"- Parties: `{profile.get('parties_display') or 'not detected'}`")
    L.append(f"- Effective date: `{profile.get('effective_date') or 'not detected'}`")
    L.append(f"- Governing law: `{profile.get('governing_law') or 'not detected'}`")
    L.append(f"- Venue: `{profile.get('venue') or 'not detected'}`")
    L.append(f"- Deadlines: `{profile.get('deadlines_display') or 'none detected'}`")
    L.append("")
    L.append("## Score axes")
    L.append("")
    L.append("| Axis | Weight | Score | Deduction |")
    L.append("|---|---|---|---|")
    for a in scoring["axes"]:
        L.append(f"| {a['label']} | {a['weight']}% | {a['raw_score']} | -{a['raw_deduction']} |")
    L.append("")
    L.append(f"**Overall (weighted):** {scoring['overall']} — *{scoring['verdict_label']}*")
    L.append("")
    if patterns:
        L.append("## Risk-pattern matches")
        L.append("")
        for p in patterns:
            L.append(f"- **[{p['severity'].upper()}] {p['label']}** (axis: `{p['axis']}`)")
            L.append(f"  - Triggered: {', '.join(p['triggered'])}")
        L.append("")
    if flags:
        L.append("## Red flags")
        L.append("")
        L.append("| Severity | Axis | Pattern | Evidence | Score |")
        L.append("|---|---|---|---|---|")
        for f in sorted(flags, key=lambda x: -x["score"]):
            if f["is_presence_check"]:
                continue
            ev = f["evidence"][:60].replace("|", "\\|")
            L.append(
                f"| {f['severity'].upper()} | {f['axis']} | {f['id']} | `{ev}` | -{f['score']} |"
            )
        L.append("")
        L.append("### Negotiation language")
        L.append("")
        for f in flags:
            if f["is_presence_check"]:
                continue
            if not f.get("negotiation"):
                continue
            L.append(f"**{f['id']}** — {f['description']}")
            L.append("")
            L.append(f"> {f['negotiation']}")
            L.append("")
    if missing:
        L.append("## Missing clauses")
        L.append("")
        L.append("| Severity | Axis | Clause | Why it matters |")
        L.append("|---|---|---|---|")
        for m in missing:
            why = m["why"][:120].replace("|", "\\|")
            L.append(f"| {m['severity'].upper()} | {m['axis']} | {m['label']} | {why}... |")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Core analyzer
# ---------------------------------------------------------------------------

def analyze_sow(text: str) -> dict:
    text = text.strip()
    if not text:
        return {"error": "empty_input"}

    engagement_counts = detect_signals(text, ENGAGEMENT_TYPE_SIGNALS)
    fee_counts = detect_signals(text, FEE_MODEL_SIGNALS)

    engagement_type = top_or_none(engagement_counts)
    fee_model = top_or_none(fee_counts)

    parties_a, parties_b = extract_parties(text)
    governing_law = extract_governing_law(text)
    venue = extract_venue(text)
    effective_date = extract_effective_date(text)
    term = extract_term(text)
    project_value = extract_project_value(text)
    deliverable_count = extract_deliverable_count(text)
    deadlines = extract_deadlines(text)

    flags, present = detect_red_flags(text)
    missing = detect_missing_clauses(present)
    scoring = score_axes(flags, missing)

    flag_ids_present = {f["id"] for f in flags if not f["is_presence_check"]}
    patterns = detect_patterns(flag_ids_present, present)

    profile = {
        "engagement_type": engagement_type,
        "fee_model": fee_model,
        "term": f"{term['value']} {term['unit']}" if term else None,
        "project_value": project_value,
        "project_value_display": (
            f"${project_value:,.0f}" if project_value is not None else None
        ),
        "deliverable_count": deliverable_count,
        "parties_a": parties_a,
        "parties_b": parties_b,
        "parties_display": (
            f"{parties_a} ↔ {parties_b}" if parties_a and parties_b else None
        ),
        "effective_date": effective_date,
        "governing_law": governing_law,
        "venue": venue,
        "deadlines": deadlines,
        "deadlines_display": ", ".join(deadlines) if deadlines else None,
        "input_length": len(text),
        "input_word_count": len(text.split()),
    }

    return {
        "profile": profile,
        "scoring": scoring,
        "flags": flags,
        "missing": missing,
        "patterns": patterns,
        "summary_markdown": render_markdown(profile, scoring, flags, missing, patterns),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("text", "")
    if not isinstance(raw, str):
        return jsonify({"error": "text must be a string"}), 400
    raw = raw.strip()
    if not raw:
        return jsonify({"error": "empty input"}), 400
    if len(raw) > MAX_INPUT_BYTES:
        return jsonify({"error": f"input exceeds {MAX_INPUT_BYTES} bytes"}), 413

    result = analyze_sow(raw)
    if "error" in result:
        return jsonify(result), 400

    # Optionally persist to session log
    save = bool(payload.get("save", False))
    if save:
        db = get_db()
        cur = db.execute(
            "INSERT INTO sessions (created_at, engagement_type, fee_model, parties, "
            "project_type, raw_input, output_json, score, verdict) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                result["profile"].get("engagement_type"),
                result["profile"].get("fee_model"),
                result["profile"].get("parties_display"),
                None,
                raw,
                json.dumps(result),
                result["scoring"]["overall"],
                result["scoring"]["verdict"],
            ),
        )
        db.commit()
        result["session_id"] = cur.lastrowid

    return jsonify(result)


@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    db = get_db()
    rows = db.execute(
        "SELECT id, created_at, engagement_type, fee_model, parties, score, verdict, "
        "rating, notes FROM sessions ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    return jsonify(
        {
            "sessions": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "engagement_type": r["engagement_type"],
                    "fee_model": r["fee_model"],
                    "parties": r["parties"],
                    "score": r["score"],
                    "verdict": r["verdict"],
                    "rating": r["rating"],
                    "notes": r["notes"],
                }
                for r in rows
            ]
        }
    )


@app.route("/api/sessions/<int:session_id>", methods=["GET"])
def api_sessions_get(session_id: int):
    db = get_db()
    row = db.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        abort(404)
    output = json.loads(row["output_json"])
    return jsonify(
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "engagement_type": row["engagement_type"],
            "fee_model": row["fee_model"],
            "parties": row["parties"],
            "raw_input": row["raw_input"],
            "rating": row["rating"],
            "notes": row["notes"],
            "output": output,
        }
    )


@app.route("/api/sessions/<int:session_id>", methods=["PATCH"])
def api_sessions_update(session_id: int):
    payload = request.get_json(silent=True) or {}
    rating = payload.get("rating")
    notes = payload.get("notes")
    if rating is not None and (not isinstance(rating, int) or not 0 <= rating <= 5):
        return jsonify({"error": "rating must be int 0-5"}), 400
    if notes is not None and not isinstance(notes, str):
        return jsonify({"error": "notes must be string"}), 400
    db = get_db()
    db.execute(
        "UPDATE sessions SET rating = COALESCE(?, rating), notes = COALESCE(?, notes) "
        "WHERE id = ?",
        (rating, notes, session_id),
    )
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/sample")
def sample_sow():
    return send_from_directory(BASE_DIR / "samples", "sample-sow.txt", as_attachment=False)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    app.run(host="127.0.0.1", port=port, debug=False)