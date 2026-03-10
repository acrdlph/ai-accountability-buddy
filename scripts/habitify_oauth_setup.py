"""One-time OAuth setup for Habitify MCP server.

Performs dynamic client registration, PKCE authorization code flow,
and stores refresh token + client ID in .env.local for headless runtime use.

Usage:
    uv run scripts/habitify_oauth_setup.py
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

# Habitify OAuth endpoints
REGISTRATION_URL = "https://account.habitify.me/reg"
AUTHORIZATION_URL = "https://account.habitify.me/auth"
TOKEN_URL = "https://account.habitify.me/token"

# Local callback server
REDIRECT_URI = "http://localhost:8976/callback"
CALLBACK_PORT = 8976

# Path to .env.local (project root)
ENV_LOCAL_PATH = Path(__file__).resolve().parent.parent / ".env.local"


def _generate_code_verifier() -> str:
    """Generate a random code verifier (43-128 URL-safe chars)."""
    return secrets.token_urlsafe(64)[:96]


def _generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _register_client() -> str:
    """Register a dynamic OAuth client and return the client_id."""
    print("Registering OAuth client with Habitify...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            REGISTRATION_URL,
            json={
                "client_name": "accountability-buddy",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "openid offline_access all",
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Client registration failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        client_id = data["client_id"]
        print(f"  Client registered: {client_id}")
        return client_id


def _build_authorization_url(
    client_id: str, code_challenge: str, state: str
) -> str:
    """Build the authorization URL with PKCE parameters."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid offline_access all",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "consent",
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


def _capture_callback(expected_state: str) -> str:
    """Start a local HTTP server to capture the OAuth callback and return the auth code."""
    auth_code: str | None = None
    error_msg: str | None = None

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal auth_code, error_msg
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if "error" in params:
                error_msg = params["error"][0]
                desc = params.get("error_description", ["Unknown error"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<h1>Authorization Failed</h1><p>{desc}</p>".encode()
                )
                return

            returned_state = params.get("state", [None])[0]
            if returned_state != expected_state:
                error_msg = "State mismatch"
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Error</h1><p>State mismatch - possible CSRF attack.</p>"
                )
                return

            auth_code = params.get("code", [None])[0]
            if not auth_code:
                error_msg = "No authorization code received"
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Error</h1><p>No authorization code in callback.</p>"
                )
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorization Successful</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )

        def log_message(self, format: str, *args: object) -> None:
            # Suppress default HTTP server logging
            pass

    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    print(f"  Listening on http://localhost:{CALLBACK_PORT} for callback...")
    server.handle_request()  # Handle exactly one request
    server.server_close()

    if error_msg:
        raise RuntimeError(f"Authorization failed: {error_msg}")
    if not auth_code:
        raise RuntimeError("No authorization code captured")

    return auth_code


def _exchange_code(
    client_id: str, auth_code: str, code_verifier: str
) -> tuple[str, str]:
    """Exchange authorization code for tokens. Returns (access_token, refresh_token)."""
    print("Exchanging authorization code for tokens...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": code_verifier,
                "scope": "openid offline_access all",
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token exchange failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        print(f"  Token response keys: {list(data.keys())}")
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            print(f"  Full response (redacted tokens): { {k: (v[:20] + '...' if isinstance(v, str) and len(v) > 20 else v) for k, v in data.items()} }")
            raise RuntimeError(
                "No refresh_token in response. "
                "Ensure 'offline_access' scope was granted."
            )
        print("  Tokens received successfully.")
        return access_token, refresh_token


def _update_env_local(client_id: str, refresh_token: str) -> None:
    """Write or update HABITIFY_CLIENT_ID and HABITIFY_REFRESH_TOKEN in .env.local."""
    env_vars = {
        "HABITIFY_CLIENT_ID": client_id,
        "HABITIFY_REFRESH_TOKEN": refresh_token,
    }

    # Read existing .env.local if it exists
    existing_lines: list[str] = []
    if ENV_LOCAL_PATH.exists():
        existing_lines = ENV_LOCAL_PATH.read_text().splitlines()

    # Update or append each env var
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in env_vars:
            new_lines.append(f"{key}={env_vars[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append any vars that weren't already present
    for key, value in env_vars.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    # Ensure file ends with newline
    content = "\n".join(new_lines)
    if not content.endswith("\n"):
        content += "\n"

    ENV_LOCAL_PATH.write_text(content)
    print(f"  Credentials written to {ENV_LOCAL_PATH}")


def main() -> None:
    """Run the full OAuth setup flow."""
    print("=" * 60)
    print("Habitify OAuth Setup for Accountability Buddy")
    print("=" * 60)
    print()

    # Step 1: Dynamic client registration
    client_id = _register_client()

    # Step 2: PKCE setup
    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)

    # Step 3: Build and open authorization URL
    auth_url = _build_authorization_url(client_id, code_challenge, state)
    print()
    print("Opening your browser for Habitify authorization...")
    print(f"  URL: {auth_url}")
    print()
    webbrowser.open(auth_url)

    # Step 4: Capture callback
    auth_code = _capture_callback(state)

    # Step 5: Exchange code for tokens
    access_token, refresh_token = _exchange_code(
        client_id, auth_code, code_verifier
    )

    # Step 6: Store credentials
    _update_env_local(client_id, refresh_token)

    print()
    print("=" * 60)
    print("Setup complete!")
    print()
    print("Stored in .env.local:")
    print(f"  HABITIFY_CLIENT_ID={client_id}")
    print(f"  HABITIFY_REFRESH_TOKEN={refresh_token[:20]}...")
    print()
    print("Quick test (should print first 20 chars of an access token):")
    print(
        '  uv run python -c "'
        "import asyncio; from habitify_auth import refresh_habitify_token; "
        "print(asyncio.run(refresh_habitify_token())[:20] + '...')\""
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
