import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useStateVars } from "../../StateContext";

export default function SentinelNode({ data }: NodeProps) {
  const stateVarNames = useStateVars();
  const kind = (data.kind as string) ?? "start"; // "start" | "end"
  const isStart = kind === "start";

  return (
    <div className={`sentinel-node sentinel-${kind}`}>
      {!isStart && <Handle type="target" position={Position.Top} />}

      <div className="sentinel-label">{isStart ? "START" : "END"}</div>

      {isStart ? (
        <div className="sentinel-fields">
          <span className="sentinel-field">user_input</span>
        </div>
      ) : (
        <div className="sentinel-fields">
          {stateVarNames.map((v) => (
            <span key={v} className="sentinel-field">{v}</span>
          ))}
        </div>
      )}

      {isStart && <Handle type="source" position={Position.Bottom} />}
    </div>
  );
}
