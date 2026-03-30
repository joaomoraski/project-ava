"""Gmail tool — read and send emails via Google API."""
from __future__ import annotations

import base64
import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.gmail")


@tool
def get_recent_emails(max_results: int = 5) -> str:
    """Get recent unread emails from Gmail.

    Args:
        max_results: number of emails to fetch (default: 5)
    """
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        access_token = vault.get_secret("GOOGLE_ACCESS_TOKEN")
        if not access_token:
            return "Gmail not configured. Connect your Google account in the dashboard."

        import httpx

        resp = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": "is:unread", "maxResults": max_results},
        )

        if resp.status_code == 401:
            return "Gmail token expired. Reconnect your account in the dashboard."

        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        if not messages:
            return "No unread emails."

        results = []
        for msg in messages:
            msg_resp = httpx.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            )
            if msg_resp.ok:
                headers = {
                    h["name"]: h["value"]
                    for h in msg_resp.json().get("payload", {}).get("headers", [])
                }
                results.append(
                    f"From: {headers.get('From', '?')}\n"
                    f"Subject: {headers.get('Subject', '(no subject)')}\n"
                    f"Date: {headers.get('Date', '?')}"
                )

        return "\n\n---\n\n".join(results) if results else "No emails fetched."

    except Exception as e:
        logger.error(f"get_recent_emails failed: {e}")
        return f"Failed to fetch emails: {e}"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail.

    Args:
        to: recipient email address
        subject: email subject
        body: plain text email body
    """
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        access_token = vault.get_secret("GOOGLE_ACCESS_TOKEN")
        if not access_token:
            return "Gmail not configured. Connect your Google account in the dashboard."

        import httpx

        raw = f"To: {to}\nSubject: {subject}\n\n{body}"
        encoded = base64.urlsafe_b64encode(raw.encode()).decode()

        resp = httpx.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": encoded},
        )

        if resp.status_code == 401:
            return "Gmail token expired. Reconnect your account in the dashboard."

        resp.raise_for_status()
        return f"Email sent to {to}: '{subject}'"

    except Exception as e:
        logger.error(f"send_email failed: {e}")
        return f"Failed to send email: {e}"
