from __future__ import annotations

import logging
from typing import Any

from ..auth import get_data_client
from ..tools import _get_mcp_client, _genie_mcp_url, _mcp_discover_and_call, _run_mcp_in_thread, _use_sdk_path
from .base import BaseNode, NodeConfigField, resolve_state
from . import register

logger = logging.getLogger(__name__)

MAX_ROWS = 50


def _format_genie_mcp_content(payload: dict) -> str:
    """Render the managed Genie MCP COMPLETED payload as human-readable text.

    Schema: content.{textAttachments[], queryAttachments[{query, description,
    statement_response{...}}], suggestedQuestions[]}.
    """
    content = payload.get("content") or {}
    parts: list[str] = []

    for txt in content.get("textAttachments", []) or []:
        if isinstance(txt, str) and txt.strip():
            parts.append(txt)
        elif isinstance(txt, dict):
            t = txt.get("content") or txt.get("text")
            if t:
                parts.append(t)

    for qa in content.get("queryAttachments", []) or []:
        # Skip qa.description — Genie restates the user's question, which
        # duplicates the textAttachment answer above without adding info.
        if qa.get("query"):
            parts.append(f"```sql\n{qa['query']}\n```")
        stmt = qa.get("statement_response") or {}
        result = (stmt.get("result") or {})
        manifest = (stmt.get("manifest") or {})
        rows = result.get("data_array") or result.get("data_typed_array") or []
        cols = [c.get("name") or f"col_{i}" for i, c in
                enumerate((manifest.get("schema") or {}).get("columns", []))]

        def _row_cells(row):
            # Managed Genie MCP returns rows as {"values": [...]} per row;
            # the SDK returns plain lists. Handle either.
            if isinstance(row, dict):
                vals = row.get("values")
                return vals if isinstance(vals, list) else list(row.values())
            return list(row) if not isinstance(row, str) else [row]

        def _unwrap_typed(v):
            # Managed MCP wraps each cell as {"string_value": ...} /
            # {"number_value": ...} / {"boolean_value": ...} / etc. Unwrap
            # to the inner scalar so str() doesn't render the dict repr.
            if isinstance(v, dict) and len(v) == 1:
                key = next(iter(v))
                if key.endswith("_value"):
                    return v[key]
            return v

        # Escape pipes so cell text doesn't break the markdown table row.
        # Underscores are fine — the renderer's inline pass only converts
        # `_word_` pairs, not bare identifiers like patient_id.
        def _md_cell(v):
            v = _unwrap_typed(v)
            if v is None:
                return ""
            return str(v).replace("|", "\\|")

        if rows:
            display = rows[:MAX_ROWS]
            if cols:
                header = "| " + " | ".join(_md_cell(c) for c in cols) + " |"
                sep = "| " + " | ".join("---" for _ in cols) + " |"
                body = ["| " + " | ".join(_md_cell(v) for v in _row_cells(row)) + " |"
                        for row in display]
                parts.append("\n".join([header, sep, *body]))
            total = manifest.get("total_row_count", len(rows))
            if len(rows) > MAX_ROWS:
                parts.append(f"_Showing {MAX_ROWS} of {total} rows_")

    return "\n\n".join(parts).strip()


@register
class GenieNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "genie"

    @property
    def display_name(self) -> str:
        return "Genie Room"

    @property
    def description(self) -> str:
        return "Query a Databricks Genie Room for structured data answers."

    @property
    def category(self) -> str:
        return "retrieval"

    @property
    def icon(self) -> str:
        return "database"

    @property
    def color(self) -> str:
        return "#f59e0b"

    @property
    def tool_compatible(self) -> bool:
        return True

    @property
    def config_fields(self) -> list[NodeConfigField]:
        return [
            NodeConfigField(
                name="question_from",
                label="Question from",
                field_type="state_variable",
                default="input",
            ),
            NodeConfigField(
                name="room_id",
                label="Genie Space",
                field_type="searchable_select",
                fetch_endpoint="/api/discover/genie-spaces",
                placeholder="01efg...",
                help_text="Select a Genie space or enter an ID manually.",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        query = resolve_state(state, config.get("question_from", "input"))
        room_id = config.get("room_id", "")

        if not room_id and not config.get("mcp_server_url"):
            return {writes_to: "Error: no Genie Room ID configured."}
        if not query:
            return {writes_to: "Error: no question provided."}

        if _use_sdk_path():
            return self._execute_sdk(config, writes_to, query, room_id)
        return self._execute_mcp(config, writes_to, query, room_id)

    def _execute_sdk(
        self, config: dict, writes_to: str, query: str, room_id: str,
    ) -> dict[str, Any]:
        """Direct SDK path — used by serving endpoints."""
        import time
        from databricks.sdk.service.dashboards import MessageStatus

        # Manual start + poll so a FAILED status surfaces the Genie-side error
        # message instead of being swallowed by SDK's generic OperationFailed.
        try:
            w = get_data_client()
            waiter = w.genie.start_conversation(room_id, query)
            conv_id, msg_id = waiter.conversation_id, waiter.message_id
            deadline = time.monotonic() + 300
            message = w.genie.get_message(room_id, conv_id, msg_id)
            terminal = {MessageStatus.COMPLETED, MessageStatus.FAILED, MessageStatus.CANCELLED}
            while message.status not in terminal and time.monotonic() < deadline:
                time.sleep(2)
                message = w.genie.get_message(room_id, conv_id, msg_id)
        except Exception as exc:
            error_detail = getattr(exc, "message", str(exc))
            logger.exception("Genie SDK call failed (space=%s)", room_id)
            return {writes_to: f"Genie API error: {error_detail}"}

        if message.status != MessageStatus.COMPLETED:
            error_text = message.error.message if message.error else f"status={message.status}"
            logger.error("Genie message did not complete (space=%s, status=%s, error=%s)",
                         room_id, message.status, error_text)
            return {writes_to: f"Genie error: {error_text}"}

        parts: list[str] = []
        for attachment in message.attachments or []:
            if attachment.text and attachment.text.content:
                parts.append(attachment.text.content)
            if attachment.query and attachment.attachment_id:
                try:
                    result = w.genie.get_message_attachment_query_result(
                        room_id, message.conversation_id,
                        message.message_id, attachment.attachment_id,
                    )
                    parts.append(self._format_query_result(attachment.query, result))
                except Exception as exc:
                    logger.exception("Failed to fetch Genie query result")
                    parts.append(f"(failed to fetch query result: {exc})")

        return {writes_to: "\n\n".join(parts) if parts else "(Genie returned no content)"}

    @staticmethod
    def _format_query_result(query_attachment, result_response) -> str:
        """Format a Genie query attachment and its results as readable text."""
        parts: list[str] = []
        if query_attachment.description:
            parts.append(query_attachment.description)
        if query_attachment.query:
            parts.append(f"```sql\n{query_attachment.query}\n```")

        stmt = result_response.statement_response if result_response else None
        if not stmt or not stmt.result or not stmt.result.data_array:
            parts.append("(no result rows)")
            return "\n\n".join(parts)

        columns = []
        if stmt.manifest and stmt.manifest.schema and stmt.manifest.schema.columns:
            columns = [c.name or f"col_{i}" for i, c in enumerate(stmt.manifest.schema.columns)]

        rows = stmt.result.data_array
        display_rows = rows[:MAX_ROWS]
        if columns:
            header = "| " + " | ".join(columns) + " |"
            sep = "| " + " | ".join("---" for _ in columns) + " |"
            table_rows = [
                "| " + " | ".join(str(v) if v is not None else "" for v in row) + " |"
                for row in display_rows
            ]
            parts.append("\n".join([header, sep, *table_rows]))

        total = stmt.manifest.total_row_count if stmt.manifest else len(rows)
        if len(rows) > MAX_ROWS:
            parts.append(f"_Showing {MAX_ROWS} of {total} rows_")
        return "\n\n".join(parts)

    def _execute_mcp(
        self, config: dict, writes_to: str, query: str, room_id: str,
    ) -> dict[str, Any]:
        """MCP path — used by the app preview.

        The managed Genie MCP server exposes two tools per space:
        ``query_space_<id>`` initiates a query but returns immediately with
        a non-terminal status, and ``poll_response_<id>`` is used to poll
        until the message reaches a terminal state. We chain them here.
        """
        import json as _json
        import time

        TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

        def _parse(text: str) -> dict:
            try:
                return _json.loads(text)
            except (ValueError, TypeError):
                return {}

        try:
            url = config.get("mcp_server_url") or _genie_mcp_url(room_id)
            client = _get_mcp_client(url)
            # 1) Kick off the query (picks first tool = query_space_<id>)
            raw = _run_mcp_in_thread(
                _mcp_discover_and_call, url, client, {"query": str(query)},
            )
            payload = _parse(raw)
            status = str(payload.get("status", "")).upper()

            # 2) Poll until terminal
            poll_tool = f"poll_response_{room_id}"
            deadline = time.monotonic() + 300
            while status and status not in TERMINAL and time.monotonic() < deadline:
                time.sleep(2)
                client = _get_mcp_client(url)
                raw = _run_mcp_in_thread(
                    _mcp_discover_and_call, url, client,
                    {
                        "conversation_id": payload.get("conversationId", ""),
                        "message_id": payload.get("messageId", ""),
                    },
                    poll_tool,
                )
                payload = _parse(raw) or payload
                status = str(payload.get("status", "")).upper()
        except Exception as exc:
            logger.exception("Genie MCP call failed (room=%s)", room_id)
            return {writes_to: f"Genie error: {exc}"}

        if status and status != "COMPLETED":
            err = payload.get("error") or f"status={status}"
            return {writes_to: f"Genie error: {err}"}

        return {writes_to: _format_genie_mcp_content(payload) or raw}
