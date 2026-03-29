import { useState, useRef, useEffect, useCallback } from "react";
import { previewGraph, validateGraph } from "../api";
import type { ChatMessage, GraphDef, StateFieldDef } from "../types";
import SimpleMarkdown from "./SimpleMarkdown";

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
  const [threadId, setThreadId] = useState<string | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const scrollOnToggle = useCallback((e: React.SyntheticEvent<HTMLDetailsElement>) => {
    const details = e.currentTarget;
    if (details.open) {
      setTimeout(() => {
        details.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 50);
    }
  }, []);

  const addErrorMessage = useCallback((error: string) => {
    const errMsg: ChatMessage = {
      id: `msg_${++msgId}`,
      role: "assistant",
      content: "",
      error,
    };
    setMessages((prev) => [...prev, errMsg]);
  }, []);

  const clearConversation = useCallback(() => {
    setMessages([]);
    setThreadId(null);
    setPendingInterrupt(false);
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() || isLoading) return;

    // ── Local preflight checks ──
    const preflightError = preflight(graphGetter, stateFieldsRef.current!);
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
      graph.state_fields = stateFieldsRef.current!;

      const validation = await validateGraph(graph);
      if (!validation.valid) {
        updatePlaceholder({
          content: "",
          error: validation.errors.join("\n"),
        });
        return;
      }

      // Branch: resume from interrupt vs. normal invocation
      const result = pendingInterrupt
        ? await previewGraph(graph, "", threadId, userInput)
        : await previewGraph(graph, userInput, threadId);

      if (!result.success) {
        updatePlaceholder({ content: "", error: result.error ?? "The agent returned an error." });
        setPendingInterrupt(false);
      } else if (result.interrupt) {
        // Graph paused at a HumanInput node — show the prompt
        setThreadId(result.thread_id);
        setPendingInterrupt(true);
        updatePlaceholder({
          content: result.interrupt,
        });
      } else {
        // Normal completion
        setThreadId(result.thread_id);
        setPendingInterrupt(false);
        updatePlaceholder({
          content: result.output || "(empty)",
          execution_trace: result.execution_trace,
          mlflow_trace: result.mlflow_trace,
        });
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : String(err);
      updatePlaceholder({
        content: "",
        error: `Something went wrong: ${message}`,
      });
      setPendingInterrupt(false);
    } finally {
      setIsLoading(false);
    }
  }, [input, graphGetter, stateFieldsRef, isLoading, addErrorMessage, threadId, pendingInterrupt]);

  return (
    <div className="chat-overlay" onClick={onClose}>
      <div className="chat-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="chat-header">
          <h2>Chat Playground</h2>
          <div className="chat-header-actions">
            <button className="btn btn-sm" onClick={clearConversation}>
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
                    <div className="chat-msg-content">
                      {(() => {
                        try {
                          const parsed = JSON.parse(msg.content);
                          if (typeof parsed === "object" && parsed !== null) {
                            return <pre className="chat-json-output">{JSON.stringify(parsed, null, 2)}</pre>;
                          }
                        } catch { /* not JSON, render as markdown */ }
                        return <SimpleMarkdown content={msg.content} />;
                      })()}
                    </div>
                  )}

                  {msg.error && (
                    <pre className="result-error">{msg.error}</pre>
                  )}

                  {msg.execution_trace && msg.execution_trace.length > 0 && (
                    <details className="result-details" onToggle={scrollOnToggle}>
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

                  {msg.mlflow_trace && msg.mlflow_trace.length > 0 && (
                    <details className="result-details" onToggle={scrollOnToggle}>
                      <summary>MLflow Trace ({msg.mlflow_trace.length} spans)</summary>
                      <div className="mlflow-trace">
                        {msg.mlflow_trace.map((span, i) => (
                          <details key={i} className="mlflow-span" onToggle={scrollOnToggle}>
                            <summary className="mlflow-span-header">
                              <span className={`mlflow-span-status mlflow-span-status-${span.status?.toLowerCase().replace(/[^a-z]/g, "")}`} />
                              <span className="mlflow-span-name">{span.name}</span>
                              {span.end_time_ms > 0 && span.start_time_ms > 0 && (
                                <span className="mlflow-span-duration">
                                  {span.end_time_ms - span.start_time_ms}ms
                                </span>
                              )}
                            </summary>
                            <div className="mlflow-span-body">
                              {span.inputs != null && (
                                <div className="mlflow-span-section">
                                  <span className="mlflow-span-label">Inputs</span>
                                  <pre className="mlflow-span-data">
                                    {String(typeof span.inputs === "string"
                                      ? span.inputs
                                      : JSON.stringify(span.inputs, null, 2))}
                                  </pre>
                                </div>
                              )}
                              {span.outputs != null && (
                                <div className="mlflow-span-section">
                                  <span className="mlflow-span-label">Outputs</span>
                                  <pre className="mlflow-span-data">
                                    {String(typeof span.outputs === "string"
                                      ? span.outputs
                                      : JSON.stringify(span.outputs, null, 2))}
                                  </pre>
                                </div>
                              )}
                            </div>
                          </details>
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
            placeholder={pendingInterrupt ? "Type your response..." : "Type a message..."}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            disabled={isLoading}
          />
          <button
            className="btn btn-primary"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
          >
            {isLoading ? "..." : pendingInterrupt ? "Reply" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
