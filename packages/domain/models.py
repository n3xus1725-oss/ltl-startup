"""Core SQLAlchemy domain models for the AI Freight Platform."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import relationship

from packages.storage.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Organization(Base):
    """Multi-tenant organization boundary."""
    __tablename__ = "organizations"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    auto_dispute_threshold_usd = Column(Numeric(12, 2), nullable=False, default=250.00)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    # Relationships
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    shipments = relationship("Shipment", back_populates="organization", cascade="all, delete-orphan")
    inbox_connections = relationship("InboxConnection", back_populates="organization", cascade="all, delete-orphan")
    conflicts = relationship("ShipmentConflict", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    """User associated with an organization."""
    __tablename__ = "users"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), nullable=False, default="operator")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="users")

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )


class InboxConnection(Base):
    """Mailbox / email provider connection credentials and status."""
    __tablename__ = "inbox_connections"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    email_address = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False, default="gmail")
    status = Column(String(50), nullable=False, default="active")
    credentials_encrypted = Column(JSON, nullable=True)
    sync_cursor = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="inbox_connections")

    __table_args__ = (
        UniqueConstraint("organization_id", "email_address", name="uq_inbox_org_email"),
    )


class Shipment(Base):
    """Canonical freight shipment record."""
    __tablename__ = "shipments"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_number = Column(String(100), nullable=False, index=True)
    carrier_name = Column(String(255), nullable=True)
    carrier_reference = Column(String(100), nullable=True, index=True)
    bol_number = Column(String(100), nullable=True, index=True)
    load_id = Column(String(100), nullable=True, index=True)
    external_invoice_id = Column(String(100), nullable=True, index=True)
    origin_address = Column(JSON, nullable=True)
    destination_address = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default="created")
    pickup_date = Column(DateTime(timezone=True), nullable=True)
    delivery_date = Column(DateTime(timezone=True), nullable=True)
    eta = Column(DateTime(timezone=True), nullable=True)
    weight_lbs = Column(Float, nullable=True)
    pallet_count = Column(Integer, nullable=True)
    total_charges = Column(Numeric(12, 2), nullable=True)
    metadata_payload = Column(JSON, nullable=True)
    canonical_data = Column(JSON, nullable=True)
    provenance_ledger = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="shipments")
    events = relationship("ShipmentEvent", back_populates="shipment", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="shipment")
    messages = relationship("Message", back_populates="shipment")
    exceptions = relationship("ExceptionRecord", back_populates="shipment")
    tasks = relationship("TaskRecord", back_populates="shipment")
    conflicts = relationship("ShipmentConflict", back_populates="shipment", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("organization_id", "shipment_number", name="uq_shipments_org_shipment_number"),
        Index("ix_shipments_org_carrier_ref", "organization_id", "carrier_reference"),
        Index("ix_shipments_org_invoice_id", "organization_id", "external_invoice_id"),
        Index("ix_shipments_org_bol", "organization_id", "bol_number"),
    )


class ShipmentEvent(Base):
    """Immutable log of state changes and events for a shipment."""
    __tablename__ = "shipment_events"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    event_timestamp = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    source = Column(String(100), nullable=False)
    raw_payload = Column(JSON, nullable=True)
    normalized_payload = Column(JSON, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    shipment = relationship("Shipment", back_populates="events")

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_shipment_events_org_idemp"),
    )


class Document(Base):
    """Uploaded or ingested freight documents (BOL, POD, Invoice, etc.)."""
    __tablename__ = "documents"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    document_type = Column(String(50), nullable=False, default="UNKNOWN")
    file_name = Column(String(255), nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)
    storage_path = Column(String(1000), nullable=False)
    checksum_sha256 = Column(String(64), nullable=True, index=True)
    extracted_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    shipment = relationship("Shipment", back_populates="documents")

    __table_args__ = (
        UniqueConstraint("organization_id", "checksum_sha256", name="uq_documents_org_checksum"),
    )


class Message(Base):
    """Inbound/outbound email or API message."""
    __tablename__ = "messages"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    inbox_connection_id = Column(Uuid(as_uuid=True), ForeignKey("inbox_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    external_message_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=False, index=True)
    direction = Column(String(20), nullable=False, default="inbound")
    sender = Column(String(255), nullable=False)
    recipients = Column(JSON, nullable=False)
    subject = Column(String(1000), nullable=True)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    is_processed = Column(Boolean, default=False, nullable=False)
    raw_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    shipment = relationship("Shipment", back_populates="messages")

    __table_args__ = (
        UniqueConstraint("organization_id", "external_message_id", name="uq_messages_org_ext_msg_id"),
        Index("ix_messages_org_thread", "organization_id", "thread_id"),
    )


class AgentRun(Base):
    """Traceable execution record for an agent workflow run."""
    __tablename__ = "agent_runs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_name = Column(String(100), nullable=False)
    trigger_event_id = Column(String(255), nullable=True, index=True)
    entity_id = Column(String(255), nullable=True, index=True)
    model_used = Column(String(100), nullable=True)
    model_version = Column(String(50), nullable=True)
    prompt_version = Column(String(50), nullable=True)
    tools_available = Column(JSON, nullable=True)
    decision = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=True)
    approval_state = Column(String(50), nullable=False, default="none")
    status = Column(String(50), nullable=False, default="running")
    final_result = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)
    cost_estimate = Column(Numeric(10, 6), default=0.0, nullable=False)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    tool_calls = relationship("ToolCall", back_populates="agent_run", cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="agent_run", cascade="all, delete-orphan")


class ToolCall(Base):
    """Record of individual typed tool invocations by agents."""
    __tablename__ = "tool_calls"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_run_id = Column(Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = Column(String(100), nullable=False)
    arguments = Column(JSON, nullable=False)
    result = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    latency_ms = Column(Integer, nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    agent_run = relationship("AgentRun", back_populates="tool_calls")

    __table_args__ = (
        UniqueConstraint("organization_id", "agent_run_id", "idempotency_key", name="uq_tool_calls_org_run_idemp"),
    )


class Approval(Base):
    """Human-in-the-loop approval requests and outcomes."""
    __tablename__ = "approvals"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_run_id = Column(Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    action_type = Column(String(100), nullable=False)
    action_payload = Column(JSON, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    requested_by = Column(String(100), nullable=False, default="agent")
    reviewed_by = Column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_comment = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    agent_run = relationship("AgentRun", back_populates="approvals")


class ExceptionRecord(Base):
    """Freight and operational exceptions requiring resolution."""
    __tablename__ = "exceptions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=True, index=True)
    exception_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False, default="medium")
    status = Column(String(50), nullable=False, default="open")
    details = Column(JSON, nullable=True)
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    shipment = relationship("Shipment", back_populates="exceptions")

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_exceptions_org_idemp"),
    )


class TaskRecord(Base):
    """Operational or review task requiring human or scheduled follow-up."""
    __tablename__ = "tasks"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    task_type = Column(String(100), nullable=False, default="review")
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(20), nullable=False, default="medium")
    status = Column(String(50), nullable=False, default="pending")
    assigned_to = Column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    details = Column(JSON, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    shipment = relationship("Shipment", back_populates="tasks")

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_tasks_org_idemp"),
    )


class AuditLog(Base):
    """Immutable system and agent audit ledger."""
    __tablename__ = "audit_log"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(String(255), nullable=False, index=True)
    action = Column(String(100), nullable=False)
    actor_type = Column(String(50), nullable=False)
    actor_id = Column(String(255), nullable=False)
    target_entity_type = Column(String(100), nullable=True)
    target_entity_id = Column(String(255), nullable=True)
    payload_before = Column(JSON, nullable=True)
    payload_after = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_audit_log_org_idemp"),
    )


class ShipmentConflict(Base):
    """Detected truth discrepancy between different sources."""
    __tablename__ = "shipment_conflicts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name = Column(String(100), nullable=False, index=True)
    conflict_type = Column(String(50), nullable=False)  # value_mismatch, missing_evidence, stale_value, incompatible_status, conflicting_party_identity
    source_a = Column(JSON, nullable=False)  # {source, source_id, value, authority, timestamp}
    source_b = Column(JSON, nullable=False)  # {source, source_id, value, authority, timestamp}
    severity = Column(String(20), nullable=False, default="medium")  # low, medium, high, critical
    explanation = Column(Text, nullable=False)
    recommended_workflow = Column(String(100), nullable=False)  # rate_dispute_agent, reweigh_verification, operator_review, carrier_inquiry
    status = Column(String(50), nullable=False, default="open")  # open, resolved, dismissed
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(255), nullable=True)
    resolved_value = Column(JSON, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="conflicts")
    shipment = relationship("Shipment", back_populates="conflicts")

    __table_args__ = (
        UniqueConstraint("organization_id", "shipment_id", "field_name", "idempotency_key", name="uq_conflicts_org_shp_field_idemp"),
    )


class RateContract(Base):
    """Carrier rate contract, lane rates, fuel formula, and accessorial schedules."""
    __tablename__ = "rate_contracts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    carrier_name = Column(String(255), nullable=False, index=True)
    customer_name = Column(String(255), nullable=True, index=True)
    contract_number = Column(String(100), nullable=False, index=True)
    lane_origin_state = Column(String(50), nullable=True)
    lane_origin_zip_prefix = Column(String(10), nullable=True)
    lane_dest_state = Column(String(50), nullable=True)
    lane_dest_zip_prefix = Column(String(10), nullable=True)
    effective_start_date = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    effective_end_date = Column(DateTime(timezone=True), nullable=True)
    rate_type = Column(String(50), nullable=False, default="flat")  # flat, per_mile, per_cwt
    base_rate = Column(Float, nullable=False, default=0.0)
    minimum_charge = Column(Float, nullable=False, default=0.0)
    fuel_schedule = Column(JSON, nullable=True)  # {type: "percent"|"table", base_rate_percent: 15.0, formula: ...}
    accessorial_schedule = Column(JSON, nullable=True)  # {detention: {rate_per_hour: 75, free_hours: 2}, ...}
    class_rate_rules = Column(JSON, nullable=True)  # {min_density: 6.0, reclass_requires_cert: true}
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization")

    __table_args__ = (
        UniqueConstraint("organization_id", "carrier_name", "contract_number", name="uq_contracts_org_carrier_num"),
    )


class CarrierInvoice(Base):
    """Carrier freight invoice document and financial record."""
    __tablename__ = "carrier_invoices"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id = Column(Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    carrier_name = Column(String(255), nullable=False, index=True)
    invoice_number = Column(String(100), nullable=False, index=True)
    invoice_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    currency = Column(String(10), nullable=False, default="USD")
    total_billed_amount = Column(Float, nullable=False, default=0.0)
    linehaul_amount = Column(Float, nullable=False, default=0.0)
    fuel_amount = Column(Float, nullable=False, default=0.0)
    accessorial_amount = Column(Float, nullable=False, default=0.0)
    weight_lbs = Column(Float, nullable=True)
    freight_class = Column(String(50), nullable=True)
    pallet_count = Column(Integer, nullable=True)
    remit_to_address = Column(JSON, nullable=True)
    line_items = Column(JSON, nullable=True)  # [{code, description, amount, quantity, unit_rate}]
    status = Column(String(50), nullable=False, default="received")  # received, audited, disputed, approved, duplicate
    audit_status = Column(String(50), nullable=False, default="pending")  # pending, clean, discrepant
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization")
    shipment = relationship("Shipment")
    document = relationship("Document")
    audit_findings = relationship("AuditFindingRecord", back_populates="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("organization_id", "carrier_name", "invoice_number", name="uq_invoices_org_carrier_num"),
    )


class AuditFindingRecord(Base):
    """Deterministic or verified billing audit discrepancy finding."""
    __tablename__ = "audit_findings"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id = Column(Uuid(as_uuid=True), ForeignKey("carrier_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_id = Column(String(100), nullable=False, index=True)
    rule_name = Column(String(255), nullable=False)
    severity = Column(String(50), nullable=False, default="medium")  # critical, high, medium, low
    expected_value = Column(JSON, nullable=True)
    billed_value = Column(JSON, nullable=True)
    discrepancy_amount = Column(Float, nullable=False, default=0.0)
    reason = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    evidence = Column(JSON, nullable=True)
    recommended_action = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False, default="open")  # open, disputed, waived, resolved
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    invoice = relationship("CarrierInvoice", back_populates="audit_findings")


class CarrierContact(Base):
    """Verified carrier billing contact registry — the ONLY source for dispute recipient emails."""
    __tablename__ = "carrier_contacts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    carrier_name = Column(String(255), nullable=False, index=True)
    billing_email = Column(String(255), nullable=False)  # THE ONLY ALLOWED DISPUTE RECIPIENT
    contact_name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "carrier_name", "billing_email", name="uq_carrier_contacts_org_carrier_email"),
    )


class CarrierDispute(Base):
    """Carrier billing dispute record. disputed_amount MUST come from AuditFindingRecord.discrepancy_amount — never from LLM."""
    __tablename__ = "carrier_disputes"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    dispute_number = Column(String(100), nullable=False, index=True)  # e.g. DSP-5821-01
    invoice_id = Column(Uuid(as_uuid=True), ForeignKey("carrier_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    shipment_id = Column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    audit_finding_ids = Column(JSON, nullable=False, default=list)  # list of AuditFindingRecord UUIDs
    carrier_name = Column(String(255), nullable=False)
    # Financial fields — ALL must be copied from AuditFindingRecord, NEVER set by LLM
    disputed_amount = Column(Numeric(12, 2), nullable=False)  # sum of discrepancy_amounts
    expected_amount = Column(Numeric(12, 2), nullable=True)
    billed_amount = Column(Numeric(12, 2), nullable=True)
    # Recipient — MUST come from CarrierContact DB record, NEVER guessed
    recipient_email = Column(String(255), nullable=True)  # NULL until verified contact resolved
    recipient_contact_name = Column(String(255), nullable=True)
    # Dispute letter — LLM-drafted subject/body; financial data injected by deterministic code
    dispute_letter_subject = Column(String(500), nullable=True)
    dispute_letter_text = Column(Text, nullable=True)
    # Status flow: draft -> pending_approval -> sent -> acknowledged -> resolved | rejected | cancelled
    status = Column(String(50), nullable=False, default="draft", index=True)
    # Approval: auto_approved | requires_human_approval | human_approved | human_rejected
    approval_status = Column(String(50), nullable=False, default="pending")
    auto_dispute_threshold_usd = Column(Numeric(12, 2), nullable=False, default=250.00)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    carrier_response_text = Column(Text, nullable=True)
    # accepted | partially_accepted | rejected | request_for_evidence | corrected_invoice_issued | credit_issued | unclear
    carrier_response_classification = Column(String(100), nullable=True)
    agent_run_id = Column(Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    invoice = relationship("CarrierInvoice")
    shipment = relationship("Shipment")
    recovery = relationship("DisputeRecovery", back_populates="dispute", uselist=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_disputes_org_idemp"),
        UniqueConstraint("organization_id", "dispute_number", name="uq_disputes_org_number"),
    )


class DisputeRecovery(Base):
    """Immutable verified recovery ledger. approved_recovery CANNOT be set without documented proof.
    Platform 15% success fee can ONLY be billed against verified entries."""
    __tablename__ = "dispute_recoveries"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    recovery_number = Column(String(100), nullable=False, index=True)  # e.g. REC-5821-01
    dispute_id = Column(Uuid(as_uuid=True), ForeignKey("carrier_disputes.id", ondelete="RESTRICT"), nullable=False, index=True)
    invoice_id = Column(Uuid(as_uuid=True), ForeignKey("carrier_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    original_invoice_amount = Column(Numeric(12, 2), nullable=True)
    disputed_amount = Column(Numeric(12, 2), nullable=False)  # copied from CarrierDispute
    # Carrier response — required before any recovery can be recorded
    carrier_response_type = Column(String(100), nullable=True)  # accepted | partially_accepted | credit_issued | corrected_invoice_issued | rejected
    # Recovery — NULL until verified proof is on file. GUARD: RecoveryRepository.verify_and_mark_recovered() checks proof first
    approved_recovery = Column(Numeric(12, 2), nullable=True)  # NULL until proven
    # Proof — at least one must be present before approved_recovery can be set
    proof_type = Column(String(100), nullable=True)  # credit_memo | corrected_invoice | ap_confirmation | human_broker_approval
    proof_document_id = Column(Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    proof_reference = Column(String(255), nullable=True)  # credit memo number, etc.
    customer_confirmation_user_id = Column(String(255), nullable=True)
    customer_confirmation_at = Column(DateTime(timezone=True), nullable=True)
    # Revenue share — computed only when verified
    revenue_share_percentage = Column(Numeric(6, 4), nullable=False, default=0.1500)
    revenue_share_amount = Column(Numeric(12, 2), nullable=True)  # NULL until verified
    platform_invoice_id = Column(String(255), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)  # NULL until verified
    verified_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    dispute = relationship("CarrierDispute", back_populates="recovery")

    __table_args__ = (
        UniqueConstraint("organization_id", "dispute_id", name="uq_recoveries_org_dispute"),
        UniqueConstraint("organization_id", "recovery_number", name="uq_recoveries_org_number"),
    )

