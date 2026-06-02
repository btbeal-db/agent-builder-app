import { memo, useState, useRef, useEffect, useCallback } from "react";
import { ArrowUp, X, Trash2 } from "lucide-react";
import { streamPreview, validateGraph } from "../api";
import type { ChatMessage, GraphDef, TraceSpan } from "../types";
import SimpleMarkdown from "./SimpleMarkdown";

interface Props {
  graphGetter: (() => GraphDef) | null;
  onClose: () => void;
}

let msgId = 0;

/** Rotating verbs shown when the agent is mid-pause (waiting on a tool, an
 *  initial response, etc.). Pure flavor — there's no real distinction between
 *  them, but variety makes a long pause feel less like a hang. */
const THINKING_VERBS = [
  // cognitive
  "thinking", "pondering", "musing", "mulling", "ruminating", "reflecting",
  "contemplating", "cogitating", "considering", "deliberating", "weighing",
  "evaluating", "analyzing", "examining", "investigating", "discerning",
  "reasoning", "theorizing", "hypothesizing", "speculating", "conjecturing",
  "philosophizing", "meditating", "ideating", "brainstorming", "processing",
  "computing", "ratiocinating",
  // crafting
  "brewing", "cooking", "simmering", "marinating", "fermenting", "distilling",
  "refining", "polishing", "honing", "crafting", "forging", "weaving",
  "sculpting", "conjuring", "concocting", "whisking", "kneading", "composing",
  "orchestrating", "harmonizing",
  // searching
  "searching", "hunting", "foraging", "scouring", "sleuthing", "digging",
  "prospecting", "divining", "exploring", "probing", "sifting", "untangling",
  "unraveling", "decoding", "parsing", "spelunking",
  // playful
  "noodling", "doodling", "tinkering", "puttering", "fiddling", "finagling",
  "futzing", "dabbling", "woolgathering", "daydreaming", "caboodling",
  "gallivanting", "hemming", "hawing", "palavering", "scheming", "plotting",
  "moseying", "meandering",
  // growth
  "percolating", "germinating", "sprouting",
  // other
  "synthesizing", "assembling", "churning", "riffing", "jamming",
];
const pickVerb = () => THINKING_VERBS[Math.floor(Math.random() * THINKING_VERBS.length)];

const THINKING_INITIAL_MS = 0;   // show immediately on send
const THINKING_PAUSE_MS = 400;   // wait this long after a delta before re-showing
const VERB_ROTATE_MS = 1500;

/**
 * Run local checks on the graph before hitting the backend.
 * Returns an error string or null if everything looks good.
 */
function preflight(graphGetter: (() => GraphDef) | null): string | null {
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

  return null;
}

export default function ChatPlayground({ graphGetter, onClose }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Tracks the in-flight preview stream so we can abort it on unmount or
  // when the user fires off a new send before the previous one finishes.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

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
    const preflightError = preflight(graphGetter);
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
      thinking: pickVerb(),
    };

    // Drop trace blobs from prior turns. Each mlflow_trace/execution_trace can
    // be hundreds of KB (full LLM IO + span tree); without this, long sessions
    // grow the heap unboundedly and Chrome eventually OOM-kills the renderer.
    setMessages((prev) => {
      const trimmed = prev.map((m) =>
        m.execution_trace || m.mlflow_trace
          ? { ...m, execution_trace: undefined, mlflow_trace: undefined }
          : m
      );
      return [...trimmed, userMsg, placeholder];
    });
    const userInput = input.trim();
    setInput("");
    setIsLoading(true);

    const updatePlaceholder = (updates: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === placeholderId ? { ...m, ...updates } : m))
      );
    };

    // Thinking-indicator timers, scoped to this send. Re-armed after every
    // delta; cleared on terminal events or errors.
    let thinkingTimeout: number | null = null;
    let thinkingInterval: number | null = null;
    const stopThinking = () => {
      if (thinkingTimeout !== null) { clearTimeout(thinkingTimeout); thinkingTimeout = null; }
      if (thinkingInterval !== null) { clearInterval(thinkingInterval); thinkingInterval = null; }
    };
    const startThinking = (delay: number) => {
      stopThinking();
      thinkingTimeout = window.setTimeout(() => {
        updatePlaceholder({ thinking: pickVerb() });
        thinkingInterval = window.setInterval(() => {
          updatePlaceholder({ thinking: pickVerb() });
        }, VERB_ROTATE_MS);
      }, delay);
    };

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const graph = graphGetter!();

      const validation = await validateGraph(graph);
      if (!validation.valid) {
        updatePlaceholder({
          content: "",
          error: validation.errors.join("\n"),
        });
        return;
      }

      // Stream: append deltas to the placeholder live; the terminal event
      // (done | interrupt | error) finalizes the message and sets the
      // execution / mlflow trace at once.
      let streamedText = "";
      const messageInput = pendingInterrupt ? "" : userInput;
      const resumeValue = pendingInterrupt ? userInput : null;

      // Start the thinking indicator immediately — we expect the first delta
      // to take a moment, and any pause >400ms after a delta will re-arm it.
      startThinking(THINKING_INITIAL_MS);

      await streamPreview(graph, messageInput, threadId, resumeValue, null, (event) => {
        if (event.type === "delta") {
          stopThinking();
          streamedText += event.text;
          updatePlaceholder({ content: streamedText, thinking: null });
          startThinking(THINKING_PAUSE_MS);
        } else if (event.type === "done") {
          stopThinking();
          setThreadId(event.thread_id);
          setPendingInterrupt(false);
          // Prefer the streamed text — it's what the user actually saw and
          // matches the deployed agent's predict_stream UX. Fall back to the
          // computed output for non-streaming graphs (structured output,
          // pure non-LLM pipelines).
          updatePlaceholder({
            content: streamedText || event.output || "(empty)",
            thinking: null,
            execution_trace: event.execution_trace,
            mlflow_trace: event.mlflow_trace,
          });
        } else if (event.type === "interrupt") {
          stopThinking();
          setThreadId(event.thread_id);
          setPendingInterrupt(true);
          updatePlaceholder({
            content: event.prompt,
            thinking: null,
            execution_trace: event.execution_trace,
            mlflow_trace: event.mlflow_trace,
          });
        } else if (event.type === "error") {
          stopThinking();
          setPendingInterrupt(false);
          updatePlaceholder({ content: "", thinking: null, error: event.message });
        }
      }, controller.signal);
    } catch (err) {
      stopThinking();
      // Aborts (unmount or new send superseded this one) are intentional —
      // don't render them as errors.
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      const message =
        err instanceof Error ? err.message : String(err);
      updatePlaceholder({
        content: "",
        thinking: null,
        error: `Something went wrong: ${message}`,
      });
      setPendingInterrupt(false);
    } finally {
      stopThinking();
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setIsLoading(false);
    }
  }, [input, graphGetter, isLoading, addErrorMessage, threadId, pendingInterrupt]);

  return (
    <div className="chat-overlay" onClick={onClose}>
      <div className="chat-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="chat-header">
          <h2>Playground</h2>
          <div className="chat-header-actions">
            <button className="chat-icon-btn" onClick={clearConversation} title="Clear conversation">
              <Trash2 size={14} />
            </button>
            <button className="chat-icon-btn" onClick={onClose} title="Close">
              <X size={16} />
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
            <ChatMessageItem key={msg.id} msg={msg} onDetailsToggle={scrollOnToggle} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-bar">
          <div className="chat-input-pill">
            <input
              type="text"
              value={input}
              placeholder={pendingInterrupt ? "Type your response..." : "Message your agent..."}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={isLoading}
              autoFocus
            />
            <button
              className="chat-send-btn"
              onClick={handleSend}
              disabled={isLoading || !input.trim()}
              title={pendingInterrupt ? "Reply" : "Send"}
            >
              <ArrowUp size={16} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

type DetailsToggleHandler = (e: React.SyntheticEvent<HTMLDetailsElement>) => void;

interface ChatMessageItemProps {
  msg: ChatMessage;
  onDetailsToggle: DetailsToggleHandler;
}

const ChatMessageItem = memo(function ChatMessageItem({ msg, onDetailsToggle }: ChatMessageItemProps) {
  return (
    <div className={`chat-msg chat-msg-${msg.role}`}>
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

      {msg.thinking && (
        <div className="chat-msg-thinking">
          <span className="chat-msg-thinking-dot" />
          {msg.thinking}…
        </div>
      )}

      {msg.error && (
        <pre className="result-error">{msg.error}</pre>
      )}

      {msg.execution_trace && msg.execution_trace.length > 0 && (
        <details className="result-details" onToggle={onDetailsToggle}>
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
        <details className="result-details" onToggle={onDetailsToggle}>
          <summary>MLflow Trace ({msg.mlflow_trace.length} spans)</summary>
          <div className="mlflow-trace">
            {msg.mlflow_trace.map((span, i) => (
              <MlflowSpanItem key={i} span={span} onDetailsToggle={onDetailsToggle} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
});

interface MlflowSpanItemProps {
  span: TraceSpan;
  onDetailsToggle: DetailsToggleHandler;
}

/** Renders a single MLflow span. The body (which may contain large JSON
 *  payloads) is only mounted when the user expands the span — keeps
 *  JSON.stringify off the hot path during streaming and initial render. */
const MlflowSpanItem = memo(function MlflowSpanItem({ span, onDetailsToggle }: MlflowSpanItemProps) {
  const [open, setOpen] = useState(false);
  const handleToggle = useCallback((e: React.SyntheticEvent<HTMLDetailsElement>) => {
    setOpen(e.currentTarget.open);
    onDetailsToggle(e);
  }, [onDetailsToggle]);

  return (
    <details className="mlflow-span" onToggle={handleToggle}>
      <summary className="mlflow-span-header">
        <span className={`mlflow-span-status mlflow-span-status-${span.status?.toLowerCase().replace(/[^a-z]/g, "")}`} />
        <span className="mlflow-span-name">{span.name}</span>
        {span.end_time_ms > 0 && span.start_time_ms > 0 && (
          <span className="mlflow-span-duration">
            {span.end_time_ms - span.start_time_ms}ms
          </span>
        )}
      </summary>
      {open && (
        <div className="mlflow-span-body">
          {span.inputs != null && (
            <div className="mlflow-span-section">
              <span className="mlflow-span-label">Inputs</span>
              <pre className="mlflow-span-data">
                {typeof span.inputs === "string"
                  ? span.inputs
                  : JSON.stringify(span.inputs, null, 2)}
              </pre>
            </div>
          )}
          {span.outputs != null && (
            <div className="mlflow-span-section">
              <span className="mlflow-span-label">Outputs</span>
              <pre className="mlflow-span-data">
                {typeof span.outputs === "string"
                  ? span.outputs
                  : JSON.stringify(span.outputs, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </details>
  );
});
