import { useState, useCallback } from "react";
import { validateGraph, deployGraphStream, deployAppStream } from "../api";
import type { GraphDef, DeployMode, AuthMode, DeployTarget, DeployStepName, DeployStepStatus, DeployEvent } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  onClose: () => void;
  defaultExperimentPath?: string;
  onGoToSetup?: () => void;
}

type Phase = "form" | "deploying" | "done" | "error";

interface StepState {
  status: DeployStepStatus;
  message: string;
}

const STEP_NAMES_SERVING: DeployStepName[] = ["validate", "provision_lakebase", "log_model", "register_model", "create_endpoint"];
const STEP_NAMES_APP: DeployStepName[] = ["validate", "provision_lakebase", "generate_project", "upload_workspace_files", "create_app", "deploy_app"];

const STEP_LABELS: Record<string, string> = {
  validate: "Validate Graph",
  provision_lakebase: "Configure Lakebase",
  log_model: "Log Model to MLflow",
  register_model: "Register in Unity Catalog",
  create_endpoint: "Create Serving Endpoint",
  generate_project: "Generate App Project",
  upload_workspace_files: "Upload to Workspace",
  create_app: "Create Databricks App",
  deploy_app: "Deploy App",
};

function preflight(graphGetter: (() => GraphDef) | null): string | null {
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

  return null;
}

/** Whether the graph needs a persistent checkpointer (Lakebase) at serving time.
 *
 * Two cases require it:
 * - any LLM with ``conversational: true`` — message history must persist
 *   across turns
 * - any ``human_input`` node — the checkpoint is what lets the next request
 *   resume from the interrupt instead of restarting the graph
 *
 * Without one, the deployed endpoint silently restarts the graph on every
 * request because LangGraph's in-memory saver is per-worker.
 */
function requiresPersistence(graphGetter: (() => GraphDef) | null): boolean {
  if (!graphGetter) return false;
  try {
    const graph = graphGetter();
    return graph.nodes.some((n) => {
      if (n.type === "human_input") return true;
      if (n.type !== "llm") return false;
      const flag = n.config.include_message_history ?? n.config.conversational;
      return String(flag).toLowerCase() === "true";
    });
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

export default function DeployModal({ graphGetter, onClose, defaultExperimentPath, onGoToSetup }: Props) {
  const [modelName, setModelName] = useState("");
  const [experimentName, setExperimentName] = useState("");
  // The full experiment path: base folder from setup + user-provided experiment name.
  // If no setup folder, experimentName is the full path (fallback for manual entry).
  const experimentPath = defaultExperimentPath
    ? (experimentName ? `${defaultExperimentPath.replace(/\/+$/, "")}/${experimentName}` : "")
    : experimentName;
  const [pat, setPat] = useState("");
  const [deployMode, setDeployMode] = useState<DeployMode>("full");
  const [authMode, setAuthMode] = useState<AuthMode>("obo");
  // Deploy target. Model Serving is the default during the transition to
  // agents-on-apps; app deploy is offered as an opt-in "beta".
  const [deployTarget, setDeployTarget] = useState<DeployTarget>("model_serving");
  // App-deploy fields. workspace_path defaults to the setup folder.
  const [appName, setAppName] = useState("");
  const workspacePath = defaultExperimentPath ?? "";
  const [phase, setPhase] = useState<Phase>("form");
  const [steps, setSteps] = useState<Record<string, StepState>>({});
  const [resultData, setResultData] = useState<DeployEvent["data"]>({});
  const [errorMsg, setErrorMsg] = useState("");

  // Lakebase options: "create" | "existing" | "connstring" | "none"
  type LakebaseMode = "create" | "existing" | "connstring" | "none";
  const [lakebaseMode, setLakebaseMode] = useState<LakebaseMode>("create");
  const [lakebaseProjectId, setLakebaseProjectId] = useState("");
  const [lakebaseExistingProjectId, setLakebaseExistingProjectId] = useState("");
  const [lakebaseConnString, setLakebaseConnString] = useState("");

  const isApp = deployTarget === "app";
  const needsCheckpointer = requiresPersistence(graphGetter);
  const needsModelName = !isApp && deployMode !== "log_only";
  const STEP_NAMES = isApp ? STEP_NAMES_APP : STEP_NAMES_SERVING;

  // Lakebase is shown for app deploys and for the serving "full" mode.
  const showLakebase = isApp || deployMode === "full";

  const lakebaseValid = (() => {
    if (!showLakebase) return true;
    if (!needsCheckpointer) return true;
    switch (lakebaseMode) {
      case "create": return lakebaseProjectId.trim().length > 0;
      case "existing": return lakebaseExistingProjectId.trim().length > 0;
      case "connstring": return lakebaseConnString.trim().length > 0;
      case "none": return true;
    }
  })();

  const handleDeploy = useCallback(async () => {
    const err = preflight(graphGetter);
    if (err) {
      setErrorMsg(err);
      setPhase("error");
      return;
    }

    const graph = graphGetter!();

    // Initialize steps
    const initial: Record<string, StepState> = {};
    for (const name of STEP_NAMES) {
      initial[name] = { status: "pending", message: "" };
    }
    setSteps(initial);
    setPhase("deploying");
    setErrorMsg("");

    let receivedTerminal = false;

    const lakebaseFields = {
      lakebase_project_id: lakebaseMode === "create" ? lakebaseProjectId : "",
      lakebase_existing_project_id: lakebaseMode === "existing" ? lakebaseExistingProjectId : "",
      lakebase_conn_string: lakebaseMode === "connstring" ? lakebaseConnString : "",
    };

    const streamFn = isApp
      ? (onEvent: (e: DeployEvent) => void) =>
          deployAppStream(
            {
              graph,
              app_name: appName,
              workspace_path: workspacePath,
              auth_mode: authMode,
              pat,
              ...lakebaseFields,
            },
            onEvent,
          )
      : (onEvent: (e: DeployEvent) => void) =>
          deployGraphStream(
            {
              graph,
              model_name: modelName,
              experiment_path: experimentPath,
              deploy_mode: deployMode,
              auth_mode: authMode,
              pat,
              ...lakebaseFields,
            },
            onEvent,
          );

    try {
      await streamFn(
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
  }, [graphGetter, isApp, appName, workspacePath, modelName, experimentPath, lakebaseMode, lakebaseProjectId, lakebaseExistingProjectId, lakebaseConnString, deployMode, authMode, pat]);

  const doneMessage = isApp
    ? "App deployed successfully!"
    : deployMode === "full"
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
                Deploy Target
                <select
                  className="deploy-input"
                  value={deployTarget}
                  onChange={(e) => setDeployTarget(e.target.value as DeployTarget)}
                >
                  <option value="model_serving">Model Serving</option>
                  <option value="app">Deploy as App (beta)</option>
                </select>
                <span className="deploy-hint">
                  {isApp
                    ? "Deploy the agent as a Databricks App (agents on apps). No PAT-based model registration or serving endpoint — the app exposes /invocations directly."
                    : "Deploy as an MLflow model + serving endpoint (classic). Requires a PAT."}
                </span>
              </label>

              {!isApp && (
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
              )}

              {isApp && (
                <label className="deploy-label">
                  App Name
                  <input
                    type="text"
                    className="deploy-input"
                    placeholder="my-agent (lowercase, hyphenated)"
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                  />
                  <span className="deploy-hint">
                    {workspacePath
                      ? `The project will be uploaded to ${workspacePath}/${appName || "<app-name>"} and deployed as a Databricks App.`
                      : "Configure your setup folder first — the app project is uploaded there."}
                  </span>
                </label>
              )}

              <label className="deploy-label">
                Authentication
                <select
                  className="deploy-input"
                  value={authMode}
                  onChange={(e) => setAuthMode(e.target.value as AuthMode)}
                >
                  <option value="obo">On-Behalf-Of (User Identity)</option>
                  <option value="passthrough">Automatic (System SP)</option>
                </select>
                <span className="deploy-hint">
                  {authMode === "obo"
                    ? "The agent acts as the calling user. Users need their own permissions on Vector Search, Genie, and UC Functions. LLM endpoints use system auth."
                    : "A system service principal accesses all resources. Users don't need individual permissions, but the SP gets broad access."}
                </span>
                {isApp && authMode === "obo" && (
                  <span className="deploy-hint">
                    Note: on-behalf-of-user auth for Apps requires a workspace admin to
                    enable user authorization, and the requested scopes must be allowed
                    by the workspace scope allowlist.
                  </span>
                )}
              </label>

              {!isApp && (
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
              )}

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

              {showLakebase && (
                <>
                  <label className="deploy-label">
                    Lakebase (Persistent State)
                    <select
                      className="deploy-input"
                      value={lakebaseMode}
                      onChange={(e) => setLakebaseMode(e.target.value as LakebaseMode)}
                    >
                      <option value="create">Create new Lakebase project</option>
                      <option value="existing">Use existing Lakebase instance</option>
                      <option value="connstring">Connection string (advanced)</option>
                      {!needsCheckpointer && <option value="none">None</option>}
                    </select>
                    <span className="deploy-hint">
                      {needsCheckpointer
                        ? "Required — your graph uses conversational LLMs or human input nodes that need state persisted across turns."
                        : "Optional. Enables multi-turn conversation memory."}
                    </span>
                  </label>

                  {lakebaseMode === "create" && (
                    <label className="deploy-label">
                      Project ID
                      <input
                        type="text"
                        className="deploy-input"
                        placeholder="my-team (lowercase, 3-63 chars)"
                        value={lakebaseProjectId}
                        onChange={(e) => setLakebaseProjectId(e.target.value)}
                      />
                      <span className="deploy-hint">
                        Choose a short name for your Lakebase project (e.g. &quot;my-team&quot;).
                        Multiple agents can share the same project — each gets its own database.
                      </span>
                    </label>
                  )}

                  {lakebaseMode === "existing" && (
                    <label className="deploy-label">
                      Project ID
                      <input
                        type="text"
                        className="deploy-input"
                        placeholder="my-team"
                        value={lakebaseExistingProjectId}
                        onChange={(e) => setLakebaseExistingProjectId(e.target.value)}
                      />
                      <span className="deploy-hint">
                        The short name you chose when creating the project (e.g. &quot;my-team&quot;, not the UUID).
                        Find it under Compute &gt; Lakebase in your workspace.
                      </span>
                    </label>
                  )}

                  {lakebaseMode === "connstring" && (
                    <label className="deploy-label">
                      Connection String
                      <input
                        type="text"
                        className="deploy-input"
                        placeholder="postgresql://user:pass@host:port/db"
                        value={lakebaseConnString}
                        onChange={(e) => setLakebaseConnString(e.target.value)}
                      />
                      <span className="deploy-hint">
                        Static credential — tokens embedded in the URI expire after 1 hour.
                      </span>
                    </label>
                  )}
                </>
              )}

              {(needsModelName || isApp) && (
                <label className="deploy-label">
                  Personal Access Token{isApp ? " (recommended)" : ""}
                  <input
                    type="password"
                    className="deploy-input"
                    placeholder="dapi..."
                    value={pat}
                    onChange={(e) => setPat(e.target.value)}
                  />
                  <span className="deploy-hint">
                    {isApp
                      ? "Used to create + deploy the app under your identity, and to provision Lakebase. Required if you configure Lakebase. Your token is not stored."
                      : "Used to register models and create endpoints under your identity. Your token is not stored — it's only used for this deploy."}
                  </span>
                </label>
              )}
            </div>

            <div className="deploy-actions">
              <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="btn btn-primary"
                disabled={
                  isApp
                    ? (!appName || !workspacePath || !lakebaseValid)
                    : (
                        !experimentPath ||
                        (needsModelName && !modelName) ||
                        (needsModelName && !pat) ||
                        !lakebaseValid
                      )
                }
                onClick={handleDeploy}
              >
                {isApp ? "Deploy as App" : MODE_LABELS[deployMode]}
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
              {resultData?.invocations_url && (
                <label className="deploy-label">
                  Invocations URL
                  <input
                    type="text"
                    className="deploy-input"
                    readOnly
                    value={resultData.invocations_url}
                    onClick={(e) => (e.target as HTMLInputElement).select()}
                  />
                </label>
              )}
              {resultData?.app_url && (
                <p className="deploy-meta">
                  App: <a href={resultData.app_url} target="_blank" rel="noreferrer">{resultData.app_url}</a>
                </p>
              )}
              {resultData?.model_version && (
                <p className="deploy-meta">Model version: {resultData.model_version}</p>
              )}
              {resultData?.run_id && (
                <p className="deploy-meta">MLflow run: {resultData.run_id}</p>
              )}

              {isApp && resultData?.workspace_path && (
                <details className="deploy-git-instructions">
                  <summary>Move to a GitHub-backed app (for team collaboration)</summary>
                  <p className="deploy-hint">
                    Your agent was deployed from workspace files. To collaborate as a
                    team (e.g. add a frontend), promote it to a git-backed Databricks App:
                  </p>
                  <ol className="deploy-hint">
                    <li>
                      Export the project locally:
                      <pre>databricks workspace export-dir {resultData.workspace_path} ./{resultData.app_name}</pre>
                    </li>
                    <li>
                      Push it to GitHub:
                      <pre>{`cd ${resultData.app_name}\ngit init && git add . && git commit -m "Initial agent app"\ngit remote add origin https://github.com/<org>/<repo>.git\ngit push -u origin main`}</pre>
                    </li>
                    <li>
                      In the Databricks Apps UI, edit the app → configure Git with the
                      repo URL + branch, add a Git credential for the app's service
                      principal (for private repos), and enable <strong>auto-deploy on
                      push</strong>. Teammates then clone, change, and push — the app
                      redeploys automatically. The generated <code>README.md</code> has
                      these steps too.
                    </li>
                  </ol>
                </details>
              )}
            </div>
            <div className="deploy-actions">
              {(resultData?.invocations_url || resultData?.endpoint_url) && (
                <button
                  className="btn btn-secondary"
                  onClick={() => navigator.clipboard.writeText(
                    resultData.invocations_url || resultData.endpoint_url!
                  )}
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
