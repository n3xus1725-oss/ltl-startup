"""Phase 1.7 — Standard Typed Tools for Freight Execution.

Implements all 8 required platform tools:
1. find_shipment
2. get_shipment
3. update_shipment
4. attach_document
5. create_task
6. send_email
7. reply_to_thread
8. create_exception
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.connectors.gmail import GmailConnector
from packages.domain.resolver import shipment_to_dict
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.exceptions import ExceptionRepository
from packages.storage.repositories.messages import MessageRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.tasks import TaskRepository
from packages.tools.registry import BaseTool, ToolContext, ToolRegistry

# ---------------- 1. find_shipment ---------------- #

class FindShipmentInput(BaseModel):
    query: str = Field(..., min_length=1, description="Shipment number, Load ID, BOL, PRO, or invoice identifier")
    limit: int = Field(default=10, ge=1, le=50, description="Max results to return")


class FindShipmentOutput(BaseModel):
    count: int
    shipments: List[Dict[str, Any]]


class FindShipmentTool(BaseTool):
    name = "find_shipment"
    purpose = "Search shipments by identifier or candidate key within organization scope."
    input_schema = FindShipmentInput
    output_schema = FindShipmentOutput
    required_permission = "shipment:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: FindShipmentInput
    ) -> FindShipmentOutput:
        repo = ShipmentRepository(db)
        matches = repo.find_by_identifier(context.organization_id, input_data.query.strip())
        results = [shipment_to_dict(s) for s in matches[: input_data.limit]]
        return FindShipmentOutput(count=len(results), shipments=results)


# ---------------- 2. get_shipment ---------------- #

class GetShipmentInput(BaseModel):
    shipment_id: str = Field(..., description="Shipment UUID")


class GetShipmentOutput(BaseModel):
    found: bool
    shipment: Optional[Dict[str, Any]] = None


class GetShipmentTool(BaseTool):
    name = "get_shipment"
    purpose = "Retrieve full canonical shipment details by shipment ID."
    input_schema = GetShipmentInput
    output_schema = GetShipmentOutput
    required_permission = "shipment:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: GetShipmentInput
    ) -> GetShipmentOutput:
        repo = ShipmentRepository(db)
        shipment_uuid = uuid.UUID(input_data.shipment_id)
        shipment = repo.get_by_id(context.organization_id, shipment_uuid)
        if not shipment:
            return GetShipmentOutput(found=False, shipment=None)
        return GetShipmentOutput(found=True, shipment=shipment_to_dict(shipment))


# ---------------- 3. update_shipment ---------------- #

class UpdateShipmentInput(BaseModel):
    shipment_id: str = Field(..., description="Shipment UUID to update")
    status: Optional[str] = Field(default=None, description="New shipment operational status")
    carrier_name: Optional[str] = Field(default=None, description="Carrier name")
    carrier_reference: Optional[str] = Field(default=None, description="Carrier PRO number or tracking reference")
    bol_number: Optional[str] = Field(default=None, description="Bill of Lading number")
    origin_address: Optional[Dict[str, Any]] = Field(default=None, description="Origin address object")
    destination_address: Optional[Dict[str, Any]] = Field(default=None, description="Destination address object")
    pickup_date: Optional[datetime] = Field(default=None, description="Actual or confirmed pickup timestamp")
    eta: Optional[datetime] = Field(default=None, description="Updated estimated time of arrival")
    delivery_date: Optional[datetime] = Field(default=None, description="Actual delivery timestamp")
    weight_lbs: Optional[float] = Field(default=None, description="Weight in lbs")
    pallet_count: Optional[int] = Field(default=None, description="Pallet count")
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, description="Additional custom metadata")
    reason: Optional[str] = Field(default=None, description="Operational explanation for update")
    idempotency_key: Optional[str] = Field(default=None, description="Unique key for update idempotency")


class UpdateShipmentOutput(BaseModel):
    success: bool
    shipment_id: str
    updated_fields: List[str]
    prior_values: Dict[str, Any]
    current_values: Dict[str, Any]


class UpdateShipmentTool(BaseTool):
    name = "update_shipment"
    purpose = "Update canonical shipment fields (status, pickup time, ETA) and append immutable history event."
    input_schema = UpdateShipmentInput
    output_schema = UpdateShipmentOutput
    required_permission = "shipment:write"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: UpdateShipmentInput
    ) -> UpdateShipmentOutput:
        repo = ShipmentRepository(db)
        shipment_uuid = uuid.UUID(input_data.shipment_id)
        existing = repo.get_by_id(context.organization_id, shipment_uuid)
        if not existing:
            raise ValueError(f"Shipment {input_data.shipment_id} not found in organization")

        prior_values: Dict[str, Any] = {}
        updates: Dict[str, Any] = {}
        updated_fields: List[str] = []

        if input_data.status is not None:
            prior_values["status"] = existing.status
            updates["status"] = input_data.status
            updated_fields.append("status")

        if input_data.carrier_name is not None:
            prior_values["carrier_name"] = existing.carrier_name
            updates["carrier_name"] = input_data.carrier_name
            updated_fields.append("carrier_name")

        if input_data.carrier_reference is not None:
            prior_values["carrier_reference"] = existing.carrier_reference
            updates["carrier_reference"] = input_data.carrier_reference
            updated_fields.append("carrier_reference")

        if input_data.bol_number is not None:
            prior_values["bol_number"] = existing.bol_number
            updates["bol_number"] = input_data.bol_number
            updated_fields.append("bol_number")

        if input_data.origin_address is not None:
            prior_values["origin_address"] = existing.origin_address
            updates["origin_address"] = input_data.origin_address
            updated_fields.append("origin_address")

        if input_data.destination_address is not None:
            prior_values["destination_address"] = existing.destination_address
            updates["destination_address"] = input_data.destination_address
            updated_fields.append("destination_address")

        if input_data.pickup_date is not None:
            prior_values["pickup_date"] = existing.pickup_date.isoformat() if existing.pickup_date else None
            updates["pickup_date"] = input_data.pickup_date
            updated_fields.append("pickup_date")

        if input_data.eta is not None:
            prior_values["eta"] = existing.eta.isoformat() if existing.eta else None
            updates["eta"] = input_data.eta
            updated_fields.append("eta")

            # Always preserve prior ETA in metadata history
            if prior_values.get("eta"):
                current_meta = dict(existing.metadata_payload or {})
                eta_history = list(current_meta.get("eta_history", []))
                eta_val = input_data.eta.isoformat() if isinstance(input_data.eta, datetime) else str(input_data.eta)
                eta_history.append({
                    "prior_eta": prior_values["eta"],
                    "new_eta": eta_val,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": input_data.reason or "Agent ETA update",
                })
                current_meta["eta_history"] = eta_history
                updates["metadata_payload"] = current_meta
                if "metadata_payload" not in updated_fields:
                    updated_fields.append("metadata_payload")

        if input_data.delivery_date is not None:
            prior_values["delivery_date"] = existing.delivery_date.isoformat() if existing.delivery_date else None
            updates["delivery_date"] = input_data.delivery_date
            updated_fields.append("delivery_date")

        if input_data.weight_lbs is not None:
            prior_values["weight_lbs"] = existing.weight_lbs
            updates["weight_lbs"] = input_data.weight_lbs
            updated_fields.append("weight_lbs")

        if input_data.pallet_count is not None:
            prior_values["pallet_count"] = existing.pallet_count
            updates["pallet_count"] = input_data.pallet_count
            updated_fields.append("pallet_count")

        if input_data.metadata_payload is not None:
            current_meta = dict(updates.get("metadata_payload") or existing.metadata_payload or {})
            prior_values["metadata_payload"] = current_meta
            current_meta.update(input_data.metadata_payload)
            updates["metadata_payload"] = current_meta
            if "metadata_payload" not in updated_fields:
                updated_fields.append("metadata_payload")

        updated_shipment = repo.update_shipment(context.organization_id, shipment_uuid, **updates)

        # Record immutable shipment history event
        idemp = input_data.idempotency_key or context.idempotency_key
        repo.add_event(
            organization_id=context.organization_id,
            shipment_id=shipment_uuid,
            event_type="agent_update",
            source="inbox_action_agent",
            raw_payload=input_data.model_dump(mode="json"),
            normalized_payload={
                "updated_fields": updated_fields,
                "prior_values": prior_values,
                "reason": input_data.reason,
            },
            idempotency_key=f"event:{idemp}" if idemp else None,
        )

        current_values = {
            field: getattr(updated_shipment, field)
            for field in updated_fields
            if hasattr(updated_shipment, field)
        }
        # Serialize datetimes for output
        for k, v in current_values.items():
            if isinstance(v, datetime):
                current_values[k] = v.isoformat()

        return UpdateShipmentOutput(
            success=True,
            shipment_id=str(shipment_uuid),
            updated_fields=updated_fields,
            prior_values=prior_values,
            current_values=current_values,
        )


# ---------------- 4. attach_document ---------------- #

class AttachDocumentInput(BaseModel):
    shipment_id: str = Field(..., description="Shipment UUID to attach document to")
    document_id: str = Field(..., description="Document UUID to associate")
    document_type: Optional[str] = Field(default=None, description="Optional override for document type (e.g. POD, BOL)")
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key")


class AttachDocumentOutput(BaseModel):
    success: bool
    shipment_id: str
    document_id: str


class AttachDocumentTool(BaseTool):
    name = "attach_document"
    purpose = "Associate an ingested document with a canonical shipment."
    input_schema = AttachDocumentInput
    output_schema = AttachDocumentOutput
    required_permission = "shipment:write"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: AttachDocumentInput
    ) -> AttachDocumentOutput:
        doc_repo = DocumentRepository(db)
        shipment_repo = ShipmentRepository(db)

        shipment_uuid = uuid.UUID(input_data.shipment_id)
        doc_uuid = uuid.UUID(input_data.document_id)

        shipment = shipment_repo.get_by_id(context.organization_id, shipment_uuid)
        if not shipment:
            raise ValueError(f"Shipment {input_data.shipment_id} not found")

        doc = doc_repo.get_by_id(context.organization_id, doc_uuid)
        if not doc:
            raise ValueError(f"Document {input_data.document_id} not found")

        doc.shipment_id = shipment_uuid
        if input_data.document_type:
            doc.document_type = input_data.document_type
        db.commit()

        return AttachDocumentOutput(
            success=True,
            shipment_id=input_data.shipment_id,
            document_id=input_data.document_id,
        )


# ---------------- 5. create_task ---------------- #

class CreateTaskInput(BaseModel):
    title: str = Field(..., min_length=1, description="Task summary")
    task_type: str = Field(default="review", description="review, missing_information, escalation")
    description: Optional[str] = Field(default=None, description="Detailed instructions or context")
    shipment_id: Optional[str] = Field(default=None, description="Optional associated shipment UUID")
    priority: str = Field(default="medium", description="low, medium, high, urgent")
    due_date: Optional[datetime] = Field(default=None, description="Optional deadline for task")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional context payload")
    idempotency_key: Optional[str] = Field(default=None, description="Task idempotency key")


class CreateTaskOutput(BaseModel):
    success: bool
    task_id: str
    title: str
    status: str


class CreateTaskTool(BaseTool):
    name = "create_task"
    purpose = "Create an operational follow-up or human review task."
    input_schema = CreateTaskInput
    output_schema = CreateTaskOutput
    required_permission = "task:write"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: CreateTaskInput
    ) -> CreateTaskOutput:
        repo = TaskRepository(db)
        shipment_uuid = uuid.UUID(input_data.shipment_id) if input_data.shipment_id else None

        task = repo.create_task(
            organization_id=context.organization_id,
            title=input_data.title,
            task_type=input_data.task_type,
            description=input_data.description,
            shipment_id=shipment_uuid,
            priority=input_data.priority,
            due_date=input_data.due_date,
            details=input_data.details,
            idempotency_key=input_data.idempotency_key or context.idempotency_key,
        )

        return CreateTaskOutput(
            success=True,
            task_id=str(task.id),
            title=task.title,
            status=task.status,
        )


# ---------------- 6. send_email ---------------- #

class SendEmailInput(BaseModel):
    to_recipients: List[str] = Field(..., min_length=1, description="Recipient email addresses")
    subject: str = Field(..., min_length=1, description="Subject line")
    body: str = Field(..., min_length=1, description="Email content")
    cc_recipients: Optional[List[str]] = Field(default=None)
    idempotency_key: Optional[str] = Field(default=None, description="Send idempotency key")


class SendEmailOutput(BaseModel):
    status: str
    message_id: str
    thread_id: str


class SendEmailTool(BaseTool):
    name = "send_email"
    purpose = "Send an outbound email communication to customers or carriers."
    input_schema = SendEmailInput
    output_schema = SendEmailOutput
    required_permission = "email:send"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: SendEmailInput
    ) -> SendEmailOutput:
        connector = GmailConnector(context.organization_id, db, mock_mode=True)
        result = await connector.send_email(
            to_recipients=input_data.to_recipients,
            subject=input_data.subject,
            body_text=input_data.body,
            cc_recipients=input_data.cc_recipients,
        )
        return SendEmailOutput(
            status=result["status"],
            message_id=result["message_id"],
            thread_id=result["thread_id"],
        )


# ---------------- 7. reply_to_thread ---------------- #

class ReplyToThreadInput(BaseModel):
    thread_id: str = Field(..., description="Target email thread ID")
    body: str = Field(..., min_length=1, description="Reply content")
    to_recipients: Optional[List[str]] = Field(default=None, description="Optional override recipients")
    idempotency_key: Optional[str] = Field(default=None, description="Reply idempotency key")


class ReplyToThreadOutput(BaseModel):
    status: str
    message_id: str
    thread_id: str


class ReplyToThreadTool(BaseTool):
    name = "reply_to_thread"
    purpose = "Send a reply to an existing email conversation thread."
    input_schema = ReplyToThreadInput
    output_schema = ReplyToThreadOutput
    required_permission = "email:send"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: ReplyToThreadInput
    ) -> ReplyToThreadOutput:
        connector = GmailConnector(context.organization_id, db, mock_mode=True)
        msg_repo = MessageRepository(db)
        thread_messages = msg_repo.list_by_thread(context.organization_id, input_data.thread_id)
        if thread_messages:
            last_msg = thread_messages[-1]
            recipients = input_data.to_recipients or [last_msg.sender]
            subj = last_msg.subject or f"Thread {input_data.thread_id}"
            if not subj.lower().startswith("re:"):
                subj = f"Re: {subj}"
            in_reply_to = last_msg.external_message_id
        else:
            recipients = input_data.to_recipients or ["operations@carrier.com"]
            subj = f"Re: Thread {input_data.thread_id}"
            in_reply_to = None

        result = await connector.send_email(
            to_recipients=recipients,
            subject=subj,
            body_text=input_data.body,
            thread_id=input_data.thread_id,
            in_reply_to=in_reply_to,
        )
        return ReplyToThreadOutput(
            status=result["status"],
            message_id=result["message_id"],
            thread_id=result["thread_id"],
        )


# ---------------- 8. create_exception ---------------- #

class CreateExceptionInput(BaseModel):
    exception_type: str = Field(..., description="Type of exception (e.g. late_pickup, missing_pod, eta_delay, ambiguity)")
    severity: str = Field(default="medium", description="low, medium, high, critical")
    shipment_id: Optional[str] = Field(default=None, description="Optional shipment UUID")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Structured exception details")
    idempotency_key: Optional[str] = Field(default=None, description="Exception idempotency key")


class CreateExceptionOutput(BaseModel):
    success: bool
    exception_id: str
    exception_type: str
    severity: str
    status: str


class CreateExceptionTool(BaseTool):
    name = "create_exception"
    purpose = "Log an operational freight exception requiring resolution or human review."
    input_schema = CreateExceptionInput
    output_schema = CreateExceptionOutput
    required_permission = "exception:write"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: CreateExceptionInput
    ) -> CreateExceptionOutput:
        repo = ExceptionRepository(db)
        shipment_uuid = uuid.UUID(input_data.shipment_id) if input_data.shipment_id else None

        exc = repo.create_exception(
            organization_id=context.organization_id,
            exception_type=input_data.exception_type,
            severity=input_data.severity,
            shipment_id=shipment_uuid,
            details=input_data.details,
            idempotency_key=input_data.idempotency_key or context.idempotency_key,
        )

        return CreateExceptionOutput(
            success=True,
            exception_id=str(exc.id),
            exception_type=exc.exception_type,
            severity=exc.severity,
            status=exc.status,
        )


# ---------------- 9. search_context ---------------- #

class SearchContextInput(BaseModel):
    query: str = Field(..., min_length=1, description="Search query across canonical data, documents, and historical messages")
    shipment_id: Optional[str] = Field(None, description="Optional shipment ID constraint")
    top_k: int = Field(default=5, ge=1, le=20, description="Max results to return")


class SearchContextOutput(BaseModel):
    count: int
    results: List[Dict[str, Any]]


class SearchContextTool(BaseTool):
    name = "search_context"
    purpose = "Retrieve canonical shipment facts, document evidence chunks, and historical messages."
    input_schema = SearchContextInput
    output_schema = SearchContextOutput
    required_permission = "shipment:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: SearchContextInput
    ) -> SearchContextOutput:
        from packages.retrieval.engine import ShipmentRetrievalEngine
        engine = ShipmentRetrievalEngine(db)
        items = engine.search_all(
            organization_id=context.organization_id,
            query=input_data.query,
            limit=input_data.top_k,
        )
        return SearchContextOutput(
            count=len(items),
            results=[i.model_dump() for i in items],
        )



# ---------------- X. create_shipment ---------------- #

class CreateShipmentInput(BaseModel):
    shipment_number: str = Field(..., description="Internal or assigned shipment identifier")
    load_id: Optional[str] = Field(default=None, description="Broker load ID or tender ID")
    carrier_name: Optional[str] = Field(default=None, description="Name of carrier")
    carrier_reference: Optional[str] = Field(default=None, description="Carrier PRO number or reference")
    bol_number: Optional[str] = Field(default=None, description="Bill of Lading number")
    origin_address: Optional[Dict[str, Any]] = Field(default=None, description="Origin address {city, state}")
    destination_address: Optional[Dict[str, Any]] = Field(default=None, description="Destination address {city, state}")
    status: str = Field(default="created", description="Initial status")
    pickup_date: Optional[datetime] = Field(default=None, description="Scheduled or confirmed pickup timestamp")
    delivery_date: Optional[datetime] = Field(default=None, description="Scheduled or confirmed delivery timestamp")
    eta: Optional[datetime] = Field(default=None, description="Estimated time of arrival")
    weight_lbs: Optional[float] = Field(default=None, description="Total weight in pounds")
    pallet_count: Optional[int] = Field(default=None, description="Pallet count")
    total_charges: Optional[float] = Field(default=None, description="Total expected charges")
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, description="Additional custom metadata")


class CreateShipmentOutput(BaseModel):
    success: bool
    shipment_id: str
    message: str


class CreateShipmentTool(BaseTool):
    name = "create_shipment"
    purpose = "Create a new shipment record when an incoming tender or new load is received."
    input_schema = CreateShipmentInput
    output_schema = CreateShipmentOutput
    required_permission = "shipment:write"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: CreateShipmentInput
    ) -> CreateShipmentOutput:
        repo = ShipmentRepository(db)
        
        # Check if already exists by load_id to avoid dups if they tried to create it blindly
        if input_data.load_id:
            existing = repo.find_by_identifier(context.organization_id, input_data.load_id)
            if existing:
                return CreateShipmentOutput(
                    success=False,
                    shipment_id=str(existing[0].id),
                    message=f"Shipment already exists with Load ID {input_data.load_id}"
                )

        shipment = repo.create(
            organization_id=context.organization_id,
            shipment_number=input_data.shipment_number,
            load_id=input_data.load_id,
            carrier_name=input_data.carrier_name,
            carrier_reference=input_data.carrier_reference,
            bol_number=input_data.bol_number,
            origin_address=input_data.origin_address,
            destination_address=input_data.destination_address,
            status=input_data.status,
            pickup_date=input_data.pickup_date,
            delivery_date=input_data.delivery_date,
            eta=input_data.eta,
            weight_lbs=input_data.weight_lbs,
            pallet_count=input_data.pallet_count,
            total_charges=input_data.total_charges,
            metadata_payload=input_data.metadata_payload,
        )
        return CreateShipmentOutput(
            success=True,
            shipment_id=str(shipment.id),
            message="Shipment created successfully"
        )


def create_standard_tool_registry() -> ToolRegistry:
    """Instantiate and register all standard freight execution tools."""
    registry = ToolRegistry()
    registry.register(FindShipmentTool())
    registry.register(GetShipmentTool())
    registry.register(UpdateShipmentTool())
    registry.register(AttachDocumentTool())
    registry.register(CreateTaskTool())
    registry.register(SendEmailTool())
    registry.register(ReplyToThreadTool())
    registry.register(CreateExceptionTool())
    registry.register(SearchContextTool())
    registry.register(CreateShipmentTool())
    return registry

