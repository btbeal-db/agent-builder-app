import { useState, useEffect, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Home, Hammer, HelpCircle } from "lucide-react";
import Canvas from "./components/Canvas";
import NodePalette from "./components/NodePalette";
import StateModelModal from "./components/StateModelModal";
import StateSummary from "./components/StateSummary";
import ChatPlayground from "./components/ChatPlayground";
import DeployModal from "./components/DeployModal";
import HomePage from "./components/HomePage";
import HelpPage from "./components/HelpPage";
import { StateProvider } from "./StateContext";
import { fetchNodeTypes, exportGraph } from "./api";
import type { NodeTypeMetadata, GraphDef, StateFieldDef } from "./types";

type AppView = "home" | "builder" | "help";

export default function App() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphGetter, setGraphGetter] = useState<(() => GraphDef) | null>(null);
  const [exportedCode, setExportedCode] = useState<string>("");
  const [showExport, setShowExport] = useState(false);
  const [stateFields, setStateFields] = useState<StateFieldDef[]>([
    { name: "input", type: "str", description: "The initial input", sub_fields: [] },
  ]);
  const [showStateModal, setShowStateModal] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  const [graphImporter, setGraphImporter] = useState<((g: GraphDef) => void) | null>(null);
  const [view, setView] = useState<AppView>("home");
  const hasOpenedBuilder = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
  }, []);

  const handleExport = useCallback(async () => {
    if (!graphGetter) return;
    const graph = graphGetter();
    graph.state_fields = stateFieldsRef.current;
    const result = await exportGraph(graph);
    setExportedCode(result.success ? result.code : `# Error: ${result.error}`);
    setShowExport(true);
  }, [graphGetter]);

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
      setShowStateModal(true);
    }
  }, []);

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
                <button className="btn btn-ghost" onClick={handleSaveJson}>
                  Save
                </button>
                <button className="btn btn-ghost" onClick={() => fileInputRef.current?.click()}>
                  Load
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".json"
                  style={{ display: "none" }}
                  onChange={handleLoadJson}
                />
                <button className="btn btn-ghost" onClick={handleExport}>
                  Export
                </button>
              </div>
              <div className="header-divider" />
              <div className="header-group">
                <button className="btn btn-primary" onClick={() => setShowChat(true)}>
                  Playground
                </button>
                <button className="btn btn-deploy" onClick={() => setShowDeploy(true)}>
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
          </nav>

          {view === "home" && (
            <HomePage onGetStarted={openBuilder} />
          )}

          {view === "help" && (
            <HelpPage onGoToBuilder={openBuilder} />
          )}

          {view === "builder" && (
            <>
              <div className="left-panel" onKeyDown={(e) => e.stopPropagation()}>
                <StateSummary
                  fields={stateFields}
                  onEdit={() => setShowStateModal(true)}
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
              />
            </>
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
        />
      )}

      {/* Export code modal */}
      {showExport && (
        <div className="modal-overlay" onClick={() => setShowExport(false)}>
          <div className="modal-card" style={{ width: 640 }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h1>Exported Python</h1>
            </div>
            <div className="modal-body">
              <pre className="export-code">{exportedCode}</pre>
            </div>
            <div className="modal-footer">
              <button
                className="btn btn-secondary"
                onClick={() => { navigator.clipboard.writeText(exportedCode); }}
              >
                Copy
              </button>
              <button className="btn btn-primary" onClick={() => setShowExport(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      </StateProvider>
    </ReactFlowProvider>
  );
}
