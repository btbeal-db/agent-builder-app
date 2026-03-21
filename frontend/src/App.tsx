import { useState, useEffect, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import Canvas from "./components/Canvas";
import NodePalette from "./components/NodePalette";
import ConfigPanel from "./components/ConfigPanel";
import StateModelModal from "./components/StateModelModal";
import StateSummary from "./components/StateSummary";
import ChatPlayground from "./components/ChatPlayground";
import DeployModal from "./components/DeployModal";
import { StateProvider } from "./StateContext";
import { fetchNodeTypes, exportGraph } from "./api";
import type { NodeTypeMetadata, GraphDef, StateFieldDef } from "./types";

const MIN_PANEL_WIDTH = 280;
const MAX_PANEL_WIDTH = 700;
const DEFAULT_PANEL_WIDTH = 380;

export default function App() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphGetter, setGraphGetter] = useState<(() => GraphDef) | null>(null);
  const [exportedCode, setExportedCode] = useState<string>("");
  const [activePanel, setActivePanel] = useState<"export" | null>(null);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [stateFields, setStateFields] = useState<StateFieldDef[]>([
    { name: "user_input", type: "str", description: "The user's initial message", sub_fields: [] },
  ]);
  const [showStateModal, setShowStateModal] = useState(true);
  const [showChat, setShowChat] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  const [graphImporter, setGraphImporter] = useState<((g: GraphDef) => void) | null>(null);
  const isResizing = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const stateVariableNames = stateFields.map((f) => f.name);
  const stateFieldsRef = useRef(stateFields);
  stateFieldsRef.current = stateFields;

  useEffect(() => {
    fetchNodeTypes().then(setNodeTypes).catch(console.error);
  }, []);

  // ── Resize handling ──────────────────────────────────────────
  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startWidth = panelWidth;

    const onMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      const delta = startX - e.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, startWidth + delta));
      setPanelWidth(newWidth);
    };

    const onMouseUp = () => {
      isResizing.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, [panelWidth]);

  const handleExport = useCallback(async () => {
    if (!graphGetter) return;
    const graph = graphGetter();
    graph.state_fields = stateFieldsRef.current;
    const result = await exportGraph(graph);
    setExportedCode(result.success ? result.code : `# Error: ${result.error}`);
    setActivePanel("export");
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
          setShowStateModal(false);
        } catch (err) {
          console.error("Failed to parse graph JSON:", err);
        }
      };
      reader.readAsText(file);
      // Reset input so the same file can be re-loaded
      e.target.value = "";
    },
    [graphImporter]
  );

  return (
    <ReactFlowProvider>
      <StateProvider value={{ names: stateVariableNames, fields: stateFields }}>
      <div className={`app${showStateModal ? " app-blurred" : ""}`}>
        {/* Header */}
        <header className="header">
          <h1>Agent Builder</h1>
          <div className="header-actions">
            <button className="btn btn-secondary" onClick={handleSaveJson}>
              Save JSON
            </button>
            <button className="btn btn-secondary" onClick={() => fileInputRef.current?.click()}>
              Load JSON
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              style={{ display: "none" }}
              onChange={handleLoadJson}
            />
            <button className="btn btn-secondary" onClick={handleExport}>
              Export Python
            </button>
            <button className="btn btn-primary" onClick={() => setShowChat(true)}>
              Chat Playground
            </button>
            <button className="btn btn-deploy" onClick={() => setShowDeploy(true)}>
              Deploy
            </button>
          </div>
        </header>

        <div className="main">
          {/* Left sidebar */}
          <div className="left-panel" onKeyDown={(e) => e.stopPropagation()}>
            <StateSummary
              fields={stateFields}
              onEdit={() => setShowStateModal(true)}
            />
            <NodePalette nodeTypes={nodeTypes} />
          </div>

          {/* Center — canvas */}
          <Canvas
            nodeTypes={nodeTypes}
            stateVariableNames={stateVariableNames}
            onNodeSelect={setSelectedNodeId}
            onGraphReady={(getter) => setGraphGetter(() => getter)}
            onImportReady={(importer) => setGraphImporter(() => importer)}
          />

          {/* Resize handle */}
          <div className="resize-handle" onMouseDown={startResize} />

          {/* Right sidebar — config & results */}
          <aside
            className="right-panel"
            style={{ width: panelWidth }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {selectedNodeId && (
              <ConfigPanel
                selectedNodeId={selectedNodeId}
                nodeTypes={nodeTypes}
                stateVariables={stateVariableNames}
              />
            )}

            {activePanel === "export" && (
              <div className="result-panel">
                <h3>Exported Code</h3>
                <pre>{exportedCode}</pre>
                <button
                  className="btn btn-sm"
                  onClick={() => navigator.clipboard.writeText(exportedCode)}
                >
                  Copy
                </button>
                <button className="btn btn-sm" onClick={() => setActivePanel(null)}>
                  Close
                </button>
              </div>
            )}
          </aside>
        </div>
      </div>

      {/* State Model Modal — shown on launch and when editing */}
      {showStateModal && (
        <StateModelModal
          fields={stateFields}
          onChange={setStateFields}
          onClose={() => setShowStateModal(false)}
        />
      )}

      {/* Chat Playground drawer */}
      {showChat && (
        <ChatPlayground
          graphGetter={graphGetter}
          stateFieldsRef={stateFieldsRef}
          onClose={() => setShowChat(false)}
        />
      )}

      {/* Deploy modal */}
      {showDeploy && (
        <DeployModal
          graphGetter={graphGetter}
          stateFieldsRef={stateFieldsRef}
          onClose={() => setShowDeploy(false)}
        />
      )}
      </StateProvider>
    </ReactFlowProvider>
  );
}
