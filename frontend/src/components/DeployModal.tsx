import { useState, useCallback, useEffect, useRef } from "react";
import { fetchAppConfig, submitDeploy, pollDeployStatus } from "../api";
import type { DeployStatusResponse } from "../api";
import type { GraphDef, StateFieldDef, AppConfig } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  stateFieldsRef: React.RefObject<StateFieldDef[]>;
  onClose: () => void;
}

type Phase = "loading" | "form" | "deploying" | "done" | "error";

const DEPLOY_STEPS = [
  { key: "submit", label: "Submit Deploy Job" },
  { key: "job", label: "Log, Register & Create Endpoint" },
];

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
  const [lakebaseConnString, setLakebaseConnString] = useState("");
  const [phase, setPhase] = useState<Phase>("loading");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [stepStatus, setStepStatus] = useState<Record<string, "pending" | "running" | "done" | "error">>({
    submit: "pending",
    job: "pending",
  });
  const [resultData, setResultData] = useState<DeployStatusResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [endpointName, setEndpointName] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isConversational = hasConversationalNode(graphGetter);
  const needsLakebase = isConversational && !lakebaseConnString.trim();

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Fetch app config on mount
  useEffect(() => {
    fetchAppConfig()
      .then((cfg) => {
        setConfig(cfg);
        setPhase("form");
      })
      .catch((e) => {
        setErrorMsg(`Failed to load app config: ${e.message}`);
        setPhase("error");
      });
  }, []);

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

    setStepStatus({ submit: "running", job: "pending" });
    setPhase("deploying");
    setErrorMsg("");
    setEndpointName(modelName.replace(/_/g, "-"));

    try {
      // Step 1: Submit
      const resp = await submitDeploy({
        graph,
        model_name: modelName,
        lakebase_conn_string: lakebaseConnString,
      });

      setStepStatus({ submit: "done", job: "running" });

      // Step 2: Poll for completion
      const runId = resp.run_id;
      pollRef.current = setInterval(async () => {
        try {
          const status = await pollDeployStatus(runId);
          if (status.status === "success") {
            if (pollRef.current) clearInterval(pollRef.current);
            setStepStatus({ submit: "done", job: "done" });
            setResultData(status);
            setPhase("done");
          } else if (status.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            setStepStatus({ submit: "done", job: "error" });
            setErrorMsg(status.error || "Deploy job failed");
            setPhase("error");
          }
          // status === "running" → keep polling
        } catch {
          // Transient network error during poll — keep trying
        }
      }, 5000);
    } catch (e: unknown) {
      setStepStatus({ submit: "error", job: "pending" });
      setErrorMsg(e instanceof Error ? e.message : "Failed to submit deploy job");
      setPhase("error");
    }
  }, [graphGetter, stateFieldsRef, modelName, lakebaseConnString]);

  const configured = config && config.catalog && config.schema_name && config.deploy_job_id;

  function StepIcon({ status }: { status: string }) {
    switch (status) {
      case "running":
        return <span className="deploy-spinner-sm" />;
      case "done":
        return <span className="deploy-step-check">&#10003;</span>;
      case "error":
        return <span className="deploy-step-cross">&#10007;</span>;
      default:
        return <span className="deploy-step-pending">&#9675;</span>;
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card deploy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h1>Deploy Agent</h1>
          <p>Log, register, and deploy your agent as a Model Serving endpoint.</p>
        </div>

        {phase === "loading" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              <div className="deploy-step deploy-step--running">
                <span className="deploy-step-icon"><span className="deploy-spinner-sm" /></span>
                <div className="deploy-step-text">
                  <span className="deploy-step-label">Loading configuration...</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {phase === "form" && config && (
          <div className="modal-body">
            {!configured && (
              <div className="deploy-error" style={{ marginBottom: "1rem" }}>
                <p>App not configured for deployment</p>
                <pre>The app admin needs to set DEPLOY_CATALOG, DEPLOY_SCHEMA, and DEPLOY_JOB_ID environment variables.</pre>
              </div>
            )}

            <div className="deploy-form">
              {configured && (
                <div className="deploy-hint" style={{ marginBottom: "0.75rem", padding: "0.5rem 0.75rem", borderRadius: "6px", background: "rgba(255,255,255,0.05)" }}>
                  Models will be registered to <strong>{config.catalog}.{config.schema_name}</strong>
                </div>
              )}

              <label className="deploy-label">
                Agent Name
                <input
                  type="text"
                  className="deploy-input"
                  placeholder="my_agent"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                />
                <span className="deploy-hint">
                  {configured
                    ? `Registers as ${config.catalog}.${config.schema_name}.${modelName || "..."} and creates endpoint "${(modelName || "...").replace(/_/g, "-")}"`
                    : "Enter a name for your agent."}
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
                disabled={!modelName || !configured || needsLakebase}
                onClick={handleDeploy}
              >
                Deploy Agent
              </button>
            </div>
          </div>
        )}

        {phase === "deploying" && (
          <div className="modal-body">
            <div className="deploy-stepper">
              {DEPLOY_STEPS.map(({ key, label }) => (
                <div key={key} className={`deploy-step deploy-step--${stepStatus[key]}`}>
                  <span className="deploy-step-icon">
                    <StepIcon status={stepStatus[key]} />
                  </span>
                  <div className="deploy-step-text">
                    <span className="deploy-step-label">{label}</span>
                    {key === "job" && stepStatus[key] === "running" && (
                      <span className="deploy-step-msg">This may take a few minutes...</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {phase === "done" && resultData && (
          <div className="modal-body">
            <div className="deploy-stepper">
              {DEPLOY_STEPS.map(({ key, label }) => (
                <div key={key} className={`deploy-step deploy-step--done`}>
                  <span className="deploy-step-icon">
                    <span className="deploy-step-check">&#10003;</span>
                  </span>
                  <div className="deploy-step-text">
                    <span className="deploy-step-label">{label}</span>
                  </div>
                </div>
              ))}
            </div>

            <div className="deploy-success">
              <p>Agent deployed successfully!</p>
              {resultData.endpoint_url && (
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
              {resultData.model_version && (
                <p className="deploy-meta">Model version: {resultData.model_version}</p>
              )}
              {resultData.run_id && (
                <p className="deploy-meta">MLflow run: {resultData.run_id}</p>
              )}
              <p className="deploy-hint" style={{ marginTop: "0.5rem" }}>
                Note: The serving endpoint may take a few additional minutes to become ready.
              </p>
            </div>
            <div className="deploy-actions">
              {resultData.endpoint_url && (
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
              {DEPLOY_STEPS.map(({ key, label }) => (
                <div key={key} className={`deploy-step deploy-step--${stepStatus[key]}`}>
                  <span className="deploy-step-icon">
                    <StepIcon status={stepStatus[key]} />
                  </span>
                  <div className="deploy-step-text">
                    <span className="deploy-step-label">{label}</span>
                  </div>
                </div>
              ))}
            </div>

            <div className="deploy-error">
              <p>Deployment failed</p>
              <pre>{errorMsg}</pre>
            </div>
            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={() => {
                setPhase("form");
                setStepStatus({ submit: "pending", job: "pending" });
              }}>Back</button>
              <button className="btn btn-primary" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
