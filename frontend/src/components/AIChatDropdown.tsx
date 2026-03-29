import { useState, useRef, useEffect, useCallback, type RefObject } from "react";
import { Send } from "lucide-react";
import SimpleMarkdown from "./SimpleMarkdown";
import { sendAIChatMessage } from "../api";
import type { GraphDef, StateFieldDef } from "../types";

interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  graph?: GraphDef | null;
  loading?: boolean;
}

interface Props {
  graphGetter: (() => GraphDef) | null;
  graphImporter: ((g: GraphDef) => void) | null;
  stateFields: StateFieldDef[];
  setStateFields: (fields: StateFieldDef[]) => void;
  onSwitchToBuilder: () => void;
  onClose: () => void;
  wrapperRef: RefObject<HTMLDivElement | null>;
}

export default function AIChatDropdown({
  graphGetter,
  graphImporter,
  stateFields,
  setStateFields,
  onSwitchToBuilder,
  onClose,
  wrapperRef,
}: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Click-outside to close — checks against the wrapper (button + dropdown)
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onClose, wrapperRef]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMsg = { id: crypto.randomUUID(), role: "user", content: text };
    const loadingMsg: ChatMsg = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      loading: true,
    };

    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    setInput("");
    setSending(true);

    try {
      const apiMessages = [...messages.filter((m) => !m.loading), userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const currentGraph = graphGetter?.() ?? null;
      const resp = await sendAIChatMessage(apiMessages, currentGraph);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === loadingMsg.id
            ? { ...m, content: resp.message, graph: resp.graph, loading: false }
            : m,
        ),
      );
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === loadingMsg.id
            ? {
                ...m,
                content: `Error: ${err instanceof Error ? err.message : String(err)}`,
                loading: false,
              }
            : m,
        ),
      );
    } finally {
      setSending(false);
    }
  }, [input, sending, messages, graphGetter]);

  const handleApply = useCallback(
    (graph: GraphDef) => {
      if (!graphImporter) return;
      graphImporter(graph);
      if (graph.state_fields?.length) {
        setStateFields(graph.state_fields);
      }
      onSwitchToBuilder();
    },
    [graphImporter, setStateFields, onSwitchToBuilder],
  );

  return (
    <div className="ai-chat-dropdown">
      <div className="ai-chat-caret" />
      <div className="ai-chat-header">AI Chat</div>

      <div className="ai-chat-messages">
        {messages.length === 0 && (
          <div className="ai-chat-empty">
            Describe the agent you want to build and I'll generate a graph for you.
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-msg chat-msg-${msg.role}`}>
            <div className="chat-msg-content">
              {msg.loading ? (
                <span className="chat-msg-loading">Thinking...</span>
              ) : (
                <>
                  <SimpleMarkdown content={msg.content} />
                  {msg.graph && (
                    <button
                      className="ai-chat-apply-btn"
                      onClick={() => handleApply(msg.graph!)}
                    >
                      Apply to Canvas
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-bar">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
          placeholder="Describe your agent..."
          disabled={sending}
          autoFocus
        />
        <button
          className="btn btn-primary"
          onClick={handleSend}
          disabled={sending || !input.trim()}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
