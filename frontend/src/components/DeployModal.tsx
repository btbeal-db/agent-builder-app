import { useState, useCallback } from "react";
import { validateGraph, deployGraph } from "../api";
import type { GraphDef, StateFieldDef } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  stateFieldsRef: React.RefObject<StateFieldDef[]>;
  onClose: () => void;
}

type DeployStage = "form" | "validating" | "logging" | "deploying" | "done" | "error";

function preflight(graphGetter: (() => GraphDef) | null, stateFields: StateFieldDef[]): string | null {
  if (!graphGetter) return "The graph hasn't loaded yet.";

  let graph: GraphDef;
  try {
    graph = graphGetter();
  } catch {
    return "Failed to read the graph. Make sure you have nodes on the canvas.";
  }

  if (!graph.nodes || graph.nodes.length === 0) {
    return "Your graph has no nodes. Drag some components onto the canvas first.";
  }

  const hasStart = graph.edges.some((e) => e.source === "__start__");
  const hasEnd = graph.edges.some((e) => e.target === "__end__");
  if (!hasStart) return "Connect the START node to your first node.";
  if (!hasEnd) return "Connect your last node to the END node.";

  for (const node of graph.nodes) {
    if (node.type === "router") continue;
    if (!node.writes_to) {
      return `Node "${node.id}" doesn't have a target state field selected.`;
    }
  }
  return null;
}

/** Check if any LLM node has conversational mode enabled. */
function hasConversationalNode(graphGetter: (() => GraphDef) | null): boolean {
  if (!graphGetter) return false;
  try {
    const graph = graphGetter();
    return graph.nodes.some(
      (n) => n.type === "llm" && String(n.config.conversational).toLowerCase() === "true"
    );
  } catch {
    return false;
  }
}

export default function DeployModal({ graphGetter, stateFieldsRef, onClose }: Props) {
  const [modelName, setModelName] = useState("");
  const [experimentPath, setExperimentPath] = useState("");
  const [lakebaseConnString, setLakebaseConnString] = useState("");
  const [stage, setStage] = useState<DeployStage>("form");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [modelVersion, setModelVersion] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const isConversational = hasConversationalNode(graphGetter);
  const needsLakebase = isConversational && !lakebaseConnString.trim();

  const handleDeploy = useCallback(async () => {
    const stateFields = stateFieldsRef.current ?? [];
    const err = preflight(graphGetter, stateFields);
    if (err) {
      setErrorMsg(err);
      setStage("error");
      return;
    }

    const graph = graphGetter!();
    graph.state_fields = stateFields;

    setStage("validating");
    const validation = await validateGraph(graph);
    if (!validation.valid) {
      setErrorMsg(validation.errors.join("\n"));
      setStage("error");
      return;
    }

    setStage("logging");

    try {
      const result = await deployGraph({
        graph,
        model_name: modelName,
        experiment_path: experimentPath,
        lakebase_conn_string: lakebaseConnString,
      });

      if (result.success) {
        setEndpointUrl(result.endpoint_url);
        setModelVersion(result.model_version);
        setStage("done");
      } else {
        setErrorMsg(result.error || "Deployment failed");
        setStage("error");
      }
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Unexpected error");
      setStage("error");
    }
  }, [graphGetter, stateFieldsRef, modelName, experimentPath, lakebaseConnString]);

  const stageLabel: Record<string, string> = {
    validating: "Validating graph...",
    logging: "Logging model & creating endpoint...",
    deploying: "Creating serving endpoint...",
  };

  const isWorking = stage === "validating" || stage === "logging" || stage === "deploying";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card deploy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h1>Deploy Agent</h1>
          <p>Package your graph as an MLflow model and deploy to a Databricks serving endpoint.</p>
        </div>

        {stage === "form" && (
          <div className="modal-body">
            <div className="deploy-form">
              <label className="deploy-label">
                Model Name (Unity Catalog)
                <input
                  type="text"
                  className="deploy-input"
                  placeholder="catalog.schema.model_name"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                />
              </label>

              <label className="deploy-label">
                Experiment Path
                <input
                  type="text"
                  className="deploy-input"
                  placeholder="/Users/your.email@company.com/agent-experiment"
                  value={experimentPath}
                  onChange={(e) => setExperimentPath(e.target.value)}
                />
              </label>

              <label className="deploy-label">
                Lakebase Connection String
                <input
                  type="text"
                  className="deploy-input"
                  placeholder="postgresql://user:pass@host:port/db"
                  value={lakebaseConnString}
                  onChange={(e) => setLakebaseConnString(e.target.value)}
                />
                <span className="deploy-hint">
                  {isConversational
                    ? "Required — your graph has conversational LLM nodes."
                    : "Optional. Enables multi-turn conversation memory."}
                </span>
                {needsLakebase && (
                  <span className="deploy-error-hint">
                    Conversational agents require a Lakebase connection to persist
                    conversation history. Model Serving is stateless — without it,
                    multi-turn will not work.
                  </span>
                )}
              </label>
            </div>

            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="btn btn-primary"
                disabled={!modelName || !experimentPath || needsLakebase}
                onClick={handleDeploy}
              >
                Deploy
              </button>
            </div>
          </div>
        )}

        {isWorking && (
          <div className="modal-body deploy-status">
            <div className="deploy-spinner" />
            <p>{stageLabel[stage]}</p>
          </div>
        )}

        {stage === "done" && (
          <div className="modal-body">
            <div className="deploy-success">
              <p>Agent deployed successfully!</p>
              <label className="deploy-label">
                Endpoint URL
                <input
                  type="text"
                  className="deploy-input"
                  readOnly
                  value={endpointUrl}
                  onClick={(e) => (e.target as HTMLInputElement).select()}
                />
              </label>
              <p className="deploy-meta">Model version: {modelVersion}</p>
            </div>
            <div className="deploy-actions">
              <button
                className="btn btn-secondary"
                onClick={() => navigator.clipboard.writeText(endpointUrl)}
              >
                Copy URL
              </button>
              <button className="btn btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}

        {stage === "error" && (
          <div className="modal-body">
            <div className="deploy-error">
              <p>Deployment failed</p>
              <pre>{errorMsg}</pre>
            </div>
            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={() => setStage("form")}>Back</button>
              <button className="btn btn-primary" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
