import {
  Brain,
  GitBranch,
  Search,
  BarChart3,
  Rocket,
  MessageSquare,
  Save,
  Upload,
  FileCode,
  Shield,
  Workflow,
  ArrowRight,
  FunctionSquare,
  User,
  Wrench,
  History,
} from "lucide-react";

interface Props {
  onGetStarted: () => void;
}

export default function HomePage({ onGetStarted }: Props) {
  return (
    <div className="home">
      {/* Hero */}
      <section className="home-hero">
        <div className="home-hero-badge">Visual Agent Builder</div>
        <h1>Build AI agents without writing code</h1>
        <p>
          Design, test, and deploy LangGraph agents on Databricks using a
          drag-and-drop interface. Build prescriptive workflows, tool-calling
          agents, or conversational chatbots — then deploy to a production
          serving endpoint in one click.
        </p>
        <button className="btn btn-primary btn-lg" onClick={onGetStarted}>
          Start building
          <ArrowRight size={16} />
        </button>
      </section>

      {/* How it works */}
      <section className="home-section">
        <h2>How it works</h2>
        <div className="home-steps">
          <div className="home-step">
            <div className="home-step-num">1</div>
            <h3>Define your state model</h3>
            <p>
              Start by defining the data your agent works with — user input,
              retrieved context, analysis results. Each field flows through
              every step of your agent.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num">2</div>
            <h3>Build your graph</h3>
            <p>
              Drag components onto the canvas and connect them. Drop tools
              onto LLM nodes for autonomous tool calling, or wire nodes
              explicitly for prescriptive flows.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num">3</div>
            <h3>Test in the playground</h3>
            <p>
              Open the Chat Playground to send messages to your agent and see
              how it responds. Inspect the execution trace, tool calls, and
              state at every step to debug and refine.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num">4</div>
            <h3>Deploy to Databricks</h3>
            <p>
              One click logs your agent to MLflow, registers it in Unity
              Catalog, and creates a Model Serving endpoint — ready for
              production traffic.
            </p>
          </div>
        </div>
      </section>

      {/* Agent Patterns */}
      <section className="home-section">
        <h2>Agent patterns</h2>
        <p className="home-section-desc">
          Combine components in different ways to build the agent you need.
        </p>
        <div className="home-patterns">
          <div className="home-pattern">
            <div className="home-pattern-header">
              <Workflow size={18} />
              <h3>Prescriptive Graph</h3>
            </div>
            <p>
              Wire nodes in a fixed sequence. You control exactly what happens
              at each step — retrieve documents, call an LLM, route based on
              results. Best for deterministic pipelines like RAG.
            </p>
            <div className="home-pattern-diagram">
              <code>START &rarr; Vector Search &rarr; LLM &rarr; END</code>
            </div>
          </div>

          <div className="home-pattern">
            <div className="home-pattern-header">
              <Wrench size={18} />
              <h3>Tool Calling</h3>
            </div>
            <p>
              Drag tools directly onto an LLM node. The model autonomously
              decides which tools to call and loops until it has enough
              information to respond. Best for open-ended questions.
            </p>
            <div className="home-pattern-diagram">
              <code>START &rarr; LLM [+ tools] &rarr; END</code>
            </div>
          </div>

          <div className="home-pattern">
            <div className="home-pattern-header">
              <History size={18} />
              <h3>Conversational</h3>
            </div>
            <p>
              Enable the "Conversational" toggle on any LLM node to include
              message history. Pair with a Lakebase connection for multi-turn
              memory that persists across requests.
            </p>
            <div className="home-pattern-diagram">
              <code>START &rarr; LLM (conversational) &rarr; END</code>
            </div>
          </div>
        </div>
      </section>

      {/* Available components */}
      <section className="home-section">
        <h2>Available components</h2>
        <div className="home-cards">
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#8b5cf6" }}>
              <Brain size={18} />
            </div>
            <div>
              <h3>LLM</h3>
              <p>
                Call any Foundation Model endpoint on Databricks. Supports
                system prompts, structured output, conversation history, and
                tool calling — drop tools onto the node to enable autonomous
                tool use.
              </p>
            </div>
          </div>
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#06b6d4" }}>
              <Search size={18} />
            </div>
            <div>
              <h3>Vector Search</h3>
              <p>
                Query a Databricks Vector Search index to retrieve relevant
                documents. Use as a graph node for prescriptive RAG, or drop
                onto an LLM for autonomous retrieval with optional filters.
              </p>
            </div>
          </div>
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#f59e0b" }}>
              <BarChart3 size={18} />
            </div>
            <div>
              <h3>Genie Room</h3>
              <p>
                Ask natural-language questions against a Databricks Genie Room
                to get structured data answers. Use standalone or as an LLM
                tool for data-driven conversations.
              </p>
            </div>
          </div>
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#8b5cf6" }}>
              <FunctionSquare size={18} />
            </div>
            <div>
              <h3>UC Function</h3>
              <p>
                Execute Unity Catalog functions as actions. Use in a graph with
                explicit parameters, or attach to an LLM as a tool — the model
                decides when and how to call it.
              </p>
            </div>
          </div>
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#f59e0b" }}>
              <User size={18} />
            </div>
            <div>
              <h3>Human Input</h3>
              <p>
                Pause the agent and ask the user a question. Supports
                template variables from state in the prompt. The user's
                response flows into the target state field.
              </p>
            </div>
          </div>
          <div className="home-card">
            <div className="home-card-icon" style={{ background: "#ef4444" }}>
              <GitBranch size={18} />
            </div>
            <div>
              <h3>Router</h3>
              <p>
                Branch your agent's flow based on state values. Supports
                boolean, string matching, and multi-way routing with
                fallback paths.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Toolbar reference */}
      <section className="home-section">
        <h2>Toolbar actions</h2>
        <div className="home-ref-grid">
          <div className="home-ref-item">
            <Save size={16} />
            <div>
              <strong>Save JSON</strong>
              <span>Export your graph config to a file for version control or sharing</span>
            </div>
          </div>
          <div className="home-ref-item">
            <Upload size={16} />
            <div>
              <strong>Load JSON</strong>
              <span>Import a previously saved graph to continue editing</span>
            </div>
          </div>
          <div className="home-ref-item">
            <FileCode size={16} />
            <div>
              <strong>Export Python</strong>
              <span>Generate a standalone Python file to run your agent outside this tool</span>
            </div>
          </div>
          <div className="home-ref-item">
            <MessageSquare size={16} />
            <div>
              <strong>Chat Playground</strong>
              <span>Test your agent with real conversations and inspect each step</span>
            </div>
          </div>
          <div className="home-ref-item">
            <Rocket size={16} />
            <div>
              <strong>Deploy</strong>
              <span>Log to MLflow, register in Unity Catalog, and create a serving endpoint</span>
            </div>
          </div>
        </div>
      </section>

      {/* Permissions */}
      <section className="home-section">
        <h2>Required permissions</h2>
        <p className="home-section-desc">
          To build and deploy agents, you'll need the following access in your
          Databricks workspace. Contact your workspace admin if any of these
          are missing.
        </p>
        <div className="home-perms">
          <div className="home-perm">
            <Shield size={16} />
            <div>
              <strong>Foundation Model endpoints</strong>
              <span>CAN QUERY access to the serving endpoints your LLM nodes reference</span>
            </div>
          </div>
          <div className="home-perm">
            <Search size={16} />
            <div>
              <strong>Vector Search indexes</strong>
              <span>CAN USE access to any Vector Search indexes your agent queries</span>
            </div>
          </div>
          <div className="home-perm">
            <BarChart3 size={16} />
            <div>
              <strong>Genie Rooms</strong>
              <span>CAN RUN access to Genie Rooms used in your agent flow</span>
            </div>
          </div>
          <div className="home-perm">
            <FunctionSquare size={16} />
            <div>
              <strong>UC Functions</strong>
              <span>EXECUTE access to any Unity Catalog functions your agent calls</span>
            </div>
          </div>
          <div className="home-perm">
            <Workflow size={16} />
            <div>
              <strong>Unity Catalog</strong>
              <span>CREATE MODEL permission on the catalog and schema where you deploy</span>
            </div>
          </div>
          <div className="home-perm">
            <Rocket size={16} />
            <div>
              <strong>Model Serving</strong>
              <span>CAN MANAGE permission to create or update serving endpoints</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
