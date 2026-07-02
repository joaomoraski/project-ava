"""Alerts CRUD API endpoints + background firing.

GET    /api/alerts              — list alerts (filter by workspace, status)
POST   /api/alerts              — create alert
GET    /api/alerts/{alert_id}   — get one alert
PUT    /api/alerts/{alert_id}   — update alert
DELETE /api/alerts/{alert_id}   — delete alert
POST   /api/alerts/{alert_id}/dismiss — mark dismissed
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_workspace
from api.schemas import AlertCreate, AlertUpdate, AlertResponse, OkResponse
from core.db.engine import get_session, async_session
from core.db.models import Alert, Workspace

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
logger = logging.getLogger("api.alerts")


async def _resolve_workspace_id(session: AsyncSession, workspace_name: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Workspace.id).where(Workspace.name == workspace_name)
    )
    return result.scalar_one_or_none()


def _alert_to_response(alert: Alert) -> AlertResponse:
    return AlertResponse(
        id=str(alert.id),
        workspace_id=str(alert.workspace_id) if alert.workspace_id else None,
        title=alert.title,
        message=alert.message or "",
        trigger_at=alert.trigger_at.isoformat() if alert.trigger_at else "",
        repeat_rule=alert.repeat_rule or "once",
        status=alert.status or "pending",
        fired_at=alert.fired_at.isoformat() if alert.fired_at else None,
        created_at=alert.created_at.isoformat() if alert.created_at else "",
    )


def _parse_datetime(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: '{value}'. Use ISO 8601.")


def _notify_os(title: str, message: str) -> None:
    """Fire native OS notification. Fails gracefully."""
    try:
        subprocess.run(
            ["notify-send", title, message],
            timeout=3,
            check=False,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # notify-send not available — WS broadcast is sufficient


@router.get("")
async def list_alerts(
    workspace: str = Depends(get_current_workspace),
    status: str | None = Query(None, description="pending/fired/dismissed"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace_id = await _resolve_workspace_id(session, workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found.")

    stmt = select(Alert).where(Alert.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    stmt = stmt.order_by(Alert.trigger_at.asc())

    result = await session.execute(stmt)
    alerts = result.scalars().all()

    return {
        "alerts": [_alert_to_response(a).model_dump() for a in alerts],
        "count": len(alerts),
    }


@router.post("", status_code=201)
async def create_alert(
    body: AlertCreate,
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    workspace_id = await _resolve_workspace_id(session, body.workspace)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{body.workspace}' not found.")

    alert = Alert(
        workspace_id=workspace_id,
        title=body.title,
        message=body.message,
        trigger_at=_parse_datetime(body.trigger_at),
        repeat_rule=body.repeat_rule,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    logger.info(f"Created alert '{alert.title}' (id={alert.id}) trigger_at={alert.trigger_at}.")
    return _alert_to_response(alert)


@router.get("/{alert_id}")
async def get_alert(
    alert_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    try:
        uid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid alert ID format.")

    result = await session.execute(select(Alert).where(Alert.id == uid))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or alert.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return _alert_to_response(alert)


@router.put("/{alert_id}")
async def update_alert(
    alert_id: str,
    body: AlertUpdate,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    try:
        uid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid alert ID format.")

    result = await session.execute(select(Alert).where(Alert.id == uid))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or alert.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")

    if body.title is not None:
        alert.title = body.title
    if body.message is not None:
        alert.message = body.message
    if body.trigger_at is not None:
        alert.trigger_at = _parse_datetime(body.trigger_at)
    if body.repeat_rule is not None:
        alert.repeat_rule = body.repeat_rule
    if body.status is not None:
        alert.status = body.status

    await session.commit()
    await session.refresh(alert)
    logger.info(f"Updated alert '{alert.title}' (id={alert.id}).")
    return _alert_to_response(alert)


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    try:
        uid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid alert ID format.")

    result = await session.execute(select(Alert).where(Alert.id == uid))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or alert.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")

    await session.delete(alert)
    await session.commit()
    logger.info(f"Deleted alert {alert_id}.")
    return OkResponse(message=f"Alert '{alert_id}' deleted.")


@router.post("/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: str,
    workspace: str = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    try:
        uid = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid alert ID format.")

    result = await session.execute(select(Alert).where(Alert.id == uid))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    ws_id = await _resolve_workspace_id(session, workspace)
    if ws_id is None or alert.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")

    alert.status = "dismissed"
    await session.commit()
    await session.refresh(alert)
    logger.info(f"Dismissed alert {alert_id}.")
    return _alert_to_response(alert)


# ─── Background firing ────────────────────────────────────────────────────────

async def check_and_fire_alerts(ws_manager) -> None:
    """Query pending alerts whose trigger_at has passed, fire them, handle repeats."""
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(Alert)
            .where(Alert.status == "pending")
            .where(Alert.trigger_at <= now)
        )
        due_alerts = result.scalars().all()

        # Pre-fetch workspace names so each broadcast includes the workspace.
        # Clients use this field to ignore alerts that belong to other workspaces.
        ws_id_to_name: dict[str, str] = {}
        all_ws_ids = {alert.workspace_id for alert in due_alerts if alert.workspace_id}
        if all_ws_ids:
            from core.db.models import Workspace as _Workspace
            ws_rows = await session.execute(
                select(_Workspace.id, _Workspace.name).where(
                    _Workspace.id.in_(all_ws_ids)
                )
            )
            for row_id, row_name in ws_rows.all():
                ws_id_to_name[str(row_id)] = row_name

        for alert in due_alerts:
            alert_id = str(alert.id)
            title = alert.title
            message = alert.message or ""
            repeat_rule = alert.repeat_rule or "once"
            alert_workspace = ws_id_to_name.get(str(alert.workspace_id), "personal")

            # 1. Broadcast WS event — include workspace so clients can scope it.
            try:
                await ws_manager.broadcast({
                    "type": "alert_fired",
                    "id": alert_id,
                    "title": title,
                    "message": message,
                    "workspace": alert_workspace,
                })
            except Exception as e:
                logger.warning(f"WS broadcast failed for alert {alert_id}: {e}")

            # 2. Native OS notification (Linux, graceful)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _notify_os, title, message)

            # 3. Mark as fired
            alert.status = "fired"
            alert.fired_at = now

            # 4. Schedule repeat if applicable
            next_trigger: datetime | None = None
            if repeat_rule == "daily":
                next_trigger = alert.trigger_at + timedelta(days=1)
            elif repeat_rule == "weekly":
                next_trigger = alert.trigger_at + timedelta(weeks=1)

            if next_trigger is not None:
                new_alert = Alert(
                    workspace_id=alert.workspace_id,
                    title=title,
                    message=message,
                    trigger_at=next_trigger,
                    repeat_rule=repeat_rule,
                    status="pending",
                )
                session.add(new_alert)
                logger.info(
                    f"Scheduled next {repeat_rule} alert '{title}' for {next_trigger.isoformat()}."
                )

            logger.info(f"Fired alert '{title}' (id={alert_id}).")

        if due_alerts:
            await session.commit()
