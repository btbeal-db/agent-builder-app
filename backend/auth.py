"""OBO (On-Behalf-Of) authentication for Databricks Apps.

When the app runs on Databricks, the user's downscoped access token is passed
in the ``x-forwarded-access-token`` request header.  We store it in a
context variable so that any code in the request path can create a
WorkspaceClient that acts on behalf of the logged-in user.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from databricks.sdk import WorkspaceClient

# Holds the current request's user token (set per-request by middleware)
_user_token: ContextVar[str | None] = ContextVar("_user_token", default=None)


def set_user_token(token: str | None) -> None:
    _user_token.set(token)


def get_user_token() -> str | None:
    return _user_token.get()


def get_workspace_client() -> WorkspaceClient:
    """Return a WorkspaceClient using the OBO user token if available,
    otherwise fall back to the default env-var credentials (local dev)."""
    token = _user_token.get()
    host = os.environ.get("DATABRICKS_HOST", "")
    if token and host:
        # Temporarily mask the SP's OAuth env vars so the SDK doesn't
        # see two auth methods (OBO token + SP client credentials).
        masked = {}
        for key in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
            if key in os.environ:
                masked[key] = os.environ.pop(key)
        try:
            return WorkspaceClient(host=host, token=token, auth_type="oauth")
        finally:
            os.environ.update(masked)
    return WorkspaceClient()
