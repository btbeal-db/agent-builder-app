"""MLflow ResponsesAgent wrapper for deploying AgentSweet graphs via Model Serving.

This file is used as a "models from code" entry point by MLflow.
It must be importable standalone (no relative imports) because MLflow
loads it directly via the file path.

The actual load/compile/run/stream logic lives in ``backend.agent_runtime``,
which is shared with the "agents on apps" deploy path (``backend.app_deploy``).
This class is a thin ``ResponsesAgent`` adapter over that shared runtime.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

# Ensure the backend package is importable when MLflow loads this file
# from the code/ directory in the serving container.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from backend.agent_runtime import (
    CompiledAgent,
    load_agent_from_file,
    run_agent,
    stream_agent,
)


class AgentGraphModel(ResponsesAgent):
    """Wraps a compiled LangGraph agent as an MLflow ResponsesAgent for serving."""

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        """Load the graph definition and compile with optional checkpointer."""
        self._agent: CompiledAgent = load_agent_from_file(context.artifacts["graph_def"])

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """Run the agent graph synchronously and return the full response."""
        return run_agent(self._agent, request)

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """Stream the agent graph, yielding token-level deltas from LLM nodes."""
        yield from stream_agent(self._agent, request)


# Register this model for MLflow "models from code" loading
mlflow.models.set_model(AgentGraphModel())
