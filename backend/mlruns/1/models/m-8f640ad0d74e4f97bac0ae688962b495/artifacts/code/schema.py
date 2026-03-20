from __future__ import annotations

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
        StateFieldDef(name="user_input", type="str", description="The user's initial message")
    ]

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


class PreviewResponse(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    execution_trace: list[dict[str, Any]] = []
    state: dict[str, Any] = {}


class ExportResponse(BaseModel):
    success: bool
    code: str = ""
    error: str | None = None


class DeployRequest(BaseModel):
    graph: GraphDef
    model_name: str  # Unity Catalog path: catalog.schema.model_name
    experiment_path: str  # MLflow experiment: /Users/email/experiment
    lakebase_conn_string: str = ""  # Lakebase Postgres connection URL


class DeployResponse(BaseModel):
    success: bool
    endpoint_url: str = ""
    model_version: str = ""
    error: str | None = None
