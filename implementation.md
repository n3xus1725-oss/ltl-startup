# IMPLEMENTATION.md — AI Freight Information & Execution Platform

Purpose: Technical implementation plan only.
Business/customer requirements: Defined in PRD.md. Do not repeat business strategy here except where required to implement a feature.
Execution rule: Work strictly phase-by-phase. Do not start a later phase until the current phase's exit criteria are met.
Primary development environment: Antigravity.
Primary language: Python 3.12+.
Primary backend: FastAPI.
Primary database: PostgreSQL/Supabase.
Initial agent framework: LangGraph.
LLM gateway: LiteLLM.
Optional integration layer: Composio, only where it reduces implementation time without weakening control/security.
Durable execution: Temporal, introduced after the basic event/action loop is proven.
Vector search: pgvector only when semantic retrieval is required; do not add it merely because it is available.

---

## 0. NON-NEGOTIABLE ENGINEERING RULES

1. Do not build six independent agents. Build one shared agent platform with six specialized workflows.
2. The LLM never directly writes to production systems. The LLM may select a typed tool; application code validates and executes the tool.
3. Business rules stay in deterministic code. Use the model for interpretation, classification, reasoning, and drafting; use code for arithmetic, thresholds, permissions, state validation, and financial calculations.
4. Every material action must be auditable. Store input event, model decision, selected tool, arguments, result, actor, timestamp, and approval state.
5. Every automated action must be idempotent. Reprocessing the same webhook/email/event must not duplicate an action.
6. Every external call must have timeout, retry, backoff, and structured error handling.
7. Human approval is a first-class capability. High-risk or low-confidence actions pause and request approval instead of guessing.
8. No silent data overwrite. Important fields retain provenance and conflicts are explicitly represented.
9. No fake confidence. If evidence is insufficient, the system must say so and escalate.
10. Build the smallest working slice first. Do not add an enterprise feature because a framework supports it.

---

## 1. REFERENCE GITHUB REPOSITORIES

These repositories are implementation references/components. Prefer installing the libraries from their official packages for the product. Clone an example repository only when it is specifically needed for study or adaptation.

### 1.1 LangGraph — PRIMARY AGENT ORCHESTRATION

Repository: https://github.com/langchain-ai/langgraph

Use for:
● stateful agent workflows
● branching decisions
● tool calling
● controlled multi-step execution
● checkpoint/persistence patterns
● human-in-the-loop flows
● long-running workflow logic

Why it is selected:
LangGraph is explicitly positioned as a low-level orchestration framework for stateful and long-running agents and supports graph-based control over agent execution.

Implementation instruction:
● Install the library; do not fork the framework.
● Study the main repository and official examples before implementing the first workflow.
● Build the application graph ourselves so the business logic stays under our control.

### 1.2 LangGraph Example — REFERENCE DEPLOYMENT PATTERN

Repository: https://github.com/langchain-ai/langgraph-example

Use for:
● project layout reference
● basic LangGraph deployment patterns
● HTTP-serving patterns
● persistence concepts

Do not use as the product's codebase. Use it as a reference only.

### 1.3 LiteLLM — MODEL GATEWAY / COST CONTROL

Repository: https://github.com/BerriAI/litellm

Use for:
● one application interface across model providers
● routing between cheap and stronger models
● spend tracking
● retries/fallbacks
● provider switching

LiteLLM provides an OpenAI-compatible interface to 100+ model providers and includes gateway features such as spend tracking, guardrails and load balancing.

Implementation instruction:
● All LLM calls go through a thin internal llm_gateway wrapper.
● The application must not call provider SDKs from business logic.
● Keep model selection in configuration/policy, not scattered through the code.

### 1.4 Temporal Python SDK — DURABLE EXECUTION

Repository: https://github.com/temporalio/sdk-python

Use for:
● long-running workflows
● retries
● timers
● durable execution
● workflow recovery after process/server failure
● human approval pauses/resumes

Temporal is designed for distributed, scalable and durable execution of asynchronous, long-running business logic.

Implementation instruction:
● Do not make Temporal a prerequisite for Phase 1.
● Introduce Temporal once the core agent loop is proven and real background workflows require durable execution.

### 1.5 Composio — OPTIONAL TOOL/INTEGRATION LAYER

Repository: https://github.com/ComposioHQ/composio

Use selectively for:
● external SaaS tool connections
● authentication helpers
● prebuilt integrations
● MCP/toolset patterns

Composio currently provides large numbers of toolkits and agent-oriented integration capabilities.

Implementation instruction:
● Do not make Composio a hard dependency for core financial logic.
● For critical operations (billing mutations, dispute status, permission checks), prefer our own typed internal tools.
● Use Composio where it materially reduces integration effort and the action can be safely wrapped/validated.

### 1.6 PydanticAI — OPTIONAL REFERENCE FOR STRICT TOOLING / VALIDATION

Repository: https://github.com/pydantic/pydantic-ai

Use for:
● typed agent interfaces
● strict tool definitions
● structured outputs
● toolsets
● validation patterns
● evaluation patterns

PydanticAI provides typed tools/toolsets and can integrate with durable execution systems such as Temporal.

Implementation instruction:
● Do not introduce PydanticAI alongside LangGraph in Phase 1 unless there is a concrete benefit.
● Borrow its typed-tool and validation design principles even if LangGraph remains the orchestration layer.

### 1.7 pgvector — OPTIONAL SEMANTIC RETRIEVAL

Repository: https://github.com/pgvector/pgvector

Use for:
● semantic search over documents/messages
● finding similar prior emails/disputes
● retrieving relevant contract/rate documents
● future long-term memory

pgvector adds vector similarity search directly to PostgreSQL and supports exact and approximate nearest-neighbor search.

Implementation instruction:
● Enable only after keyword/relational retrieval is insufficient.
● Do not put primary shipment truth in vectors; canonical truth remains relational.

---

## 2. TARGET TECHNICAL ARCHITECTURE

```
EXTERNAL EVENTS
|
+------------+-------------+
|                          |
Gmail/365             Webhooks/APIs
|                          |
+------------+-------------+
|
FastAPI Ingestion
|
Idempotency + Queue
|
Canonical Event Log
|
Entity / Shipment Resolver
|
Source-of-Truth Layer
|
Rules + Evidence Engine
|
LangGraph Workflow
|
+-----------+-----------+
|                       |
Deterministic Tool    LLM Call
|                       |
+-----------+-----------+
|
Policy / Permission
|
Execute External Action
|
Verify External Result
|
Persist Audit Evidence
|
Schedule Next Follow-up
```

Primary services:
/apps/api              FastAPI application
/apps/worker           background worker(s)
/apps/agent            LangGraph workflows
/packages/tools        typed action tools
/packages/domain       domain models + validation
/packages/rules        deterministic audit/risk rules
/packages/llm          LiteLLM wrapper + routing policy
/packages/connectors   Gmail/TMS/accounting/carrier adapters
/packages/storage      repositories + document storage
/packages/audit        event/audit ledger
/packages/evals        test datasets + agent evaluations

---

## 3. PHASED IMPLEMENTATION PLAN

### PHASE 1 — FOUNDATION + INBOX AGENT VERTICAL SLICE

Goal: Build one complete end-to-end path: email arrives → system identifies shipment → agent reasons → permitted action occurs → result is verified and logged.

Do not implement all six capabilities yet.

#### Phase 1.1 — Repository Bootstrap

GitHub references:
● LangGraph: https://github.com/langchain-ai/langgraph
● LangGraph example: https://github.com/langchain-ai/langgraph-example
● LiteLLM: https://github.com/BerriAI/litellm

Tasks:
1. Create mono-repo or clearly separated application repository.
2. Configure Python 3.12+.
3. Add package manager (uv recommended) and lockfile.
4. Create FastAPI service.
5. Create worker entrypoint.
6. Add LangGraph dependency.
7. Add LiteLLM dependency.
8. Add Pydantic settings/configuration.
9. Add structured logging.
10. Add .env.example; never commit secrets.
11. Add Dockerfile(s) for local/production parity.
12. Add GitHub Actions for lint, unit tests and type checks.
13. Create /health and /ready endpoints.

Exit criteria:
● Fresh clone installs successfully.
● API starts.
● Worker starts.
● CI passes.
● No secrets are committed.

#### Phase 1.2 — Database + Core Data Model

Tasks:
1. Create PostgreSQL/Supabase project.
2. Implement tables for:
   ○ organizations
   ○ users
   ○ inbox_connections
   ○ shipments
   ○ shipment_events
   ○ documents
   ○ messages
   ○ agent_runs
   ○ tool_calls
   ○ approvals
   ○ exceptions
   ○ audit_log
3. Add organization scoping to all customer data.
4. Add UUID primary keys.
5. Add created/updated timestamps.
6. Add unique constraints required for idempotency.
7. Add indexes for:
   ○ shipment ID
   ○ external message ID
   ○ external invoice ID
   ○ carrier reference
   ○ email thread ID
8. Create repository/service layer; do not query the database directly from agent nodes.

Exit criteria:
● Migrations run from a clean database.
● CRUD tests pass.
● Duplicate event constraints work.

#### Phase 1.3 — Event Ingestion Layer

Tasks:
1. Create inbound event endpoint.
2. Normalize external event payloads to an internal event schema.
3. Assign event_id.
4. Enforce idempotency.
5. Persist raw payload metadata.
6. Push normalized event to background processing.
7. Record processing status:
   ○ received
   ○ queued
   ○ processing
   ○ completed
   ○ failed
8. Add retry-safe processing.

Exit criteria:
● Same event sent twice creates one logical event/action.
● Failed processing can be retried.
● Raw event metadata is preserved.

#### Phase 1.4 — Gmail/Email Connector

Tasks:
1. Implement OAuth connection for a test mailbox.
2. Retrieve message metadata and body.
3. Retrieve attachments.
4. Normalize sender/recipient/thread/message IDs.
5. Store message metadata and document references.
6. Implement outbound send-email action.
7. Implement thread/reply lookup.
8. Implement connector-level rate-limit handling.

Agent-facing tools:
● search_emails
● get_email
● get_attachment
● send_email
● reply_to_thread
● mark_email_processed

Exit criteria:
● Test mailbox can be connected.
● New email enters the system.
● Agent can safely reply through a typed tool.

#### Phase 1.5 — Shipment Resolver

Tasks:
1. Extract candidate identifiers from email:
   ○ load ID
   ○ shipment ID
   ○ PRO number
   ○ invoice number
   ○ BOL number
2. Match candidates against database.
3. Score deterministic matches.
4. Only use LLM reasoning when deterministic matching fails/ambiguous.
5. Return:
   ○ matched entity
   ○ match type
   ○ evidence
   ○ confidence
6. Never choose arbitrarily when two shipments are plausible.

Exit criteria:
● Known test emails resolve to the correct shipment.
● Ambiguous emails create a review task instead of a wrong update.

#### Phase 1.6 — First LangGraph Workflow

Tasks:
1. Define graph state.
2. Nodes:
   ○ ingest
   ○ resolve_entity
   ○ load_context
   ○ classify_intent
   ○ propose_action
   ○ permission_check
   ○ execute_tool
   ○ verify_result
   ○ audit
3. Add checkpointing/persistence appropriate for MVP.
4. Add explicit terminal outcomes:
   ○ completed
   ○ needs_human
   ○ failed
5. Keep the graph deterministic around tool execution.

#### Phase 1.7 — Tool Registry + Permissions

Initial tools:
● find_shipment
● get_shipment
● update_shipment
● attach_document
● create_task
● send_email
● reply_to_thread
● create_exception

Each tool must define:
● name
● purpose
● input schema
● output schema
● required permission
● idempotency key
● audit metadata
● external side effects

#### Phase 1.8 — Inbox Action Agent v0

Implement only 3 high-confidence workflows:

A. Pickup confirmation
● parse carrier message
● resolve shipment
● update pickup status/time
● store evidence
● optionally notify customer

B. ETA update
● identify shipment
● update ETA
● preserve prior ETA as history
● optionally notify customer

C. Missing-information request
● identify missing required field
● send a standardized request to the relevant sender
● create follow-up timer/task

#### Phase 1.9 — Auditability + Evaluation

Create a test dataset of at least:
● 50 normal emails
● 20 ambiguous emails
● 20 irrelevant emails
● 20 emails with conflicting information

Measure:
● correct shipment match rate
● correct action rate
● false-action rate
● human escalation rate
● duplicate-action rate

PHASE 1 EXIT CRITERIA:
● End-to-end inbox vertical slice works.
● Actions are actually executed, not merely suggested.
● Duplicate events are safe.
● High-risk/ambiguous cases escalate.
● Audit logs show every action.
● Automated tests pass.

---

### PHASE 2 — SOURCE-OF-TRUTH + DOCUMENT RECONCILIATION

Goal: Make shipment truth the shared information layer used by all later agents.

#### Phase 2.1 — Canonical Shipment Model

Add structured entities for:
● shipment parties
● locations
● dates/times
● freight details
● pricing
● carrier
● documents
● billing references
● operational events

Implement field-level provenance:
● source
● source ID
● timestamp
● confidence
● authority
● writer

#### Phase 2.2 — Document Processing Pipeline

Implement document intake for:
● PDF
● image
● CSV/XLSX where required
● email attachments

Pipeline:
Document received
→ file validation
→ text/table extraction
→ document classification
→ field normalization
→ validation
→ provenance storage

LLMs may interpret ambiguous text; deterministic parsers handle structured formats first.

#### Phase 2.3 — Source Authority Rules

Define field-specific precedence, for example:

invoice amount:
contract/rate agreement > invoice

shipment weight:
verified reweigh > BOL > other communication

pickup status:
verified carrier event > email statement > manual note

These are examples only; exact precedence must be configurable per customer.

#### Phase 2.4 — Conflict Engine

Create conflicts for:
● value mismatch
● missing evidence
● stale value
● incompatible status
● conflicting party identity

Each conflict gets:
● field
● source A
● source B
● severity
● explanation
● recommended workflow

#### Phase 2.5 — Retrieval Layer

Start with relational/keyword retrieval.

Only after evaluation proves a need for semantic retrieval:
● enable pgvector
● embed approved document chunks
● store embedding metadata
● retrieve only within organization scope

#### Phase 2.6 — Upgrade Inbox Agent

Inbox Agent now consults the Source-of-Truth Engine before acting.

PHASE 2 EXIT CRITERIA:
● A shipment can be reconstructed from multiple information sources.
● Conflicts are surfaced, never silently overwritten.
● All later agents can read the same canonical shipment context.

---

### PHASE 3 — BILLING AUDIT AGENT

Goal: Detect financial discrepancies with deterministic evidence first, then use AI for explanation/ambiguity.

#### Phase 3.1 — Invoice Intake

Implement:
● invoice email ingestion
● invoice document storage
● invoice number detection
● shipment association
● duplicate detection

#### Phase 3.2 — Rate / Contract Data Model

Create models for:
● base rate
● minimum charge
● fuel schedule
● accessorials
● class/rate rules
● effective dates
● lane/customer/carrier scope

#### Phase 3.3 — Deterministic Audit Engine

Implement rules for:
1. duplicate invoice
2. linehaul mismatch
3. fuel mismatch
4. unsupported accessorial
5. weight mismatch
6. class mismatch
7. reclass discrepancy
8. dimension/pallet discrepancy
9. quote-versus-invoice mismatch
10. arithmetic/total mismatch

Every rule returns structured evidence.

#### Phase 3.4 — Audit Agent with LangGraph

Workflow:
Invoice received
→ identify shipment
→ gather evidence
→ run deterministic rules
→ classify result
→ request LLM explanation only where useful
→ produce audit finding
→ decide next workflow

#### Phase 3.5 — Finding Quality Controls

Every finding must have:
● expected value
● billed value
● difference
● reason
● source documents
● rule ID
● confidence
● evidence references

Never output “confirmed” without sufficient evidence.

#### Phase 3.6 — Cost Optimization

Use cheap/local model for:
● document classification
● simple extraction validation
● email classification

Use stronger model only for:
● ambiguous contract language
● complex discrepancy explanation
● multi-document reasoning

PHASE 3 EXIT CRITERIA:
● Historical invoices can be audited.
● Findings are explainable.
● Financial arithmetic is deterministic.
● Agent cost per audited invoice is measurable.

---

### PHASE 4 — DISPUTE AGENT

Goal: Convert verified audit findings into real recovery actions.

#### Phase 4.1 — Dispute Data Model

Implement:
● dispute ID
● invoice ID
● shipment ID
● disputed amount
● expected amount
● evidence set
● recipient
● submission timestamp
● status
● carrier response
● recovery amount

#### Phase 4.2 — Recipient Resolution

Create a verified carrier-contact registry.

Sources may include:
● customer-configured billing address
● verified carrier profile
● approved portal destination

Never generate a destination from model memory.

#### Phase 4.3 — Dispute Package Generator

Generate:
● subject
● concise explanation
● invoice details
● disputed lines
● expected amount
● evidence references
● attachments

LLM drafts language; deterministic data fills financial facts.

#### Phase 4.4 — Approval Policy

Rules:
low-risk + high-confidence + below customer auto-send limit
→ auto-send

otherwise
→ human approval

#### Phase 4.5 — Send + Track

Implement:
● email submission
● portal submission adapter later if needed
● response thread tracking
● follow-up scheduling
● status transitions
● timeout/escalation

#### Phase 4.6 — Recovery Verification

Accept recovery only when supported by:
● carrier approval
● credit memo
● corrected invoice
● AP/TMS confirmation
● explicit customer confirmation

Create a recovery ledger separate from the agent's predicted recovery.

PHASE 4 EXIT CRITERIA:
● A verified discrepancy can become a real dispute.
● Dispute status is trackable.
● Recovery is based on evidence, not self-reported assumptions.

---

### PHASE 5 — RISK / VERIFICATION AGENT

Goal: Prevent suspicious carrier or transaction actions.

#### Phase 5.1 — Carrier Identity Model

Store:
● legal name
● MC/USDOT IDs where applicable
● authority status snapshot
● insurance metadata where legally/technically available
● approved contacts
● domains
● payment destinations
● verification timestamps

#### Phase 5.2 — Verification Connectors

Implement the minimum needed external verification sources.

Connector rules:
● cache responses where permitted
● record source and timestamp
● rate-limit requests
● never represent stale data as live

#### Phase 5.3 — Risk Rules

Start with deterministic checks:
● identity mismatch
● domain/contact mismatch
● unexpected payment change
● missing/invalid authority data
● conflicting carrier information
● suspicious reuse patterns

#### Phase 5.4 — Risk Agent

The model explains evidence; deterministic rules generate core risk signals.

Outputs:
● allow
● review
● hold

Never output an unsupported allegation of fraud.

#### Phase 5.5 — Action Guardrails

High-risk outcomes can block:
● tender
● payment detail changes
● sensitive document release

Blocking must be configurable and reversible by an authorized human.

PHASE 5 EXIT CRITERIA:
● Risk checks can run automatically before configured actions.
● Evidence is visible to the reviewer.
● High-risk actions can be safely held.

---

### PHASE 6 — EXCEPTION AGENT + DURABLE 24/7 EXECUTION

Goal: Turn the product from event-driven automation into continuous operations.

#### Phase 6.1 — Exception Engine

Standardize exception types:
● late pickup
● missing pickup confirmation
● missing POD
● delayed ETA
● no carrier response
● missing document
● unresolved billing dispute
● unresolved truth conflict

#### Phase 6.2 — Follow-up Policy Engine

Each exception has:
● trigger condition
● SLA
● first action
● follow-up interval
● maximum attempts
● escalation owner
● stop/resolution condition

#### Phase 6.3 — Temporal Integration

GitHub repo: https://github.com/temporalio/sdk-python

Implement Temporal workflows for operations that must survive process/server failure and wait for external responses.

Candidate workflows:
● dispute follow-up
● missing POD follow-up
● carrier response chase
● human approval pause/resume
● scheduled audit jobs

Keep short request/response operations in FastAPI/background tasks where Temporal adds no value.

#### Phase 6.4 — 24/7 Worker Model

Deploy:
● API service
● background worker
● Temporal worker if enabled
● database
● queue/cache as needed

Add:
● health checks
● automatic restart
● graceful shutdown
● retry policies
● dead-letter handling
● monitoring

#### Phase 6.5 — Exception Agent

Workflow:
Detect exception
→ verify exception
→ load shipment truth
→ select action
→ execute communication/tool
→ wait
→ interpret response
→ update truth
→ resolve or escalate

PHASE 6 EXIT CRITERIA:
● Agent workflows continue after process restarts.
● Long-running tasks survive delayed external responses.
● Exceptions are automatically followed through to resolution/escalation.

---

### PHASE 7 — INTEGRATION HARDENING + PRODUCTION SECURITY

Goal: Make the MVP safe enough for real customer data and real money-related workflows.

#### Phase 7.1 — TMS Adapter Interface

Do not hard-code one TMS throughout the application.

Create an interface such as:
TMSAdapter
├── get_shipment()
├── update_shipment()
├── add_note()
├── attach_document()
├── get_invoice()
├── update_invoice()
└── search_shipments()

Implement the first customer's TMS only.

#### Phase 7.2 — Accounting Adapter Interface

Create:
● get_invoice
● get_credit_memo
● record_verified_recovery
● get_payment_status

Do not grant the agent unrestricted accounting permissions.

#### Phase 7.3 — Authentication / Authorization

Implement:
● organization isolation
● role-based permissions
● service-to-service authentication
● encrypted OAuth/token storage
● secret rotation strategy
● audit of privileged actions

#### Phase 7.4 — Tool Permission Matrix

Example:
read_email → auto
send_email → policy-controlled
update_shipment → auto if validated
create_dispute → auto/approval by amount
send_dispute → approval by policy
change_payment_information → human only
approve financial adjustment → human only

#### Phase 7.5 — Observability

Track:
● request IDs
● agent run IDs
● tool latency
● tool failure rate
● LLM latency
● token usage
● cost per run
● actions per shipment
● human escalation rate
● external API errors

PHASE 7 EXIT CRITERIA:
● Customer data is isolated.
● Sensitive actions are permission-controlled.
● Operational failures are observable.
● Cost per workflow is measurable.

---

## 4. AGENT IMPLEMENTATION STANDARD

Every agent must have these files/modules:
agent_name/
├── graph.py        # LangGraph definition
├── state.py        # typed state
├── prompts.py      # only model instructions
├── policies.py     # deterministic policy checks
├── tools.py        # allowed tools for this agent
├── validators.py   # input/output validation
├── service.py      # business orchestration outside model
└── tests/
    ├── test_happy_path.py
    ├── test_ambiguous.py
    ├── test_conflict.py
    ├── test_tool_failure.py
    └── test_idempotency.py

Agent run must always contain:
run_id
organization_id
trigger_event_id
entity_id
model_used
model_version
prompt_version
tools_available
tool_calls
decision
confidence
approval_state
final_result
errors
cost_estimate
started_at
completed_at

---

## 5. MODEL / COST ENGINEERING

### 5.1 Routing policy

Use LiteLLM as the model gateway.

Routing principle:
Simple classification / normalization
→ cheapest acceptable model

Normal multi-document reasoning
→ mid-tier model

Financially ambiguous / high-value reasoning
→ strongest approved model

### 5.2 Never use an LLM for deterministic work

Do not ask the model to calculate:
● invoice totals
● percentage differences
● duplicate detection
● SLA timing
● authorization limits
● date arithmetic
● exact policy thresholds

Do those in code.

### 5.3 Cache aggressively

Cache:
● carrier metadata
● static contract/rate documents
● repeated document interpretations where safe
● deterministic lookups

### 5.4 Limit context

Retrieve only the documents/fields needed for the current decision.
Do not send an entire customer database to the model.

### 5.5 Tool search / progressive disclosure

As the tool count grows, selectively expose only relevant tools. PydanticAI's current toolset patterns provide a useful reference for deferred tool loading and tool discovery.

---

## 6. TESTING STRATEGY

### 6.1 Unit tests

Test every:
● rule
● parser
● validator
● permission
● repository
● tool wrapper

### 6.2 Agent trajectory tests

For each agent test:
● correct tool chosen
● wrong tool rejected
● missing information escalated
● duplicate trigger is harmless
● external failure retries safely
● final result matches expected outcome

### 6.3 Golden datasets

Create fixed datasets from anonymized real-like freight examples.
Track regressions across every model/prompt change.

### 6.4 Financial correctness tests

Billing audit calculations require deterministic expected answers.

Example:
Expected linehaul = 1200
Billed linehaul = 1500
Variance = 300

The model must never determine the $300 arithmetic.

### 6.5 Safety tests

Attempt to induce the agent to:
● send to an unverified recipient
● change payment data
● create duplicate disputes
● overwrite trusted values
● approve unauthorized financial actions

All must fail safely.

---

## 7. DEPLOYMENT PLAN

### Development
● Antigravity
● local FastAPI
● local Postgres or Supabase development project
● local/test model where practical

### Staging
● separate Supabase project
● test Google/Microsoft mailbox
● staging model keys
● fake/sandbox external integrations where available

### Production

Minimum services:
Frontend/dashboard
API
Worker
PostgreSQL/Supabase
Object storage
LLM gateway
Queue/cache
Monitoring

Add Temporal in Phase 6 for durable long-running workflows.

---

## 8. PHASE ORDER — DO NOT SKIP

PHASE 1
Foundation + Inbox vertical slice
↓
PHASE 2
Source-of-Truth + Reconciliation
↓
PHASE 3
Billing Audit
↓
PHASE 4
Dispute Agent
↓
PHASE 5
Risk / Verification
↓
PHASE 6
Exception Agent + 24/7 durable execution
↓
PHASE 7
Production hardening

Within each phase, execute the numbered subphases in order.

When Antigravity is instructed:
“Build Phase 1.1”
it must perform only Phase 1.1, run its tests, and stop.

When instructed:
“Build Phase 1.2”
it may proceed only after Phase 1.1 exit criteria are satisfied.

Do not jump to Phase 3 because a feature appears easy.

---

## 9. ANTIGRAVITY EXECUTION INSTRUCTION

For every requested phase/subphase:
1. Read this file first.
2. Read the relevant existing code before changing it.
3. Check the previous phase exit criteria.
4. Identify the GitHub reference relevant to the subphase.
5. Install/use only the required dependencies.
6. Implement the smallest complete version of the subphase.
7. Add/update tests before declaring completion.
8. Run lint/type-check/tests.
9. Fix failures.
10. Summarize:
● files created/changed
● dependencies added
● tests run
● test results
● remaining blockers
11. Do not implement future phases unless explicitly instructed.

Primary implementation principle:
Build reliable deterministic infrastructure first, put agentic reasoning on top of it, and only grant the AI the minimum tools required to safely perform the job.
