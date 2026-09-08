"""Connectors API: Google Gmail OAuth 2.0 and automated inbox synchronization."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.agent.inbox.service import InboxAgentService
from apps.agent.inbox.validators import InboxAgentInput
from packages.connectors.gmail import GmailConnector
from packages.domain.config import get_settings
from packages.domain.dispatcher import EventDispatcher
from packages.domain.events import InboundEventPayload
from packages.domain.logging import logger
from packages.domain.models import Organization, Shipment
from packages.storage.credentials_crypto import encrypt_credentials
from packages.storage.db import get_db
from packages.storage.repositories.inbox_connections import InboxConnectionRepository

router = APIRouter(prefix="/connectors", tags=["Connectors"])


class SyncGmailRequest(BaseModel):
    organization_id: Optional[str] = Field(default=None, description="Optional target organization UUID")
    query: Optional[str] = Field(default="in:inbox", description="Gmail search query")
    limit: int = Field(default=10, ge=1, le=50, description="Max messages to sync")


def _get_org(db: Session, org_id_str: Optional[str] = None) -> Organization:
    """Retrieve requested organization or default demo organization."""
    if org_id_str and org_id_str not in ("null", "undefined"):
        try:
            org = db.query(Organization).filter(Organization.id == uuid.UUID(org_id_str)).first()
            if org:
                return org
        except ValueError:
            pass
    org = db.query(Organization).filter(Organization.slug == "nexus-freight-demo").first()
    if not org:
        org = Organization(name="Nexus Freight Logistics", slug="nexus-freight-demo", is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


@router.get("/gmail/auth-url")
def get_gmail_auth_url(
    organization_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Generate Google OAuth 2.0 consent URL for live Gmail connection."""
    settings = get_settings()
    client_id = settings.GMAIL_CLIENT_ID or "google-client-id-pending"
    org = _get_org(db, organization_id)
    r_uri = redirect_uri or f"https://ltl-startup-dusky.vercel.app{settings.API_V1_PREFIX}/connectors/gmail/oauth/callback"

    auth_url = GmailConnector.create_oauth_url(
        client_id=client_id,
        redirect_uri=r_uri,
        state=str(org.id),
    )
    return {
        "auth_url": auth_url,
        "client_id_configured": bool(settings.GMAIL_CLIENT_ID),
        "redirect_uri": r_uri,
        "organization_id": str(org.id),
    }


@router.get("/gmail/oauth/callback")
async def gmail_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Exchange authorization code for tokens and save connection to Supabase."""
    if error:
        logger.error(f"Google OAuth error: {error}")
        return RedirectResponse(url=f"/dashboard?oauth_error={error}")

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code")

    settings = get_settings()

    org_id = None
    if state:
        try:
            candidate_id = uuid.UUID(state)
            org = db.query(Organization).filter(Organization.id == candidate_id).first()
            if org:
                org_id = candidate_id
            else:
                logger.error(f"OAuth callback: state UUID {state} does not match any organization. Rejecting.")
                return RedirectResponse(url="/dashboard?oauth_error=invalid_state")
        except ValueError:
            logger.error(f"OAuth callback: state parameter is not a valid UUID: {state!r}")
            return RedirectResponse(url="/dashboard?oauth_error=invalid_state")
    else:
        org_id = _get_org(db).id

    redirect_uri = f"https://ltl-startup-dusky.vercel.app{settings.API_V1_PREFIX}/connectors/gmail/oauth/callback"

    try:
        token_data = await GmailConnector.exchange_code_for_tokens(
            code=code,
            client_id=settings.GMAIL_CLIENT_ID or "",
            client_secret=settings.GMAIL_CLIENT_SECRET or "",
            redirect_uri=redirect_uri,
        )
        email_addr = token_data.get("email_address", "connected@gmail.com")

        conn_repo = InboxConnectionRepository(db)
        conn_repo.upsert_connection(
            organization_id=org_id,
            email_address=email_addr,
            provider="gmail",
            status="active",
            credentials=encrypt_credentials(token_data),
        )
        logger.info(f"Successfully connected Gmail inbox for {email_addr}")
        response = RedirectResponse(url=f"/dashboard?gmail=connected&email={email_addr}")
        response.set_cookie(key="auth_token", value=str(org_id), httponly=True, max_age=86400)
        return response
    except Exception as exc:
        logger.error(f"Failed to complete Gmail OAuth callback: {exc}")
        return {"status": "error", "message": str(exc)}


@router.get("/gmail/status")
def get_gmail_status(
    organization_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Retrieve active Gmail inbox connection and synchronization status."""
    org = _get_org(db, organization_id)
    conn_repo = InboxConnectionRepository(db)
    connections = conn_repo.list_by_org(org.id, provider="gmail")

    return {
        "connected": len(connections) > 0,
        "connections": [
            {
                "id": str(c.id),
                "email_address": c.email_address,
                "provider": c.provider,
                "status": c.status,
                "last_synced_at": c.last_synced_at.isoformat() if c.last_synced_at else None,
            }
            for c in connections
        ],
    }


@router.post("/gmail/sync")
async def sync_gmail_inbox(
    request: SyncGmailRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Trigger manual or headless sync of Gmail inbox matching a query."""
    org = _get_org(db, request.organization_id)
    conn_repo = InboxConnectionRepository(db)

    active_conns = [
        c for c in conn_repo.list_by_org(org.id, provider="gmail")
        if c.status == "active"
    ]
    # Prioritize the most recently connected / updated mailbox (e.g. krishnawararkar15@gmail.com)
    active_conns.sort(
        key=lambda c: c.updated_at or c.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    if not active_conns and not request.mock_mode:
        raise HTTPException(
            status_code=400,
            detail="No active Gmail connection found for organization. Please connect Google first.",
        )

    # Fetch messages from all active mailboxes (most recently active first)
    messages: List[Dict[str, Any]] = []
    active_conn = active_conns[0] if active_conns else None

    # Try connections in priority order
    for conn in (active_conns or [None]):
        connector = GmailConnector(
            organization_id=org.id,
            db=db,
            inbox_connection_id=conn.id if conn else None,
            mock_mode=request.mock_mode,
        )
        try:
            fetched = await connector.fetch_messages(query=request.query, limit=request.limit)
            if fetched:
                messages.extend(fetched)
                # Keep active_conn as the one that had messages
                active_conn = conn
        except Exception as e:
            logger.warning(f"Error fetching messages from connection {conn.email_address if conn else 'default'}: {e}")

    # Remove duplicates if any across mailboxes
    seen_ids = set()
    unique_messages = []
    for m in messages:
        if m["id"] not in seen_ids:
            seen_ids.add(m["id"])
            unique_messages.append(m)
    messages = unique_messages[:request.limit]

    dispatcher = EventDispatcher(db)
    agent_service = InboxAgentService(db=db)
    results: List[Dict[str, Any]] = []

    for msg in messages:
        attachments = []
        if active_conn:
            c_inst = GmailConnector(
                organization_id=org.id,
                db=db,
                inbox_connection_id=active_conn.id,
                mock_mode=request.mock_mode,
            )
            attachments = await c_inst.get_attachments(msg["id"])

        inbound = InboundEventPayload(
            organization_id=org.id,
            source="gmail",
            event_type="email_received",
            idempotency_key=f"gmail-sync-{msg['id']}",
            payload={
                "message_id": msg["id"],
                "thread_id": msg.get("threadId"),
                "sender": msg.get("sender"),
                "recipients": msg.get("recipients", []),
                "subject": msg.get("subject"),
                "body_text": msg.get("body"),
                "attachments": attachments,
            },
        )

        event, is_duplicate = dispatcher.ingest(inbound)
        if is_duplicate:
            results.append({
                "message_id": msg["id"],
                "subject": msg.get("subject"),
                "sender": msg.get("sender"),
                "status": "skipped_duplicate",
                "is_duplicate": True,
            })
            continue

        agent_input = InboxAgentInput(
            organization_id=str(org.id),
            trigger_event_id=str(event.event_id),
            sender=msg.get("sender") or "unknown@carrier.com",
            subject=msg.get("subject") or "No Subject",
            body_text=msg.get("body") or "",
            thread_id=msg.get("threadId"),
            attachments=attachments,
        )

        output = await agent_service.run(agent_input)

        # Retrieve rich shipment details if matched or auto-created
        ship_details = None
        if output.entity_id:
            from apps.worker.pipeline import process_e2e_pipeline
            background_tasks.add_task(process_e2e_pipeline, str(org.id), output.entity_id)

            try:
                s_rec = db.query(Shipment).filter(Shipment.id == uuid.UUID(output.entity_id)).first()
                if s_rec:
                    ship_details = {
                        "id": str(s_rec.id),
                        "load_id": s_rec.load_id,
                        "shipment_number": s_rec.shipment_number,
                        "carrier_name": s_rec.carrier_name,
                        "carrier_reference": s_rec.carrier_reference,
                        "bol_number": s_rec.bol_number,
                        "status": s_rec.status,
                        "origin": s_rec.origin_address,
                        "destination": s_rec.destination_address,
                        "pickup_date": s_rec.pickup_date.isoformat() if s_rec.pickup_date else None,
                        "eta": s_rec.eta.isoformat() if s_rec.eta else None,
                        "weight_lbs": s_rec.weight_lbs,
                        "pallet_count": s_rec.pallet_count,
                    }
            except Exception as e:
                logger.warning(f"Error loading shipment details for output {output.entity_id}: {e}")

        results.append({
            "message_id": msg["id"],
            "subject": msg.get("subject"),
            "sender": msg.get("sender"),
            "body_preview": (msg.get("body") or "")[:300],
            "status": "processed",
            "run_id": output.run_id,
            "decision": output.decision,
            "reasoning": output.reasoning,
            "terminal_outcome": output.terminal_outcome,
            "matched_entity_id": output.entity_id,
            "shipment": ship_details,
            "tool_calls": output.tool_calls,
            "tool_calls_count": len(output.tool_calls),
            "attachment_count": len(attachments),
        })

    # Update sync timestamp for all active connections
    for c in active_conns:
        conn_repo.update_sync_status(org.id, c.id)

    return {
        "status": "success",
        "messages_found": len(messages),
        "messages_processed": len(results),
        "executions": results,
    }
