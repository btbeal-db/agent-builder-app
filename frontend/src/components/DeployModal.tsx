import { useState, useCallback } from "react";
import { validateGraph, deployGraphStream } from "../api";
import type { GraphDef, StateFieldDef, DeployMode, DeployStepName, DeployStepStatus, DeployEvent } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  stateFieldsRef: React.RefObject<StateFieldDef[]>;
  onClose: () => void;
  defaultExperimentPath?: string;
  onGoToSetup?: () => void;
}

type Phase = "form" | "deploying" | "done" | "error";

interface StepState {
  status: DeployStepStatus;
  message: string;
}

const STEP_NAMES: DeployStepName[] = ["validate", "log_model", "register_model", "create_endpoint"];

const STEP_LABELS: Record<string, string> = {
  validate: "Validate Graph",
  log_model: "Log Model to MLflow",
  register_model: "Register in Unity Catalog",
  create_endpoint: "Create Serving Endpoint",
};

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

const MODE_LABELS: Record<DeployMode, string> = {
  full: "Log, Register & Deploy",
  log_and_register: "Log & Register Only",
  log_only: "Log Only",
};

const MODE_DESCRIPTIONS: Record<DeployMode, string> = {
  full: "Log model, register in Unity Catalog, and create a serving endpoint.",
  log_and_register: "Log model and register in Unity Catalog. No serving endpoint.",
  log_only: "Log model to MLflow experiment only. No registration or endpoint.",
};

function StepIcon({ status }: { status: DeployStepStatus }) {
  switch (status) {
    case "running":
      return <span className="deploy-spinner-sm" />;
    case "done":
      return <span className="deploy-step-check">&#10003;</span>;
    case "error":
      return <span className="deploy-step-cross">&#10007;</span>;
    case "skipped":
      return <span className="deploy-step-dash">&mdash;</span>;
    default:
      return <span className="deploy-step-pending">&#9675;</span>;
  }
}

export default function DeployModal({ graphGetter, stateFieldsRef, onClose, defaultExperimentPath, onGoToSetup }: Props) {
  const [modelName, setModelName] = useState("");
  const [experimentName, setExperimentName] = useState("");
  // The full experiment path: base folder from setup + user-provided experiment name.
  // If no setup folder, experimentName is the full path (fallback for manual entry).
  const experimentPath = defaultExperimentPath
    ? (experimentName ? `${defaultExperimentPath.replace(/\/+$/, "")}/${experimentName}` : "")
    : experimentName;
  const [lakebaseConnString, setLakebaseConnString] = useState("");
  const [deployMode, setDeployMode] = useState<DeployMode>("full");
  const [phase, setPhase] = useState<Phase>("form");
  const [steps, setSteps] = useState<Record<string, StepState>>({});
  const [resultData, setResultData] = useState<DeployEvent["data"]>({});
  const [errorMsg, setErrorMsg] = useState("");

  const isConversational = hasConversationalNode(graphGetter);
  const needsLakebase = isConversational && !lakebaseConnString.trim() && deployMode === "full";
  const needsModelName = deployMode !== "log_only";

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

    // Initialize steps
    const initial: Record<string, StepState> = {};
    for (const name of STEP_NAMES) {
      initial[name] = { status: "pending", message: "" };
    }
    setSteps(initial);
    setPhase("deploying");
    setErrorMsg("");

    let receivedTerminal = false;

    try {
      await deployGraphStream(
        {
          graph,
          model_name: modelName,
          experiment_path: experimentPath,
          lakebase_conn_string: lakebaseConnString,
          deploy_mode: deployMode,
        },
        (event: DeployEvent) => {
          if (event.step === "complete") {
            receivedTerminal = true;
            setResultData(event.data ?? {});
            setPhase("done");
            return;
          }
          setSteps((prev) => ({
            ...prev,
            [event.step]: {
              status: event.status as DeployStepStatus,
              message: event.message,
            },
          }));
          if (event.status === "error") {
            receivedTerminal = true;
            setErrorMsg(event.message);
            setPhase("error");
          }
        },
      );

      // Stream ended without a terminal event — treat as unexpected error
      if (!receivedTerminal) {
        setErrorMsg("Connection to server closed unexpectedly.");
        setPhase("error");
      }
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Connection error");
      setPhase("error");
    }
  }, [graphGetter, stateFieldsRef, modelName, experimentPath, lakebaseConnString, deployMode]);

  const doneMessage = deployMode === "full"
    ? "Agent deployed successfully!"
    : deployMode === "log_and_register"
      ? "Model registered successfully!"
      : "Model logged successfully!";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card deploy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h1>Deploy Agent</h1>
          <p>Package your graph as an MLflow model and optionally register and deploy it.</p>
        </div>

        {phase === "form" && (
          <div className="modal-body">
            <div className="deploy-form">
              <label className="deploy-label">
                Deploy Mode
                <select
                  className="deploy-input"
                  value={deployMode}
                  onChange={(e) => setDeployMode(e.target.value as DeployMode)}
                >
                  {(Object.keys(MODE_LABELS) as DeployMode[]).map((mode) => (
                    <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>
                  ))}
                </select>
                <span className="deploy-hint">{MODE_DESCRIPTIONS[deployMode]}</span>
              </label>

              <label className="deploy-label">
                Experiment Path
                {defaultExperimentPath ? (
                  <div className="deploy-experiment-path">
                    <span className="deploy-experiment-prefix">{defaultExperimentPath}/</span>
                    <input
                      type="text"
                      className="deploy-input deploy-experiment-name"
                      placeholder="my-experiment"
                      value={experimentName}
                      onChange={(e) => setExperimentName(e.target.value)}
                    />
                  </div>
                ) : (
                  <>
                    <input
                      type="text"
                      className="deploy-input"
                      placeholder="/Users/your.email@company.com/folder/experiment"
                      value={experimentName}
                      onChange={(e) => setExperimentName(e.target.value)}
                    />
                    {onGoToSetup && (
                      <span className="deploy-error-hint">
                        No experiment directory configured.{" "}
                        <button className="btn-link" onClick={onGoToSetup}>
                          Go to Setup
                        </button>{" "}
                        to configure your MLflow experiment directory.
                      </span>
                    )}
                  </>
                )}
                <span className="deploy-hint">
                  The experiment will be created inside your setup folder.
                </span>
              </label>

              {needsModelName && (
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
              )}

              {deployMode === "full" && (
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
              )}
            </div>

            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="btn btn-primary"
                disabled={
                  !experimentPath ||
                  (needsModelName && !modelName) ||
                  needsLakebase
                }
                onClick={handleDeploy}
              >
                {MODE_LABELS[deployMode]}
              </button>
            </div>
          </div>
        )}

        {phase === "deploying" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              {STEP_NAMES.map((name) => {
                const s = steps[name];
                if (!s) return null;
                return (
                  <div key={name} className={`deploy-step deploy-step--${s.status}`}>
                    <span className="deploy-step-icon">
                      <StepIcon status={s.status} />
                    </span>
                    <div className="deploy-step-text">
                      <span className="deploy-step-label">{STEP_LABELS[name]}</span>
                      {s.message && <span className="deploy-step-msg">{s.message}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {phase === "done" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              {STEP_NAMES.map((name) => {
                const s = steps[name];
                if (!s) return null;
                return (
                  <div key={name} className={`deploy-step deploy-step--${s.status}`}>
                    <span className="deploy-step-icon">
                      <StepIcon status={s.status} />
                    </span>
                    <div className="deploy-step-text">
                      <span className="deploy-step-label">{STEP_LABELS[name]}</span>
                      {s.message && <span className="deploy-step-msg">{s.message}</span>}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="deploy-success">
              <p>{doneMessage}</p>
              {resultData?.endpoint_url && (
                <label className="deploy-label">
                  Endpoint URL
                  <input
                    type="text"
                    className="deploy-input"
                    readOnly
                    value={resultData.endpoint_url}
                    onClick={(e) => (e.target as HTMLInputElement).select()}
                  />
                </label>
              )}
              {resultData?.model_version && (
                <p className="deploy-meta">Model version: {resultData.model_version}</p>
              )}
              {resultData?.run_id && (
                <p className="deploy-meta">MLflow run: {resultData.run_id}</p>
              )}
            </div>
            <div className="deploy-actions">
              {resultData?.endpoint_url && (
                <button
                  className="btn btn-secondary"
                  onClick={() => navigator.clipboard.writeText(resultData.endpoint_url!)}
                >
                  Copy URL
                </button>
              )}
              <button className="btn btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              {STEP_NAMES.map((name) => {
                const s = steps[name];
                if (!s) return null;
                return (
                  <div key={name} className={`deploy-step deploy-step--${s.status}`}>
                    <span className="deploy-step-icon">
                      <StepIcon status={s.status} />
                    </span>
                    <div className="deploy-step-text">
                      <span className="deploy-step-label">{STEP_LABELS[name]}</span>
                      {s.message && <span className="deploy-step-msg">{s.message}</span>}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="deploy-error">
              <p>Deployment failed</p>
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
