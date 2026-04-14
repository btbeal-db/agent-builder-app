"""Authentication helpers for Databricks Apps.

Two per-request credentials are stored in ContextVars:

* **OBO token** — injected by the Apps proxy via ``x-forwarded-access-token``.
  Useful for identity and APIs with working OBO scopes (SQL, Genie).
* **User PAT** — optionally provided by the user for preview/playground.
  Has full permissions (no scope gaps), never stored beyond the request.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from databricks.sdk import WorkspaceClient

# Per-request credential storage (set by middleware / endpoint, read by nodes)
_user_token: ContextVar[str | None] = ContextVar("_user_token", default=None)
_user_pat: ContextVar[str | None] = ContextVar("_user_pat", default=None)

# Auth mode for served models — set once at load time, immutable per container.
# "obo" = on-behalf-of (user identity), "passthrough" = system SP.
_auth_mode: str = "passthrough"


def set_auth_mode(mode: str) -> None:
    global _auth_mode
    _auth_mode = mode


def get_auth_mode() -> str:
    return _auth_mode


def set_user_token(token: str | None) -> None:
    _user_token.set(token)


def get_user_token() -> str | None:
    return _user_token.get()


def set_user_pat(pat: str | None) -> None:
    _user_pat.set(pat)


def get_user_pat() -> str | None:
    return _user_pat.get()


def get_data_client() -> WorkspaceClient:
    """Return a WorkspaceClient for data-access operations (VS, Genie, UC).

    Credential priority:
    1. **User PAT** — full permissions, no scope gaps (app preview).
    2. **OBO serving mode** — ``ModelServingUserCredentials`` acts as the
       end user calling the serving endpoint.
    3. **OBO token from Apps proxy** — works for APIs with OBO scopes.
    4. **Default** — system SP credentials (passthrough serving mode,
       local dev).
    """
    pat = _user_pat.get()
    if pat:
        return create_pat_client(pat)

    if _auth_mode == "obo":
        from databricks_ai_bridge import ModelServingUserCredentials
        return WorkspaceClient(credentials_strategy=ModelServingUserCredentials())

    return get_workspace_client()


def get_workspace_client() -> WorkspaceClient:
    """Return a WorkspaceClient using the OBO user token if available,
    otherwise fall back to the default env-var credentials (local dev).

    When an OBO token is present we temporarily mask the service principal's
    OAuth env vars so the SDK doesn't load them into its Config, then pass
    ``auth_type="pat"`` so it uses bearer-token auth directly.  Both are
    required: masking keeps ``client_id``/``client_secret`` out of the
    Config (otherwise the server treats the request as an SP OAuth call
    with wrong scopes), and ``auth_type="pat"`` forces bearer-token auth.
    """
    token = _user_token.get()
    host = os.environ.get("DATABRICKS_HOST", "")
    if token and host:
        masked = {}
        for key in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
            if key in os.environ:
                masked[key] = os.environ.pop(key)
        try:
            return WorkspaceClient(host=host, token=token, auth_type="pat")
        finally:
            os.environ.update(masked)
    return WorkspaceClient()


def get_sp_workspace_client() -> WorkspaceClient:
    """Return a WorkspaceClient using the app's service principal credentials.

    Unlike :func:`get_workspace_client`, this always uses the SP env vars
    regardless of whether an OBO token is available.  Use this for operations
    that must run as the application identity (e.g. MLflow experiment access,
    setup config persistence, loading graphs from MLflow runs).
    """
    host = os.environ.get("DATABRICKS_HOST", "")
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    if not all([host, client_id, client_secret]):
        raise RuntimeError(
            "Service principal credentials not available in environment. "
            "Expected DATABRICKS_HOST, DATABRICKS_CLIENT_ID, and DATABRICKS_CLIENT_SECRET."
        )
    return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)


def create_pat_client(pat: str) -> WorkspaceClient:
    """Create a WorkspaceClient authenticated with a user's PAT.

    Masks SP OAuth env vars and uses ``auth_type="pat"`` so the SDK
    sees only the PAT.  Always restores env vars via ``finally``.
    """
    host = os.environ.get("DATABRICKS_HOST", "")
    masked = {}
    for key in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
        if key in os.environ:
            masked[key] = os.environ.pop(key)
    try:
        return WorkspaceClient(host=host, token=pat, auth_type="pat")
    finally:
        os.environ.update(masked)
