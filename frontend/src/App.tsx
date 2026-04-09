import { useState, useEffect, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Home, Hammer, HelpCircle, Trash2, CloudDownload, Save, Upload, MessageSquare, Rocket, Sparkles, Settings } from "lucide-react";
import Canvas from "./components/Canvas";
import NodePalette from "./components/NodePalette";
import StateModelModal from "./components/StateModelModal";
import StatePanel from "./components/StatePanel";
import ChatPlayground from "./components/ChatPlayground";
import DeployModal from "./components/DeployModal";
import HomePage from "./components/HomePage";
import HelpPage from "./components/HelpPage";
import BuilderWalkthrough from "./components/BuilderWalkthrough";
import AIChatDropdown from "./components/AIChatDropdown";
import SetupPage from "./components/SetupPage";
import { StateProvider } from "./StateContext";
import { fetchNodeTypes, loadGraphFromRun, getSetupStatus } from "./api";
import type { NodeTypeMetadata, GraphDef, StateFieldDef, SetupStatusResponse } from "./types";

type AppView = "home" | "builder" | "help" | "setup";

export default function App() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphGetter, setGraphGetter] = useState<(() => GraphDef) | null>(null);
  const [stateFields, setStateFields] = useState<StateFieldDef[]>([
    { name: "input", type: "str", description: "The initial input", sub_fields: [] },
  ]);
  const [showStateModal, setShowStateModal] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [showAIChat, setShowAIChat] = useState(false);
  const aiChatWrapperRef = useRef<HTMLDivElement>(null);
  const [showDeploy, setShowDeploy] = useState(false);
  const [showRunIdPrompt, setShowRunIdPrompt] = useState(false);
  const [runIdInput, setRunIdInput] = useState("");
  const [runIdLoading, setRunIdLoading] = useState(false);
  const [runIdError, setRunIdError] = useState("");
  const [runIdResult, setRunIdResult] = useState<{
    graph: GraphDef;
    run_name: string;
    experiment_id: string;
    found_at: string;
    searched: string[];
  } | null>(null);
  const [graphImporter, setGraphImporter] = useState<((g: GraphDef) => void) | null>(null);
  const [view, setView] = useState<AppView>("home");
  const [showWalkthrough, setShowWalkthrough] = useState(false);
  const hasOpenedBuilder = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);
  const [experimentPath, setExperimentPath] = useState<string | null>(null);

  const stateVariableNames = stateFields.flatMap((f) => {
    const paths = [f.name];
    if ((f.type === "structured" || f.type === "vector_search_filter") && f.sub_fields?.length) {
      for (const sf of f.sub_fields) {
        if (sf.name) paths.push(`${f.name}.${sf.name}`);
      }
    }
    return paths;
  });
  const stateFieldsRef = useRef(stateFields);
  stateFieldsRef.current = stateFields;

  useEffect(() => {
    fetchNodeTypes().then(setNodeTypes).catch(console.error);
    getSetupStatus()
      .then((status) => {
        setSetupStatus(status);
        if (status.setup_complete && status.experiment_path) {
          setExperimentPath(status.experiment_path);
        }
      })
      .catch(console.error);
  }, []);

  const handleSaveJson = useCallback(() => {
    if (!graphGetter) return;
    const graph = graphGetter();
    graph.state_fields = stateFieldsRef.current;
    const json = JSON.stringify(graph, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "graph.json";
    a.click();
    URL.revokeObjectURL(url);
  }, [graphGetter]);

  const handleLoadJson = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file || !graphImporter) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const graph = JSON.parse(reader.result as string) as GraphDef;
          if (graph.state_fields?.length) {
            setStateFields(graph.state_fields);
          }
          graphImporter(graph);
          hasOpenedBuilder.current = true;
          setShowStateModal(false);
          setView("builder");
        } catch (err) {
          console.error("Failed to parse graph JSON:", err);
        }
      };
      reader.readAsText(file);
      e.target.value = "";
    },
    [graphImporter]
  );

  const openBuilder = useCallback(() => {
    setView("builder");
    if (!hasOpenedBuilder.current) {
      hasOpenedBuilder.current = true;
      setShowWalkthrough(true);
    }
  }, []);

  const handleClearAll = useCallback(() => {
    if (!graphImporter) return;
    graphImporter({ nodes: [], edges: [], state_fields: [], output_fields: [] });
    setStateFields([
      { name: "input", type: "str", description: "The initial input", sub_fields: [] },
    ]);
    setSelectedNodeId(null);
  }, [graphImporter]);

  const handleFetchRun = useCallback(async () => {
    if (!runIdInput.trim()) return;
    setRunIdLoading(true);
    setRunIdError("");
    setRunIdResult(null);
    try {
      const result = await loadGraphFromRun(runIdInput.trim());
      if (result.success && result.graph) {
        setRunIdResult(result as unknown as NonNullable<typeof runIdResult>);
      } else {
        setRunIdError(result.error || "Failed to load graph.");
      }
    } catch (err) {
      setRunIdError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunIdLoading(false);
    }
  }, [runIdInput]);

  const handleAcceptRun = useCallback(() => {
    if (!runIdResult?.graph || !graphImporter) return;
    const graph = runIdResult.graph as GraphDef;
    if (graph.state_fields?.length) {
      setStateFields(graph.state_fields);
    }
    graphImporter(graph);
    hasOpenedBuilder.current = true;
    setShowRunIdPrompt(false);
    setRunIdInput("");
    setRunIdResult(null);
    setView("builder");
  }, [graphImporter, runIdResult]);

  return (
    <ReactFlowProvider>
      <StateProvider value={{ names: stateVariableNames, fields: stateFields }}>
      <div className={`app${showStateModal && view === "builder" ? " app-blurred" : ""}`}>
        <header className="header">
          <div className="header-left">
            <span className="header-logo">Agent Builder</span>
          </div>
          {view === "builder" && (
            <div className="header-actions">
              <div className="header-group">
                <button className="btn btn-ghost btn-with-icon" onClick={handleSaveJson}>
                  <Save size={14} />
                  Save
                </button>
                <button className="btn btn-ghost btn-with-icon" onClick={() => fileInputRef.current?.click()}>
                  <Upload size={14} />
                  Load
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".json"
                  style={{ display: "none" }}
                  onChange={handleLoadJson}
                />
                <button className="btn btn-ghost btn-with-icon" onClick={() => setShowRunIdPrompt(true)} title="Load graph from an MLflow run">
                  <CloudDownload size={14} />
                  MLflow
                </button>
                <button className="btn btn-ghost btn-danger-ghost" onClick={handleClearAll}>
                  <Trash2 size={14} />
                  Clear All
                </button>
              </div>
              <div className="header-divider" />
              <div className="header-group">
                <div className="ai-chat-wrapper" ref={aiChatWrapperRef}>
                  <button
                    className={`btn btn-ai-chat btn-with-icon${showAIChat ? " btn-active" : ""}`}
                    onClick={() => setShowAIChat((v) => !v)}
                  >
                    <Sparkles size={14} />
                    AI Chat
                  </button>
                  {showAIChat && (
                    <AIChatDropdown
                      graphGetter={graphGetter}
                      graphImporter={graphImporter}
                      stateFields={stateFields}
                      setStateFields={setStateFields}
                      onSwitchToBuilder={() => {
                        setView("builder");
                        hasOpenedBuilder.current = true;
                      }}
                      onClose={() => setShowAIChat(false)}
                      wrapperRef={aiChatWrapperRef}
                    />
                  )}
                </div>
                <button className="btn btn-primary btn-with-icon" onClick={() => setShowChat(true)}>
                  <MessageSquare size={14} />
                  Playground
                </button>
                <button className="btn btn-deploy btn-with-icon" onClick={() => setShowDeploy(true)}>
                  <Rocket size={14} />
                  Deploy
                </button>
              </div>
            </div>
          )}
        </header>

        <div className="main">
          <nav className="nav-rail" onKeyDown={(e) => e.stopPropagation()}>
            <button
              className={`nav-rail-btn${view === "home" ? " active" : ""}`}
              onClick={() => setView("home")}
              title="Home"
            >
              <Home size={18} />
              <span>Home</span>
            </button>
            <button
              className={`nav-rail-btn${view === "builder" ? " active" : ""}`}
              onClick={openBuilder}
              title="Builder"
            >
              <Hammer size={18} />
              <span>Builder</span>
            </button>
            <button
              className={`nav-rail-btn${view === "help" ? " active" : ""}`}
              onClick={() => setView("help")}
              title="Help"
            >
              <HelpCircle size={18} />
              <span>Help</span>
            </button>
            <button
              className={`nav-rail-btn${view === "setup" ? " active" : ""}`}
              onClick={() => setView("setup")}
              title="Setup"
            >
              <Settings size={18} />
              <span>Setup</span>
            </button>
          </nav>

          {view === "home" && (
            <HomePage onGetStarted={openBuilder} />
          )}

          {view === "help" && (
            <HelpPage onGoToBuilder={openBuilder} />
          )}

          {view === "setup" && (
            <SetupPage
              setupStatus={setupStatus}
              onSetupComplete={(path) => {
                setExperimentPath(path);
                setSetupStatus((prev) =>
                  prev ? { ...prev, setup_complete: true, experiment_path: path } : prev
                );
              }}
            />
          )}

          <div className="builder-container" style={{ display: view === "builder" ? "contents" : "none" }}>
            <div className="left-panel" onKeyDown={(e) => e.stopPropagation()}>
              <StatePanel
                fields={stateFields}
                onChange={setStateFields}
                onOpenModal={() => setShowStateModal(true)}
              />
              <NodePalette nodeTypes={nodeTypes} />
            </div>

            <Canvas
              nodeTypes={nodeTypes}
              stateVariableNames={stateVariableNames}
              selectedNodeId={selectedNodeId}
              onNodeSelect={setSelectedNodeId}
              onGraphReady={(getter) => setGraphGetter(() => getter)}
              onImportReady={(importer) => setGraphImporter(() => importer)}
              visible={view === "builder"}
            />
          </div>

          {showWalkthrough && view === "builder" && (
            <BuilderWalkthrough onDismiss={() => setShowWalkthrough(false)} />
          )}
        </div>
      </div>

      {showStateModal && (
        <StateModelModal
          fields={stateFields}
          onChange={setStateFields}
          onClose={() => setShowStateModal(false)}
        />
      )}

      {showChat && (
        <ChatPlayground
          graphGetter={graphGetter}
          stateFieldsRef={stateFieldsRef}
          onClose={() => setShowChat(false)}
        />
      )}

      {showDeploy && (
        <DeployModal
          graphGetter={graphGetter}
          stateFieldsRef={stateFieldsRef}
          onClose={() => setShowDeploy(false)}
          defaultExperimentPath={experimentPath ?? ""}
          onGoToSetup={() => { setShowDeploy(false); setView("setup"); }}
        />
      )}

      {/* Load from MLflow run modal */}
      {showRunIdPrompt && (
        <div className="modal-overlay" onClick={() => { setShowRunIdPrompt(false); setRunIdError(""); setRunIdResult(null); }}>
          <div className="modal-card" style={{ width: runIdResult ? 600 : 440 }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h1>Load from MLflow Run</h1>
              {!runIdResult && (
                <p>
                  Enter the MLflow Run ID from a previously deployed graph. The
                  graph definition is stored as an artifact during deployment.
                </p>
              )}
            </div>
            <div className="modal-body">
              {!runIdResult ? (
                <>
                  <input
                    className="preview-input"
                    style={{ width: "100%" }}
                    value={runIdInput}
                    placeholder="e.g. a1b2c3d4e5f6..."
                    onChange={(e) => setRunIdInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleFetchRun()}
                    autoFocus
                  />
                  {runIdError && (
                    <pre className="result-error" style={{ marginTop: "0.5rem", fontSize: "0.75rem" }}>
                      {runIdError}
                    </pre>
                  )}
                </>
              ) : (
                <div className="mlflow-load-preview">
                  <div className="mlflow-load-success">Graph found</div>
                  <div className="mlflow-load-meta">
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">Run</span>
                      <span>{runIdResult.run_name}</span>
                    </div>
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">Found at</span>
                      <code>{runIdResult.found_at}</code>
                    </div>
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">Nodes</span>
                      <span>{runIdResult.graph.nodes?.length ?? 0}</span>
                    </div>
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">State fields</span>
                      <span>{runIdResult.graph.state_fields?.map((f: { name: string }) => f.name).join(", ")}</span>
                    </div>
                  </div>
                  <details className="mlflow-load-json-details">
                    <summary>Graph JSON</summary>
                    <pre className="mlflow-load-json">
                      {JSON.stringify(runIdResult.graph, null, 2)}
                    </pre>
                  </details>
                  <div className="mlflow-load-hint">
                    Older graphs may reference state fields or configs that have since
                    been renamed. You can review the JSON above and fix any issues after importing.
                  </div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              {!runIdResult ? (
                <>
                  <button
                    className="btn btn-ghost"
                    onClick={() => { setShowRunIdPrompt(false); setRunIdError(""); }}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn btn-primary"
                    onClick={handleFetchRun}
                    disabled={runIdLoading || !runIdInput.trim()}
                  >
                    {runIdLoading ? "Searching..." : "Find Graph"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="btn btn-ghost"
                    onClick={() => setRunIdResult(null)}
                  >
                    Back
                  </button>
                  <button className="btn btn-primary" onClick={handleAcceptRun}>
                    Continue
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      </StateProvider>
    </ReactFlowProvider>
  );
}
