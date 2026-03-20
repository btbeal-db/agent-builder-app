import { Handle, Position, useReactFlow } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useStateVars } from "../../StateContext";

export const ICON_MAP: Record<string, string> = {
  brain: "\u{1F9E0}",
  search: "\u{1F50D}",
  database: "\u{1F4CA}",
  "git-branch": "\u{1F500}",
  puzzle: "\u{1F9E9}",
};

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

function routeHandleId(route: RouteEntry): string {
  return route.match_value || route.label || "default";
}

export default function AgentNode({ id, data, selected }: NodeProps) {
  const { setNodes } = useReactFlow();
  const stateVarNames = useStateVars();

  const color = (data.color as string) ?? "#6366f1";
  const icon = ICON_MAP[(data.icon as string) ?? "puzzle"] ?? "\u{1F9E9}";
  const displayName = (data.display_name as string) ?? "Node";
  const isRouter = (data.is_router as boolean) ?? false;
  const writesTo = (data.writes_to as string) ?? "";

  const config = (data.config ?? {}) as Record<string, unknown>;
  const routes = isRouter ? parseRoutes(config) : [];

  const handleWritesToChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    e.stopPropagation();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, writes_to: e.target.value } } : n
      )
    );
  };

  return (
    <div className={`agent-node${selected ? " selected" : ""}${isRouter ? " agent-node-router" : ""}`}>
      <Handle type="target" position={Position.Top} />

      <div className="agent-node-header" style={{ background: color }}>
        <span>{icon}</span>
        <span>{displayName}</span>
      </div>

      <div className="agent-node-body">
        {!isRouter && (
          <div className="agent-node-writes-to">
            <span className="writes-to-label">updates</span>
            <select
              className="writes-to-select"
              value={writesTo}
              onChange={handleWritesToChange}
              onClick={(e) => e.stopPropagation()}
            >
              <option value="">select field...</option>
              {stateVarNames.filter((v) => v !== "user_input").map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
        )}

        {/* Router: each route is a row with a labeled handle on the right */}
        {isRouter && routes.length > 0 && (
          <div className="router-outputs">
            {routes.map((route, i) => {
              const isFallback = i === routes.length - 1 && routes.length > 1 && !route.match_value;
              return (
                <div key={routeHandleId(route)} className="router-output-row">
                  <span className={`router-output-label${isFallback ? " router-output-fallback" : ""}`}>
                    {route.label || route.match_value || "?"}{isFallback ? " (fallback)" : ""}
                  </span>
                  <Handle
                    type="source"
                    position={Position.Right}
                    id={routeHandleId(route)}
                    className="router-output-handle"
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Non-router: single source handle at the bottom */}
      {!isRouter && <Handle type="source" position={Position.Bottom} />}
    </div>
  );
}
