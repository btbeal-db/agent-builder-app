"""Resource discovery endpoints for auto-populating node config dropdowns.

Uses the OBO token (via ``get_workspace_client()``) so results are scoped to
what the current user can see.  Every endpoint returns
``{"options": [...], "error": null}`` on success and
``{"options": [], "error": "..."}`` on failure (HTTP 200 either way) so the
frontend can degrade gracefully to manual text entry.
"""

from __future__ import annotations

import logging

from databricks.sdk.service.serving import EndpointStateReady
from fastapi import APIRouter
from pydantic import BaseModel

from .auth import get_workspace_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response models ──────────────────────────────────────────────────────────

class DiscoveryOption(BaseModel):
    value: str
    label: str
    description: str = ""
    provider: str | None = None  # only for serving endpoints


class DiscoveryResponse(BaseModel):
    options: list[DiscoveryOption] = []
    error: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _detect_provider(endpoint_name: str) -> str:
    """Map a serving endpoint name to a model provider string."""
    n = endpoint_name.lower()
    if "llama" in n or "meta" in n:
        return "meta"
    if "claude" in n or "anthropic" in n:
        return "anthropic"
    if "gpt" in n or "openai" in n or "o1-" in n or "o3-" in n:
        return "openai"
    if "mistral" in n or "mixtral" in n:
        return "mistral"
    if "dbrx" in n or "databricks" in n:
        return "databricks"
    if "gemini" in n or "google" in n:
        return "google"
    if "qwen" in n:
        return "qwen"
    return "unknown"


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/serving-endpoints", response_model=DiscoveryResponse)
def list_serving_endpoints() -> DiscoveryResponse:
    """List serving endpoints the current user can see."""
    try:
        w = get_workspace_client()
        options: list[DiscoveryOption] = []
        for ep in w.serving_endpoints.list():
            state = "Ready" if ep.state and ep.state.ready == EndpointStateReady.READY else "Not ready"
            creator = ep.creator or ""
            desc_parts = [state]
            if creator:
                desc_parts.append(creator)
            options.append(
                DiscoveryOption(
                    value=ep.name,
                    label=ep.name,
                    description=" | ".join(desc_parts),
                    provider=_detect_provider(ep.name),
                )
            )
        return DiscoveryResponse(options=options)
    except Exception as exc:
        logger.warning("Failed to list serving endpoints: %s", exc)
        return DiscoveryResponse(error=str(exc))


@router.get("/genie-spaces", response_model=DiscoveryResponse)
def list_genie_spaces() -> DiscoveryResponse:
    """List Genie spaces the current user can see."""
    try:
        w = get_workspace_client()
        result = w.genie.list_spaces()
        options: list[DiscoveryOption] = []
        for space in result.spaces or []:
            desc = (space.description or "")[:100]
            options.append(
                DiscoveryOption(
                    value=space.space_id,
                    label=space.title or space.space_id,
                    description=desc,
                )
            )
        return DiscoveryResponse(options=options)
    except Exception as exc:
        logger.warning("Failed to list Genie spaces: %s", exc)
        return DiscoveryResponse(error=str(exc))
