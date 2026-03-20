import { useState, useRef, useEffect, useCallback } from "react";
import { previewGraph, validateGraph } from "../api";
import type { ChatMessage, GraphDef, StateFieldDef } from "../types";

interface Props {
  graphGetter: (() => GraphDef) | null;
  stateFieldsRef: React.RefObject<StateFieldDef[]>;
  onClose: () => void;
}

let msgId = 0;

/**
 * Run local checks on the graph before hitting the backend.
 * Returns an error string or null if everything looks good.
 */
function preflight(graphGetter: (() => GraphDef) | null, stateFields: StateFieldDef[]): string | null {
  if (!graphGetter) {
    return "The graph hasn't loaded yet. Try closing and reopening the playground.";
  }

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

  if (!hasStart && !hasEnd) {
    return "Connect the START node to your first node and your last node to the END node.";
  }
  if (!hasStart) {
    return "Connect the START node to your first node.";
  }
  if (!hasEnd) {
    return "Connect your last node to the END node.";
  }

  // Check that non-router nodes have a writes_to field set
  for (const node of graph.nodes) {
    if (node.type === "router") continue;
    if (!node.writes_to) {
      return `Node "${node.id}" doesn't have a target state field selected. Click on it and choose which field it updates.`;
    }
    if (!stateFields.some((f) => f.name === node.writes_to)) {
      return `Node "${node.id}" writes to "${node.writes_to}" which doesn't exist in your state model.`;
    }
  }

  return null;
}

export default function ChatPlayground({ graphGetter, stateFieldsRef, onClose }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const addErrorMessage = useCallback((error: string) => {
    const errMsg: ChatMessage = {
      id: `msg_${++msgId}`,
      role: "assistant",
      content: "",
      error,
    };
    setMessages((prev) => [...prev, errMsg]);
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() || isLoading) return;

    // ── Local preflight checks ──
    const preflightError = preflight(graphGetter, stateFieldsRef.current);
    if (preflightError) {
      const userMsg: ChatMessage = { id: `msg_${++msgId}`, role: "user", content: input.trim() };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      addErrorMessage(preflightError);
      return;
    }

    const userMsg: ChatMessage = {
      id: `msg_${++msgId}`,
      role: "user",
      content: input.trim(),
    };

    const placeholderId = `msg_${++msgId}`;
    const placeholder: ChatMessage = {
      id: placeholderId,
      role: "assistant",
      content: "",
      loading: true,
    };

    setMessages((prev) => [...prev, userMsg, placeholder]);
    const userInput = input.trim();
    setInput("");
    setIsLoading(true);

    const updatePlaceholder = (updates: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === placeholderId ? { ...m, loading: false, ...updates } : m))
      );
    };

    try {
      const graph = graphGetter!();
      graph.state_fields = stateFieldsRef.current;

      const validation = await validateGraph(graph);
      if (!validation.valid) {
        updatePlaceholder({
          content: "",
          error: validation.errors.join("\n"),
        });
        return;
      }

      const result = await previewGraph(graph, userInput);

      if (!result.success) {
        updatePlaceholder({ content: "", error: result.error ?? "The agent returned an error." });
      } else {
        updatePlaceholder({
          content: result.output || "(empty)",
          execution_trace: result.execution_trace,
          state: result.state,
        });
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : String(err);
      updatePlaceholder({
        content: "",
        error: `Something went wrong: ${message}`,
      });
    } finally {
      setIsLoading(false);
    }
  }, [input, graphGetter, stateFieldsRef, isLoading, addErrorMessage]);

  return (
    <div className="chat-overlay" onClick={onClose}>
      <div className="chat-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="chat-header">
          <h2>Chat Playground</h2>
          <div className="chat-header-actions">
            <button className="btn btn-sm" onClick={() => setMessages([])}>
              Clear
            </button>
            <button className="btn btn-sm" onClick={onClose}>
              &times;
            </button>
          </div>
        </div>

        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              Send a message to test your agent.
            </div>
          )}

          {messages.map((msg) => (
            <div key={msg.id} className={`chat-msg chat-msg-${msg.role}`}>
              {msg.loading ? (
                <div className="chat-msg-loading">Thinking...</div>
              ) : (
                <>
                  {msg.content && (
                    <div className="chat-msg-content">{msg.content}</div>
                  )}

                  {msg.error && (
                    <pre className="result-error">{msg.error}</pre>
                  )}

                  {msg.execution_trace && msg.execution_trace.length > 0 && (
                    <details className="result-details">
                      <summary>Trace ({msg.execution_trace.length} steps)</summary>
                      <div className="trace">
                        {msg.execution_trace.map((step, i) => (
                          <div key={i} className="trace-step">
                            <span className="trace-badge">{step.node ?? step.role}</span>
                            <span className="trace-content">{step.content}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}

                  {msg.state && Object.keys(msg.state).length > 0 && (
                    <details className="result-details">
                      <summary>State</summary>
                      <div className="state-grid">
                        {Object.entries(msg.state).map(([key, val]) => (
                          <div key={key} className="state-entry">
                            <span className="state-key">{key}</span>
                            <pre className="state-val">{val || "(empty)"}</pre>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </>
              )}
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-bar">
          <input
            type="text"
            value={input}
            placeholder="Type a message..."
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            disabled={isLoading}
          />
          <button
            className="btn btn-primary"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
          >
            {isLoading ? "..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
