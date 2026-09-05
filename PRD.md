# PRD — AI Freight Information & Execution Platform

**Document Type:** Product Requirements Document (PRD) + Technical Implementation Blueprint  
**Status:** Master PRD for Antigravity Implementation  
**Primary Development Environment:** Antigravity  
**Primary Market:** U.S. Less-Than-Truckload (LTL) Freight Brokers / 3PLs  
**Initial Commercial Goal:** Ship a narrow MVP, secure design partners, prove measurable ROI, and reach first revenue before expanding platform scope.

---

## PART I — BUSINESS, CUSTOMER, PRODUCT

---

### 1. Executive Summary

We are building an **AI-native information and execution layer for LTL (Less-Than-Truckload) freight operations**.

The core problem in freight brokerage is not simply that freight brokers lack software. Freight brokers already use Transportation Management Systems (TMS), email clients, load boards, rate engines, accounting software, and carrier portals. The core problem is that **freight information is fundamentally fragmented** across:
- Inbound and outbound emails
- PDF attachments (BOLs, PODs, rate confirmations, carrier invoices)
- Contracts and tariff sheets
- Disparate carrier communications
- Internal TMS records
- Invoices and billing statements
- Ad-hoc operational spreadsheets
- Carrier web portals
- Accounting and AP systems

Today, human operators repeatedly perform the exact same manual, ten-step operational cycle for every load:
1. **Read incoming information** (emails, attachments, carrier notifications).
2. **Determine what shipment it belongs to** (matching load numbers, PRO numbers, reference IDs).
3. **Compare it with what is already known** (checking TMS status, quoted rates, origin/destination details).
4. **Decide whether something changed or went wrong** (delay, accessorial surcharge, reweigh, missed pickup).
5. **Update systems** (typing updates into the TMS, updating status flags).
6. **Email someone** (notifying the customer, checking in with the carrier).
7. **Create a follow-up** (setting a calendar reminder or mental note to check ETA in 2 hours).
8. **Audit a document** (matching carrier invoices line-by-line against rate cons and BOLs).
9. **Dispute an incorrect charge** (composing a dispute email with attachments).
10. **Keep checking until the issue is resolved** (tracking whether carrier accepted, adjusted, or issued credit).

Our product converts this from **reactive human information processing** into **autonomous agentic execution**.

#### Behavior Profile: The Capable Freight Operations Employee
The platform behaves like a seasoned freight operations specialist:
- Continuously **watches** incoming operational data streams (email, EDI/API, documents).
- **Maintains** a single, verified canonical state of truth for every shipment.
- **Determines** what next operational or financial action must happen.
- **Performs** authorized actions autonomously via integrated tools.
- **Verifies** the outcome of every action taken.
- **Escalates** to human operators only when ambiguity, high financial exposure, or policy thresholds require human judgment.

> **What We Are NOT:**  
> The first product is **not a chatbot**. It is **not an OCR / document extraction tool**. OCR and document parsing are merely internal platform capabilities.  
> **What The Customer Pays For:**  
> The customer pays for **completed operational work, prevented losses, recovered money, and reduced operational labor.**

---

### 2. Exact Customer

#### 2.1 Ideal Customer Profile (ICP)

**Primary ICP:** Small and mid-sized U.S. LTL freight brokers and third-party logistics providers (3PLs).

**Firmographic & Operational Characteristics:**
- **Team Size:** 5 to 100 operations, administrative, and accounting employees.
- **Shipment Profile:** Meaningful, daily LTL shipment volume (where complexity, accessorials, and re-weighs are common).
- **Communication Volume:** High daily email volume (inboxes overloaded with tracking updates, check calls, and invoices).
- **Carrier Network:** Active relationships with multiple LTL regional and national carriers (e.g., Old Dominion, Estes, SAIA, R+L Carriers, AAA Cooper, TForce, Pitt Ohio, etc.).
- **Software Stack:** Uses a standard operational database or commercial TMS (e.g., McLeod, Tai, Trimble, Turvo, Ascend, etc.).
- **Workflow Bottlenecks:** Manual carrier invoice auditing; frequent billing disputes and reclassification surcharges; recurring shipment visibility/documentation gaps.
- **Email Infrastructure:** Google Workspace (Gmail) or Microsoft 365.
- **Buying Velocity:** Owner-led or operational leadership with authority to purchase without an enterprise 6-month procurement bureaucracy.

#### Strong Buying Signals
A prospect is an immediate high-priority sales target if they articulate any of the following pain points:
- *"Our operations team lives in email."*
- *"We miss important carrier/customer messages."*
- *"Our team manually enters information into the TMS."*
- *"We have to audit every carrier invoice manually."*
- *"We know carriers overcharge, but auditing everything is difficult."*
- *"Our team constantly follows up with carriers."*
- *"We lose track of discrepancies."*
- *"Different documents often disagree."*
- *"We need to prove why an invoice is wrong."*

#### 2.2 Primary Buyer
- **Owner / Founder:** Motivated by margin expansion, automated scalability, and immediate cash recovery.
- **Director / VP of Operations:** Motivated by team throughput (loads per rep), customer satisfaction, and SLA compliance.
- **Brokerage Manager / Operations Manager:** Motivated by relieving coordinators from repetitive email grunt work and status chasing.
- **Finance / Accounts Payable (AP) Leader:** Motivated by eliminating billing leakage, auditing carrier invoices, and automating dispute collections.

#### 2.3 Daily Users
- **Freight Brokers:** Monitoring high-priority accounts, exceptions, and revenue protection.
- **Operations Coordinators:** Relieved of manual status entry, check-calls, tracking, and document indexing.
- **Billing / AP Specialists:** Reviewing verified audit discrepancies, approving dispute packages, and tracking credits.
- **Customer Service Representatives:** Answering customer inquiries with instantaneous, authoritative status.
- **Operations Managers:** Overseeing team exception queues, agent action logs, and performance metrics.

#### 2.4 Initial Geography
- **United States first.**
- Terminology (BOL, NMFC classes, accessorials, PRO numbers, MC/DOT numbers), regulatory frameworks (FMCSA), carrier verification directories, and commercial dispute practices are strictly U.S.-centric for the initial MVP.

---

### 3. The Actual Problem We Solve

#### 3.1 Core Thesis: The Information Synchronization Problem
LTL operations suffer from an endemic **information synchronization problem**. The exact same shipment exists across multiple disparate systems and communications, yet those representations are rarely guaranteed to agree.

```
┌────────────────────────────────────────────────────────┐
│               THE SYNCHRONIZATION CONFLICT             │
├─────────────────┬──────────────────────────────────────┤
│ Source Channel  │ Stated Shipment Weight               │
├─────────────────┼──────────────────────────────────────┤
│ Carrier Email   │ 4,200 lbs                            │
│ Quoted TMS      │ 4,200 lbs                            │
│ Shipper BOL     │ 4,200 lbs                            │
│ Signed POD      │ 4,200 lbs                            │
│ Carrier Invoice │ 5,800 lbs  <── [SILENT LEAKAGE]      │
└─────────────────┴──────────────────────────────────────┘
```

In traditional operations, a human must manually spot this conflict, gather the BOL and rate sheet, calculate the overcharge, draft a dispute, and email carrier AP. Most brokers lack the manpower to catch this, bleeding 2–8% of margin directly to carrier billing discrepancies.

Our platform converts this breakdown into an automated, machine-managed execution loop:

$$\mathbf{Observe} \longrightarrow \mathbf{Reconcile} \longrightarrow \mathbf{Decide} \longrightarrow \mathbf{Act} \longrightarrow \mathbf{Verify} \longrightarrow \mathbf{Record} \longrightarrow \mathbf{Continue}$$

#### 3.2 The Information-to-Action Gap
Legacy logistics software stops short at extraction, presentation, or passive alerting:
- *Extracting information* (reading text from a PDF).
- *Displaying information* (showing raw values in a dashboard).
- *Summarizing information* (creating an email digest).
- *Notifying a human* (pinging an already-overwhelmed coordinator).

Our platform bridges the **Information-to-Action Gap**:

$$\mathbf{Understand} \longrightarrow \mathbf{Decide} \longrightarrow \mathbf{Execute} \longrightarrow \mathbf{Verify\;Outcome}$$

---

### 4. Product Promise

#### Customer-Facing Promise
> *"Your AI freight operations employee. It continuously watches freight information, keeps shipment records correct, finds money and workflow problems, and takes action on your behalf."*

#### Long-Term Product Vision
> *Build the intelligence and execution layer through which freight information moves and freight operations get completed across global logistics.*

---

### 5. MVP — Six Capabilities

The MVP comprises **six specialized capabilities** operating on top of a single unified substrate: **one shared data model, one unified permission system, one immutable event log, and one structured tool/action layer**.

```
                           ┌─────────────────────────────────────────┐
                           │      UNIFIED OPERATIONAL SUBSTRATE      │
                           │  • Canonical Shipment Model             │
                           │  • Strict Provenance & Authority Engine │
                           │  • Role-Based & Policy Permission Layer │
                           │  • Immutable Audit Event Ledger         │
                           │  • Validated Tool Execution Framework   │
                           └────────────────────┬────────────────────┘
                                                │
         ┌───────────────┬───────────────┬──────┴────────┬───────────────┬───────────────┐
         ▼               ▼               ▼               ▼               ▼               ▼
   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
   │ Feature 1 │   │ Feature 2 │   │ Feature 3 │   │ Feature 4 │   │ Feature 5 │   │ Feature 6 │
   │   Inbox   │   │ Source of │   │  Billing  │   │  Dispute  │   │  Risk &   │   │ Exception │
   │  Action   │   │   Truth   │   │   Audit   │   │   Agent   │   │ Verify    │   │   Agent   │
   │   Agent   │   │  Engine   │   │   Agent   │   │           │   │   Agent   │   │           │
   └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘
```

These are **not six disconnected applications**; they are specialized agent workflows orchestrating the lifecycle of shipments.

---

### 6. Feature 1 — Inbox Action Agent

#### Objective
Eliminate manual email triaging and data entry by converting incoming freight communications directly into operational changes and verified downstream actions.

#### Operational Flow & Lifecycle
When an operational email arrives:
```
Incoming Email: "Load 5821 picked up at 3:20 PM. ETA tomorrow 11 AM. POD attached."
                                  │
                                  ▼
 1. Identify Load ID / PRO / BOL references (e.g., Load 5821)
 2. Validate sender address against trusted carrier profiles & active load assignments
 3. Extract status, timestamps, ETAs, and process document attachments
 4. Update the canonical shipment state in the Source-of-Truth Engine
 5. Push synchronized status updates to the customer's connected TMS (if integrated)
 6. Store, index, and link attached document (POD) to shipment record
 7. Append structured chronological events (Pickup completed, ETA scheduled)
 8. Trigger customer milestone notifications (if configured for this account)
 9. Mark email as processed with thread tags / internal labels
10. Write an immutable entry to the platform audit trail
```

#### Supported Tool Actions
- `update_shipment_status`: Update milestone (e.g., Dispatched, Picked Up, In Transit, Delivered).
- `update_timestamps`: Set actual pickup, delivery, or checkpoint timestamps.
- `update_eta`: Revise expected delivery window.
- `attach_store_documents`: Categorize and persist attachments (BOL, POD, Scale Tickets, Lumper Receipts).
- `create_task`: Queue a human action item when intervention is required.
- `send_email`: Send outbound confirmation, status dispatch, or request for information.
- `schedule_followup`: Register a timer/cron task for time-sensitive verifications.
- `create_exception`: Flag an operational deviation in the Exception Agent.
- `update_customer_status`: Push milestone update to external tracking portals or customer notifications.
- `request_missing_info`: Dispatch targeted inquiry for missing PRO#, piece count, or appointment time.

#### Human Escalation Triggers
The Inbox Action Agent must pause autonomous execution and route to a human coordinator if:
- Shipment identity cannot be resolved with high confidence.
- Ambiguous match: multiple active loads match the extracted reference.
- Sender is unrecognized, unverified, or flagged for domain mismatch.
- Action involves financial adjustments exceeding pre-set broker thresholds.
- Extracted information directly contradicts higher-authority documentation.
- The required action falls outside the agent’s delegated permissions.

---

### 7. Feature 2 — Source-of-Truth Engine

#### Objective
Establish and maintain a single, trusted, canonical, and auditable representation for every shipment across its entire lifecycle.

#### Canonical Shipment Schema (Minimum Fields)
- **Identifiers:** Internal Shipment/Load ID, Carrier PRO Number, Shipper BOL Number, Purchase Order (PO) Numbers, Customer Reference Numbers.
- **Entities:** Customer Account, Freight Carrier, Shipper (Origin Facility), Consignee (Destination Facility), Bill-To Party.
- **Geography & Routing:** Origin Address/Postal Code, Destination Address/Postal Code, Distance, Transit Miles, Origin Terminal, Destination Terminal.
- **Scheduling:** Pickup Appointment Window, Actual Pickup Timestamp, Delivery Appointment Window, Actual Delivery Timestamp, Updated Estimated Time of Arrival (ETA).
- **Physical Cargo Specifications:** Stated/Actual Weight, Dimensions ($L \times W \times H$), Pallet / Carton / Handling Unit Count, NMFC Freight Class, Commodity Description, Hazardous Material (HazMat) indicators, Temperature Control settings.
- **Financials & Rating:** Quoted Customer Rate, Quoted Carrier Cost (Linehaul), Fuel Surcharge (FSC) Schedule, Authorized Accessorial Line Items, Billed Invoices, Recovered Amounts.
- **Associated Documents:** Bill of Lading (BOL), Proof of Delivery (POD), Carrier Rate Confirmation, Carrier Invoices, Scale / Reweigh Tickets, Weight & Inspection (W&I) Certificates.
- **State & System Records:** Current Operational Status, Rate Contract Source, Connected TMS Sync State, Complete Event Timeline, Active Exceptions, Billing Audit Findings, Active Disputes, Carrier Risk Status.

#### Field Provenance Architecture
Every single material attribute in the canonical record maintains strict metadata:
- **`source_type`**: Email, PDF attachment, TMS webhook, Carrier EDI/API, User manual entry.
- **`source_document_id` / `source_message_id`**: Foreign key to the exact source payload.
- **`source_timestamp`**: Timestamp when the information was originally generated.
- **`extraction_timestamp`**: Timestamp when the platform extracted the value.
- **`confidence`**: Numerical confidence score ($0.00 - 1.00$).
- **`authority_level`**: Strict hierarchical rank of evidentiary weight.
- **`actor`**: System agent ID or human user ID that committed the write.

#### Authority Hierarchy & Conflict Policy
> **Core Architectural Rule:** The platform must **NEVER** silently overwrite a high-authority value with weaker evidence.

```
                  ┌──────────────────────────────────────────────┐
                  │          AUTHORITY LEVEL HIERARCHY           │
                  ├──────────────────────────────────────────────┤
                  │ 1. Signed Legal Documents (Signed BOL / POD) │ (Highest Authority)
                  │ 2. Contracted Rate Confirmation              │
                  │ 3. Direct TMS / ERP Synchronized Database    │
                  │ 4. Official Carrier Invoices                 │
                  │ 5. Operational Carrier / Customer Emails     │
                  │ 6. Raw Unverified Extraction                 │ (Lowest Authority)
                  └──────────────────────────────────────────────┘
```

**Conflict Handling Example:**
- $\text{TMS Weight} = 4,200\text{ lbs}$ (Authority: Level 3)
- $\text{Signed BOL Weight} = 4,200\text{ lbs}$ (Authority: Level 1)
- $\text{Carrier Invoice Weight} = 5,800\text{ lbs}$ (Authority: Level 4)

*Action:* The engine does not overwrite the shipment weight with $5,800\text{ lbs}$. Instead, it locks the canonical weight at $4,200\text{ lbs}$, flags a **Weight Conflict Event**, attaches the signed BOL as supporting evidence, and routes the invoice to the Billing Audit Agent for discrepancy investigation.

---

### 8. Feature 3 — Billing Audit Agent

#### Objective
Systematically detect revenue leakage on incoming carrier invoices, validate each charge against documented evidence, and structure dispute-ready findings.

#### Audit Inputs
1. Carrier Invoice (PDF / EDI 210 / Electronic Data)
2. Quoted Broker-Carrier Rate Confirmation
3. Master Contract / Carrier Tariff Rate Sheet
4. Shipper Bill of Lading (BOL)
5. Signed Proof of Delivery (POD)
6. Internal TMS Financial Records
7. Approved Accessorial Authorizations (pre-authorized detention, liftgate, etc.)
8. Certified Weight & Inspection (W&I) certificates or Scale Tickets (when supplied)
9. Weekly DOE / Carrier Fuel Surcharge Index

#### Initial Eleven (11) Audit Rules
1. **Duplicate Invoice:** Invoice number already recorded, or identical load charges billed under a distinct invoice number.
2. **Wrong Linehaul:** Billed linehaul rate differs from the agreed rate confirmation.
3. **Wrong Fuel Surcharge:** FSC percentage or per-mile calculation deviates from contract fuel table or weekly national index.
4. **Unsupported Accessorial:** Invoice includes unauthorized surcharges (e.g., residential delivery, liftgate, inside delivery, limited access, detention) lacking BOL documentation or pre-approval.
5. **Weight Discrepancy:** Billed weight exceeds the BOL weight without an attached certified scale ticket.
6. **Freight-Class Discrepancy:** Carrier billed a higher NMFC freight class without W&I inspection proof.
7. **Reclassification Discrepancy:** Discrepancy resulting from arbitrary density reclassification.
8. **Pallet / Dimension Discrepancy:** Piece or pallet count higher than certified BOL count, altering cubic capacity pricing.
9. **Quote-versus-Invoice Mismatch:** Total billed exceeds total contracted quote without documented variance approvals.
10. **Contract-versus-Invoice Mismatch:** Terms applied contradict base contract rules (e.g., payment discount terms, accessorial caps).
11. **Arithmetic / Total Discrepancy:** Line items do not sum to total billed amount.

#### Output Finding Structure
Every detected discrepancy generates a structured object containing:
- **`invoice_id`**: Carrier invoice identifier.
- **`shipment_id`**: Associated canonical shipment load number.
- **`disputed_line`**: Specific line item being challenged (e.g., "Linehaul", "Residential Fee").
- **`expected_value`**: Contracted or verified amount ($USD).
- **`billed_value`**: Invoiced amount ($USD).
- **`difference`**: Net financial variance ($USD).
- **`reason`**: Clear, factual operational rationale.
- **`confidence`**: Statistical assessment of rule evaluation.
- **`evidence`**: Direct links to source documents (e.g., BOL page 1, rate con section 3).
- **`rule_evaluated`**: Formal rule identification code (Rules 1–11).
- **`recommended_action`**: Suggested next step (e.g., "Submit dispute", "Request scale ticket", "Approve variance").

#### Discrepancy Classification Engine
The system strictly categorizes findings into three states with **zero tolerance for hallucinated or fabricated claims**:
- **Confirmed Discrepancy:** Mathematical and contractual certainty backed by authoritative documents.
- **Possible Discrepancy:** Inconsistency exists, but critical documentation (e.g., delivery receipt) is missing.
- **Insufficient Evidence:** Conflict detected, but neither source meets authority threshold; requires investigation.

---

### 9. Feature 4 — Dispute Agent

#### Objective
Transform verified audit findings into automated, professional, end-to-end recovery workflows that recover overbilled capital.

#### Complete Dispute Workflow
```
[Audit Finding Generated]
           │
           ▼
[Evidence Collection] ────▶ Pulls canonical BOL, Rate Con, POD, contracts
           │
           ▼
[Dispute Package Creation] ─▶ Compiles structured claim payload & formal dispute letter
           │
           ▼
[Recipient Resolution] ────▶ Resolves carrier billing/dispute contact from verified records
           │
           ▼
[Approval Gate] ──────────▶ Policy Check: Autonomous Send vs. Human Approval Required
           │
           ▼
[Transmission] ───────────▶ Dispatches via Email or Carrier Dispute Portal
           │
           ▼
[Response Tracking] ──────▶ Monitors inbox & logs carrier acknowledgment
           │
           ▼
[Automated Follow-Up] ────▶ Executes scheduled follow-ups against resolution SLA
           │
           ▼
[Capture Outcome] ────────▶ Ingests carrier response (Credit Memo / Corrected Invoice)
           │
           ▼
[Ledger Reconciliation] ──▶ Records verified recovery & updates recovery ledger
```

#### Recipient Resolution Guardrails
Disputes are dispatched strictly to designated carrier endpoints:
- Carrier Billing Department
- Carrier Accounts Receivable (AR) Department
- Designated Freight Invoice Dispute Email
- Direct Carrier Web Dispute Portal

> **Safety Rule:** Destination addresses **must** originate from trusted broker configuration, established carrier master profiles, or verified directory data. The agent must **never invent or guess an email address**.

#### Standard Dispute Package Contents
1. Invoice Number
2. Shipment / Load ID & Carrier PRO Number
3. Disputed Charge Line Item(s)
4. Billed Amount ($USD)
5. Expected Contract Amount ($USD)
6. Variance / Total Disputed Sum ($USD)
7. Concise, factual justification citing contractual terms
8. Supporting Rate Confirmation / Contract excerpts
9. Supporting Signed BOL / Delivery Receipt attachments
10. Broker Master Account Identifier
11. Unique Generated Dispute ID (for thread tracking)

#### Standard Dispute Communication Structure
```email
Subject: Invoice Dispute — INV-8892 / Load 5821 / Carrier PRO 9823411

Dear Carrier Billing Department,

We are formally disputing a total of $460.00 on invoice INV-8892 (Load 5821).

The current invoice includes:
- $250.00 Residential Delivery Fee
- $210.00 Freight Reclassification Surcharge

The canonical shipment records, rate confirmation, and signed Bill of Lading (attached) 
confirm that this delivery was completed at a commercial facility with an active loading dock, 
and cargo specifications match the agreed tender. Supporting documentation contains no 
authorization or proof for either charge.

Supporting documents (BOL, Rate Confirmation) are attached.
Please issue a corrected invoice for the adjusted amount of $1,420.00 or provide certified 
inspection certificates to dispute-records@broker.com citing Dispute ID DSP-5821-01.

Sincerely,
Automated Freight Dispute Operations
```

#### Carrier Response Classification Taxonomies
Incoming responses from carrier billing are classified into 8 states:
1. **`accepted`**: Carrier agrees to full disputed amount.
2. **`partially_accepted`**: Carrier waives part of the charge (e.g., drops residential fee, holds reclass).
3. **`rejected`**: Carrier denies dispute with stated reason.
4. **`request_for_evidence`**: Carrier asks for additional documentation (e.g., delivery stamp, invoice copy).
5. **`request_for_payment`**: Automated or dunning notice repeating payment demand.
6. **`corrected_invoice_issued`**: Carrier attaches revised invoice reflecting adjusted total.
7. **`credit_issued`**: Carrier issues credit memo reference.
8. **`unclear`**: Message body cannot be classified with high confidence; routed to human review.

#### Recovery Verification & Financial Protection
> **Revenue-Share Compliance:**  
> A recovery is recognized **ONLY** when verified by hard documentary evidence:
> - Carrier approval notification / official credit memo
> - Formal corrected carrier invoice
> - Direct AP / TMS accounting ledger adjustment
> - Explicit human broker confirmation
> 
> The platform must **NEVER** bill a success fee on an unverified or pending dispute claim.

---

### 10. Feature 5 — Risk / Verification Agent

#### Objective
Detect, flag, and prevent freight fraud, carrier identity theft, double-brokering, and unauthorized payment redirection prior to executing sensitive transactions.

#### Verification Checks
1. **Carrier Identity Consistency:** Matching DOT, MC, legal business name, and physical address against FMCSA registers.
2. **Operating Authority Status:** Active common/contract carrier authority status, pending revocations, or recent transfers.
3. **Insurance Coverage Verification:** Minimum cargo and auto liability thresholds, active policy cancellation alerts.
4. **Email / Domain Analysis:** Detecting spoofed domains (e.g., `carrier-logistics.co` vs `carrierlogistics.com`), freemail accounts (Gmail, Yahoo) claiming to represent established fleets.
5. **Contact Inconsistencies:** Phone numbers and contact names differing from established master profiles.
6. **Payment / Banking Anomalies:** Sudden updates to factoring company information, requests for QuickPay redirect to unverified bank routing numbers.
7. **Metadata Inconsistencies:** PDF metadata showing altered text layers or suspicious authoring tools on certificates of insurance (COI).
8. **Duplicate Identity Clustering:** Shared IP addresses, phone numbers, or bank accounts across multiple disparate MC entities.
9. **Behavioral Outliers:** Bidding patterns or dispatch behavior departing radically from historical lane norms.

#### Standard Risk Output Classifications
Every risk evaluation yields one of three definitive statuses accompanied by transparent evidentiary reasoning:
- **`LOW RISK`**: Passed all cross-checks against master records. Permitted to proceed autonomously.
- **`REVIEW REQUIRED`**: Minor anomaly detected (e.g., new dispatcher email from valid company domain). Coordinator review prompted.
- **`HIGH RISK / HOLD`**: Critical violation (e.g., banking redirect request, revoked authority, domain spoofing). Immediate operational lock placed on dispatch/payment.

#### Communication Standard
> **Defamation & Tone Policy:**  
> The agent must **never** output unsupported, subjective accusations such as *"This carrier is fraudulent."*  
> All risk outputs must be objective, factual, and strictly evidence-based:  
> *Compliant Example:*  
> *"Carrier verification failed because the contact identity (john@freight-dispatch-fast.com) and payment instructions differ from the trusted carrier profile on file for MC #109283."*

---

### 11. Feature 6 — Exception Agent

#### Objective
Continuously monitor real-time shipment milestones, detect operational delays or deviations, and proactively execute multi-step resolution workflows.

#### Initial Monitored Exceptions
1. **Missed Pickup Confirmation:** Scheduled pickup window elapsed without driver arrival or carrier confirmation.
2. **Late Pickup:** Carrier confirmed pickup after appointed cutoff time, jeopardizing destination transit.
3. **Missing POD:** Shipment marked delivered, but Proof of Delivery not received within SLA (e.g., 24 hours).
4. **Delayed ETA:** Carrier tracking, transit update, or check-call indicates delivery will breach customer appointment.
5. **Carrier Not Responding:** Carrier fails to acknowledge dispatch, tracking ping, or check-call within designated time window.
6. **Missing Documentation:** Absence of required paperwork (BOL, hazardous declaration, customs papers).
7. **Shipment-Status Conflict:** Contradictory milestones reported across systems (e.g., Carrier EDI says "Out for Delivery" while email says "Held at Terminal").
8. **Overdue Billing Dispute:** Carrier dispute unanswered beyond 15 business days.
9. **Overdue Follow-Up:** Scheduled operational check-in timer expired.

#### Operational Workflow: Continuous Resolution
The Exception Agent does not simply drop a passive alert into a channel and terminate. It **actively pursues resolution**:

```
[Exception Detected: Pickup Scheduled for 10:00 AM; No confirmation by 11:30 AM]
                                  │
                                  ▼
 1. Check TMS status and integrated GPS/ELD tracking pings (if available)
 2. Scan recent email threads for unindexed carrier status notes
 3. Validate that exception is real (confirm appointment wasn't rescheduled)
 4. Dispatch automated inquiry to carrier via designated channel (Email / API)
 5. Register timer against configured SLA (e.g., 30-minute response window)
 6. Ingest carrier reply: parse revised arrival time or tractor breakdown note
 7. Update canonical shipment ETA and operational milestone
 8. Trigger customer advisory notice (if notification policy active for delay > 1 hr)
 9. Register secondary verification timer for revised arrival window
10. Escalate to human coordinator ONLY if carrier fails to reply or revised time breaches critical dock cutoff
```

---

### 12. Shared Agent Architecture & Execution Model

#### Common Operational Lifecycle
All six capabilities run on a unified deterministic-agentic execution loop:

```
                  ┌───────────────────────────────┐
                  │             EVENT             │ (Email, Webhook, Timer, File)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │            INGEST             │ (Parse raw payload, normalize)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │   IDENTIFY SHIPMENT/ENTITY    │ (Resolve Load#, PRO#, MC#)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │       LOAD CURRENT TRUTH      │ (Retrieve canonical state)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │   EVALUATE RULES & EVIDENCE   │ (Deterministic policy checks)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │    LLM REASONING (IF REQ.)    │ (Extract unstructured semantics)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │      SELECT TOOL / ACTION     │ (Pick structured tool call)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │       PERMISSION CHECK        │ (Validate role, policy & safety)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │            EXECUTE            │ (Run validated code function)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │         VERIFY RESULT         │ (Confirm DB write / API response)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │       WRITE AUDIT EVENT       │ (Append immutable event log)
                  └───────────────┬───────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │    WAIT FOR EVENT / TIMER     │ (Hibernate until next trigger)
                  └───────────────────────────────┘
```

#### Foundational Architecture Rule: Guarded Execution
> **LLM Isolation Principle:**  
> **Do not allow the Large Language Model to directly mutate production database records arbitrarily.**  
> - The LLM's sole role is semantic understanding, classification, and selecting typed tools with structured schemas.  
> - All mutations (database inserts/updates, email dispatches, TMS API calls) are executed exclusively by deterministic, validated application code.  
> - Strict input validation and permission verification precede every tool execution.

---

### 13. Revenue Model & Commercial Structure

#### Commercial Pricing Structure
The platform implements a **hybrid SaaS + Value-Capture pricing structure**:
- **Base Subscription:** **`$499 / month`**  
  *Covers platform access, inbox ingestion, canonical tracking engine, and continuous exception monitoring.*
- **Performance Recovery Fee:** **`15% of Verified Recovered Billing Discrepancies`**  
  *Aligns platform revenue directly with hard-dollar financial ROI generated for the broker.*

#### Optional Future Expansion Pricing
- Per-shipment transaction processing fee ($0.25 - $1.00 per load)
- Per-invoice audited fee ($0.50 per invoice)
- Per-automated-action fee
- Premium TMS & ERP native integration connectors

#### Value Alignment Example
$$\begin{aligned}
\text{Base Monthly Subscription} &= \$499.00 \\
\text{Verified Discrepancies Recovered (Month)} &= \$10,000.00 \\
\text{Platform Recovery Fee (15\%)} &= \$1,500.00 \\
\hline
\mathbf{Total\;Platform\;Monthly\;Cost} &= \mathbf{\$1,999.00} \\
\mathbf{Net\;Cash\;Retained\;by\;Broker} &= \mathbf{\$8,501.00}
\end{aligned}$$

*The broker achieves an immediate 4.25x ROI on software spend strictly from recovered capital that would have otherwise leaked, while receiving full operational automation across email, visibility, and exceptions.*

#### Revenue-Share Controls: The Recovery Ledger
To prevent billing disputes and maintain audit integrity, all recovery fees must be derived from an immutable, cryptographically verifiable **Recovery Ledger**:

```
┌───────────────────────────────────────────────────────────────┐
│                    RECOVERY LEDGER SCHEMA                     │
├──────────────────────────┬────────────────────────────────────┤
│ Field                    │ Type / Description                 │
├──────────────────────────┼────────────────────────────────────┤
│ dispute_id               │ UUID (FK to Dispute Table)         │
│ invoice_id               │ UUID (FK to Carrier Invoice)       │
│ original_invoice_amount  │ Decimal ($)                        │
│ disputed_amount          │ Decimal ($)                        │
│ carrier_response         │ Enum (Accepted, Credit, etc.)      │
│ approved_recovery        │ Decimal ($)                        │
│ credit_memo_proof        │ File URI / Reference ID            │
│ customer_confirmation    │ Timestamp + User ID                │
│ verified_timestamp       │ Timestamp (UTC)                    │
│ revenue_share_percentage │ Decimal (Default: 0.15)            │
│ revenue_share_invoice_id │ UUID (FK to Platform Invoicing)    │
└──────────────────────────┴────────────────────────────────────┘
```
**Strict Enforcement:** Platform revenue share invoices can only be generated against entries where `approved_recovery` is substantiated by `credit_memo_proof` or `customer_confirmation`.

---

### 14. Success & Performance Metrics

The platform is evaluated against two complementary dimensions: **Business Outcome Metrics** and **Agent Technical Quality Metrics**.

#### 14.1 Primary Business & Operational Metrics
- **Dollars of Verified Recovery per Customer:** Absolute dollar amount recovered from carrier overcharges per billing cycle.
- **Percentage of Freight Emails Handled Autonomously:** Ratio of inbound emails triaged and executed without human intervention.
- **Loads Handled per Operations Employee:** Measurable expansion in coordinator capacity (e.g., increasing loads managed from 25/day to 60/day).
- **Email-to-Action Latency:** Time elapsed from email receipt to canonical state sync and downstream execution (target: < 60 seconds).
- **Percentage of Exceptions Resolved Autonomously:** Operational deviations brought to resolution without manual escalation.
- **Invoice Audit Precision:** Ratio of validated carrier discrepancies vs total audited invoices.
- **Human Intervention Rate:** Percentage of agent execution runs requiring human routing.
- **False-Positive Dispute Rate:** Disputes rejected by carriers due to lack of contractual merit (target: < 3%).
- **Net Customer Retention:** Month-over-month retention and net revenue expansion.

#### 14.2 Agent System Quality Metrics
- **Tool-Call Success Rate:** Percentage of tool calls executed without schema errors, runtime exceptions, or retries.
- **Action Success Rate:** Percentage of executed actions that achieve their intended downstream state in connected systems.
- **Evidence Coverage:** Percentage of canonical data updates backed by linked, auditable source documents.
- **Confidence Calibration:** Correlation between agent confidence scores and actual real-world ground truth.
- **Escalation Correctness:** Accuracy in identifying genuine edge cases requiring human judgment without over-escalating routine events.
- **Duplicate-Action Prevention:** 100% prevention of duplicate emails, duplicate disputes, or duplicate TMS writes.
- **Hallucination Rate in Audited Workflows:** Strict zero-tolerance threshold ($0.0\%$) for invented tracking milestones, fake dispute reasons, or fabricated numbers.

---

## PART II — SYSTEM ARCHITECTURE & IMPLEMENTATION SPECIFICATION

---

### 15. System Architecture Overview

```
 [Inbound Channels]            [Core Execution Engine]            [External Systems]
 ┌─────────────────┐           ┌──────────────────────┐           ┌──────────────────┐
 │  Gmail / M365   │──Webhook─▶│  Ingestion & Parsing │           │  Broker TMS      │
 │  Inboxes        │           │  (Email, OCR, Attach)│           │  (McLeod/Tai/etc)│
 └─────────────────┘           └──────────┬───────────┘           └────────▲─────────┘
                                          │                                │
 ┌─────────────────┐                      ▼                                │
 │  Carrier Portal │           ┌──────────────────────┐                    │
 │  Webhooks/Files │──────────▶│  Canonical Engine    │                    │
 └─────────────────┘           │  (State & Provenance)│                    │
                               └──────────┬───────────┘                    │
                                          │                                │
                               ┌──────────┴───────────┐                    │
                               ▼                      ▼                    │
                        ┌─────────────┐        ┌─────────────┐             │
                        │ Audit Rules │        │ LLM Planner │             │
                        │ Determinism │        │ & Reasoner  │             │
                        └──────┬──────┘        └──────┬──────┘             │
                               │                      │                    │
                               └──────────┬───────────┘                    │
                                          │                                │
                                          ▼                                │
                               ┌──────────────────────┐                    │
                               │ Tool Layer & Policy  │────────────────────┤
                               │ Permissions Check    │                    │
                               └──────────┬───────────┘                    │
                                          │                                │
                                          ▼                                │
                               ┌──────────────────────┐           ┌──────────────────┐
                               │ Action Execution &   │──────────▶│ Carrier Outbound │
                               │ Audit Log Ledger     │           │ (Disputes/Pings) │
                               └──────────────────────┘           └──────────────────┘
```

### 16. Implementation Roadmap & Development Milestones

#### Phase 1: Canonical Foundation & Ingestion Engine
- Deploy relational schema supporting canonical shipments, documents, and provenance tracking.
- Implement Gmail & Microsoft 365 OAuth ingestion webhooks.
- Build document classification and parser pipelines for BOL, POD, and Rate Confirmations.

#### Phase 2: Billing Audit Agent & Dispute Engine
- Implement 11 deterministic audit rules against carrier invoices.
- Build the dispute package builder, email generator, and recipient resolution engine.
- Implement the immutable Recovery Ledger with credit verification safeguards.

#### Phase 3: Inbox Action Agent & Exception Monitoring
- Implement email intent classification and load identification.
- Build automated action executor for status milestones, timestamps, and document indexing.
- Deploy background cron/timer scheduler for proactive SLA monitoring and exception follow-ups.

#### Phase 4: Customer UI & Design Partner Rollout
- Operations review dashboard for human-in-the-loop approvals.
- Real-time audit finding and dispute status views.
- Verified recovery reports and billing metrics dashboard.
