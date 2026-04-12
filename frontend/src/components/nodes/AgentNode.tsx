import { Handle, Position, useReactFlow, useUpdateNodeInternals } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useStateVars, useAddField, useStateFields } from "../../StateContext";
import { NodeIcon } from "../NodeIcon";
import ToolChip from "./ToolChip";
import InlineFieldCreator from "../InlineFieldCreator";
import type { AttachedTool } from "../../types";

interface RouteEntry {
  label: string;
  match_value: string;
}

function parseRoutes(config: Record<string, unknown>): RouteEntry[] {
  const raw = config.routes_json;
  if (Array.isArray(raw)) return raw as RouteEntry[];
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    } catch { /* ignore */ }
  }
  return [];
}

function routeHandleId(route: RouteEntry, index: number): string {
  return route.match_value || route.label || `route_${index}`;
}

export default function AgentNode({ id, data, selected }: NodeProps) {
  const { setNodes } = useReactFlow();
  const stateVarNames = useStateVars();
  const addField = useAddField();
  const currentFields = useStateFields();
  const [showNewField, setShowNewField] = useState(false);
  const updateNodeInternals = useUpdateNodeInternals();
  const nodeRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);

  const color = (data.color as string) ?? "#6366f1";
  const iconKey = (data.icon as string) ?? "puzzle";
  const displayName = (data.display_name as string) ?? "Node";
  const customName = (data.name as string) ?? "";
  const isRouter = (data.is_router as boolean) ?? false;
  const isLlm = (data.nodeType as string) === "llm";
  const writesTo = (data.writes_to as string) ?? "";

  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(customName || displayName);

  const config = (data.config ?? {}) as Record<string, unknown>;
  const routes = isRouter ? parseRoutes(config) : [];
  const tools = (data.tools ?? []) as AttachedTool[];

  const handleKey = useMemo(
    () => routes.map((r, i) => routeHandleId(r, i)).join("|"),
    [routes]
  );

  // Measure row positions to align handles with labels
  const [handleTops, setHandleTops] = useState<number[]>([]);

  useEffect(() => {
    if (!isRouter || !nodeRef.current) return;

    const measure = () => {
      const nodeRect = nodeRef.current!.getBoundingClientRect();
      const nodeHeight = nodeRect.height;
      if (nodeHeight === 0) return;

      const tops = rowRefs.current.map((row) => {
        if (!row) return 50;
        const rowRect = row.getBoundingClientRect();
        const rowCenter = rowRect.top + rowRect.height / 2 - nodeRect.top;
        return (rowCenter / nodeHeight) * 100;
      });
      setHandleTops(tops);
    };

    // Measure after layout settles
    const timer = setTimeout(measure, 20);
    return () => clearTimeout(timer);
  }, [isRouter, handleKey]);

  // Force React Flow to recalculate handle bounds after positions update
  useEffect(() => {
    if (isRouter && handleTops.length > 0) {
      const timer = setTimeout(() => updateNodeInternals(id), 50);
      return () => clearTimeout(timer);
    }
  }, [id, isRouter, handleTops, updateNodeInternals]);

  const handleWritesToChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    e.stopPropagation();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, writes_to: e.target.value } } : n
      )
    );
  };

  const commitName = () => {
    const trimmed = editValue.trim();
    setEditing(false);
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, name: trimmed || "" } } : n
      )
    );
  };

  const removeTool = useCallback(
    (toolId: string) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== id) return n;
          const updatedTools = ((n.data.tools ?? []) as AttachedTool[]).filter(
            (t) => t.id !== toolId
          );
          return {
            ...n,
            data: { ...n.data, tools: updatedTools },
          };
        })
      );
    },
    [id, setNodes]
  );

  const onToolClick = useCallback(
    (toolId: string) => {
      // Dispatch a custom event so the parent Canvas can open the tool config
      window.dispatchEvent(
        new CustomEvent("tool-chip-click", { detail: { nodeId: id, toolId } })
      );
    },
    [id]
  );

  // Tool drop zone handlers (for dropping tools from palette onto the LLM)
  const [dragOver, setDragOver] = useState(false);

  const handleToolDragOver = useCallback((e: React.DragEvent) => {
    const nodeType = e.dataTransfer.types.includes("application/agentbuilder-tool");
    if (!nodeType) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleToolDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleToolDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);

      const toolData = e.dataTransfer.getData("application/agentbuilder-tool");
      if (!toolData) return;

      try {
        const tool = JSON.parse(toolData) as AttachedTool;
        setNodes((nds) =>
          nds.map((n) => {
            if (n.id !== id) return n;
            const existing = (n.data.tools ?? []) as AttachedTool[];
            return {
              ...n,
              data: { ...n.data, tools: [...existing, tool] },
            };
          })
        );
      } catch { /* ignore bad data */ }
    },
    [id, setNodes]
  );

  return (
    <div
      ref={nodeRef}
      className={`agent-node${selected ? " selected" : ""}${isRouter ? " agent-node-router" : ""}${isLlm && tools.length > 0 ? " agent-node-with-tools" : ""}`}
    >
      <Handle type="target" position={Position.Top} />

      <div className="agent-node-header" style={{ background: color }}>
        <NodeIcon name={iconKey} size={15} />
        {editing ? (
          <input
            className="agent-node-name-input"
            value={editValue}
            autoFocus
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitName();
              if (e.key === "Escape") { setEditValue(customName || displayName); setEditing(false); }
            }}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span onDoubleClick={() => { setEditValue(customName || displayName); setEditing(true); }}>
            {customName || displayName}
          </span>
        )}
      </div>

      <div className="agent-node-body">
        {!isRouter && (
          <div className="agent-node-writes-to">
            <span className="writes-to-label">updates</span>
            <select
              className="writes-to-select"
              value={writesTo}
              onChange={(e) => {
                if (e.target.value === "__new__") {
                  setShowNewField(true);
                  return;
                }
                handleWritesToChange(e);
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <option value="">select field...</option>
              {stateVarNames.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
              <option value="__new__">+ New field...</option>
            </select>
            {showNewField && (
              <InlineFieldCreator
                existingNames={currentFields.map((f) => f.name)}
                onAdd={(field) => {
                  addField(field);
                  setShowNewField(false);
                  // Set writes_to to the new field
                  setNodes((nds) =>
                    nds.map((n) =>
                      n.id === id ? { ...n, data: { ...n.data, writes_to: field.name } } : n
                    )
                  );
                }}
                onCancel={() => setShowNewField(false)}
              />
            )}
          </div>
        )}

        {isRouter && routes.length > 0 && (
          <div className="router-outputs">
            {routes.map((route, i) => {
              const handleId = routeHandleId(route, i);
              const isFallback = i === routes.length - 1 && routes.length > 1 && !route.match_value;
              return (
                <div
                  key={handleId}
                  className="router-output-row"
                  ref={(el) => { rowRefs.current[i] = el; }}
                >
                  <span className={`router-output-label${isFallback ? " router-output-fallback" : ""}`}>
                    {route.label || route.match_value || handleId}{isFallback ? " (fallback)" : ""}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Tools drop zone — only for LLM nodes */}
      {isLlm && (
        <div
          className={`tools-zone${dragOver ? " tools-zone-active" : ""}${tools.length > 0 ? " tools-zone-has-tools" : ""}`}
          onDragOver={handleToolDragOver}
          onDragLeave={handleToolDragLeave}
          onDrop={handleToolDrop}
        >
          {tools.length === 0 && !dragOver && (
            <span className="tools-zone-hint">Drop tools here</span>
          )}
          {dragOver && (
            <span className="tools-zone-hint tools-zone-hint-active">Release to attach</span>
          )}
          {tools.map((tool) => (
            <ToolChip
              key={tool.id}
              tool={tool}
              onClick={() => onToolClick(tool.id)}
              onRemove={() => removeTool(tool.id)}
            />
          ))}
        </div>
      )}

      {/* Router source handles — positioned to align with their label rows */}
      {isRouter && routes.map((route, i) => {
        const handleId = routeHandleId(route, i);
        const top = handleTops[i] ?? ((i + 1) / (routes.length + 1)) * 100;
        return (
          <Handle
            key={`${handleKey}-${i}`}
            type="source"
            position={Position.Right}
            id={handleId}
            style={{ top: `${top}%` }}
          />
        );
      })}

      {!isRouter && <Handle type="source" position={Position.Bottom} />}
    </div>
  );
}
