import { useCallback, useRef, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { X } from "lucide-react";

import AgentNode from "./nodes/AgentNode";
import SentinelNode from "./nodes/SentinelNode";
import ConfigPanel from "./ConfigPanel";
import ToolConfigPanel from "./ToolConfigPanel";
import type { NodeTypeMetadata, GraphDef, AttachedTool } from "../types";
import { useAddField, useStateFields } from "../StateContext";

const START_ID = "__start__";
const END_ID = "__end__";

const INITIAL_NODES: Node[] = [
  {
    id: START_ID,
    type: "sentinelNode",
    position: { x: 250, y: 30 },
    data: { kind: "start" },
    deletable: false,
  },
  {
    id: END_ID,
    type: "sentinelNode",
    position: { x: 250, y: 500 },
    data: { kind: "end" },
    deletable: false,
  },
];

interface Props {
  nodeTypes: NodeTypeMetadata[];
  stateVariableNames: string[];
  onNodeSelect: (nodeId: string | null) => void;
  selectedNodeId: string | null;
  onGraphReady: (getter: () => GraphDef) => void;
  onImportReady?: (importer: (graph: GraphDef) => void) => void;
  onNodesUpdaterReady?: (updater: (fn: (nodes: Node[]) => Node[]) => void) => void;
  visible?: boolean;
}

let nodeIdCounter = 0;

/** Compute screen-space position for the popover anchored to a node. */
function usePopoverPosition(selectedNodeId: string | null, wrapperRef: React.RefObject<HTMLDivElement | null>) {
  const { getNode, flowToScreenPosition } = useReactFlow();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!selectedNodeId) { setPos(null); return; }
    const update = () => {
      const node = getNode(selectedNodeId);
      const wrapper = wrapperRef.current;
      if (!node || !wrapper) { setPos(null); return; }
      const wrapperRect = wrapper.getBoundingClientRect();
      // Anchor to the right edge of the node, vertically centered
      const screenPos = flowToScreenPosition({
        x: node.position.x + (node.measured?.width ?? 160),
        y: node.position.y,
      });
      setPos({
        x: screenPos.x - wrapperRect.left + 12,
        y: screenPos.y - wrapperRect.top,
      });
    };
    update();
    // Update on short interval to track panning/zooming
    const id = setInterval(update, 60);
    return () => clearInterval(id);
  }, [selectedNodeId, getNode, flowToScreenPosition, wrapperRef]);

  return pos;
}

export default function Canvas({ nodeTypes, stateVariableNames, onNodeSelect, selectedNodeId, onGraphReady, onImportReady, onNodesUpdaterReady, visible = true }: Props) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(INITIAL_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const addField = useAddField();
  const currentFields = useStateFields();
  const [selectedTool, setSelectedTool] = useState<{ nodeId: string; toolId: string } | null>(null);

  // Re-fit the viewport when the canvas becomes visible (e.g. switching back from Home/Help)
  useEffect(() => {
    if (visible) {
      // Small delay so the container has layout dimensions before fitView calculates bounds
      const id = setTimeout(() => fitView({ duration: 200 }), 50);
      return () => clearTimeout(id);
    }
  }, [visible, fitView]);

  const customNodeTypes = useMemo(
    () => ({ agentNode: AgentNode, sentinelNode: SentinelNode }),
    []
  );

  const SENTINEL_IDS = new Set([START_ID, END_ID]);

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const safe = changes.filter(
        (c) => c.type !== "remove" || !SENTINEL_IDS.has(c.id)
      );
      const removedIds = safe
        .filter((c) => c.type === "remove")
        .map((c) => c.id);
      if (removedIds.length > 0) {
        onNodeSelect(null);
      }
      onNodesChange(safe);
    },
    [onNodesChange, onNodeSelect]
  );

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds: Edge[]) => addEdge({ ...params, type: "smoothstep" }, eds));
    },
    [setEdges]
  );

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (SENTINEL_IDS.has(node.id)) return;
      setSelectedTool(null);
      onNodeSelect(node.id);
    },
    [onNodeSelect]
  );

  const onPaneClick = useCallback(() => {
    setSelectedTool(null);
    onNodeSelect(null);
  }, [onNodeSelect]);

  // Expose graph serialization
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  nodesRef.current = nodes;
  edgesRef.current = edges;

  useEffect(() => {
    onGraphReady(() => {
      const graphNodes = nodesRef.current
        .filter((n) => n.id !== START_ID && n.id !== END_ID)
        .map((n) => {
          const config = { ...((n.data.config as Record<string, unknown>) ?? {}) };

          // Serialize attached tools into tools_json for LLM nodes
          const tools = (n.data.tools ?? []) as AttachedTool[];
          if (tools.length > 0) {
            config.tools_json = JSON.stringify(
              tools.map((t) => ({ type: t.type, config: t.config }))
            );
          }

          return {
            id: n.id,
            type: (n.data.nodeType as string) ?? "llm",
            name: (n.data.name as string) ?? "",
            writes_to: (n.data.writes_to as string) ?? "",
            config,
            position: n.position,
          };
        });
      const graphEdges = edgesRef.current.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        source_handle: e.sourceHandle ?? null,
      }));
      const endNode = nodesRef.current.find((n) => n.id === END_ID);
      const outputFields = (endNode?.data?.output_fields as string[]) ?? [];
      return { nodes: graphNodes, edges: graphEdges, state_fields: [], output_fields: outputFields } as GraphDef;
    });
  }, [onGraphReady]);

  // Import handler
  useEffect(() => {
    if (!onImportReady) return;
    onImportReady((graph: GraphDef) => {
      const newNodes: Node[] = INITIAL_NODES.map((n) => {
        if (n.id === END_ID && graph.output_fields?.length) {
          return { ...n, data: { ...n.data, output_fields: graph.output_fields } };
        }
        return n;
      });
      for (const gn of graph.nodes) {
        const meta = nodeTypes.find((nt) => nt.type === gn.type);
        nodeIdCounter = Math.max(nodeIdCounter, parseInt(gn.id.replace(/\D/g, "") || "0", 10));

        // Restore attached tools from tools_json for LLM nodes
        let tools: AttachedTool[] = [];
        const toolsJson = gn.config?.tools_json;
        if (gn.type === "llm" && toolsJson && typeof toolsJson === "string") {
          try {
            const parsed = JSON.parse(toolsJson) as Array<{ type: string; config: Record<string, unknown> }>;
            tools = parsed.map((tc, i) => {
              const toolMeta = nodeTypes.find((nt) => nt.type === tc.type);
              return {
                id: `tool_${++toolIdCounter.current}`,
                type: tc.type,
                display_name: toolMeta?.display_name ?? tc.type,
                icon: toolMeta?.icon ?? "puzzle",
                color: toolMeta?.color ?? "#6366f1",
                config: tc.config ?? {},
              };
            });
          } catch { /* ignore bad JSON */ }
        }

        newNodes.push({
          id: gn.id,
          type: "agentNode",
          position: gn.position ?? { x: 250, y: 200 },
          data: {
            nodeType: gn.type,
            name: gn.name ?? "",
            display_name: meta?.display_name ?? gn.type,
            description: meta?.description ?? "",
            icon: meta?.icon ?? "",
            color: meta?.color ?? "#6366f1",
            config_fields: meta?.config_fields ?? [],
            is_router: gn.type === "router",
            writes_to: gn.writes_to ?? "",
            config: gn.config ?? {},
            tools,
          },
        });
      }
      const newEdges: Edge[] = graph.edges.map((ge) => ({
        id: ge.id,
        source: ge.source,
        target: ge.target,
        sourceHandle: ge.source_handle ?? undefined,
        type: "smoothstep",
      }));
      setNodes(newNodes);
      setEdges(newEdges);
    });
  }, [onImportReady, nodeTypes, setNodes, setEdges]);

  // Expose setNodes for cascade rename from App
  useEffect(() => {
    if (onNodesUpdaterReady) onNodesUpdaterReady(setNodes);
  }, [onNodesUpdaterReady, setNodes]);

  // Drop from palette
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  /** Find an LLM node whose bounding box contains the given flow position. */
  const findLlmNodeAtPosition = useCallback(
    (flowPos: XYPosition): Node | undefined => {
      return nodesRef.current.find((n) => {
        if ((n.data.nodeType as string) !== "llm") return false;
        const w = n.measured?.width ?? 160;
        const h = n.measured?.height ?? 100;
        return (
          flowPos.x >= n.position.x &&
          flowPos.x <= n.position.x + w &&
          flowPos.y >= n.position.y &&
          flowPos.y <= n.position.y + h
        );
      });
    },
    []
  );

  const toolIdCounter = useRef(0);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const nodeType = e.dataTransfer.getData("application/agentbuilder-node");
      if (!nodeType) return;

      const meta = nodeTypes.find((nt) => nt.type === nodeType);
      if (!meta) return;

      const wrapperBounds = reactFlowWrapper.current?.getBoundingClientRect();
      if (!wrapperBounds) return;

      const flowPos = screenToFlowPosition({ x: e.clientX, y: e.clientY });

      // If this is a tool-compatible node dropped onto an LLM node, attach it
      if (meta.tool_compatible) {
        const llmNode = findLlmNodeAtPosition(flowPos);
        if (llmNode) {
          const newTool: AttachedTool = {
            id: `tool_${++toolIdCounter.current}`,
            type: meta.type,
            display_name: meta.display_name,
            icon: meta.icon,
            color: meta.color,
            config: {},
          };
          setNodes((nds) =>
            nds.map((n) => {
              if (n.id !== llmNode.id) return n;
              const existing = (n.data.tools ?? []) as AttachedTool[];
              return {
                ...n,
                data: { ...n.data, tools: [...existing, newTool] },
              };
            })
          );
          return;
        }
      }

      // Standard drop — create a new graph node
      const position = {
        x: flowPos.x - 80,
        y: flowPos.y - 20,
      };

      const defaultConfig: Record<string, unknown> = {};
      for (const field of meta.config_fields) {
        if (field.default != null) {
          if (field.field_type === "route_editor" && typeof field.default === "string") {
            try { defaultConfig[field.name] = JSON.parse(field.default); } catch { defaultConfig[field.name] = field.default; }
          } else {
            defaultConfig[field.name] = field.default;
          }
        }
      }

      // Auto-create a state field for this node type if it has a template
      let defaultWritesTo = "";
      const template = meta.default_field_template;
      if (template && meta.type !== "router") {
        const existingNames = new Set(currentFields.map((f) => f.name));
        let fieldName = template.name;
        let counter = 1;
        while (existingNames.has(fieldName)) {
          counter++;
          fieldName = `${template.name}_${counter}`;
        }
        addField({ name: fieldName, type: template.type, description: template.description, sub_fields: [] });
        defaultWritesTo = fieldName;
      }

      const newNode: Node = {
        id: `node_${++nodeIdCounter}`,
        type: "agentNode",
        position,
        data: {
          nodeType: meta.type,
          display_name: meta.display_name,
          description: meta.description,
          icon: meta.icon,
          color: meta.color,
          config_fields: meta.config_fields,
          is_router: meta.type === "router",
          writes_to: defaultWritesTo,
          config: defaultConfig,
        },
      };

      setNodes((nds) => [...nds, newNode]);
    },
    [nodeTypes, setNodes, findLlmNodeAtPosition, screenToFlowPosition, currentFields, addField]
  );

  // ── Tool chip selection ──────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: Event) => {
      const { nodeId, toolId } = (e as CustomEvent).detail;
      setSelectedTool({ nodeId, toolId });
      onNodeSelect(nodeId);  // anchor the popover to the parent LLM node
    };
    window.addEventListener("tool-chip-click", handler);
    return () => window.removeEventListener("tool-chip-click", handler);
  }, [onNodeSelect]);

  // Clear tool selection when the main node selection changes externally
  const prevSelectedRef = useRef(selectedNodeId);
  useEffect(() => {
    if (selectedNodeId !== prevSelectedRef.current) {
      // Only clear tool selection if we navigated away from the parent node
      if (selectedTool && selectedNodeId !== selectedTool.nodeId) {
        setSelectedTool(null);
      }
      prevSelectedRef.current = selectedNodeId;
    }
  }, [selectedNodeId, selectedTool]);

  // Floating popover position
  const popoverPos = usePopoverPosition(selectedNodeId, reactFlowWrapper);

  return (
    <div className="canvas-wrapper" ref={reactFlowWrapper}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        connectOnClick={false}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={customNodeTypes}
        fitView
        defaultEdgeOptions={{ type: "smoothstep" }}
      >
        <Background />
      </ReactFlow>

      {/* Floating config popover */}
      {selectedNodeId && popoverPos && (
        <div
          className="config-popover"
          style={{ left: popoverPos.x, top: popoverPos.y }}
          onKeyDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="config-popover-close"
            onClick={() => {
              if (selectedTool) {
                setSelectedTool(null);
              } else {
                onNodeSelect(null);
              }
            }}
          >
            <X size={14} />
          </button>
          {selectedTool && selectedTool.nodeId === selectedNodeId ? (
            <ToolConfigPanel
              parentNodeId={selectedTool.nodeId}
              toolId={selectedTool.toolId}
              nodeTypes={nodeTypes}
            />
          ) : (
            <ConfigPanel
              selectedNodeId={selectedNodeId}
              nodeTypes={nodeTypes}
              stateVariables={stateVariableNames}
            />
          )}
        </div>
      )}
    </div>
  );
}
