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
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    # Relationships
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    shipments = relationship("Shipment", back_populates="organization", cascade="all, delete-orphan")
    inbox_connections = relationship("InboxConnection", back_populates="organization", cascade="all, delete-orphan")


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
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="shipments")
    events = relationship("ShipmentEvent", back_populates="shipment", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="shipment")
    messages = relationship("Message", back_populates="shipment")
    exceptions = relationship("ExceptionRecord", back_populates="shipment")
    tasks = relationship("TaskRecord", back_populates="shipment")

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
