import { useState } from "react";
import { X, Layers, GripVertical, Link, Wrench, Play, Save, ArrowLeft, ArrowRight } from "lucide-react";

interface Props {
  onDismiss: () => void;
}

/**
 * Each step anchors to a different part of the UI.
 *
 * position tells CSS where to render the popover:
 *   "left-top"     → next to the State Model section
 *   "left-bottom"  → next to the Components palette
 *   "center"       → center of the canvas
 *   "top-right"    → near the toolbar actions
 *
 * highlight is a CSS selector to pulse with a highlight ring.
 */
interface Step {
  icon: JSX.Element;
  title: string;
  text: string;
  position: "left-top" | "left-bottom" | "center" | "top-right";
  highlight: string;
}

const STEPS: Step[] = [
  {
    icon: <Layers size={16} />,
    title: "Define your state",
    text: "The State Model defines your agent's shared memory. Every node reads the full state and updates a specific field via its \"writes to\" setting.",
    position: "left-top",
    highlight: ".state-panel",
  },
  {
    icon: <GripVertical size={16} />,
    title: "Add components",
    text: "Drag nodes from the Components palette onto the canvas. Each node performs one action — call an LLM, search documents, route logic, or ask the user a question.",
    position: "left-bottom",
    highlight: ".palette",
  },
  {
    icon: <Link size={16} />,
    title: "Connect & configure",
    text: "Drag between node handles to set the execution order. Click a node to configure it and set which state field it \"writes to\" — that's how data flows through your agent.",
    position: "center",
    highlight: ".canvas-wrapper",
  },
  {
    icon: <Wrench size={16} />,
    title: "Attach tools",
    text: "Drop a Vector Search, Genie, or UC Function node directly onto an LLM node. The LLM autonomously decides when to call its tools — no wiring needed.",
    position: "center",
    highlight: ".canvas-wrapper",
  },
  {
    icon: <Save size={16} />,
    title: "Save & restore",
    text: "Your entire graph — nodes, edges, configs, and state model — is just JSON. Use Save to export it and Load to restore it later or share with others. Version control friendly.",
    position: "top-right",
    highlight: ".header-group",
  },
  {
    icon: <Play size={16} />,
    title: "Test & deploy",
    text: "Open the Playground to chat with your agent. Inspect the trace, state, and MLflow spans at each step. When ready, Deploy to a serving endpoint in one click.",
    position: "top-right",
    highlight: ".header-actions",
  },
];

export default function BuilderWalkthrough({ onDismiss }: Props) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];

  return (
    <>
      {/* Highlight ring on the target element */}
      <style>{`
        ${current.highlight} {
          outline: 2px solid var(--accent) !important;
          outline-offset: -2px;
          transition: outline 0.3s ease;
        }
      `}</style>

      <div className={`walkthrough walkthrough-${current.position}`}>
        <button className="walkthrough-close" onClick={onDismiss} title="Dismiss">
          <X size={14} />
        </button>

        <div className="walkthrough-step">
          <div className="walkthrough-icon">{current.icon}</div>
          <div>
            <div className="walkthrough-title">
              <span className="walkthrough-step-num">{step + 1}/{STEPS.length}</span>
              {current.title}
            </div>
            <div className="walkthrough-text">{current.text}</div>
          </div>
        </div>

        <div className="walkthrough-footer">
          <div className="walkthrough-dots">
            {STEPS.map((_, i) => (
              <button
                key={i}
                className={`walkthrough-dot${i === step ? " active" : ""}`}
                onClick={() => setStep(i)}
              />
            ))}
          </div>
          <div className="walkthrough-nav">
            {step > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => setStep(step - 1)}>
                <ArrowLeft size={12} /> Back
              </button>
            )}
            {step < STEPS.length - 1 ? (
              <button className="btn btn-sm walkthrough-next" onClick={() => setStep(step + 1)}>
                Next <ArrowRight size={12} />
              </button>
            ) : (
              <button className="btn btn-primary btn-sm" onClick={onDismiss}>
                Got it
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
