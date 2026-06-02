import { useState, useEffect, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Home, Hammer, Trash2, CloudDownload, Save, Upload, MessageSquare, Rocket, Sparkles, Settings, Package, X, HelpCircle, CakeSlice } from "lucide-react";
import Canvas from "./components/Canvas";
import NodePalette from "./components/NodePalette";
import ChatPlayground from "./components/ChatPlayground";
import DeployModal from "./components/DeployModal";
import HomePage from "./components/HomePage";
import BuilderWalkthrough from "./components/BuilderWalkthrough";
import AIChatDropdown from "./components/AIChatDropdown";
import SetupPage from "./components/SetupPage";
import SetupPromptModal from "./components/SetupPromptModal";
import ModelsPage from "./components/ModelsPage";
import { fetchNodeTypes, fetchModels, getSetupStatus } from "./api";
import type { NodeTypeMetadata, GraphDef, SetupStatusResponse, ModelInfo } from "./types";

type AppView = "home" | "builder" | "models" | "setup";

export default function App() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphGetter, setGraphGetter] = useState<(() => GraphDef) | null>(null);
  const [showChat, setShowChat] = useState(false);
  const [showAIChat, setShowAIChat] = useState(false);
  const aiChatWrapperRef = useRef<HTMLDivElement>(null);
  const [showDeploy, setShowDeploy] = useState(false);
  const [showImportJson, setShowImportJson] = useState(false);
  const [importJsonInput, setImportJsonInput] = useState("");
  const [importJsonError, setImportJsonError] = useState("");
  const [importJsonPreview, setImportJsonPreview] = useState<GraphDef | null>(null);
  const [graphImporter, setGraphImporter] = useState<((g: GraphDef) => void) | null>(null);
  const [view, setView] = useState<AppView>("home");
  const [showWalkthrough, setShowWalkthrough] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);
  const [experimentPath, setExperimentPath] = useState<string | null>(null);
  const [setupPromptDismissed, setSetupPromptDismissed] = useState(false);
  const [cachedModels, setCachedModels] = useState<ModelInfo[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);

  const refreshModels = useCallback(() => {
    setModelsLoading(true);
    fetchModels()
      .then((res) => setCachedModels(res.models))
      .catch(console.error)
      .finally(() => setModelsLoading(false));
  }, []);

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
          graphImporter(graph);
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
  }, []);

  // Stable so Canvas's [onGraphReady]/[onImportReady] effects don't re-run on
  // every App render — that pattern was driving an idle render storm.
  const handleGraphReady = useCallback((getter: () => GraphDef) => {
    setGraphGetter(() => getter);
  }, []);
  const handleImportReady = useCallback((importer: (g: GraphDef) => void) => {
    setGraphImporter(() => importer);
  }, []);

  const handleClearAll = useCallback(() => {
    if (!graphImporter) return;
    graphImporter({ nodes: [], edges: [], state_fields: [], output_fields: [] });
    setSelectedNodeId(null);
  }, [graphImporter]);

  const handleImportJsonParse = useCallback(() => {
    setImportJsonError("");
    setImportJsonPreview(null);
    try {
      const parsed = JSON.parse(importJsonInput.trim());
      if (!parsed.nodes || !parsed.edges) {
        setImportJsonError("JSON must contain \"nodes\" and \"edges\" fields.");
        return;
      }
      setImportJsonPreview(parsed as GraphDef);
    } catch {
      setImportJsonError("Invalid JSON.");
    }
  }, [importJsonInput]);

  const handleImportJsonAccept = useCallback(() => {
    if (!importJsonPreview || !graphImporter) return;
    graphImporter(importJsonPreview);
    setShowImportJson(false);
    setImportJsonInput("");
    setImportJsonPreview(null);
    setView("builder");
  }, [graphImporter, importJsonPreview]);

  return (
    <ReactFlowProvider>
      <div className="app">
        <header className="header">
          <div className="header-left">
            <div className="header-logo">
              <CakeSlice size={20} className="header-logo-icon" />
              <span>AgentSweet</span>
            </div>
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
                <button className="btn btn-ghost btn-with-icon" onClick={() => setShowImportJson(true)} title="Import graph from JSON (e.g. from an MLflow run artifact)">
                  <CloudDownload size={14} />
                  Import
                </button>
                <button className="btn btn-ghost btn-danger-ghost" onClick={handleClearAll}>
                  <Trash2 size={14} />
                  Clear All
                </button>
                <button className="btn btn-ghost btn-with-icon" onClick={() => setShowWalkthrough(true)} title="Take a guided tour">
                  <HelpCircle size={14} />
                  Tour
                </button>
              </div>
              <div className="header-divider" />
              <div className="header-group">
                <button className="btn btn-playground btn-with-icon" onClick={() => setShowChat(true)}>
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
              className={`nav-rail-btn${view === "models" ? " active" : ""}`}
              onClick={() => setView("models")}
              title="Models"
            >
              <Package size={18} />
              <span>Models</span>
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
            <HomePage
              onGetStarted={openBuilder}
              onTakeTour={() => { setView("builder"); setShowWalkthrough(true); }}
              userEmail={setupStatus?.user_email ?? ""}
            />
          )}

          {view === "models" && (
            <ModelsPage
              graphImporter={graphImporter}
              onSwitchToBuilder={() => setView("builder")}
              cachedModels={cachedModels}
              modelsLoading={modelsLoading}
              onRefresh={refreshModels}
            />
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
              <NodePalette nodeTypes={nodeTypes} />
            </div>

            <Canvas
              nodeTypes={nodeTypes}
              selectedNodeId={selectedNodeId}
              onNodeSelect={setSelectedNodeId}
              onGraphReady={handleGraphReady}
              onImportReady={handleImportReady}
              visible={view === "builder"}
            />
          </div>

          {showWalkthrough && view === "builder" && (
            <BuilderWalkthrough onDismiss={() => setShowWalkthrough(false)} />
          )}
        </div>
      </div>

      {/* Floating AI Chat bubble */}
      {view === "builder" && (
        <div className="ai-chat-fab-container" ref={aiChatWrapperRef}>
          {showAIChat && (
            <AIChatDropdown
              graphGetter={graphGetter}
              graphImporter={graphImporter}
              onSwitchToBuilder={() => setView("builder")}
              onClose={() => setShowAIChat(false)}
              wrapperRef={aiChatWrapperRef}
            />
          )}
          <button
            className={`ai-chat-fab${showAIChat ? " ai-chat-fab-active" : ""}`}
            onClick={() => setShowAIChat((v) => !v)}
            title="AI Chat"
          >
            {showAIChat ? <X size={20} /> : <Sparkles size={20} />}
          </button>
        </div>
      )}

      {showChat && (
        <ChatPlayground
          graphGetter={graphGetter}
          onClose={() => setShowChat(false)}
        />
      )}

      {showDeploy && (
        <DeployModal
          graphGetter={graphGetter}
          onClose={() => { setShowDeploy(false); refreshModels(); }}
          defaultExperimentPath={experimentPath ?? ""}
          onGoToSetup={() => { setShowDeploy(false); setView("setup"); }}
        />
      )}

      {/* First-sign-in prompt — shown until setup is complete or dismissed
          for this session. Auto-setup creates the conventional folder and
          grants the SP access; dismissing falls through to the Setup tab. */}
      {setupStatus &&
        !setupStatus.setup_complete &&
        !setupPromptDismissed && (
          <SetupPromptModal
            experimentPath={setupStatus.experiment_path ?? `/Users/${setupStatus.user_email}/agent-sweet`}
            spDisplayName={setupStatus.sp_display_name}
            onCreated={(path) => {
              setExperimentPath(path);
              setSetupStatus((prev) =>
                prev ? { ...prev, setup_complete: true, experiment_path: path } : prev
              );
              setSetupPromptDismissed(true);
            }}
            onDismiss={() => setSetupPromptDismissed(true)}
            onGoToSetup={() => {
              setSetupPromptDismissed(true);
              setView("setup");
            }}
          />
        )}

      {/* Import graph JSON modal */}
      {showImportJson && (
        <div className="modal-overlay" onClick={() => { setShowImportJson(false); setImportJsonError(""); setImportJsonPreview(null); setImportJsonInput(""); }}>
          <div className="modal-card" style={{ width: importJsonPreview ? 600 : 500 }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h1>Import Graph JSON</h1>
              {!importJsonPreview && (
                <p>
                  Paste the graph definition JSON below. You can copy this from
                  an MLflow run artifact or a saved graph file.
                </p>
              )}
            </div>
            <div className="modal-body">
              {!importJsonPreview ? (
                <>
                  <textarea
                    className="preview-input"
                    style={{ width: "100%", minHeight: 200, fontFamily: "monospace", fontSize: "0.8rem", resize: "vertical" }}
                    value={importJsonInput}
                    placeholder='{"nodes": [...], "edges": [...], ...}'
                    onChange={(e) => setImportJsonInput(e.target.value)}
                    autoFocus
                  />
                  {importJsonError && (
                    <pre className="result-error" style={{ marginTop: "0.5rem", fontSize: "0.75rem" }}>
                      {importJsonError}
                    </pre>
                  )}
                </>
              ) : (
                <div className="mlflow-load-preview">
                  <div className="mlflow-load-success">Valid graph definition</div>
                  <div className="mlflow-load-meta">
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">Nodes</span>
                      <span>{importJsonPreview.nodes?.length ?? 0}</span>
                    </div>
                    <div className="mlflow-load-row">
                      <span className="mlflow-load-label">Edges</span>
                      <span>{importJsonPreview.edges?.length ?? 0}</span>
                    </div>
                  </div>
                  <details className="mlflow-load-json-details">
                    <summary>Graph JSON</summary>
                    <pre className="mlflow-load-json">
                      {JSON.stringify(importJsonPreview, null, 2)}
                    </pre>
                  </details>
                </div>
              )}
            </div>
            <div className="modal-footer">
              {!importJsonPreview ? (
                <>
                  <button
                    className="btn btn-ghost"
                    onClick={() => { setShowImportJson(false); setImportJsonError(""); setImportJsonInput(""); }}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn btn-primary"
                    onClick={handleImportJsonParse}
                    disabled={!importJsonInput.trim()}
                  >
                    Parse
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="btn btn-ghost"
                    onClick={() => setImportJsonPreview(null)}
                  >
                    Back
                  </button>
                  <button className="btn btn-primary" onClick={handleImportJsonAccept}>
                    Import
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </ReactFlowProvider>
  );
}
