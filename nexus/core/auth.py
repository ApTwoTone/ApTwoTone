"""
WorkOS Authentication for Nexus Network.

Uses WorkOS User Management for email+password auth.
Two users: Carlos (owner) and Kai (operator).

Setup:
  1. Create WorkOS account at workos.com (free, 1M MAU)
  2. Get API key + Client ID from WorkOS Dashboard
  3. Add to ~/.nexus/config.json:
     {
       "workos_api_key": "sk_...",
       "workos_client_id": "client_..."
     }
  4. Create users in WorkOS Dashboard (carlos@zoarbathroom.com, kai@zoarbathroom.com)
"""

import json
import secrets
import time
from pathlib import Path

CONFIG_FILE = Path.home() / ".nexus" / "config.json"

# In-memory session store (simple for 2-user desktop app)
_sessions = {}  # token -> session dict


def _load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _get_workos_client():
    """Initialize WorkOS client from config."""
    cfg = _load_config()
    api_key = cfg.get("workos_api_key")
    client_id = cfg.get("workos_client_id")
    if not api_key or not client_id:
        return None, None
    try:
        from workos import WorkOSClient
        client = WorkOSClient(api_key=api_key)
        return client, client_id
    except Exception as e:
        print(f"[Auth] WorkOS init error: {e}")
        return None, None


def authenticate_email_password(email: str, password: str) -> dict:
    """
    Authenticate user via WorkOS email+password.
    Returns session dict with token on success, None on failure.
    """
    client, client_id = _get_workos_client()

    if not client:
        # Fallback: allow local dev without WorkOS
        cfg = _load_config()
        local_users = cfg.get("local_users", {})
        if email in local_users and local_users[email] == password:
            token = secrets.token_hex(32)
            session = {
                "token": token,
                "email": email,
                "name": email.split("@")[0].title(),
                "role": "owner" if "carlos" in email.lower() else "operator",
                "expires_at": time.time() + 86400 * 7,  # 7 days
            }
            _sessions[token] = session
            return session
        return None

    try:
        auth_response = client.user_management.authenticate_with_password(
            email=email,
            password=password,
            client_id=client_id,
        )
        user = auth_response.user
        token = secrets.token_hex(32)
        session = {
            "token": token,
            "email": user.email,
            "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "role": "owner" if "carlos" in user.email.lower() else "operator",
            "workos_user_id": user.id,
            "expires_at": time.time() + 86400 * 7,
        }
        _sessions[token] = session
        return session
    except Exception as e:
        print(f"[Auth] Authentication failed for {email}: {e}")
        return None


def validate_session(token: str) -> dict:
    """Check if a session token is valid and not expired."""
    session = _sessions.get(token)
    if not session:
        return None
    if time.time() > session.get("expires_at", 0):
        _sessions.pop(token, None)
        return None
    return session


def logout(token: str):
    """Invalidate a session."""
    _sessions.pop(token, None)
