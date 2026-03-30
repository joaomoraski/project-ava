"""Google OAuth2 authentication endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from core.config import settings

logger = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


@router.get("/google")
async def google_auth_start() -> RedirectResponse:
    """Initiate Google OAuth flow. Redirects user to Google consent screen."""
    if not settings.google_client_id or not settings.google_client_secret:
        return RedirectResponse(
            url=f"http://localhost:{settings.frontend_port}?"
                f"auth_error=Google+credentials+not+configured+in+.env"
        )

    try:
        from google_auth_oauthlib.flow import Flow  # type: ignore

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uris": [f"http://localhost:{settings.api_port}/auth/google/callback"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=GOOGLE_SCOPES,
        )
        flow.redirect_uri = f"http://localhost:{settings.api_port}/auth/google/callback"
        auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true")
        return RedirectResponse(url=auth_url)
    except Exception as e:
        logger.error(f"Failed to start Google OAuth: {e}")
        return RedirectResponse(
            url=f"http://localhost:{settings.frontend_port}?auth_error=OAuth+init+failed"
        )


@router.get("/google/callback")
async def google_auth_callback(code: str | None = None, error: str | None = None) -> RedirectResponse:
    """Handle Google OAuth redirect. Stores tokens in encrypted vault."""
    if error or not code:
        logger.warning(f"Google OAuth error: {error}")
        return RedirectResponse(
            url=f"http://localhost:{settings.frontend_port}?auth_error={error or 'no_code'}"
        )

    try:
        from google_auth_oauthlib.flow import Flow  # type: ignore
        from core.secrets.vault import SecretsVault

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uris": [f"http://localhost:{settings.api_port}/auth/google/callback"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=GOOGLE_SCOPES,
        )
        flow.redirect_uri = f"http://localhost:{settings.api_port}/auth/google/callback"
        flow.fetch_token(code=code)

        creds = flow.credentials
        vault = SecretsVault()
        vault.set_secret("GOOGLE_ACCESS_TOKEN", creds.token or "")
        vault.set_secret("GOOGLE_REFRESH_TOKEN", creds.refresh_token or "")

        logger.info("Google OAuth tokens stored in vault.")
        return RedirectResponse(
            url=f"http://localhost:{settings.frontend_port}?auth_success=google"
        )
    except Exception as e:
        logger.error(f"Google OAuth callback failed: {e}")
        return RedirectResponse(
            url=f"http://localhost:{settings.frontend_port}?auth_error=callback_failed"
        )


@router.get("/google/status")
async def google_auth_status() -> dict:
    """Check if Google OAuth is configured and tokens are present."""
    try:
        from core.secrets.vault import SecretsVault
        vault = SecretsVault()
        has_access = vault.get_preview("GOOGLE_ACCESS_TOKEN")["is_set"]
        has_refresh = vault.get_preview("GOOGLE_REFRESH_TOKEN")["is_set"]
        return {
            "configured": bool(settings.google_client_id and settings.google_client_secret),
            "authenticated": has_access and has_refresh,
            "scopes": GOOGLE_SCOPES if has_access else [],
        }
    except Exception:
        return {"configured": False, "authenticated": False, "scopes": []}
