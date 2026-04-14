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
    pat: str | None = None  # optional PAT for data-access ops (VS, Genie)


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
    deploy_mode: DeployMode = DeployMode.FULL
    pat: str = ""  # Optional PAT for UC registration + endpoint creation

    # Lakebase checkpointing — option A: auto-provision a new project
    lakebase_project_id: str = ""  # e.g. "my-team" → creates project + db

    # Lakebase checkpointing — option B: use an existing Lakebase project
    lakebase_existing_project_id: str = ""  # e.g. "my-team" → resolves details

    # Lakebase checkpointing — option C: raw connection string (legacy)
    lakebase_conn_string: str = ""


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


class SetupValidateRequest(BaseModel):
    experiment_path: str


class SetupValidateResponse(BaseModel):
    success: bool
    experiment_id: str | None = None
    error: str | None = None


# ── Models listing ──────────────────────────────────────────────────────────


class ModelInfo(BaseModel):
    name: str
    experiment_id: str
    latest_run_id: str | None = None
    latest_run_time: str | None = None
    deploy_mode: str | None = None
    registered_model_name: str | None = None
    endpoint_name: str | None = None
    node_count: int | None = None
    node_types: list[str] = []
    has_graph_def: bool = False
    experiment_url: str = ""


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    workspace_url: str = ""
