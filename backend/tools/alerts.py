"""LangChain tool for managing user alerts/reminders."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.tools import tool

logger = logging.getLogger("tools.alerts")


@tool
async def manage_alerts(
    action: str,
    title: str = "",
    message: str = "",
    trigger_at: str = "",
    repeat_rule: str = "once",
    alert_id: str = "",
    workspace: str = "personal",
) -> str:
    """Create and manage reminders/alerts. Actions: create, list, dismiss.
    Use when user says 'remind me', 'set an alert', 'notify me at X time'.

    Args:
        action: one of create / list / dismiss
        title: alert title (required for create)
        message: optional alert message body
        trigger_at: ISO 8601 datetime when the alert should fire (required for create)
        repeat_rule: once / daily / weekly (default: once)
        alert_id: UUID of the alert (required for dismiss)
        workspace: target workspace (default: personal)
    """
    try:
        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import Alert, Workspace

        async with async_session() as session:
            ws_result = await session.execute(
                select(Workspace.id).where(Workspace.name == workspace)
            )
            ws_id = ws_result.scalar_one_or_none()
            if ws_id is None:
                return f"Workspace '{workspace}' not found."

            # ── create ──────────────────────────────────────────────────────
            if action == "create":
                if not title:
                    return "Please provide a title for the alert."
                if not trigger_at:
                    return "Please provide a trigger_at datetime (ISO 8601)."
                try:
                    trigger_dt = datetime.fromisoformat(trigger_at)
                    if trigger_dt.tzinfo is None:
                        trigger_dt = trigger_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    return f"Invalid trigger_at format: '{trigger_at}'. Use ISO 8601."

                alert = Alert(
                    workspace_id=ws_id,
                    title=title,
                    message=message,
                    trigger_at=trigger_dt,
                    repeat_rule=repeat_rule,
                )
                session.add(alert)
                await session.commit()
                await session.refresh(alert)
                return (
                    f"Alert '{alert.title}' set for {trigger_dt.strftime('%Y-%m-%d %H:%M %Z')} "
                    f"(repeat={repeat_rule}, id={alert.id})."
                )

            # ── list ─────────────────────────────────────────────────────────
            elif action == "list":
                stmt = (
                    select(Alert)
                    .where(Alert.workspace_id == ws_id)
                    .order_by(Alert.trigger_at.asc())
                    .limit(20)
                )
                result = await session.execute(stmt)
                alerts = result.scalars().all()

                if not alerts:
                    return "No alerts found."

                lines = []
                for a in alerts:
                    trigger_str = a.trigger_at.strftime("%Y-%m-%d %H:%M") if a.trigger_at else "unknown"
                    lines.append(
                        f"- [{a.status.upper()}] {a.title} @ {trigger_str} "
                        f"(repeat={a.repeat_rule}, id={a.id})"
                    )
                return "\n".join(lines)

            # ── dismiss ───────────────────────────────────────────────────────
            elif action == "dismiss":
                if not alert_id:
                    return "Please provide an alert_id to dismiss."
                import uuid as _uuid
                try:
                    uid = _uuid.UUID(alert_id)
                except ValueError:
                    return f"Invalid alert_id: '{alert_id}'."

                result = await session.execute(select(Alert).where(Alert.id == uid))
                alert = result.scalar_one_or_none()
                if alert is None:
                    return f"Alert '{alert_id}' not found."

                alert.status = "dismissed"
                await session.commit()
                return f"Alert '{alert.title}' dismissed."

            else:
                return f"Unknown action '{action}'. Use: create, list, dismiss."

    except Exception as e:
        logger.error(f"manage_alerts failed: {e}")
        return f"Alert operation failed: {e}"
