import { useState, useCallback } from "react";
import { deployNotebook } from "../api";
import type { GraphDef, StateFieldDef, DeployNotebookResponse } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  stateFieldsRef: React.RefObject<StateFieldDef[]>;
  onClose: () => void;
}

type Phase = "form" | "submitting" | "done" | "error";

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
      (n) => n.type === "llm" && (
        String(n.config.include_message_history ?? n.config.conversational ?? "false").toLowerCase() === "true"
      )
    );
  } catch {
    return false;
  }
}

export default function DeployModal({ graphGetter, stateFieldsRef, onClose }: Props) {
  const [modelName, setModelName] = useState("");
  const [experimentPath, setExperimentPath] = useState("");
  const [lakebaseConnString, setLakebaseConnString] = useState("");
  const [notebookPath, setNotebookPath] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [result, setResult] = useState<DeployNotebookResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const isConversational = hasConversationalNode(graphGetter);

  const handleDeploy = useCallback(async () => {
    const stateFields = stateFieldsRef.current ?? [];
    const err = preflight(graphGetter, stateFields);
    if (err) {
      setErrorMsg(err);
      setPhase("error");
      return;
    }

    const graph = graphGetter!();
    graph.state_fields = stateFields;

    setPhase("submitting");
    setErrorMsg("");

    try {
      const resp = await deployNotebook({
        graph,
        model_name: modelName,
        experiment_path: experimentPath,
        lakebase_conn_string: lakebaseConnString,
        notebook_path: notebookPath,
      });
      if (resp.success) {
        setResult(resp);
        setPhase("done");
      } else {
        setErrorMsg(resp.error || "Unknown error");
        setPhase("error");
      }
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Connection error");
      setPhase("error");
    }
  }, [graphGetter, stateFieldsRef, modelName, experimentPath, lakebaseConnString, notebookPath]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card deploy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h1>Deploy Agent</h1>
          <p>Generate a deployment notebook that logs, registers, and deploys your agent.</p>
        </div>

        {phase === "form" && (
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
                Notebook Destination
                <input
                  type="text"
                  className="deploy-input"
                  placeholder="/Users/your.email@company.com/deploy_my_agent"
                  value={notebookPath}
                  onChange={(e) => setNotebookPath(e.target.value)}
                />
                <span className="deploy-hint">
                  Workspace path where the deployment notebook will be created.
                </span>
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
                {isConversational && !lakebaseConnString.trim() && (
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
                disabled={
                  !modelName ||
                  !experimentPath ||
                  !notebookPath ||
                  (isConversational && !lakebaseConnString.trim())
                }
                onClick={handleDeploy}
              >
                Generate Notebook
              </button>
            </div>
          </div>
        )}

        {phase === "submitting" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              <div className="deploy-step deploy-step--running">
                <span className="deploy-step-icon"><span className="deploy-spinner-sm" /></span>
                <div className="deploy-step-text">
                  <span className="deploy-step-label">Generating deployment notebook...</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {phase === "done" && result && (
          <div className="modal-body">
            <div className="deploy-success">
              <p>Deployment notebook created!</p>
              <p style={{ fontSize: "0.9rem", opacity: 0.8, marginTop: "0.5rem" }}>
                Open the notebook and <strong>Run All</strong> to log, register, and deploy your agent.
              </p>
              {result.notebook_url && (
                <a
                  href={result.notebook_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-primary"
                  style={{ display: "inline-block", marginTop: "1rem", textDecoration: "none" }}
                >
                  Open Notebook
                </a>
              )}
            </div>
            <div className="deploy-actions">
              <button className="btn btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="modal-body">
            <div className="deploy-error">
              <p>Failed to generate notebook</p>
              <pre>{errorMsg}</pre>
            </div>
            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={() => setPhase("form")}>Back</button>
              <button className="btn btn-primary" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
