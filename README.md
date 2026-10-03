# sow-forge

> **Local-first Statement of Work (SOW) reviewer for freelancers & consultants.**
> Paste a SOW, get a 0–100 client-friendliness score, red flags across ~35 clause patterns, missing-clause detection, and ready-to-paste negotiation language.

![sow-forge](https://img.shields.io/badge/sow--forge-local--first-6b3fa0?style=flat-square)
![no API keys](https://img.shields.io/badge/no_API_keys-required-2c6e49?style=flat-square)
![no LLM](https://img.shields.io/badge/no_LLM-required-2c6e49?style=flat-square)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square)

---

## What it does

`sow-forge` is a single-purpose web app: **review a SOW (Statement of Work / Scope of Work) before you sign it.** Whether you're a freelance designer, developer, consultant, copywriter, or analyst, every engagement starts with a SOW — and most SOWs are tilted heavily toward the client. sow-forge reads the document, scores it across 6 axes, and tells you exactly which ones to negotiate.

Paste your SOW (text from a Word doc, a Google Doc, a PDF, an email — anything you can copy-paste) and you get back:

- **0–100 client-friendliness score** across 6 weighted axes
  - Payment safety · Scope clarity · IP & ownership · Risk allocation · Termination freedom · Compliance & legal hygiene
- **~35 clause-pattern red flags** with severity (high / medium / low), evidence quote, and score deduction
- **Missing-clause detection** — clauses a healthy SOW should have but yours is missing
- **Risk-pattern combinations** — e.g. "uncapped liability + broad indemnification" together is a dealbreaker pattern, not just two yellow flags
- **Negotiation language** for each top red flag — ready-to-paste counter-proposal paragraphs
- **Markdown + JSON export** so you can forward the review to a lawyer, a Slack channel, or a Notion page
- **Session log** with 1–5 star ratings + free-text notes (so you can compare different clients over time)

All analysis is **local-first, deterministic, regex-based**. No API keys. No LLM calls. No telemetry. The app reads your SOW, runs ~120 regex patterns + a missing-clause checklist + a risk-pattern detector, and returns a structured report in < 100 ms.

---

## Why it matters

Most freelancers sign SOWs without reading them carefully. The ones that are most carefully tilted against the freelancer are also the ones that look the most professional. Common red flags hide in plain sight:

- **"Net 60 payment terms"** sounds businesslike. It means you fund the client's payroll for two months.
- **"Work made for hire" + "all right, title, and interest in any Work Product, including pre-existing tools"** means you give the client libraries you built before the engagement.
- **"Indemnification without limitation"** + **"uncapped liability"** = one claim can wipe out years of fees.
- **"No deposit is required"** is a yellow flag: it usually means the client expects you to fund the work.
- **"Termination for cause upon material breach with 30 days' notice"** without a for-convenience clause means your only exit is litigation.
- **"Confidentiality in perpetuity" + "portfolio disclosure constitutes a breach"** means you can't name the client on your website after the engagement.

`sow-forge` reads the SOW the way a lawyer would, but instantly and for free. It doesn't replace legal advice for material engagements — but it tells you *which clauses to flag for legal review*, and it gives you counter-language you can paste into the redline.

---

## Tech stack

- **Backend:** Python 3.8+, Flask 3
- **Frontend:** Vanilla HTML, CSS, JavaScript — no framework, no build step
- **Database:** SQLite (single-file, lives in `data/sow_forge.db`)
- **Analysis engine:** ~120 regex patterns, ~35 clause types, 6-axis weighted scoring, missing-clause checklist, risk-pattern combinations
- **No external services.** No LLM. No API keys. No CDN dependencies. Runs on your laptop.

---

## Quickstart

### 1. Install

```bash
cd sow-forge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run

```bash
python3 app.py
```

Then open <http://127.0.0.1:5057> in any browser.

### 3. Try it

- Click **"Try the sample SOW →"** in the header to load a deliberately bad SOW (net-60, no deposit, uncapped liability, broad IP assignment, non-compete, etc.)
- Or paste your own SOW into the textarea
- Click **Analyze SOW**
- Use the tabs to inspect **Risk patterns / Red flags / Missing clauses / Healthy clauses / Markdown / JSON**

To run on a different port:

```bash
PORT=8080 python3 app.py
```

---

## What it scores

The 0–100 score is a weighted average across 6 axes. Each axis is a category of risk; the weight reflects how often disputes in freelance SOWs center on that axis.

| Axis | Weight | What it covers |
|---|---|---|
| Payment safety | 22% | Net terms, kill fee, deposit, late-payment interest, payment-conditional-on-acceptance |
| Scope clarity | 18% | Vague language ("as needed"), revision caps, change-order process, deliverable count |
| IP & ownership | 18% | Assignment language, work-for-hire, pre-existing IP carve-out, portfolio rights, moral rights |
| Risk allocation | 17% | Liability cap, indemnification breadth & cap, warranty disclaimers |
| Termination freedom | 12% | Termination for cause + convenience, auto-renewal length |
| Compliance & legal hygiene | 13% | NDA duration, non-compete, non-solicit, force majeure, governing law & venue, attorneys' fees |

Verdict bands:

| Score | Verdict | What to do |
|---|---|---|
| 80–100 | `client_favorable` | Sign as-is |
| 65–79 | `mostly_fair` | Minor tweaks only |
| 50–64 | `needs_negotiation` | Don't sign unmodified |
| 35–49 | `high_risk` | Significant changes required |
| 0–34 | `do_not_sign` | Dealbreaker clauses present |

---

## What it detects

### Red-flag patterns (~35)

Each clause pattern has a regex, a description, a severity (high / medium / low), an axis assignment, and a score deduction. Patterns include:

**Payment safety**
- `payment_net_60_plus` — net 60+ days
- `payment_net_45_59` — net 45–59 days
- `no_kill_fee` — kill fee present but verify amount
- `kill_fee_present_too_low` — kill fee < 25% of project
- `no_late_payment_interest` — late-payment interest present but verify rate
- `deposit_required` — deposit / upfront payment mentioned
- `no_deposit` — explicitly waives deposit
- `payment_dependent_on_acceptance` — payment contingent on client acceptance

**Scope clarity**
- `vague_scope` — "as needed", "reasonable", "best efforts"
- `scope_assistance_only` — scope described as "assistance" rather than deliverables
- `no_revisions_cap` — revisions described as "unlimited" or "reasonable"
- `revisions_capped` — 2–3 revision rounds (positive signal)
- `no_change_order_process` — change-order process present (positive signal)

**IP & ownership**
- `ip_assignment_broad` — "all right, title, and interest"
- `work_for_hire` — work-for-hire language
- `ip_includes_pre_existing` — captures pre-existing IP
- `portfolio_rights_carved` — portfolio rights preserved (positive)
- `no_portfolio_rights` — NDA may forbid portfolio mention
- `moral_rights_waiver` — moral-rights waiver present

**Risk allocation**
- `uncapped_liability` — unlimited / uncapped liability
- `liability_cap_present` — liability cap present (positive)
- `liability_cap_too_low` — cap appears to be a low fixed dollar amount
- `broad_indemnification` — "any and all claims"
- `mutual_indemnification` — mutual indemnification (positive)
- `no_indemnification_cap` — indemnification carved out of cap
- `warranty_disclaimer_present` — warranty disclaimer (positive)

**Termination freedom**
- `termination_for_convenience` — termination for convenience (positive)
- `termination_clause_missing` — termination clause present (positive)
- `auto_renewal` — auto-renewal language present

**Compliance & legal hygiene**
- `non_compete` — non-compete clause present
- `non_solicit_long` — non-solicit > 12 months
- `non_solicit_present` — non-solicit present (positive)
- `exclusivity` — exclusivity clause present
- `nda_present` — confidentiality clause present (positive)
- `no_nda_duration` — confidentiality is perpetual
- `force_majeure_present` — force majeure clause (positive)
- `venue_in_client_state` — venue fixed in client state
- `attorneys_fees_present` — prevailing-party clause (positive)
- `entire_agreement` — integration clause (positive)
- `acceptance_criteria_present` — acceptance window (positive)
- `client_state_law_venue` — governing law fixed to a state

### Missing-clause checklist

A healthy SOW should contain clauses sow-forge checks for and reports missing:

| Missing clause | Why it matters | Severity |
|---|---|---|
| Kill fee | Client can cancel after kickoff; you eat ramp-up cost | high |
| Late-payment interest | Late invoices become polite requests | medium |
| Deposit | 30–50% deposit aligns client incentives | medium |
| Revision cap | Scope creep is unbounded without a cap | medium |
| Change-order process | Verbal changes → disputed invoices | medium |
| Portfolio carve-out | NDA may forbid mentioning engagement | low |
| Liability cap | One claim can wipe out years of fees | high |
| Mutual indemnification | Only one side bears third-party risk | medium |
| Warranty disclaimer | Implicit fitness-for-purpose warranty | low |
| Termination for convenience | Only exit is material breach | medium |
| Termination (for cause) | No exit without litigation | high |
| Confidentiality / NDA | Either side's confidential info unprotected | low |
| Force majeure | Pandemic / disaster can put you in breach | medium |
| Attorneys' fees | Recovering legal fees requires statutory authority | low |
| Entire-agreement clause | Verbal promises override the SOW | low |
| Acceptance criteria | Client delays acceptance indefinitely | medium |

### Risk-pattern combinations

A SOW with two yellow flags together is often worse than a SOW with one red flag. sow-forge detects combinations:

| Pattern | What it means |
|---|---|
| `payment_dependent_on_acceptance_with_long_net_terms` | Payment is contingent on acceptance *and* net 45+. Either alone is workable; together they're a payment trap. |
| `uncapped_liability_with_broad_indemnification` | Two dealbreakers stacked. Either alone is bad; together they're a career-ending risk. |
| `ip_grab_with_no_portfolio_rights` | Broad IP assignment + NDA forbids portfolio mention. You give away the work *and* the case study. |
| `long_auto_renewal_with_no_change_order` | Auto-renews for another year *and* no formal way to add scope. Client can extend at last year's terms. |
| `vague_scope_with_no_change_order` | "As needed" + no formal change-order. Verbal requests become unpaid work. |

---

## API

`sow-forge` exposes a JSON API at `/api/analyze`:

```bash
curl -X POST http://127.0.0.1:5057/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "This Statement of Work is...", "save": true}'
```

Response shape:

```json
{
  "profile": {
    "engagement_type": "fixed_price",
    "fee_model": "fixed",
    "term": "18 months",
    "project_value": 48000,
    "project_value_display": "$48,000",
    "deliverable_count": 4,
    "parties_a": "Acme Widgets Inc.",
    "parties_b": "Freelance Consulting LLC",
    "effective_date": "October 1, 2026",
    "governing_law": "Delaware",
    "venue": "Wilmington, Delaware",
    "deadlines": [],
    "input_word_count": 412
  },
  "scoring": {
    "overall": 22,
    "verdict": "do_not_sign",
    "verdict_label": "Do not sign — dealbreaker clauses present",
    "axes": [...]
  },
  "flags": [...],
  "missing": [...],
  "patterns": [...],
  "summary_markdown": "# SOW Review — 2026-10-03 ..."
}
```

Other endpoints:
- `GET  /api/sessions` — list past analyses (most recent 100)
- `GET  /api/sessions/<id>` — fetch a single past analysis
- `PATCH /api/sessions/<id>` — rate (1–5 stars) or annotate

---

## Project layout

```
sow-forge/
├── app.py                # Flask app + analysis engine (single file)
├── requirements.txt
├── README.md
├── samples/
│   └── sample-sow.txt    # Deliberately bad SOW for testing
├── data/
│   └── sow_forge.db      # SQLite session log (created on first run)
├── static/
│   ├── styles.css
│   └── app.js
└── templates/
    └── index.html
```

---

## Different from related tools

- **`offer-letter-forge`** (employment offer review) — same flavor of red-flag detection, but for employment offers. Different clauses (non-compete scope, vesting, termination severance, at-will language, non-solicit of employees).
- **`lease-decode`** (residential lease) — different domain, different vocabulary. Lease clauses include pet policy, subletting, security deposit, maintenance responsibilities.
- **`lease-renewal-comparator`** (residential renewal) — comparative diff between two lease versions, not first-pass review.
- **`closing-cost-cipher`** (real estate closing) — costs only, not contract terms.
- **`small-claims-prep-forge`** (consumer dispute prep) — dispute element + evidence checklist, not contract review.

`sow-forge` fills the MISSING first-pass SOW review moment: "I just received a Statement of Work from a prospective client — what should I push back on, and what counter-language should I use?" It does not replace legal advice for material engagements, but it tells you exactly which clauses to flag for an attorney.

---

## License

MIT.