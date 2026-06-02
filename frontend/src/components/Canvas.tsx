import { useCallback, useRef, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  useStore,
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
import type { NodeTypeMetadata, GraphDef, AttachedTool, StateFieldDef } from "../types";
import { StateProvider } from "../StateContext";
import { deriveStateFields, deriveStateNames, deriveNodeFieldName, deriveNewNodeLabel } from "../derivedState";

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
  onNodeSelect: (nodeId: string | null) => void;
  selectedNodeId: string | null;
  onGraphReady: (getter: () => GraphDef) => void;
  onImportReady?: (importer: (graph: GraphDef) => void) => void;
  onStateFieldsChange?: (fields: StateFieldDef[]) => void;
  visible?: boolean;
}

let nodeIdCounter = 0;

/** Compute screen-space position for the popover anchored to a node.
 *  Subscribes to the React Flow store so we only recompute when the viewport
 *  transform or the node's position/size actually change — the previous
 *  setInterval(60ms) polled forever while a node was selected and triggered
 *  a Canvas re-render every tick. */
function usePopoverPosition(selectedNodeId: string | null, wrapperRef: React.RefObject<HTMLDivElement | null>) {
  const { flowToScreenPosition } = useReactFlow();
  const node = useStore((s) =>
    selectedNodeId ? s.nodeLookup.get(selectedNodeId) : undefined,
  );
  const transform = useStore((s) => s.transform);

  return useMemo(() => {
    if (!selectedNodeId || !node) return null;
    const wrapper = wrapperRef.current;
    if (!wrapper) return null;
    const wrapperRect = wrapper.getBoundingClientRect();
    const screenPos = flowToScreenPosition({
      x: node.position.x + (node.measured?.width ?? 160),
      y: node.position.y,
    });
    return {
      x: screenPos.x - wrapperRect.left + 12,
      y: screenPos.y - wrapperRect.top,
    };
    // `transform` is the React Flow viewport — re-anchor on pan/zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, node?.position.x, node?.position.y, node?.measured?.width, transform, flowToScreenPosition, wrapperRef]);
}

export default function Canvas({ nodeTypes, onNodeSelect, selectedNodeId, onGraphReady, onImportReady, onStateFieldsChange, visible = true }: Props) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(INITIAL_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [selectedTool, setSelectedTool] = useState<{ nodeId: string; toolId: string } | null>(null);

  // Derive state fields from the current nodes.
  const stateFields = useMemo(() => deriveStateFields(nodes), [nodes]);
  const stateVariableNames = useMemo(() => deriveStateNames(stateFields), [stateFields]);

  // Push the derived list up to App so save/deploy/chat consumers can read it.
  useEffect(() => {
    onStateFieldsChange?.(stateFields);
  }, [stateFields, onStateFieldsChange]);

  // Renaming a node retitles its state-field key and cascades ``{old}`` → ``{new}``
  // through every other node's string config values.
  const renameNode = useCallback(
    (nodeId: string, newName: string) => {
      setNodes((nds) => {
        const target = nds.find((n) => n.id === nodeId);
        if (!target) return nds;
        const trimmed = newName.trim();
        const oldField = (target.data?.writes_to as string) ?? "";
        const isRouter = (target.data?.is_router as boolean) ?? false;

        const others = new Set<string>();
        for (const n of nds) {
          if (n.id === nodeId) continue;
          const wt = (n.data?.writes_to as string) ?? "";
          if (wt) others.add(wt);
        }

        const displayName = (target.data?.display_name as string) ?? "Node";
        const newField = isRouter
          ? ""
          : deriveNodeFieldName(trimmed, displayName, nodeId, others);

        return nds.map((n) => {
          if (n.id === nodeId) {
            return {
              ...n,
              data: { ...n.data, name: trimmed, writes_to: isRouter ? "" : newField },
            };
          }
          // Cascade rename of {oldField} → {newField} in every other node's config strings.
          if (!oldField || !newField || oldField === newField) return n;
          const config = n.data?.config as Record<string, unknown> | undefined;
          if (!config) return n;
          let changed = false;
          const updated: Record<string, unknown> = { ...config };
          for (const key of Object.keys(updated)) {
            const v = updated[key];
            if (typeof v === "string" && v.includes(`{${oldField}}`)) {
              updated[key] = v.split(`{${oldField}}`).join(`{${newField}}`);
              changed = true;
            } else if (v === oldField) {
              updated[key] = newField;
              changed = true;
            }
          }
          return changed ? { ...n, data: { ...n.data, config: updated } } : n;
        });
      });
    },
    [setNodes],
  );

  const stateContextValue = useMemo(
    () => ({ names: stateVariableNames, fields: stateFields, renameNode }),
    [stateVariableNames, stateFields, renameNode],
  );

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
      const removedIds = new Set(
        safe.filter((c) => c.type === "remove").map((c) => c.id)
      );
      if (removedIds.size > 0) onNodeSelect(null);
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
      const derivedFields = deriveStateFields(nodesRef.current);
      return { nodes: graphNodes, edges: graphEdges, state_fields: derivedFields, output_fields: outputFields } as GraphDef;
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

      const isRouter = meta.type === "router";
      const newId = `node_${++nodeIdCounter}`;

      // Pick the header label + state-field key together so they stay aligned
      // ("LLM" / "llm", "LLM 2" / "llm_2"). Routers don't write to state.
      let writesTo = "";
      let initialName = "";
      if (!isRouter) {
        const taken = new Set<string>();
        for (const n of nodesRef.current) {
          const wt = (n.data?.writes_to as string) ?? "";
          if (wt) taken.add(wt);
        }
        const labeled = deriveNewNodeLabel(meta.display_name, taken);
        writesTo = labeled.writesTo;
        initialName = labeled.name;
      }

      const newNode: Node = {
        id: newId,
        type: "agentNode",
        position,
        data: {
          nodeType: meta.type,
          name: initialName,
          display_name: meta.display_name,
          description: meta.description,
          icon: meta.icon,
          color: meta.color,
          config_fields: meta.config_fields,
          is_router: isRouter,
          writes_to: writesTo,
          config: defaultConfig,
        },
      };

      setNodes((nds) => [...nds, newNode]);
    },
    [nodeTypes, setNodes, findLlmNodeAtPosition, screenToFlowPosition]
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
    <StateProvider value={stateContextValue}>
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
    </StateProvider>
  );
}
