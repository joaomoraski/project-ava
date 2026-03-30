"""Google Calendar tool — placeholder for full OAuth integration."""
from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.tools import tool

logger = logging.getLogger("tools.google_calendar")


@tool
def get_calendar_events(days_ahead: int = 7) -> str:
    """Get upcoming Google Calendar events.

    Args:
        days_ahead: how many days ahead to fetch (default: 7)
    """
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        access_token = vault.get_secret("GOOGLE_ACCESS_TOKEN")
        if not access_token:
            return "Google Calendar not configured. Connect your Google account in the dashboard."

        import httpx
        from datetime import timezone, timedelta

        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        resp = httpx.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": now.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 10,
            },
        )

        if resp.status_code == 401:
            return "Google Calendar token expired. Reconnect your account in the dashboard."

        resp.raise_for_status()
        events = resp.json().get("items", [])
        if not events:
            return f"No events in the next {days_ahead} days."

        lines = []
        for event in events:
            start = event.get("start", {})
            start_time = start.get("dateTime") or start.get("date", "")
            title = event.get("summary", "(no title)")
            lines.append(f"- {start_time}: {title}")

        return "\n".join(lines)

    except ImportError as e:
        return f"Google Calendar unavailable: {e}"
    except Exception as e:
        logger.error(f"get_calendar_events failed: {e}")
        return f"Failed to fetch calendar: {e}"


@tool
def create_calendar_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
) -> str:
    """Create a Google Calendar event.

    Args:
        title: event title
        start_datetime: ISO 8601 datetime string (e.g., '2025-01-15T14:00:00')
        end_datetime: ISO 8601 datetime string
        description: optional event description
    """
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        access_token = vault.get_secret("GOOGLE_ACCESS_TOKEN")
        if not access_token:
            return "Google Calendar not configured. Connect your Google account in the dashboard."

        import httpx

        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_datetime},
            "end": {"dateTime": end_datetime},
        }

        resp = httpx.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )

        if resp.status_code == 401:
            return "Google Calendar token expired. Reconnect your account in the dashboard."

        resp.raise_for_status()
        event = resp.json()
        return f"Event created: '{event.get('summary')}' on {event.get('start', {}).get('dateTime', 'unknown time')}"

    except Exception as e:
        logger.error(f"create_calendar_event failed: {e}")
        return f"Failed to create event: {e}"
