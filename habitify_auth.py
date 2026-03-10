from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("accountability-buddy")

TOKEN_URL = "https://account.habitify.me/token"


async def refresh_habitify_token() -> str:
    """Exchange refresh token for a fresh access token.

    Reads HABITIFY_CLIENT_ID and HABITIFY_REFRESH_TOKEN from environment.
    Returns the access token string.
    Raises RuntimeError if credentials are missing or token exchange fails.
    """
    client_id = os.getenv("HABITIFY_CLIENT_ID")
    refresh_token = os.getenv("HABITIFY_REFRESH_TOKEN")

    if not client_id or not refresh_token:
        raise RuntimeError(
            "HABITIFY_CLIENT_ID and HABITIFY_REFRESH_TOKEN must be set in .env.local. "
            "Run: uv run scripts/habitify_oauth_setup.py"
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "scope": "openid offline_access all",
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Habitify token refresh failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        logger.info("Habitify access token refreshed successfully")
        return data["access_token"]
