from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class StateFieldDef(BaseModel):
    """A field in the agent's state model."""

    name: str
    type: str = "str"  # str | int | float | bool | list[str] | structured
    description: str = ""
    sub_fields: list[dict[str, str]] = []  # for structured: [{name, type, description}]


class NodeDef(BaseModel):
    id: str
    type: str
    name: str = ""  # user-facing label, used as LangGraph node name
    writes_to: str = ""  # which state field this node updates
    config: dict[str, Any] = {}
    position: dict[str, float] = {}


class EdgeDef(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str | None = None


class GraphDef(BaseModel):
    nodes: list[NodeDef]
    edges: list[EdgeDef]
    state_fields: list[StateFieldDef] = [
        StateFieldDef(name="input", type="str", description="The initial input")
    ]
    output_fields: list[str] = []  # which state fields to include in output; empty = all

    @property
    def state_variable_names(self) -> list[str]:
        return [f.name for f in self.state_fields]

    def get_state_field(self, name: str) -> StateFieldDef | None:
        for f in self.state_fields:
            if f.name == name:
                return f
        return None


class PreviewRequest(BaseModel):
    graph: GraphDef
    input_message: str
    thread_id: str | None = None
    resume_value: str | None = None


class TraceSpan(BaseModel):
    """A single span from an MLflow trace."""
    name: str = ""
    status: str = ""
    start_time_ms: int = 0
    end_time_ms: int = 0
    inputs: Any = None
    outputs: Any = None

class PreviewResponse(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    execution_trace: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    thread_id: str | None = None
    interrupt: str | None = None
    mlflow_trace: list[dict[str, Any]] = []


class ExportResponse(BaseModel):
    success: bool
    code: str = ""
    error: str | None = None


class DeployMode(str, Enum):
    LOG_ONLY = "log_only"
    LOG_AND_REGISTER = "log_and_register"
    FULL = "full"


class DeployStepStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class DeployEvent(BaseModel):
    step: str
    status: DeployStepStatus
    message: str
    data: dict[str, str] | None = None


class DeployRequest(BaseModel):
    graph: GraphDef
    model_name: str = ""  # Unity Catalog path: catalog.schema.model_name
    experiment_path: str  # MLflow experiment: /Users/email/experiment
    lakebase_conn_string: str = ""  # Lakebase Postgres connection URL
    deploy_mode: DeployMode = DeployMode.FULL


class DeployResponse(BaseModel):
    success: bool
    endpoint_url: str = ""
    model_version: str = ""
    error: str | None = None


# ── Setup (MLflow experiment one-time config) ────────────────────────────────

class SetupStatusResponse(BaseModel):
    setup_complete: bool
    user_email: str
    sp_display_name: str
    experiment_path: str | None = None


class SetupInfoResponse(BaseModel):
    user_email: str
    sp_display_name: str
    sp_id: str


class SetupGrantRequest(BaseModel):
    experiment_path: str


class SetupGrantResponse(BaseModel):
    success: bool
    manual_instructions: str | None = None


class SetupValidateRequest(BaseModel):
    experiment_path: str


class SetupValidateResponse(BaseModel):
    success: bool
    experiment_id: str | None = None
    error: str | None = None
