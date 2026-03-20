import { useState, useEffect, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import Canvas from "./components/Canvas";
import NodePalette from "./components/NodePalette";
import ConfigPanel from "./components/ConfigPanel";
import StateModelModal from "./components/StateModelModal";
import StateSummary from "./components/StateSummary";
import ChatPlayground from "./components/ChatPlayground";
import { StateProvider } from "./StateContext";
import { fetchNodeTypes, previewGraph, exportGraph, validateGraph } from "./api";
import type { NodeTypeMetadata, GraphDef, PreviewResponse, StateFieldDef } from "./types";

const MIN_PANEL_WIDTH = 280;
const MAX_PANEL_WIDTH = 700;
const DEFAULT_PANEL_WIDTH = 380;

export default function App() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMetadata[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [graphGetter, setGraphGetter] = useState<(() => GraphDef) | null>(null);
  const [previewInput, setPreviewInput] = useState("What were Q4 sales?");
  const [previewResult, setPreviewResult] = useState<PreviewResponse | null>(null);
  const [exportedCode, setExportedCode] = useState<string>("");
  const [activePanel, setActivePanel] = useState<"preview" | "export" | null>(null);
  const [loading, setLoading] = useState(false);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [stateFields, setStateFields] = useState<StateFieldDef[]>([
    { name: "user_input", type: "str", description: "The user's initial message", sub_fields: [] },
  ]);
  const [showStateModal, setShowStateModal] = useState(true);
  const [showChat, setShowChat] = useState(false);
  const isResizing = useRef(false);

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

  const handlePreview = useCallback(async () => {
    if (!graphGetter) return;
    setLoading(true);
    const graph = graphGetter();
    graph.state_fields = stateFieldsRef.current;
    const validation = await validateGraph(graph);
    if (!validation.valid) {
      setPreviewResult({
        success: false,
        output: "",
        error: `Validation errors:\n${validation.errors.join("\n")}`,
        execution_trace: [],
        state: {},
      });
      setActivePanel("preview");
      setLoading(false);
      return;
    }
    const result = await previewGraph(graph, previewInput);
    setPreviewResult(result);
    setActivePanel("preview");
    setLoading(false);
  }, [graphGetter, previewInput]);

  const handleExport = useCallback(async () => {
    if (!graphGetter) return;
    const graph = graphGetter();
    graph.state_fields = stateFieldsRef.current;
    const result = await exportGraph(graph);
    setExportedCode(result.success ? result.code : `# Error: ${result.error}`);
    setActivePanel("export");
  }, [graphGetter]);

  return (
    <ReactFlowProvider>
      <StateProvider value={{ names: stateVariableNames, fields: stateFields }}>
      <div className={`app${showStateModal ? " app-blurred" : ""}`}>
        {/* Header */}
        <header className="header">
          <h1>Agent Builder</h1>
          <div className="header-actions">
            <input
              type="text"
              className="preview-input"
              value={previewInput}
              onChange={(e) => setPreviewInput(e.target.value)}
              placeholder="Test message..."
              onKeyDown={(e) => e.key === "Enter" && handlePreview()}
            />
            <button className="btn btn-primary" onClick={handlePreview} disabled={loading}>
              {loading ? "Running..." : "Preview"}
            </button>
            <button className="btn btn-secondary" onClick={handleExport}>
              Export Python
            </button>
            <button className="btn btn-primary" onClick={() => setShowChat(true)}>
              Chat Playground
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

            {activePanel === "preview" && previewResult && (
              <div className="result-panel">
                <h3>Preview Result</h3>

                {previewResult.error ? (
                  <pre className="result-error">{previewResult.error}</pre>
                ) : (
                  <>
                    <div className="result-section">
                      <div className="result-label">Output</div>
                      <pre>{previewResult.output || "(empty)"}</pre>
                    </div>

                    <details className="result-details">
                      <summary>State</summary>
                      <div className="state-grid">
                        {Object.entries(previewResult.state).map(([key, val]) => (
                          <div key={key} className="state-entry">
                            <span className="state-key">{key}</span>
                            <pre className="state-val">{val || "(empty)"}</pre>
                          </div>
                        ))}
                      </div>
                    </details>

                    <details className="result-details">
                      <summary>Execution Trace ({previewResult.execution_trace.length} steps)</summary>
                      <div className="trace">
                        {previewResult.execution_trace.map((msg, i) => (
                          <div key={i} className="trace-step">
                            <span className="trace-badge">{msg.node ?? msg.role}</span>
                            <span className="trace-content">{msg.content}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  </>
                )}

                <button className="btn btn-sm" onClick={() => setActivePanel(null)}>
                  Close
                </button>
              </div>
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
      </StateProvider>
    </ReactFlowProvider>
  );
}
