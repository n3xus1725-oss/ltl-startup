import sys

def modify_test_ui():
    with open(r'c:\Users\krish\Downloads\logistics\apps\api\api\v1\test_ui.py', 'a', encoding='utf-8') as f:
        f.write('''

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 DISPUTE AGENT TESTING ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

class RunDisputeAgentRequest(BaseModel):
    scenario: str = Field(
        default="auto_approved",
        description="auto_approved | requires_human | no_contact",
    )
    force_amount: Optional[float] = Field(
        default=None,
        description="Override discrepancy amount for testing threshold logic",
    )


@router.post("/dispute-agent/run", summary="[Phase 4] Run dispute agent simulation")
def run_dispute_agent_simulation(
    body: RunDisputeAgentRequest,
    db: Session = Depends(get_db),
):
    """Simulate a full dispute agent run for UI testing.
    Creates ephemeral test data, runs the agent, and returns results.
    Scenario options:
    - 'auto_approved': verified contact + amount under threshold -> auto_approved + sent
    - 'requires_human': verified contact + amount over threshold -> requires_human_approval
    - 'no_contact': no contact -> escalate to human
    """
    from apps.agent.dispute.service import run_dispute_agent
    from packages.llm.gateway import LLMGateway
    from unittest.mock import AsyncMock, MagicMock

    # Get or create test org
    org = db.query(Organization).filter(Organization.slug == DEFAULT_TEST_ORG_SLUG).first()
    if not org:
        org = Organization(name=DEFAULT_TEST_ORG_NAME, slug=DEFAULT_TEST_ORG_SLUG)
        db.add(org)
        db.commit()
        db.refresh(org)

    # Determine scenario amounts
    scenarios = {
        "auto_approved": {"amount": 180.00, "carrier": "Old Dominion", "has_contact": True},
        "requires_human": {"amount": 500.00, "carrier": "SAIA Freight", "has_contact": True},
        "no_contact": {"amount": 150.00, "carrier": "Unknown Carrier Co", "has_contact": False},
    }
    scenario_config = scenarios.get(body.scenario, scenarios["auto_approved"])
    amount = body.force_amount if body.force_amount is not None else scenario_config["amount"]
    carrier = scenario_config["carrier"]

    # Create test shipment
    import random
    suffix = random.randint(1000, 9999)
    shipment = Shipment(
        organization_id=org.id,
        shipment_number=f"SHP-UI-{suffix}",
        carrier_name=carrier,
        status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    # Create test invoice
    invoice = CarrierInvoice(
        organization_id=org.id,
        shipment_id=shipment.id,
        carrier_name=carrier,
        invoice_number=f"INV-UI-{suffix}",
        total_billed_amount=amount + 1000.0,
        linehaul_amount=1000.0,
        fuel_amount=amount,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # Create audit finding with the discrepancy amount
    finding = AuditFindingRecord(
        organization_id=org.id,
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        rule_id="RULE_02_LINEHAUL_MISMATCH",
        rule_name="Wrong Linehaul Rate",
        severity="high",
        discrepancy_amount=amount,  # This is the AUTHORITATIVE disputed amount
        reason=f"Billed ${amount + 1000:.2f}, contracted rate is $1000.00",
        confidence=0.92,
        recommended_action="dispute",
        evidence={"source_documents": ["SIGNED_BOL", "RATE_CONFIRMATION"]},
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    # Create carrier contact if scenario requires it
    if scenario_config["has_contact"]:
        existing_contact = CarrierContactRepository(db).get_verified_contact(org.id, carrier)
        if not existing_contact:
            contact = CarrierContact(
                organization_id=org.id,
                carrier_name=carrier,
                billing_email=f"billing@{carrier.lower().replace(' ', '')}.com",
                contact_name=f"{carrier} Billing Dept",
                is_verified=True,
            )
            db.add(contact)
            db.commit()

    # Mock LLM for deterministic UI testing
    mock_response = MagicMock()
    mock_response.content = (
        f"SUBJECT: Invoice Dispute — INV-UI-{suffix} / {carrier}\\n\\n"
        f"LETTER:\\nDear {carrier} Billing Department,\\n\\n"
        f"We are formally disputing a total of ${amount:.2f} on invoice INV-UI-{suffix}.\\n\\n"
        f"Our records show the billed amount exceeds the contracted rate by ${amount:.2f}.\\n"
        f"Please issue a corrected invoice or credit memo.\\n\\nBest regards,\\nFreight Operations"
    )
    mock_response.cost_estimate = 0.0012
    mock_response.prompt_tokens = 350
    mock_response.completion_tokens = 120

    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini (simulated)"
    mock_llm.complete = AsyncMock(return_value=mock_response)

    try:
        result = run_dispute_agent(
            organization_id=str(org.id),
            invoice_id=str(invoice.id),
            finding_ids=[str(finding.id)],
            db=db,
            llm=mock_llm,
            triggered_by="ui-simulation",
        )
        return {
            "scenario": body.scenario,
            "run_id": result.run_id,
            "dispute_id": result.dispute_id,
            "dispute_number": result.dispute_number,
            "status": result.status,
            "approval_status": result.approval_status,
            "recipient_email": result.recipient_email,
            "disputed_amount": result.disputed_amount,
            "dispute_letter_subject": result.dispute_letter_subject,
            "dispute_letter_preview": result.dispute_letter_preview,
            "needs_human": result.needs_human,
            "error": result.error,
            "trajectory": result.trajectory,
            "cost_estimate": result.cost_estimate,
            "model_used": mock_llm.default_model,
            "financial_integrity_note": f"disputed_amount={result.disputed_amount} sourced from AuditFindingRecord.discrepancy_amount={amount} — NOT from LLM",
        }
    except Exception as e:
        return {
            "scenario": body.scenario,
            "error": str(e),
            "status": "failed",
            "trajectory": [],
        }
''')

def modify_dashboard():
    dashboard_path = r'c:\Users\krish\Downloads\logistics\apps\api\templates\dashboard.html'
    with open(dashboard_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Change 1: Update the page title badge in the header
    content = content.replace(
        '<span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800 font-medium">Phase 2: Source-of-Truth Active</span>',
        '<span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800 font-medium">Phase 4: Dispute Agent Active</span>'
    )

    # Change 2: Add Tab 8 button
    tab7_btn = '<button onclick="switchMainTab(\'audit\')" id="main-tab-audit" class="py-3 tab-inactive flex items-center space-x-1.5 text-emerald-400 font-semibold">\n      <span>💰</span><span>7. Billing Audit Engine (Phase 3)</span>\n    </button>'
    tab8_btn = '<button onclick="switchMainTab(\'dispute\')" id="main-tab-dispute" class="py-3 tab-inactive flex items-center space-x-1.5 text-blue-400 font-semibold">\n      <span>⚖️</span><span>8. Dispute Agent (Phase 4)</span>\n    </button>'
    content = content.replace(tab7_btn, tab7_btn + '\n    ' + tab8_btn)

    # Change 3: Add Tab 8 content panel
    tab8_content = '''
    <!-- TAB 8: DISPUTE AGENT (PHASE 4) -->
    <div id="section-dispute" class="hidden flex flex-col space-y-6">
      <div class="p-6 space-y-6">
        <!-- Header -->
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white">⚖️ Dispute Agent — Phase 4</h2>
            <p class="text-sm text-slate-400 mt-1">Convert verified audit findings into automated dispute workflows with approval gateway and recovery ledger</p>
          </div>
          <div class="flex items-center space-x-2 text-xs">
            <span class="px-2 py-1 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">✓ Financial amounts from DB only</span>
            <span class="px-2 py-1 rounded bg-amber-950 text-amber-400 border border-amber-800">✓ Recipients from verified contacts only</span>
          </div>
        </div>

        <!-- Safety Rules Banner -->
        <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4">
          <h3 class="text-sm font-semibold text-slate-300 mb-3">🔐 Phase 4 Safety Guarantees (Non-Negotiable)</h3>
          <div class="grid grid-cols-2 gap-3 text-xs">
            <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
              <div class="text-emerald-400 font-semibold mb-1">💰 Financial Integrity</div>
              <div class="text-slate-400">LLM never calculates dollar amounts. All $disputed, $expected, $billed flow from AuditFindingRecord.discrepancy_amount.</div>
            </div>
            <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
              <div class="text-amber-400 font-semibold mb-1">📧 Zero Recipient Guessing</div>
              <div class="text-slate-400">Agent never invents email addresses. Only CarrierContact DB records are used. No contact = human escalation.</div>
            </div>
            <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
              <div class="text-blue-400 font-semibold mb-1">🚦 Two-Tier Approval</div>
              <div class="text-slate-400">AUTO_SEND: confidence ≥ 0.85 + signed docs + amount ≤ $250. Everything else → REQUIRES_HUMAN_APPROVAL.</div>
            </div>
            <div class="bg-slate-900 rounded-lg p-3 border border-slate-700">
              <div class="text-purple-400 font-semibold mb-1">📒 Immutable Recovery Ledger</div>
              <div class="text-slate-400">approved_recovery is NULL until credit memo / corrected invoice / human approval proof is on file.</div>
            </div>
          </div>
        </div>

        <!-- Scenario Runner -->
        <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-5">
          <h3 class="text-base font-semibold text-white mb-4">🧪 Run Dispute Agent Simulation</h3>
          <div class="grid grid-cols-3 gap-3 mb-4">
            <button onclick="runDisputeScenario('auto_approved')" class="flex flex-col items-start p-4 rounded-xl border border-emerald-700/50 bg-emerald-950/30 hover:bg-emerald-950/60 text-left transition">
              <span class="text-emerald-400 text-lg mb-1">✅</span>
              <span class="text-sm font-semibold text-emerald-300">Auto-Approved</span>
              <span class="text-xs text-slate-400 mt-1">Verified contact + $180 (under threshold) + high confidence → auto_approved + sent</span>
            </button>
            <button onclick="runDisputeScenario('requires_human')" class="flex flex-col items-start p-4 rounded-xl border border-amber-700/50 bg-amber-950/30 hover:bg-amber-950/60 text-left transition">
              <span class="text-amber-400 text-lg mb-1">👤</span>
              <span class="text-sm font-semibold text-amber-300">Requires Human</span>
              <span class="text-xs text-slate-400 mt-1">Verified contact + $500 (above threshold) → requires_human_approval, queued for review</span>
            </button>
            <button onclick="runDisputeScenario('no_contact')" class="flex flex-col items-start p-4 rounded-xl border border-red-700/50 bg-red-950/30 hover:bg-red-950/60 text-left transition">
              <span class="text-red-400 text-lg mb-1">🚫</span>
              <span class="text-sm font-semibold text-red-300">No Contact</span>
              <span class="text-xs text-slate-400 mt-1">No CarrierContact on file → agent escalates to human, NEVER guesses email</span>
            </button>
          </div>
          <div id="dispute-agent-loading" class="hidden text-center py-6">
            <div class="inline-flex items-center space-x-2 text-emerald-400">
              <svg class="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
              <span class="text-sm">Running Dispute Agent...</span>
            </div>
          </div>
        </div>

        <!-- Dispute Agent Result -->
        <div id="dispute-agent-result" class="hidden space-y-4">

          <!-- Status Banner -->
          <div id="dispute-status-banner" class="rounded-xl p-4 border"></div>

          <!-- Two-column: Outcome + Financial -->
          <div class="grid grid-cols-2 gap-4">
            <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4">
              <h4 class="text-sm font-semibold text-slate-300 mb-3">📋 Dispute Outcome</h4>
              <div class="space-y-2 text-sm" id="dispute-outcome-fields"></div>
            </div>
            <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4">
              <h4 class="text-sm font-semibold text-slate-300 mb-3">💰 Financial Facts (Deterministic)</h4>
              <div class="space-y-2 text-sm" id="dispute-financial-fields"></div>
            </div>
          </div>

          <!-- Trajectory Stepper -->
          <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4">
            <h4 class="text-sm font-semibold text-slate-300 mb-3">🔄 Agent Trajectory</h4>
            <div id="dispute-trajectory" class="space-y-2"></div>
          </div>

          <!-- Dispute Letter Preview -->
          <div id="dispute-letter-section" class="hidden bg-slate-800/60 border border-slate-700 rounded-xl p-4">
            <h4 class="text-sm font-semibold text-slate-300 mb-3">📝 Generated Dispute Letter (LLM Draft)</h4>
            <div class="text-xs text-amber-400 mb-2">⚠️ Financial amounts in this letter were injected by application code — not generated by LLM</div>
            <pre id="dispute-letter-text" class="text-xs text-slate-300 bg-slate-900 rounded-lg p-3 whitespace-pre-wrap"></pre>
          </div>
        </div>

        <!-- Carrier Contact Registry -->
        <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-5">
          <div class="flex items-center justify-between mb-4">
            <div>
              <h3 class="text-base font-semibold text-white">📇 Carrier Contact Registry</h3>
              <p class="text-xs text-slate-400 mt-0.5">Verified billing contacts — the ONLY permitted dispute recipients</p>
            </div>
            <button onclick="loadCarrierContacts()" class="text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition">🔄 Refresh</button>
          </div>
          <div id="carrier-contacts-list" class="text-sm text-slate-400">Click Refresh to load contacts...</div>
        </div>

        <!-- Recovery Ledger -->
        <div class="bg-slate-800/60 border border-slate-700 rounded-xl p-5">
          <div class="flex items-center justify-between mb-4">
            <div>
              <h3 class="text-base font-semibold text-white">📒 Recovery Ledger</h3>
              <p class="text-xs text-slate-400 mt-0.5">Immutable verified recovery entries — platform 15% fee only billed on verified entries</p>
            </div>
            <button onclick="loadRecoveryLedger()" class="text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition">🔄 Refresh</button>
          </div>
          <div id="recovery-ledger-summary" class="hidden grid grid-cols-3 gap-3 mb-4"></div>
          <div id="recovery-ledger-list" class="text-sm text-slate-400">Click Refresh to load recovery ledger...</div>
        </div>

      </div>
    </div>
    <!-- END TAB 8 -->
'''
    end_of_tabs = '<!-- Bottom Explorer: Supabase Live Tables -->'
    content = content.replace(end_of_tabs, tab8_content + '\n  ' + end_of_tabs)

    # Change 4: Add Tab 8 JavaScript before the closing </script> tag
    tab8_js = '''
// ══════════════════════════════════════════════════════════
// PHASE 4 — DISPUTE AGENT FUNCTIONS
// ══════════════════════════════════════════════════════════

async function runDisputeScenario(scenario) {
  document.getElementById('dispute-agent-loading').classList.remove('hidden');
  document.getElementById('dispute-agent-result').classList.add('hidden');
  try {
    const resp = await fetch('/api/v1/test-ui/dispute-agent/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario })
    });
    const data = await resp.json();
    renderDisputeAgentResult(data, scenario);
  } catch (err) {
    console.error('Dispute agent error:', err);
    document.getElementById('dispute-agent-result').classList.remove('hidden');
    document.getElementById('dispute-status-banner').innerHTML =
      `<div class="text-red-400">❌ Error: ${err.message}</div>`;
  } finally {
    document.getElementById('dispute-agent-loading').classList.add('hidden');
  }
}

function renderDisputeAgentResult(data, scenario) {
  const resultEl = document.getElementById('dispute-agent-result');
  resultEl.classList.remove('hidden');

  // Status banner
  const banner = document.getElementById('dispute-status-banner');
  const isAutoApproved = data.approval_status === 'auto_approved';
  const needsHuman = data.needs_human;
  const haserror = data.status === 'failed';
  if (haserror) {
    banner.className = 'rounded-xl p-4 border border-red-700 bg-red-950/40';
    banner.innerHTML = `<div class="flex items-center space-x-3"><span class="text-2xl">❌</span><div><div class="font-semibold text-red-300">Agent Failed</div><div class="text-xs text-slate-400">${data.error || 'Unknown error'}</div></div></div>`;
  } else if (isAutoApproved) {
    banner.className = 'rounded-xl p-4 border border-emerald-700 bg-emerald-950/40';
    banner.innerHTML = `<div class="flex items-center space-x-3"><span class="text-2xl">✅</span><div><div class="font-semibold text-emerald-300">AUTO-APPROVED & SENT</div><div class="text-xs text-slate-400">Dispute ${data.dispute_number} sent to ${data.recipient_email} — no human intervention required</div></div></div>`;
  } else if (needsHuman) {
    banner.className = 'rounded-xl p-4 border border-amber-700 bg-amber-950/40';
    banner.innerHTML = `<div class="flex items-center space-x-3"><span class="text-2xl">👤</span><div><div class="font-semibold text-amber-300">REQUIRES HUMAN APPROVAL</div><div class="text-xs text-slate-400">Dispute ${data.dispute_number || 'pending'} queued for human review — ${data.recipient_email ? 'recipient resolved but above threshold' : 'no verified carrier contact found'}</div></div></div>`;
  }

  // Outcome fields
  const outcomeEl = document.getElementById('dispute-outcome-fields');
  const outcomeRows = [
    ['Dispute Number', data.dispute_number || 'N/A'],
    ['Status', data.status],
    ['Approval Status', data.approval_status],
    ['Recipient Email', data.recipient_email || '⚠️ Not resolved (escalated)'],
    ['Needs Human', data.needs_human ? '✅ Yes' : '❌ No'],
    ['Run ID', (data.run_id || '').slice(0, 8) + '...'],
  ];
  outcomeEl.innerHTML = outcomeRows.map(([k, v]) =>
    `<div class="flex justify-between border-b border-slate-700/50 pb-1">
      <span class="text-slate-400">${k}</span>
      <span class="text-slate-200 font-mono text-xs">${v}</span>
    </div>`
  ).join('');

  // Financial fields
  const finEl = document.getElementById('dispute-financial-fields');
  const finRows = [
    ['Disputed Amount', data.disputed_amount ? `$${data.disputed_amount.toFixed(2)}` : 'N/A'],
    ['Source', 'AuditFindingRecord.discrepancy_amount'],
    ['LLM Calculated?', '❌ Never — deterministic code only'],
    ['Note', data.financial_integrity_note || 'N/A'],
  ];
  finEl.innerHTML = finRows.map(([k, v]) =>
    `<div class="flex justify-between border-b border-slate-700/50 pb-1">
      <span class="text-slate-400">${k}</span>
      <span class="text-slate-200 font-mono text-xs max-w-48 text-right">${v}</span>
    </div>`
  ).join('');

  // Trajectory stepper
  const trajEl = document.getElementById('dispute-trajectory');
  const steps = data.trajectory || [];
  if (steps.length === 0) {
    trajEl.innerHTML = '<div class="text-slate-500 text-xs">No trajectory data</div>';
  } else {
    trajEl.innerHTML = steps.map((step, i) => {
      const statusColor = step.status === 'ok' ? 'emerald' :
                         step.status === 'escalate' || step.status === 'escalated' ? 'amber' :
                         step.status === 'error' ? 'red' : 'blue';
      const statusIcon = step.status === 'ok' ? '✓' :
                        step.status === 'escalate' || step.status === 'escalated' ? '👤' :
                        step.status === 'error' ? '✗' : '→';
      return `<div class="flex items-start space-x-3">
        <div class="flex-shrink-0 w-6 h-6 rounded-full bg-${statusColor}-950 border border-${statusColor}-700 flex items-center justify-center text-${statusColor}-400 text-xs font-bold">${statusIcon}</div>
        <div class="flex-1 pb-2 border-b border-slate-700/30">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-slate-200">${step.node}</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-${statusColor}-950 text-${statusColor}-400 border border-${statusColor}-800">${step.status}</span>
          </div>
          <div class="text-xs text-slate-400 mt-0.5">${step.summary}</div>
        </div>
      </div>`;
    }).join('');
  }

  // Dispute letter
  if (data.dispute_letter_preview) {
    document.getElementById('dispute-letter-section').classList.remove('hidden');
    document.getElementById('dispute-letter-text').textContent = data.dispute_letter_preview;
  } else {
    document.getElementById('dispute-letter-section').classList.add('hidden');
  }
}

async function loadCarrierContacts() {
  const el = document.getElementById('carrier-contacts-list');
  el.innerHTML = '<div class="text-slate-500 text-xs">Loading...</div>';
  try {
    // Get org ID from state
    const stateResp = await fetch('/api/v1/test-ui/state');
    const state = await stateResp.json();
    const orgId = state.organization_id;
    if (!orgId) { el.innerHTML = '<div class="text-slate-500 text-xs">No organization found. Seed data first.</div>'; return; }
    const resp = await fetch(`/api/v1/carrier-contacts?organization_id=${orgId}`);
    const data = await resp.json();
    if (!data.contacts || data.contacts.length === 0) {
      el.innerHTML = '<div class="text-slate-500 text-xs">No carrier contacts registered. The dispute agent will always escalate to human until contacts are added.</div>';
      return;
    }
    el.innerHTML = `<div class="overflow-x-auto"><table class="w-full text-xs">
      <thead><tr class="text-slate-500 border-b border-slate-700">
        <th class="text-left py-2 pr-4">Carrier</th>
        <th class="text-left py-2 pr-4">Billing Email</th>
        <th class="text-left py-2 pr-4">Contact</th>
        <th class="text-left py-2">Verified</th>
      </tr></thead>
      <tbody>${data.contacts.map(c => `
        <tr class="border-b border-slate-800 hover:bg-slate-800/30">
          <td class="py-2 pr-4 text-slate-200 font-medium">${c.carrier_name}</td>
          <td class="py-2 pr-4 text-emerald-400 font-mono">${c.billing_email}</td>
          <td class="py-2 pr-4 text-slate-400">${c.contact_name || '—'}</td>
          <td class="py-2">${c.is_verified ? '<span class="text-emerald-400">✓ Verified</span>' : '<span class="text-red-400">✗ Unverified</span>'}</td>
        </tr>`).join('')}
      </tbody></table></div>`;
  } catch (err) {
    el.innerHTML = `<div class="text-red-400 text-xs">Error: ${err.message}</div>`;
  }
}

async function loadRecoveryLedger() {
  const listEl = document.getElementById('recovery-ledger-list');
  const summaryEl = document.getElementById('recovery-ledger-summary');
  listEl.innerHTML = '<div class="text-slate-500 text-xs">Loading...</div>';
  try {
    const stateResp = await fetch('/api/v1/test-ui/state');
    const state = await stateResp.json();
    const orgId = state.organization_id;
    if (!orgId) { listEl.innerHTML = '<div class="text-slate-500 text-xs">No organization found. Seed data first.</div>'; return; }
    const resp = await fetch(`/api/v1/disputes/recovery-ledger/all?organization_id=${orgId}`);
    const data = await resp.json();

    // Summary cards
    summaryEl.classList.remove('hidden');
    summaryEl.innerHTML = `
      <div class="bg-slate-900 rounded-lg p-3 border border-slate-700 text-center">
        <div class="text-2xl font-bold text-emerald-400">$${(data.total_verified_recovery_usd || 0).toFixed(2)}</div>
        <div class="text-xs text-slate-400 mt-1">Verified Recovery</div>
      </div>
      <div class="bg-slate-900 rounded-lg p-3 border border-slate-700 text-center">
        <div class="text-2xl font-bold text-purple-400">$${(data.total_platform_revenue_share_usd || 0).toFixed(2)}</div>
        <div class="text-xs text-slate-400 mt-1">Platform Fee (15%)</div>
      </div>
      <div class="bg-slate-900 rounded-lg p-3 border border-slate-700 text-center">
        <div class="text-2xl font-bold text-blue-400">${data.count || 0}</div>
        <div class="text-xs text-slate-400 mt-1">Total Entries</div>
      </div>`;

    if (!data.entries || data.entries.length === 0) {
      listEl.innerHTML = '<div class="text-slate-500 text-xs">No recovery entries yet. Run a dispute scenario first.</div>';
      return;
    }
    listEl.innerHTML = `<div class="overflow-x-auto"><table class="w-full text-xs">
      <thead><tr class="text-slate-500 border-b border-slate-700">
        <th class="text-left py-2 pr-3">Recovery #</th>
        <th class="text-left py-2 pr-3">Disputed</th>
        <th class="text-left py-2 pr-3">Approved Recovery</th>
        <th class="text-left py-2 pr-3">Proof Type</th>
        <th class="text-left py-2 pr-3">Revenue Share (15%)</th>
        <th class="text-left py-2">Verified</th>
      </tr></thead>
      <tbody>${data.entries.map(r => `
        <tr class="border-b border-slate-800 hover:bg-slate-800/30">
          <td class="py-2 pr-3 text-slate-200 font-mono">${r.recovery_number}</td>
          <td class="py-2 pr-3 text-slate-300">$${r.disputed_amount?.toFixed(2) || '—'}</td>
          <td class="py-2 pr-3">${r.approved_recovery !== null ? `<span class="text-emerald-400 font-semibold">$${r.approved_recovery.toFixed(2)}</span>` : '<span class="text-slate-500">NULL (unverified)</span>'}</td>
          <td class="py-2 pr-3 text-slate-400">${r.proof_type || '—'}</td>
          <td class="py-2 pr-3">${r.revenue_share_amount !== null ? `<span class="text-purple-400">$${r.revenue_share_amount.toFixed(2)}</span>` : '<span class="text-slate-500">—</span>'}</td>
          <td class="py-2">${r.verified_at ? '<span class="text-emerald-400">✓ Verified</span>' : '<span class="text-amber-400">⏳ Pending</span>'}</td>
        </tr>`).join('')}
      </tbody></table></div>`;
  } catch (err) {
    listEl.innerHTML = `<div class="text-red-400 text-xs">Error: ${err.message}</div>`;
  }
}
'''
    content = content.replace('</script>', tab8_js + '\n</script>')

    # Change 5: Update the switchTab JavaScript function
    content = content.replace(
        "['inbox', 'canonical', 'conflicts', 'documents', 'retrieval', 'simulation', 'audit'].forEach(t => {",
        "['inbox', 'canonical', 'conflicts', 'documents', 'retrieval', 'simulation', 'audit', 'dispute'].forEach(t => {"
    )
    content = content.replace("if (tab === 'audit') loadAuditScenarios();", "if (tab === 'audit') loadAuditScenarios();\n      if (tab === 'dispute') { loadCarrierContacts(); loadRecoveryLedger(); }")

    with open(dashboard_path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify_test_ui()
    modify_dashboard()
