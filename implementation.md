# Algolyra — Master Implementation Plan (v4)

**Status:** Authoritative build specification. Supersedes all earlier PRD/implementation drafts. Built from two prior AI-generated specs (a feature-first PRD and a 3,300-line deep implementation plan) plus fresh technical/tooling research, read and cross-checked in full before this synthesis was written.

**Business model:** Contingency-only, 15–20% of recovered dollars, configurable per customer, $0 fee on $0 recovered.
**Primary customer:** Freight brokers/3PLs, uninsured/self-insured segment ("Flow B").
**Product principle:** AI does the repetitive evidence-processing work; a human controls every consequential decision — negotiation, submission, anything above a configurable dollar threshold, anything with legal exposure.
**Immediate objective:** A narrow, evidence-grounded, human-reviewed working demo — not the full platform — validated against a real broker before scope expands.

---

## 0. What Algolyra is, and the sentence that governs every build decision

Algolyra is the operating layer for freight cargo claims recovery. A shipment gets damaged, lost, short, or stolen; someone has to gather documents, figure out what happened, file a claim against the carrier within a hard deadline, track it for weeks or months, follow up, handle the response, and reconcile what actually came back. Today a broker does this by hand, badly, or not at all. Algolyra automates the document-heavy, repetitive parts of that chain and keeps a human in control of everything that involves money, negotiation, or legal risk.

> **Algolyra must never optimize for "AI looks smart." It must optimize for: every claim submitted is complete, evidence-backed, correctly calculated, deadline-safe, auditable, and reviewed by a human when uncertainty or financial/legal risk is high.**

Every section below should be read against that sentence. If a feature request conflicts with it, the sentence wins.

### 0.1 The economic promise, stated precisely

`Customer recovered dollars × contracted contingency rate = Algolyra revenue`

Example: claim submitted $10,000 → recovered $7,500 → contingency rate 20% → Algolyra fee $1,500 → customer keeps $6,000 before any other cost. If recovery is $0, Algolyra's fee is $0. This is the entire pitch, and it means the product's core engineering job is making claim-processing cheap enough that pursuing even a $400 claim is worth Algolyra's time.

### 0.2 Product boundary — what the system may and may not do autonomously

**May:** read documents, extract facts, classify claim type, check completeness, calculate deadlines from *approved, sourced* rules, draft claim packages, prepare follow-ups, summarize carrier responses, recommend next actions, flag risk, maintain the claim record.

**Must never, autonomously:** negotiate a settlement amount, make an irreversible external commitment, send a sensitive/high-value communication without approval, override a customer-defined approval threshold, or invent claim facts/evidence. The human is the source of authority for every consequential decision — enforced server-side, not just hidden behind a UI button.

---

## 1. Market context (carried forward from customer research — do not re-litigate these)

- Uninsured/self-insured brokers write off real money yearly ($15-20k/broker/year data point) because pursuing claims, especially small ones, isn't worth manual time.
- A marine claims manager's data point shapes the whole escalation model: 5-8 open claims per broker at once, 30-120 day resolution windows, claims under ~$5,000 need minimal human involvement, above that a human is required, negotiation is *always* human.
- The single most important UX constraint in this entire product, from a skeptical prospect interview: customers disengage the instant they sense AI is handling their claims. The fix is not deception — see Section 4 — it's keeping the broker as the accountable, visible party.
- Insured brokers have no pain (subrogation already handles it for them) — confirms the ICP stays narrow: uninsured/self-insured only.
- No competitor prices cargo-claim recovery on pure contingency at broker/3PL/mid-market scale. FreightClaims.com (subscription) is the closest. Freehand bypasses brokers for Fortune 500 shippers. MercuryGate/BlueShip/CargoWise/TriumphPay/CTSI-Global bundle claims inside enterprise TMS at enterprise pricing. Algolyra's wedge is structural: zero adoption cost, paid only from money the broker wouldn't have recovered anyway — which only works if AI compresses processing cost enough to make small claims worth pursuing.

---

## 2. Core questions the system must answer for every claim

1. What shipment is this? 2. What went wrong? 3. Is this actually a claim? 4. What type? 5. How much money is at stake? 6. What evidence do we have? 7. What's missing? 8. What deadline applies? 9. What package should be submitted? 10. Does a human need to review it? 11. What happened after submission? 12. When do we follow up? 13. What did the carrier say? 14. What's the next action? 15. How much was recovered? 16. How much does Algolyra earn?

If the system answers all sixteen, consistently, it has solved the core operational problem.

---

## 3. Product principles (non-negotiable, enforced in architecture, not just policy)

1. **Evidence before language.** No fact enters a claim unless it's traceable to a source document.
2. **Deterministic rules before probabilistic AI.** Deadlines, fees, and eligibility are calculated by code, not inferred by a model.
3. **Every important AI conclusion needs provenance** — document, page, region/excerpt.
4. **Human approval is a first-class state**, not a UI checkbox — see the state machine in Section 9.
5. **Recoveries are financial events, not a single field.** Multiple partial payments are normal; model them as events.
6. **Human-facing and customer-facing communication are different surfaces.** What a claims operator sees internally (AI confidence, flags) is never what a shipper or carrier sees.
7. **Build for evidence-backed automation, not novelty automation.** If a feature doesn't reduce missing-evidence, missed-deadline, or bad-classification failure modes, it's not Phase 0-2 work.

---

## 4. Non-negotiable constraints

1. **No autonomous negotiation, ever** — AI drafts, a human sends.
2. **No autonomous action above a per-customer dollar threshold** (default $5,000, configurable).
3. **Nothing constituting legal advice or sign-off ships without human review.** Corollary (important correction from technical review): Algolyra may identify *factual indicators* relevant to potential legal/contractual issues (e.g., "packaging documentation does not show packaging condition") — it must never state a legal conclusion ("carrier is liable under Carmack"). This applies directly to how the drafting engine talks about Carmack Amendment exclusions.
4. **Broker remains the accountable sender and decision-maker.** Reframed from "AI must be invisible": the hard rule is Algolyra must never falsely represent an AI-generated communication as human-authored where disclosure is legally required, and the architecture must not be built around deceptive identity behavior. In practice, broker-first branding/tone stays as validated by research — but the mechanism is accountability, not deception.
5. **AI-generated claims must be grounded exclusively in evidence.** If a fact isn't present in a document, the system outputs `UNKNOWN / NOT PROVIDED`, never a plausible guess. No invented damage, dates, shipment numbers, values, signatures, carrier admissions, inspection findings, packaging/delivery condition, or legal conclusions.
6. **Every AI action is logged richly** — model, model version, prompt version, input references, output, confidence, human modification, final human decision. This is both the trust mechanism and the seed data for Phase 5 intelligence.

---

## 5. System architecture

```
Web App (broker workspace)
   ↓
API / Backend (auth, authorization, routing)
   ↓
Claim Service (owns the state machine, orchestrates everything below)
   ↓
Document Processing Pipeline
   ├── Object Storage (signed URLs, no public access)
   ├── OCR/Vision Provider (abstracted — Section 8)
   ├── Document Classification
   └── Structured Extraction (+ per-field confidence + provenance)
   ↓
Claim Intelligence Layer
   ├── Claim Classification
   ├── Rules Engine (sourced, versioned — Section 7.9)
   ├── Evidence/Completeness Checker (+ contradiction detection)
   ├── Deadline Engine (deterministic, never LLM arithmetic)
   └── Readiness Engine (score + decision explanation)
   ↓
Human Review Queue → Approval (server-side enforced)
   ↓
Claim Submission (hard blockers enforced here)
   ↓
Claim Tracking (status/timeline/events, follow-up engine)
   ↓
Recovery (carrier-response parsing, appeal loop, negotiation support)
   ↓
Contingency Billing (fee calc, invoicing, audit trail)
```

**Recommended shape for a solo founder: a modular monolith plus async jobs, not microservices.**

```
Web App → API/Backend → PostgreSQL + Object Storage → Job/Workflow Queue → 
   Document Processing workers | AI/Rules workers | Notification workers → LLM/VLM/OCR
```

Keep code modules logically separated even inside one process:

```
apps/{web, api}
packages/{domain, claims, documents, rules, ai, workflows, billing, communications, analytics, auth, storage, audit}
workers/{document_worker, extraction_worker, claim_worker, notification_worker}
```

Python is the strongest backend choice given the document-processing and AI ecosystem, but the exact language should follow founder speed over "best practice."

### 5.1 AI architecture — a set of narrow functions, not one giant agent

- **Document extraction worker** — document in, typed facts + provenance out.
- **Claim classifier** — normalized context in, claim type + confidence + evidence out.
- **Completeness assistant** — claim + requirements in, missing/unknown list out.
- **Drafting worker** — verified facts + template in, claim package draft out.
- **Response parser** — carrier communication in, structured response out.
- **Follow-up drafting worker** — claim state + correspondence in, follow-up draft out.
- **Risk/escalation evaluator** — claim + policy in, required approval path out (thresholds are deterministic; AI only supplements risk interpretation, never sets the threshold).

That's six functions. Resist adding more because they sound advanced — see Section 8's "what not to add early."

---

## 6. AI confidence & human escalation framework

Every AI task gets its **own configurable confidence threshold** — not one global number. Starting policy (tune per task, don't hardcode as final):

| Confidence | Behavior |
|---|---|
| ≥ 95% | Automatic processing |
| 90–95% | Process, flag for review |
| 70–90% | Human verification required before proceeding |
| < 70% | AI cannot decide — human determines the result |

Tasks needing independent thresholds: carrier extraction, shipment-number extraction, claim classification, damage classification, amount extraction, document classification. Your product's biggest risk isn't "AI doesn't work" — it's "AI confidently makes the wrong claim decision." This framework is what makes that risk manageable.

---

## 7. Full feature catalog

### 7.1 Organization/account management
Multi-tenant organizations, users, roles, permissions, customer configuration, billing configuration, approval thresholds, communication preferences. Initial roles: **Admin** (full control), **Claims Manager** (create/review/approve/submit/manage), **Claims Operator** (prepare claims, cannot approve high-value), **Finance** (view recoveries/fees/invoices), **Read-only**. Later: **Senior Approver** (required above threshold).

### 7.2 Shipment management
Each claim links to a shipment record: shipment ID, customer reference, carrier, broker/3PL, shipper, consignee, origin, destination, pickup/delivery dates, BOL number, PRO/reference number, declared value, currency, commodity, quantity, weight, source system, imported timestamp. The structured shipment record is authoritative after validation — never treat raw LLM text as source of truth.

### 7.3 Document management
Initial types: BOL, POD, invoice, damage photos, inspection report, carrier correspondence. Later: rate confirmation, delivery exception notices, warehouse records, packing lists, proof of value, claim forms, carrier portal exports. Each document: ID, claim ID, shipment ID, type, filename, MIME type, storage location, hash/checksum, uploader, upload time, extraction status, parser version, page count, sensitivity classification. Use content-addressable storage/strong checksums to prevent duplicate processing.

### 7.4 AI document extraction
Returns typed JSON with per-field evidence, not free text:
```json
{
  "document_type": "POD",
  "shipment_reference": {"value": "847293", "evidence": [{"page": 1, "text": "PRO: 847293"}]},
  "delivery_date": {"value": "2026-08-03", "evidence": [{"page": 1, "text": "Delivered 08/03/2026"}]},
  "damage_noted": {"value": true, "evidence": [{"page": 1, "text": "3 cartons damaged"}]}
}
```
Schema defined centrally, validated with Pydantic (or equivalent) before any database write.

### 7.5 Image/damage evidence processing
Photos aren't ordinary documents: store original → generate safe preview → normalize orientation → run vision model → extract observations → **never convert a visual inference into a stated fact without marking it as a model observation** → require human confirmation for consequential damage statements below the confidence threshold. Example: `AI observation: possible physical damage visible on outer packaging. Confidence: 0.86. Human confirmation required before use in an external claim.`

### 7.6 Shipment-document matching
Match by (in order): exact shipment/reference number → BOL/PRO number → carrier name → dates → origin/destination → invoice number → fuzzy semantic matching only as a last resort. Every match gets a confidence state: confirmed / probable / ambiguous / unresolved. Ambiguous documents go to human review — never silently attach to the wrong claim.

### 7.7 Claim classification
Classes: damage, shortage, loss, theft (separate class: demurrage/detention — different workflow). Output includes confidence + evidence. Low-confidence classification never silently enters the submission workflow.

### 7.8 Claim eligibility/completeness engine
Answers "can this claim safely proceed?" Checks: shipment identified, carrier identified, claim type known, amount supported, required documents present, mandatory dates present, deadline known, customer-specific rules satisfied, approval threshold satisfied. Output states: `READY` / `BLOCKED (missing: ...)` / `ESCALATED (reason: ...)`.

### 7.9 Rules engine (sourced and versioned)
Do not embed rules in prompts. Structured `CarrierRuleSet` per carrier: rule version, effective-from/to, claim types covered, filing window, required documents, amount/variance thresholds, submission channel, contact details, special notes, **source citation/reference, verification status**. Every important rule needs source URL/document, retrieved date, reviewer, confidence, last-verified date — because rules change, and an unverified stale rule → wrong deadline → real financial loss. The platform never presents legal interpretations as legal advice; material legal conclusions require customer policy or qualified legal review.

### 7.10 Deadline engine
Inputs: event date, carrier, claim type, rule version, customer policy. Outputs: deadline date, time remaining, urgency, status, source rule. **Never calculate deadlines with LLM-generated arithmetic** — the LLM may identify candidate dates/rules; a deterministic service performs the final calculation.

### 7.11 Claim amount engine
`proposed_amount = evidence-backed calculation` from invoice value, affected quantity, unit value, repair/salvage cost, customer policy, carrier-specific constraints. Every externally displayed number carries a provenance trail (e.g., "$8,000 — supported by: invoice $20,000 total × 40% affected quantity, confirmed by damage evidence"). Review the exact calculation logic per claim class before treating it as automated financial logic.

### 7.12 Claim package generator
Outputs: cover summary, claim form fields, factual narrative, chronology, amount claimed, evidence checklist, attachments, carrier/reference details. Hard requirements: no unsupported facts, no invented dates/carrier statements/legal conclusions, consistent numbers and shipment identifiers across all documents, provenance preserved internally.

### 7.13 Claim readiness score
Deterministic + AI-assisted score from: evidence completeness, field completeness, deadline safety, claim-type confidence, amount support, carrier rule confidence, human approval status. Always paired with a **decision explanation** (why not 100%), not a bare number:
```
Readiness: 78%
✓ BOL found  ✓ POD found  ✓ Invoice found  ✓ Amount verified
✗ Damage notation missing from POD  ✗ Inspection report missing
```
This score is an internal workflow aid, not a claim-acceptance prediction, until validated against real outcome data (Section 12).

### 7.14 Human review UI — one of the highest-priority screens in the product
Layout: document viewer (left), structured claim fields (center), AI findings/missing evidence/deadline/risk/draft communication (right). Actions: approve, edit, reject, request document, escalate, save draft. Every edit records original value, final value, actor, timestamp, reason — this becomes training/evaluation data.

### 7.15 Follow-up engine
A core feature, not a notification add-on. Per claim: next/previous follow-up date, response SLA/policy, outstanding request, owner, escalation level. AI drafts first follow-up, overdue follow-up, document-request response, settlement response draft, internal escalation summary — approval rules configurable.

### 7.16 Carrier-response intelligence
Extract: acceptance/rejection/partial acceptance, offer amount, requested documents, stated rejection reason, deadline/request date, reference number, next action → structured response object with `requires_human_review: true` wherever ambiguous. Never let AI convert ambiguous language into an irreversible financial decision.

### 7.17 Negotiation support (assistant, not autonomous negotiator)
AI can: summarize carrier position, compare offer vs. requested amount, identify missing arguments/evidence, prepare a response draft, surface prior claim history and relevant policy, flag high-value/high-risk cases. **Final response is always human-sent.**

### 7.18 Recovery management
Support partial payment, multiple payments, full payment, rejected recovery, disputed payment, corrected/reversed payment, currency conversion where needed. Model as **events**, not a single field:
```
RecoveryEvent #1: amount=4000, received_at=..., source=carrier
RecoveryEvent #2: amount=3000, received_at=..., source=carrier
```
Current recovered amount is always derived from events, never stored as a mutable single value.

### 7.19 Contingency billing
Per-customer config: contingency rate, currency, invoice timing, tax handling, billing contact, payment terms. `fee = eligible_recovered_amount × contingency_rate`. Every invoice line references customer, claim, recovery event(s), recovered amount, rate, fee, invoice ID — fully auditable.

### 7.20 Analytics
**Recovery:** total claimed, total recovered, recovery rate, average recovery time, pending recovery. **Operations:** open claims, claims nearing deadline, claims missing documents, overdue follow-ups, claims awaiting approval. **Carrier intelligence:** claim count, recovery rate, average response time, rejection reasons, average settlement ratio — carefully permissioned so one customer's data never leaks to another.

---

## 8. Recommended GitHub repositories, by layer (researched and current)

*Every open-source dependency below is a replaceable implementation detail — product interfaces must not depend directly on a specific OCR/LLM/auth vendor (Section 5's provider abstraction). Verify current license terms before production use.*

**Document conversion / OCR**
- **[docling-project/docling](https://github.com/docling-project/docling)** — PDF and multi-format conversion, advanced PDF structure parsing, OCR support, unified document representation, runs locally. Ships an MCP server, meaning an agentic coding tool (Antigravity) can call it directly as a tool. Best fit for Phase 1+ structured parsing.
- **[PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)** — 76,000+ stars, strong on tables and multilingual documents, ships PP-Structure for layout/table detection (relevant since BOLs are table-heavy). Real GPU/dependency setup overhead — don't add until direct LLM-vision extraction (Phase 0/1 default) proves insufficient.
- **Tesseract** (`tesseract-ocr/tesseract`) — simplest fallback, 73,000+ stars, weak on layout/tables — not recommended as primary given BOL/POD table structure.

**Multi-tenant SaaS foundation (auth, orgs, billing) — highest-leverage repo in this whole list**
- **[better-auth/better-auth](https://github.com/better-auth/better-auth)** — MIT-licensed, framework-agnostic TypeScript auth, 13,000+ stars, 100K+ weekly downloads. Built-in: organizations/multi-tenancy, teams, roles, invitations, access control, passkeys, 2FA, magic links, API keys, JWTs. This maps almost directly onto the `organizations`/`users`/roles schema in Section 10 — adopt at the start of Phase 2, not Phase 0.
- **[ixartz/SaaS-Boilerplate](https://github.com/ixartz/SaaS-Boilerplate)** — free Next.js starter bundling multi-tenancy, RBAC, auth, and Stripe billing in one repo. Building this stack from scratch costs real weeks of solo-founder time; cloning it when multi-tenancy/billing actually become necessary (Phase 2) is the pragmatic move.

**Durable workflows / orchestration**
- **[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)** — durable, stateful execution with human-in-the-loop interruption/checkpointing — maps well to claims that stay open for weeks/months and need a human-approval pause built into the workflow itself. Adopt only once durable long-running state is a real requirement (Phase 2+), not because it's an "agent framework."
- **[pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)** — type-safe AI workflows, structured/validated outputs, model-provider abstraction, evaluation and human-approval hooks, multi-provider support. Directly implements the AI provider abstraction and structured-output validation this spec requires (Sections 4, 7.4).

**Retrieval / semantic search**
- **[pgvector/pgvector](https://github.com/pgvector/pgvector)** — adds exact and approximate nearest-neighbor search inside Postgres. Sufficient for early similar-claim retrieval and carrier-response retrieval without standing up a separate vector database.

**LLM evaluation (this is what makes AI reliability testable — do not skip)**
- **[confident-ai/deepeval](https://github.com/confident-ai/deepeval)** — the closest thing to pytest for LLM outputs; 14+ scored, self-explaining metrics, native Pytest integration, synthetic dataset generation from your own document set. Best fit for gating extraction/classification/drafting quality in CI (Section 15).
- **[explodinggradients/ragas](https://github.com/explodinggradients/ragas)** — reference-free metrics (faithfulness, answer relevancy) plus retrieval metrics — useful if/when historical-claim retrieval (Phase 5) is built.
- **[promptfoo/promptfoo](https://github.com/promptfoo/promptfoo)** — CLI-first, zero-cloud-dependency, YAML-driven, strong red-teaming/adversarial suite (500+ attack vectors) — useful for testing that the drafting engine can't be prompted into inventing facts or making legal conclusions (Section 4).
- **[langfuse/langfuse](https://github.com/langfuse/langfuse)** — open-source, self-hostable LLM observability/tracing with a managed-cloud option if self-hosting overhead becomes a problem later. Good fit for the AI audit log requirements in Section 14.

**EDI/X12 (Phase 4+ only — do not integrate speculatively)**
- **nerdocs/pydifact** (EDIFACT) and **git-albertomarin/badX12** (ANSI ASC X12) — real, maintained, free Python parsers. Evaluate only once a specific pilot customer's TMS requires EDI ingestion.

**What NOT to add early:** five agent frameworks, three vector databases, multiple orchestration engines, or a large event-bus architecture just to look advanced. There is no meaningful open-source shortcut for TMS integration specifically — that's closed, proprietary, vendor-by-vendor work, scoped only when a real pilot customer needs it. The strongest early architecture is the smallest one that reliably processes a claim end to end.

---

## 9. Claim state machine

```
DRAFT → DOCUMENTS_PROCESSING → NEEDS_INFORMATION → CLASSIFIED → READY_FOR_REVIEW
   → HUMAN_REVIEW → APPROVED → SUBMITTED → AWAITING_RESPONSE → FOLLOW_UP_DUE
   → CARRIER_RESPONDED → NEGOTIATION → APPROVED_FOR_PAYMENT
   → PARTIALLY_RECOVERED / RECOVERED / REJECTED → CLOSED
   (ESCALATED can branch in from most states)
```

No direct `DRAFT → RECOVERED` or any transition that skips the required workflow events. **Critical rule: AI cannot trigger `→ SUBMITTED`.** This is a backend permission check, not a UI restriction:
```python
if claim.requires_approval and not claim.approved:
    block_submission()
```
Every transition is recorded (from_status, to_status, actor [ai/human], user_id, timestamp, reason) — this is what makes the human-control requirement in Section 4 real rather than cosmetic.

---

## 10. Data model

```
organizations
  id, name, type (broker|3pl|shipper|other), status, timezone, currency, created_at, updated_at

users
  id, organization_id, name, email, role, status, created_at, last_login_at

customer_policies
  id, organization_id, high_value_threshold, approval_policy_version,
  contingency_rate, communication_policy, follow_up_policy, timezone, effective_at

carriers
  id, canonical_name, aliases, mc_number/identifiers, contact_channels, active

carrier_rule_sets
  id, carrier_id, version, effective_from, effective_to, rule_status,
  source_reference, verified_at, verified_by

carrier_claim_rules   -- child of carrier_rule_sets
  carrier_rule_set_id, claim_type, filing_window_type, filing_window_value,
  filing_window_unit, required_document_type, submission_channel, special_rule_json

shipments
  id, organization_id, external_reference, bol_number, carrier_id,
  shipper_name, consignee_name, origin, destination, pickup_at, delivery_at,
  declared_value, currency, commodity, quantity, weight, created_at

claims
  id, organization_id, shipment_id, claim_type, status, lifecycle_version,
  claimed_amount, currency, approved_claim_amount, deadline_at,
  human_threshold_triggered, reimbursement_mode (pure_recovery|reimbursement_after_payout),
  owner_user_id, created_at, submitted_at, closed_at

documents
  id, organization_id, claim_id, shipment_id, document_type, filename, mime_type,
  object_key, sha256, page_count, extraction_status, parser_version,
  uploaded_by, created_at

document_evidence
  id, document_id, page_number, bbox_json, source_text, field_name,
  normalized_value_json, extraction_method, model_version, confidence

claim_facts   -- provenance-first fact table; do not write LLM results directly onto claims
  id, claim_id, field_name, value_json, source_document_id, source_location,
  confidence, verification_status, model_version, created_at

claim_requirements
  id, claim_id, requirement_type, description, source_rule_id,
  status (met|missing|unknown|waived), evidence_document_id

claim_submissions
  id, claim_id, submission_channel, submitted_at, external_reference,
  payload_hash, status, submitted_by

communications
  id, claim_id, channel, direction, sender, recipient, subject, body,
  draft_status, approved_by, sent_at, source_document_id

tasks
  id, claim_id, type, owner_user_id, due_at, status, priority, created_by, completed_at

recovery_events
  id, claim_id, amount, currency, received_at, payment_reference, payer,
  evidence_document_id, status, created_by

fee_events
  id, claim_id, recovery_event_id, eligible_amount, contingency_rate,
  fee_amount, currency, status

invoices
  id, organization_id, invoice_number, status, issue_date, due_date,
  currency, subtotal, tax, total

audit_events
  id, organization_id, actor_type, actor_id, entity_type, entity_id,
  action, before_json, after_json, reason, created_at
```

---

## 11. Acceptance-rate optimization system

The business objective is not "file more claims" — it's **increase valid claim acceptance and recovery**.

- **Pre-submission quality gate:** is claim type confident? Carrier known? Shipment known? Amount supported? Required documents present? Deadline safe? Approval policy passed? No factual inconsistencies? Any "no" → block or escalate.
- **Evidence completeness matrix:** `claim type + carrier + policy → required evidence set → match against uploaded evidence → PASS/MISSING/UNKNOWN`.
- **Contradiction detection:** BOL shipment number ≠ POD shipment number; invoice amount ≠ claim amount; delivery date conflicts with carrier response; one document says "shortage," another says "damage" — always surfaced to a human, never silently resolved.
- **Claim narrative grounding:** every factual sentence in the generated claim traces internally to evidence (sentence-level citations: `Sentence 1 → BOL p1`, etc.).
- **Rejection reason learning:** every rejection stored as structured data against a fixed taxonomy (missing document, late filing, insufficient proof of damage, amount unsupported, carrier denies liability, improper claimant, duplicate claim, policy/contract issue, other). Free-text carrier reasons are never treated as ground truth without human review.
- **Outcome feedback loop:** `claim submitted → carrier response → accepted/partial/rejected → recovery outcome → reason code → dataset → evaluation → workflow/prompt/model improvement`. This loop is the long-term data advantage — but it only starts compounding once the logging (Section 4, item 6) has been running for months.

**Acceptance prediction is explicitly Phase 5, not v1.** Do not set an internal 80-90% target as a requirement now — there isn't enough Algolyra outcome data yet to justify it. Instead measure, from day one: claims submitted/accepted/partially-settled/denied/appealed, $ recovered vs. $ claimed, acceptance rate, recovery rate, appeal success rate, average recovery time and %. Set an internal target only once real data justifies one — that's the scientifically defensible version of the same ambition. Once real labeled outcomes exist, a model estimating `P(accepted | claim features, evidence, carrier, amount, timing, history)` can be built — but it must never decide the outcome autonomously, only recommend ("needs additional evidence" / "high rejection risk, senior review recommended"), and must be evaluated for calibration, not just accuracy.

---

## 12. Human-in-the-loop design

**Approval levels:**
- **Level 0 — Routine:** low value, complete evidence, low ambiguity → human reviews before external submission.
- **Level 1 — Elevated:** medium value or moderate uncertainty → experienced claims operator reviews.
- **Level 2 — High value:** above customer-configured threshold → senior approver required.
- **Level 3 — Exceptional:** potential legal dispute, major financial exposure, severe ambiguity, sensitive communication → manual handling required.

Workflow enforcement is server-side (Section 9's code example), never just a hidden frontend button. Every human edit records the AI draft, the human's final version, the diff, the rejection reason if any, and approval duration — this is valuable future training/evaluation data, and it's also how you'll eventually know whether the AI is actually getting better.

---

## 13. Failure handling, idempotency, and reliability

**Explicit failure states required for:** OCR/vision failure, AI call timeout, corrupted PDF, invalid JSON from a model, unrecognized carrier, low extraction confidence, duplicate document upload, an email with 20 attachments, an uncalculable deadline, an unknown carrier rule. States: `processing_failed`, `needs_human_review`, `unsupported_document`, `unknown_carrier`, `unknown_rule`, `invalid_extraction`. **Never silently continue.**

**Idempotency:** file fingerprinting/hashing (the `sha256` field on `documents`) prevents the same POD being processed twice or the same shipment producing two separate claims.

**Reliability, given claims stay open for months:** idempotent jobs, retry with exponential backoff, dead-letter queue, workflow checkpoints, resumable processing, unique event IDs, transactional state changes, explicit timeouts, alerting for stuck claims, reconciliation jobs. A duplicate job must never generate a duplicate invoice or duplicate submission.

---

## 14. Security and privacy

Minimum controls from **Phase 0**, not Phase 2: tenant isolation, encrypted transport, encrypted object storage, encrypted database backups, role-based access control, server-side authorization checks, audit logs, secure secret management, signed/temporary file URLs (no public document URLs), virus/malware scanning for uploads, file-type validation, upload size limits, retention/deletion policy, backup/restore procedure. Never put customer documents or full claim/email bodies into logs by default; redact sensitive data from telemetry where possible.

---

## 15. Observability and AI evaluation

**Track:** application (API latency, error rate, queue depth, job failure rate, processing time), AI (model latency, token usage/cost, extraction error rate, schema validation failures, hallucination/unsupported-fact rate, human edit rate), business (readiness, acceptance, rejection, recovery, follow-up performance, fee generation). Every AI operation gets a trace ID connected to the claim/document, so "why did Claim #104 fail?" is answerable in seconds.

**Build an AI evaluation system earlier than most founders expect.** Versioned test set of real/synthetic claims:
- **Document extraction eval:** field-level accuracy on carrier, shipment ID, dates, amounts, quantities, exception type.
- **Classification eval:** precision/recall/confusion matrix, with special attention to damage-vs-shortage, loss-vs-theft, demurrage-vs-physical-cargo-claim.
- **Completeness eval:** missing-document recall, false block rate.
- **Draft eval:** human-scored factual correctness, completeness, clarity, unsupported claims, correct amount/attachments, tone.
- **Response-extraction eval:** acceptance/rejection detection, offer-amount extraction, rejection-reason classification, requested-document extraction.
- **Regression suite:** every prompt/model/workflow change reruns against the full test set. No model upgrade ships to production because it "feels better."

---

## 16. Production stack recommendation

**Frontend:** Next.js/React, TypeScript, a component library with strong form/table/document-review support.
**Backend:** Python, FastAPI, Pydantic, SQLAlchemy (or equivalent ORM).
**Database:** PostgreSQL + pgvector where semantic retrieval is needed.
**Object storage:** S3-compatible.
**Jobs/workflows:** a conventional job queue for simple async work first; add LangGraph/Pydantic AI or a workflow engine only when durable, long-running, human-interrupted execution becomes a real requirement.
**Document processing:** Docling / PaddleOCR / a commercial OCR API, evaluated per Section 8.
**AI:** provider abstraction (Pydantic AI or hand-rolled) so models swap without rewriting business logic.
**Auth:** Better Auth or a managed service unless auth itself is strategically important.
**Payments/billing:** a reliable payment/invoicing provider for card processing; the contingency-fee ledger itself stays inside Algolyra since it's core business data.

**Repository structure:**
```
algolyra/
├── apps/{web/{app,components,features/{claims,documents,dashboard,billing,settings},lib}, api/{routes,dependencies,main.py}}
├── packages/{domain,claims,documents,ai,rules,workflows,billing,communications,audit,analytics}
├── workers/{document_worker,ai_worker,workflow_worker,notification_worker}
├── migrations/
├── tests/{unit,integration,evals,fixtures}
├── scripts/  ├── infra/  ├── docs/{architecture,decisions,product,runbooks}
└── README.md
```
Every schema change is a migration (`001_initial_schema`, `002_add_document_confidence`, ...) — never a manual production edit. A `seed_demo_data` script should produce 5 claims across different statuses (one rejected, one appealed, one recovered) on demand, so the product can be demoed without live customer data.

---

## 17. Testing strategy

- **Unit tests:** deadline calculations, fee calculations, claim state transitions, rules engine, amount calculations.
- **Integration tests:** upload → extraction → claim creation → readiness → draft, end to end.
- **E2E tests:** the full vertical slice (Section 19) exercised as a user would.
- **AI evals:** the golden dataset described in Section 15, run in CI via DeepEval/RAGAS/Promptfoo (Section 8).
- **Security tests:** tenant isolation (customer A can never retrieve customer B's data), file-upload attack surface, authorization bypass attempts.

---

## 18. Critical failure modes to design against (name these explicitly, don't discover them in production)

1. **Hallucinated claim facts** — mitigated by Section 4's grounding rule + Section 7.4's evidence-linked extraction.
2. **Wrong shipment matched** — mitigated by Section 7.6's confidence-staged matching.
3. **Wrong deadline** — mitigated by Section 7.10's deterministic-only calculation + Section 7.9's sourced/versioned rules.
4. **Incorrect claim amount** — mitigated by Section 7.11's provenance trail.
5. **Duplicate submission** — mitigated by Section 13's idempotency.
6. **AI sends an unauthorized communication** — mitigated by Section 9's server-side state-machine enforcement.
7. **Duplicate recovery billing** — mitigated by Section 7.18's event-based recovery model (never a mutable single field).
8. **One customer's data retrieved for another** — mitigated by `organization_id` scoping enforced at the query layer, tested explicitly (Section 17).

---

## 19. Definition of done for a production claim

A claim is "done" only when: shipment identified, claim type classified with sufficient confidence, all required evidence present or explicitly waived, amount calculated with a provenance trail, deadline calculated deterministically from a sourced rule, human review completed and recorded, submission recorded with a channel and external reference, every subsequent carrier response parsed and stored, every recovery event recorded individually, the fee event calculated and tied to a specific recovery event, and the full chain is auditable end to end from a single claim ID.

---

## 20. Phase roadmap

### Phase 0 — Business validation before deep engineering
Goal: prove a real decision-maker will use and evaluate the workflow. Deliverables: 5-10 structured customer interviews, 2-3 real/anonymized claim examples, current-process mapping, estimated claims/month, average claim value, current recovery rate if available, current staff time per claim, current write-off pattern, buyer identified, pilot criteria defined. **Exit criterion:** at least one credible prospective customer agrees to test a working workflow with real or realistic data.

### Phase 1 — Demo
Build: auth, organization, claim creation, document upload/parsing, structured extraction, classification, missing-document detection, draft generation, human review — for **one carrier, one claim type (damage), one document workflow, one submission format, one user.** Do not build: carrier portal integrations, full automated billing, autonomous negotiation, a large carrier-rule catalog, sophisticated analytics. **Exit criterion:** a real broker looks at one claim and says "this saves me meaningful work and I would use it."

### Phase 2 — Pilot workflow
Add: claim dashboard, statuses, deadlines, tasks, follow-ups, audit log, carrier responses, approval thresholds, recovery recording, multi-tenancy enforcement (adopt Better Auth/SaaS-Boilerplate here), responsive web (not a dedicated mobile build), broker-only workspace (not multi-party collaboration yet). **Exit criterion:** a customer uses Algolyra for multiple claims without the founder manually operating every step.

### Phase 3 — Monetization
Add: contingency contracts, recovery events, fee calculation, invoice generation, reconciliation, revenue reporting. **Exit criterion:** at least one customer reaches a recovery and pays a fee under the contractual model.

### Phase 4 — Reliability and scale
Add: durable workflows, stronger tenant isolation, carrier rule versioning, email ingestion, TMS connectors, retry/recovery controls, production observability, security hardening, the full AI evaluation suite. **Exit criterion:** the platform handles a real increase in claim volume without founder-operated workarounds.

### Phase 5 — Acceptance-rate optimization
Once enough outcomes exist: rejection taxonomy, carrier outcome analytics, evidence recommendations, acceptance-risk model, similar-claim retrieval, proactive claim-quality recommendations. **Exit criterion:** measured improvement in acceptance/recovery metrics against the customer's own historical baseline.

### Phase 6 — Shipper product (later, per prior sequencing decisions)
Reuse the core engine; add shipper-specific roles, dashboard, submission workflows, internal approvals, carrier/broker relationship mapping, shipper-focused analytics. Don't clone the backend — add a different experience on the same claim/recovery platform.

### Phase 7 — Recovery platform (later)
Carrier intelligence, broader recovery analytics, more claim classes, more integrations, partner/API platform, additional recovery workflows. Only then consider insurance/subrogation and other large adjacent markets.

---

## 21. Demo scope for the immediate build

```
Create claim → Upload BOL/POD/invoice/photos → Parse documents → Extract facts
→ Classify (damage) → Identify missing evidence → Calculate proposed amount
→ Check deadline → Generate claim package → Human review → Approve
→ Mark submitted → Track status
```
Demo story: system shows `Shipment: 847293 | Carrier: ABC Trucking | Claim type: Damage | Claim amount: $8,000 | Deadline: September 15 | Evidence: 4/4 present`, AI produces a draft, user reviews highlighted evidence, approves, system shows `Claim Ready for Submission`. That's the entire first meaningful demo — nothing more is required to prove the concept.

---

## 22. What to explicitly postpone

Full autonomous negotiation, broad carrier-portal automation, every claim type/jurisdiction at once, insurer/subrogation platform, shipper platform before the broker workflow is validated, complex marketplace/network features, large-scale predictive models before enough outcome data exists, microservices, custom foundation models, custom OCR models before the baseline pipeline actually fails, and any generic AI assistant/chatbot that doesn't improve claim throughput.

---

## 23. Non-negotiable decisions (the ones that should survive any future scope debate)

1. Broker/3PL first. 2. Shipper later, on the shared core platform. 3. Contingency pricing is a product requirement, not a pricing-page decision. 4. Recovered dollars must be traceable to claims and payment events. 5. AI outputs must be evidence-grounded. 6. Rules and financial logic must be deterministic. 7. Human approval enforced server-side. 8. No autonomous high-stakes negotiation. 9. Every major workflow action is auditable. 10. The first production architecture must be simple enough for a solo founder to operate. 11. The first optimization target is recovered dollars, not AI novelty. 12. No feature gets built merely because it sounds impressive.

---

## 24. Development order and first 30 engineering tasks

```
1. Org/auth  2. Shipment/claim/document core  3. Document processing pipeline
4. Claim classification/completeness  5. Rules/deadline engine  6. Claim drafting
7. Human review/approval  8. Claim state machine  9. Submission record
10. Follow-up tasks  11. Carrier response parsing  12. Recovery events
13. Contingency fee ledger  14. Invoice generation  15. Analytics
16. Integrations  17. Advanced outcome models
```
Do not reverse this order by building analytics before the core claim lifecycle works.

**First 30 tasks:** (1) create monorepo (2) configure frontend (3) configure backend (4) configure PostgreSQL (5) org/user tables (6) shipment table (7) claim table (8) document table (9) object storage (10) document upload (11) document viewer (12) integrate first document parser (13) define extraction schemas (14) extraction worker (15) persist evidence/provenance (16) shipment matching (17) claim classification (18) claim requirement model (19) completeness checks (20) basic deadline model (21) draft generation (22) claim review screen (23) approval endpoint (24) claim state machine (25) claim detail screen (26) submission record (27) follow-up task model (28) recovery event model (29) contingency fee calculator (30) audit log.

At the end of this list you have a real vertical slice, not a collection of disconnected AI demos.

**Immediate milestone (not "production-ready Algolyra"):** a broker uploads a real or realistic claim package and, within a few minutes, Algolyra produces a structured, evidence-linked, human-reviewable claim package with visible missing evidence and deadline status. Once that works, add tracking → follow-up → recovery → billing. Then you have the beginnings of the actual business.

---

## 25. Business model implementation

**Contract must define:** eligible claims, recovery definition, contingency percentage, payment trigger, treatment of partial recovery, treatment of offsets/credits, cancellation rules, claim ownership, confidentiality, document/data processing, dispute process — have qualified legal counsel review before production use.

**Unit economics to track from Phase 3 onward:** cost to process a claim (AI + infra + time), average recovered $ per claim, average fee $ per claim, contribution margin per claim, customer acquisition cost, payback period, gross margin at scale.

**Go-to-market:** ICP is uninsured/self-insured brokers/3PLs doing 200+ shipments/month. First sales motion is high-touch, founder-led pilots, not self-serve — the product is too new and the trust bar (per customer research) is too high for cold self-serve signup. Pilot design: free or heavily discounted first cohort, explicit success criteria (X claims processed, Y% time saved, Z$ recovered) defined upfront with the pilot customer.

**Competitive strategy:** win on contingency pricing (nobody else offers it at this segment) and on evidence-grounded trust (Section 4-11) rather than trying to out-feature enterprise TMS-bundled claims modules.

**Long-term moat:** a proprietary claim-outcome dataset (Section 11's feedback loop), a carrier-intelligence graph built from real claims across many customers, claim-quality intelligence that improves with volume, and the resulting network advantage — none of which exist until Phase 4/5 real data accumulates. Don't build the moat features before the moat has data to build on.

---

## 26. Final architecture (target state, not v1)

```
                    ALGOLYRA
                        |
       +----------------+----------------+
       |                                 |
 Broker / 3PL Workspace             Shipper Workspace (Phase 6)
       |                                 |
       +----------------+----------------+
                        |
                Claims Platform
                        |
      +-----------------+------------------+
      |                 |                  |
 Documents          Rules Engine      Workflow Engine
      |                 |                  |
 OCR / VLM         Deadlines         Human Approval
 Extraction        Requirements       Follow-ups
      |                 |                  |
      +-----------------+------------------+
                        |
                 Claim Intelligence (Phase 5)
                        |
      +-----------------+------------------+
      |                 |                  |
 Drafting          Response AI       Outcome Data
      |                 |                  |
      +-----------------+------------------+
                        |
                 Recovery Engine
                        |
               Recovery Events
                        |
                 Fee Calculation
                        |
                    Billing
                        |
                  Revenue $$$
```

Strategic feedback loop: `more claims → more structured outcomes → better intelligence → better claim preparation → better recovery → more customer value → more claims`. That loop is the long-term product advantage — but it only starts once Phase 0-3 have produced real claims to learn from.

---

## 27. The actual company-building sequence

1. Get the first real broker/3PL to use the system. 2. Process multiple real claims. 3. Measure acceptance and recovery. 4. Identify why claims fail. 5. Build features that reduce those specific failures. 6. Prove Algolyra produces more recovered dollars with less manual work. 7. Charge on recovered dollars. 8. Expand the workflow until Algolyra is the system of record for claims. 9. Use accumulated outcomes to build carrier/recovery intelligence. 10. Add shippers as a second customer category on the same core engine. 11. Expand into broader recovery workflows only after the first machine works.

---

## 28. Final implementation principle

```
PROBLEM → DOCUMENTS → UNDERSTANDING → COMPLETE + VALID CLAIM → HUMAN APPROVAL
   → SUBMISSION → FOLLOW-UP → RECOVERY → MONEY → DATA → BETTER CLAIMS → HIGHER RECOVERY
```

That loop is the product. The AI is the acceleration layer. The recovery is the value. The contingency fee is the monetization. The accumulated outcomes are the long-term intelligence moat. Nothing in this document should be built out of sequence with that loop — build the loop first, narrow, for one carrier and one claim type, before touching anything else in this file.

---

## 29. Addendum — Expanded problem research and strategic expansion modules

*(Added from an external market/product research report, cross-checked against independent sources before inclusion. Nothing above this line was altered — this addendum only adds.)*

### 29.1 The core economic framing, restated with the industry data behind it

Freight claims industry-wide have a baseline recovery rate commonly cited around 30–50%, meaning a large share of damaged/lost/short freight value is written off annually — consistent with the $15-20k/broker/year abandonment figure already in this plan's customer research (Section 1). The root causes are the same three this plan already targets, now with the legal backing made explicit:

- **Filing-window compliance.** Under the Carmack Amendment (49 U.S.C. § 14706), claimants have a federal minimum of **nine months from delivery (or expected delivery, if lost)** to file a formal written claim — but individual carrier tariffs and bills of lading frequently impose **much shorter** notice periods, commonly 5-15 days for concealed damage. This is exactly the visible-vs-concealed distinction already built into `carrier_rule_sets`/`carrier_claim_rules` (Section 10) — this addendum confirms the legal basis is real and the distinction is correctly modeled.
- **Documentation incompleteness.** Missing exception notations or lost photos are enough for an instant carrier denial — already the top denial cause in this plan's acceptance-rate research (Section 11).
- **Small-claim abandonment.** Claims in the $400-$5,000 range often cost more in manual labor to file than their expected payout — the exact economic justification for AI-driven cost compression already stated in Section 0.1.

**One new, previously-unmodeled deadline to add to the rules engine:** if a carrier denies a claim, the claimant generally has **two years and one day from the date of the carrier's written denial** to file a lawsuit. This is a second, separate deadline clock that should be tracked once a claim enters `denied`/`appealed` status — add `lawsuit_deadline_at` (computed as `denial_date + 2 years + 1 day`) to the `claims` table's deadline logic in Section 10, and surface it in the deadline engine (Section 7.10) alongside the filing deadline. This matters specifically for Section 31.3's legal-escalation-partnership feature below — it's the clock that determines whether a legal partner still has time to act on a denied claim.

### 29.2 Four additional claim-lifecycle failure points (verified, add to the denial-cause table and completeness engine)

These are real, additional reasons claims get denied, beyond the ones already in Section 11's table. Each maps to a specific completeness-engine check (Section 7.8) or claim-requirement (Section 10's `claim_requirements` table).

**A. Concealed damage and the short notification window.** Already partially modeled (Section 10's `concealed_deadline_days`), but the addendum sharpens the point: most carrier tariffs require written notice of concealed damage within roughly 2-5 business days of delivery — tighter than the general 5-business-day assumption already in this plan. Treat the exact window as carrier-specific data pulled into `carrier_claim_rules`, not a single hardcoded constant.

**B. Failure to retain damaged freight (salvage/mitigation duty) — independently confirmed as a real, common, unappealable denial reason.** GSA's own freight-claims guidance states plainly that failure to preserve damaged cargo and packaging until the carrier authorizes disposal can result in claim denial, and NMFC Item #300150 establishes a legal duty to mitigate the carrier's loss where salvage has value. **New required completeness check:** add a `salvage_retained` boolean/status field to `claim_requirements`, defaulting to "unknown" and requiring explicit confirmation before submission — a claim should never reach `READY_FOR_REVIEW` without this being addressed, since it's an outright, unappealable denial ground.

**C. Double-brokering and carrier identity mismatch.** A real and growing problem: brokers file against the carrier named on the rate confirmation, only to discover a different, unverified party actually hauled the load — whose insurance may not cover the claim, or may not exist. This directly threatens the collectibility of a claim regardless of how well-documented it is. See Section 31.2 for the proposed countermeasure feature.

**D. Packaging vs. carrier negligence disputes.** Carriers routinely invoke the Carmack Amendment's "act or default of the shipper" exclusion, arguing inadequate packaging or improper load securement caused the damage. This is already captured in this plan's existing Section 4 constraint (factual indicators, not legal conclusions) and Section 11's contradiction detection — no new architecture needed, but it confirms that pre-filtering likely-shipper-fault claims (already planned) is addressing a real, heavily-used carrier denial tactic, not a hypothetical one.

### 29.3 Strategic expansion modules (Phase 5-7 — do not build before Phase 0-4 are working, per this plan's existing phase discipline)

These are legitimate, well-reasoned revenue/product expansions. Each is tagged with the phase it belongs to, consistent with this plan's existing anti-scope-creep principles (Sections 22-23) — none of them belong in the Phase 0-2 build.

**29.3.1 Automated salvage valuation & liquidation module (Phase 5+).** Damaged goods often retain real residual value that brokers have no time to realize, resulting in either a full write-off or an excessive carrier salvage deduction. Proposed: an AI-vision-assisted salvage valuation step off the same damage photos already captured in Section 7.5, connected to liquidation marketplaces or salvage buyers, deducting salvage value accurately before submission. **Monetization:** a platform fee or revenue share on liquidated salvage separate from the core recovery contingency fee — a genuine second revenue line on the same evidence pipeline, similar in spirit to the freight-bill-audit add-on already noted in Section 25. Requires real claim volume and salvage-buyer relationships before it's viable — Phase 5, not earlier.

**29.3.2 Carrier insurance & double-brokering verification ("Risk Shield") (Phase 5+, with one factual correction).** The proposed feature: query FMCSA data at shipment/claim intake to flag carriers with lapsed authority or uncertain insurance, addressing failure point 29.2.C above. **Correction to the source report:** FMCSA's public SAFER system (`safer.fmcsa.dot.gov`) provides authority/registration/safety data for free, but **insurance status specifically lives in a separate system, FMCSA Licensing & Insurance (`li-public.fmcsa.dot.gov`)** — a common mistake even among brokers, per independent research. Both are free, public, and have no official modern API (third-party wrappers exist but aren't official FMCSA products) — build this as a scheduled lookup/scrape against both public endpoints, not a single unified "SAFER API" call. **Monetization:** a premium risk-management tier or a higher contingency rate on claims flagged as higher-collectibility-risk, as the source report proposes.

**29.3.3 Tiered contingency & legal escalation partnerships (Phase 6+).** For claims a carrier denies and won't reconsider through the standard appeal loop (Section 21's negotiation/appeal workflow), partner with freight-specialized law firms for formal demand letters, litigation, or arbitration, at a materially higher contingency rate (e.g., 30-35% vs. the standard 15-20%) on amounts recovered through that escalation path. This is a real, sound structure — it monetizes the highest-value disputed claims without requiring in-house legal headcount, and it's exactly what the newly-added `lawsuit_deadline_at` field (Section 29.1) exists to support: knowing precisely how long a legal partner has to act on a denied claim before the right disappears entirely.

**29.3.4 Proactive statute & tariff guardian (Phase 5+).** Beyond the standard 9-month federal Carmack window, specific broker-carrier Master Service Agreements (MSAs) and international multimodal waybills can impose shorter contractual limitation periods (60-180 days is cited as typical). Proposed: a contract-parsing step that ingests MSAs alongside the BOL to extract custom, shorter limitation clauses and override the standard statutory clock when a contract requires it. This is a natural extension of the sourced/versioned rules engine already specified in Section 7.9/10 — the same "every rule needs a source, verification, and effective dates" discipline just applies to contract-derived rules, not only carrier-tariff rules. Positions Algolyra as risk mitigation against broker E&O exposure, not just a recovery tool — a meaningful differentiator once the core product has traction, but explicitly not a Phase 0-2 concern since it requires reliable contract-parsing accuracy that doesn't exist yet.

### 29.4 Updated denial-cause reference (supersedes nothing — read alongside Section 11's original table)

| Additional denial/collectibility cause | Countermeasure | Phase |
|---|---|---|
| Failure to retain damaged freight (salvage/mitigation duty) | Hard completeness check (`salvage_retained` status) before `READY_FOR_REVIEW` | Phase 1 (check), Phase 5 (salvage valuation module) |
| Concealed-damage notice window narrower than assumed | Carrier-specific window in `carrier_claim_rules`, not a hardcoded constant | Phase 1 |
| Double-brokering / uncollectible carrier | FMCSA SAFER + L&I verification at intake, flagged as risk | Phase 5 |
| Post-denial lawsuit statute of limitations missed | `lawsuit_deadline_at` tracked on denied/appealed claims | Phase 2 (field), Phase 6 (legal partnership workflow) |
| Contract-specific limitation periods shorter than statutory | MSA-parsing extension of the sourced rules engine | Phase 5 |

### 29.5 Sources for this addendum

Freight claims recovery-rate and industry framing: CXTMS (2026). Carmack Amendment statutory basis: 49 U.S.C. § 14706. Concealed-damage/denial patterns: Logistics Plus; Partnership Transportation. Salvage/mitigation duty: GSA freight-damage-claims guidance; FedEx Freight Loss & Damage Claims Guide; NMFC Item #300150 (via ATS Logistics); GetTent. Double-brokering risk: r/FreightBrokers community discussion (qualitative signal, not a primary legal/statistical source — treat accordingly). Post-denial lawsuit window: uslawexplained.com summary of Carmack Amendment litigation timelines. FMCSA SAFER vs. L&I system distinction: carriervets.com, verified independently against FMCSA's own public system structure.
