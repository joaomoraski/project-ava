"""Calendar API endpoints - sync and view Google Calendar events."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import select, and_

from api.schemas import OkResponse
from core.db.engine import async_session
from core.db.models import CalendarEvent, GoogleAccount

logger = logging.getLogger("api.calendar")
router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/events")
async def list_events(
    days_ahead: int = Query(7, ge=1, le=30),
    days_back: int = Query(1, ge=0, le=7),
) -> dict:
    """List calendar events from DB (synced from Google)."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)

    async with async_session() as session:
        result = await session.execute(
            select(CalendarEvent).where(
                and_(
                    CalendarEvent.start_time >= start,
                    CalendarEvent.start_time <= end,
                )
            ).order_by(CalendarEvent.start_time)
        )
        events = result.scalars().all()

    return {
        "events": [
            {
                "id": str(e.id),
                "google_event_id": e.google_event_id,
                "title": e.title,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "end_time": e.end_time.isoformat() if e.end_time else None,
                "attendees": e.attendees or [],
                "meet_link": e.meet_link,
            }
            for e in events
        ],
        "count": len(events),
    }


@router.post("/sync")
async def sync_calendar() -> OkResponse:
    """Sync Google Calendar events to local DB."""
    try:
        synced = await _do_sync()
        return OkResponse(message=f"Synced {synced} events")
    except Exception as e:
        logger.warning(f"Calendar sync failed: {e}")
        return OkResponse(message=f"Sync failed: {str(e)}")


async def _do_sync() -> int:
    """Pull events from Google Calendar API and upsert into CalendarEvent table."""
    from core.secrets.vault import SecretsVault

    vault = SecretsVault()
    async with async_session() as session:
        access_token = await vault.get_secret(session, "GOOGLE_ACCESS_TOKEN")
    if not access_token:
        return 0

    import httpx
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=1)).isoformat()
    time_max = (now + timedelta(days=7)).isoformat()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 50,
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Calendar API returned {resp.status_code}: {resp.text[:200]}")
            return 0
        data = resp.json()

    items = data.get("items", [])

    async with async_session() as session:
        # Get first google account
        result = await session.execute(select(GoogleAccount).limit(1))
        account = result.scalar_one_or_none()
        if not account:
            return 0

        count = 0
        for item in items:
            start_str = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end_str = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")

            if not start_str:
                continue

            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else start_dt
            except Exception:
                continue

            attendees = [
                a.get("displayName") or a.get("email", "")
                for a in item.get("attendees", [])
                if a.get("email")
            ]

            meet_link = None
            for ep in item.get("conferenceData", {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break

            # Upsert by google_event_id
            existing = await session.execute(
                select(CalendarEvent).where(CalendarEvent.google_event_id == item["id"])
            )
            ev = existing.scalar_one_or_none()

            if ev:
                ev.title = item.get("summary", "Untitled")
                ev.start_time = start_dt
                ev.end_time = end_dt
                ev.attendees = attendees
                ev.meet_link = meet_link
            else:
                ev = CalendarEvent(
                    google_account_id=account.id,
                    google_event_id=item["id"],
                    title=item.get("summary", "Untitled"),
                    start_time=start_dt,
                    end_time=end_dt,
                    attendees=attendees,
                    meet_link=meet_link,
                )
                session.add(ev)
            count += 1

        await session.commit()

    return count
