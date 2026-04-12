from __future__ import annotations

import logging
from typing import Any

from databricks.sdk.service.dashboards import MessageStatus

from ..auth import get_workspace_client

from .base import BaseNode, NodeConfigField, resolve_state
from . import register

logger = logging.getLogger(__name__)

MAX_ROWS = 50


def _format_query_result(
    query_attachment,
    result_response,
) -> str:
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
    truncated = len(rows) > MAX_ROWS
    display_rows = rows[:MAX_ROWS]

    if columns:
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join("---" for _ in columns) + " |"
        table_rows = [
            "| " + " | ".join(str(v) if v is not None else "" for v in row) + " |"
            for row in display_rows
        ]
        parts.append("\n".join([header, separator, *table_rows]))
    else:
        for row in display_rows:
            parts.append(", ".join(str(v) if v is not None else "" for v in row))

    total = stmt.manifest.total_row_count if stmt.manifest else len(rows)
    if truncated:
        parts.append(f"_Showing {MAX_ROWS} of {total} rows_")

    return "\n\n".join(parts)


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
    def default_field_template(self) -> dict[str, str] | None:
        return {"name": "genie_result", "type": "str", "description": "Genie query result"}

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
                label="Genie Room ID",
                placeholder="01efg...",
                help_text="Returns a natural language summary with supporting data. Optionally chain into an LLM node for further reasoning.",
            ),
        ]

    def execute(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        writes_to = config.get("_writes_to", "")
        query = resolve_state(state, config.get("question_from", "input"))
        space_id = config.get("room_id", "")

        if not space_id:
            return {
                writes_to: "Error: no Genie Room ID configured.",
                "messages": [{"role": "system", "content": "Genie: missing room_id.", "node": "genie"}],
            }
        if not query:
            return {
                writes_to: "Error: no question provided.",
                "messages": [{"role": "system", "content": "Genie: empty question.", "node": "genie"}],
            }

        try:
            w = get_workspace_client()
            message = w.genie.start_conversation_and_wait(space_id, query)
        except Exception as exc:
            # If the wait failed, try to get more detail from the message
            error_detail = str(exc)
            if hasattr(exc, 'message'):
                error_detail = exc.message
            logger.exception(
                "Genie API call failed (space=%s, query=%s): %s",
                space_id, query[:100], error_detail,
            )
            return {
                writes_to: f"Genie API error: {error_detail}",
                "messages": [{"role": "system", "content": f"Genie error: {error_detail}", "node": "genie"}],
            }

        if message.status == MessageStatus.FAILED:
            error_text = message.error.message if message.error else "Unknown error"
            return {
                writes_to: f"Genie error: {error_text}",
                "messages": [{"role": "system", "content": f"Genie failed: {error_text}", "node": "genie"}],
            }

        # Parse attachments
        parts: list[str] = []
        for attachment in message.attachments or []:
            if attachment.text and attachment.text.content:
                parts.append(attachment.text.content)

            if attachment.query and attachment.attachment_id:
                try:
                    result = w.genie.get_message_attachment_query_result(
                        space_id,
                        message.conversation_id,
                        message.message_id,
                        attachment.attachment_id,
                    )
                    parts.append(_format_query_result(attachment.query, result))
                except Exception as exc:
                    logger.exception("Failed to fetch Genie query result")
                    parts.append(f"(failed to fetch query result: {exc})")

        result_text = "\n\n".join(parts) if parts else "(Genie returned no content)"

        return {
            writes_to: result_text,
            "messages": [
                {"role": "system", "content": f"[Genie Room {space_id}]\n{result_text}", "node": "genie"},
            ],
        }
