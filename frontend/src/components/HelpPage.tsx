import {
  Brain,
  GitBranch,
  Search,
  BarChart3,
  FunctionSquare,
  User,
  Wrench,
  Workflow,
  ArrowRight,
  MousePointerClick,
  Layers,
  Link,
  GripVertical,
  History,
  MessageSquare,
  Rocket,
  Save,
  Upload,
  FileCode,
  CircleDot,
  Settings,
  Zap,
  Info,
  AlertTriangle,
} from "lucide-react";

interface Props {
  onGoToBuilder: () => void;
}

export default function HelpPage({ onGoToBuilder }: Props) {
  return (
    <div className="home">
      {/* Hero */}
      <section className="home-hero">
        <div className="home-hero-badge">Documentation</div>
        <h1>How to use Agent Builder</h1>
        <p>
          A complete guide to building, testing, and deploying AI agents. Learn
          how the canvas, state model, tool use, and graph execution work
          together.
        </p>
      </section>

      {/* Table of contents */}
      <section className="home-section">
        <h2>Contents</h2>
        <div className="help-toc">
          <a href="#concepts">Core concepts</a>
          <a href="#state-model">State model</a>
          <a href="#canvas">Using the canvas</a>
          <a href="#nodes">Node types</a>
          <a href="#tool-use">Tool use</a>
          <a href="#graph">How the graph works</a>
          <a href="#playground">Chat Playground</a>
          <a href="#toolbar">Toolbar actions</a>
          <a href="#deploy">Deploying your agent</a>
          <a href="#tips">Tips &amp; troubleshooting</a>
        </div>
      </section>

      {/* Core concepts */}
      <section className="home-section" id="concepts">
        <h2>Core concepts</h2>
        <p className="home-section-desc">
          Agent Builder uses a <strong>graph-based</strong> model. Your agent is
          a directed graph where each node performs an action (call an LLM,
          search documents, route logic) and edges define the execution order.
          Data flows through a shared <strong>state</strong> object that every
          node can read from and write to.
        </p>
        <div className="home-steps">
          <div className="home-step">
            <div className="home-step-num"><Layers size={14} /></div>
            <h3>State</h3>
            <p>
              A shared data object with named fields. Every node reads input
              from state and writes its output back. You define the shape of
              state before building.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num"><CircleDot size={14} /></div>
            <h3>Nodes</h3>
            <p>
              Individual steps in your agent — LLM calls, searches, routers,
              etc. Each node has a configuration panel and a "writes to" field
              that determines which state variable receives its output.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num"><Link size={14} /></div>
            <h3>Edges</h3>
            <p>
              Connections between nodes that define execution order. Drag from
              one node's output handle to another node's input handle to create
              an edge.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num"><Wrench size={14} /></div>
            <h3>Tools</h3>
            <p>
              Capabilities you can attach to LLM nodes. Instead of wiring a
              fixed path, the LLM autonomously decides when and how to call its
              tools.
            </p>
          </div>
        </div>
      </section>

      {/* State model */}
      <section className="home-section" id="state-model">
        <h2>State model</h2>
        <p className="home-section-desc">
          The state model defines the data your agent works with. When you first
          open the Builder, you'll be prompted to define your state fields.
        </p>
        <div className="help-detail-grid">
          <div className="help-detail">
            <h3>Defining fields</h3>
            <p>
              Each state field has a <strong>name</strong>, a <strong>type</strong>{" "}
              (string, list of strings, structured, etc.), and an optional
              description. Fields are accessible to every node in your graph.
            </p>
          </div>
          <div className="help-detail">
            <h3>How nodes use state</h3>
            <p>
              Every node has a <strong>"writes to"</strong> dropdown that
              specifies which state field receives the node's output. Nodes can
              also <em>read</em> from state — for example, an LLM's system
              prompt can reference <code>{"{{context}}"}</code> to inject a
              state variable.
            </p>
          </div>
          <div className="help-detail">
            <h3>Structured fields</h3>
            <p>
              Use the "structured" type for complex data with sub-fields. For
              example, a <code>user_profile</code> field could have sub-fields
              like <code>name</code>, <code>role</code>, and{" "}
              <code>department</code>.
            </p>
          </div>
          <div className="help-detail">
            <h3>Editing later</h3>
            <p>
              Click the <strong>State</strong> summary in the left panel at any
              time to re-open the state model editor and add, remove, or modify
              fields.
            </p>
          </div>
        </div>
      </section>

      {/* Canvas */}
      <section className="home-section" id="canvas">
        <h2>Using the canvas</h2>
        <p className="home-section-desc">
          The canvas is where you visually build your agent graph. Here's how to
          interact with it.
        </p>
        <div className="home-ref-grid">
          <div className="home-ref-item">
            <GripVertical size={16} />
            <div>
              <strong>Add nodes</strong>
              <span>
                Drag a component from the left panel onto the canvas. It will
                appear as a new node you can position and configure.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Link size={16} />
            <div>
              <strong>Connect nodes</strong>
              <span>
                Drag from a node's bottom handle to another node's top handle to
                create an edge. This defines the execution order of your graph.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Settings size={16} />
            <div>
              <strong>Configure nodes</strong>
              <span>
                Click any node to open its configuration panel. Each node type
                has specific settings — endpoints, prompts, indexes, etc.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <MousePointerClick size={16} />
            <div>
              <strong>Delete nodes &amp; edges</strong>
              <span>
                Select a node or edge and press <kbd>Delete</kbd> or{" "}
                <kbd>Backspace</kbd> to remove it from the graph.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Wrench size={16} />
            <div>
              <strong>Attach tools to LLM nodes</strong>
              <span>
                Drag a tool-compatible component (Vector Search, Genie, UC
                Function) directly onto an LLM node. It appears as a chip inside
                the node — click the chip to configure the tool.
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Node types */}
      <section className="home-section" id="nodes">
        <h2>Node types in detail</h2>
        <p className="home-section-desc">
          Each node type serves a specific purpose. Some can be used both as
          standalone graph nodes and as tools attached to LLM nodes.
        </p>
        <div className="help-node-details">
          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#8b5cf6" }}>
              <Brain size={18} />
            </div>
            <div>
              <h3>LLM</h3>
              <p>
                Calls a Foundation Model endpoint on Databricks. Configure the
                endpoint name, system prompt, and which state field provides the
                user message. Supports:
              </p>
              <ul>
                <li><strong>System prompts</strong> — with <code>{"{{variable}}"}</code> template syntax to inject state values</li>
                <li><strong>Tool calling</strong> — drag tools onto the node; the LLM loops calling tools until it has an answer</li>
                <li><strong>Structured output</strong> — constrain the LLM's response to a JSON schema</li>
                <li><strong>Conversation history</strong> — toggle "Conversational" to include prior messages (requires Lakebase for persistence across requests)</li>
                <li><strong>Max iterations</strong> — limit how many tool-call loops the LLM can make (default: 10)</li>
              </ul>
            </div>
          </div>

          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#06b6d4" }}>
              <Search size={18} />
            </div>
            <div>
              <h3>Vector Search</h3>
              <p>
                Queries a Databricks Vector Search index to retrieve relevant
                documents. Can be used two ways:
              </p>
              <ul>
                <li><strong>As a graph node</strong> — place it in the graph for prescriptive RAG; it reads a query from state and writes retrieved documents back</li>
                <li><strong>As an LLM tool</strong> — drag onto an LLM node; the model decides when to search and what to query</li>
              </ul>
              <p>
                Configure the index name, number of results, and optional column
                filters.
              </p>
            </div>
          </div>

          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#f59e0b" }}>
              <BarChart3 size={18} />
            </div>
            <div>
              <h3>Genie Room</h3>
              <p>
                Sends a natural-language question to a Databricks Genie Room and
                returns structured data results. Two usage modes:
              </p>
              <ul>
                <li><strong>As a graph node</strong> — reads a question from state, writes the answer back</li>
                <li><strong>As an LLM tool</strong> — the LLM formulates questions and calls Genie autonomously</li>
              </ul>
              <p>Configure the Genie Room ID (the space ID from the Genie URL).</p>
            </div>
          </div>

          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#8b5cf6" }}>
              <FunctionSquare size={18} />
            </div>
            <div>
              <h3>UC Function</h3>
              <p>
                Executes a Unity Catalog function. Provide the full function name
                (<code>catalog.schema.function_name</code>). Two usage modes:
              </p>
              <ul>
                <li><strong>As a graph node</strong> — pass parameters from state explicitly</li>
                <li><strong>As an LLM tool</strong> — the LLM decides when to call the function and supplies arguments</li>
              </ul>
            </div>
          </div>

          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#ef4444" }}>
              <GitBranch size={18} />
            </div>
            <div>
              <h3>Router</h3>
              <p>
                Branches your graph based on a state value. Define routes as
                value-to-node mappings. When the router evaluates, it checks the
                state field and sends execution down the matching branch.
              </p>
              <ul>
                <li>Supports string matching, boolean routing, and a default fallback</li>
                <li>Each route creates a separate output handle on the node</li>
                <li>Connect each handle to the appropriate downstream node</li>
              </ul>
            </div>
          </div>

          <div className="help-node-detail">
            <div className="home-card-icon" style={{ background: "#f59e0b" }}>
              <User size={18} />
            </div>
            <div>
              <h3>Human Input</h3>
              <p>
                Pauses the agent and presents a question to the user. The user's
                response is written to the target state field. Useful for:
              </p>
              <ul>
                <li>Gathering clarifying information mid-flow</li>
                <li>Approval gates before expensive operations</li>
                <li>Template variables from state: <code>{"{{field_name}}"}</code> in the prompt text</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* Tool use */}
      <section className="home-section" id="tool-use">
        <h2>How tool use works</h2>
        <p className="home-section-desc">
          Tool use lets an LLM autonomously decide which actions to take rather
          than following a fixed path. This is a key distinction from
          prescriptive graphs.
        </p>

        <div className="home-patterns">
          <div className="home-pattern">
            <div className="home-pattern-header">
              <Workflow size={18} />
              <h3>Prescriptive (no tools)</h3>
            </div>
            <p>
              You define the exact path: <em>always</em> search, <em>then</em>{" "}
              call the LLM, <em>then</em> route. Every execution follows the
              same sequence.
            </p>
            <div className="home-pattern-diagram">
              <code>Search &rarr; LLM &rarr; Router &rarr; ...</code>
            </div>
          </div>

          <div className="home-pattern">
            <div className="home-pattern-header">
              <Wrench size={18} />
              <h3>Tool calling (autonomous)</h3>
            </div>
            <p>
              You give the LLM tools. It decides <em>which</em> to call,{" "}
              <em>how many times</em>, and <em>in what order</em>. It loops
              until it can answer or hits max iterations.
            </p>
            <div className="home-pattern-diagram">
              <code>LLM &harr; Tool 1 / Tool 2 / Tool 3</code>
            </div>
          </div>

          <div className="home-pattern">
            <div className="home-pattern-header">
              <Zap size={18} />
              <h3>Hybrid</h3>
            </div>
            <p>
              Combine both: use prescriptive edges for the overall flow, but
              give specific LLM nodes tools for steps that need flexibility.
            </p>
            <div className="home-pattern-diagram">
              <code>Search &rarr; LLM [+tools] &rarr; Router</code>
            </div>
          </div>
        </div>

        <div className="help-callout" style={{ marginTop: "1rem" }}>
          <Info size={16} />
          <div>
            <strong>How the tool loop works</strong>
            <p>
              When an LLM node has tools attached, execution follows this cycle:
              (1) The LLM is called with the conversation so far. (2) If the LLM
              responds with a tool call, the tool is executed and its result is
              added to the conversation. (3) The LLM is called again with the
              tool result. This repeats until the LLM produces a final text
              response or hits the max iteration limit.
            </p>
          </div>
        </div>

        <div className="help-detail-grid" style={{ marginTop: "1rem" }}>
          <div className="help-detail">
            <h3>Attaching tools</h3>
            <p>
              Drag a tool-compatible node type (Vector Search, Genie, UC
              Function) from the palette <strong>directly onto</strong> an
              existing LLM node on the canvas. It will appear as a colored chip
              inside the LLM node.
            </p>
          </div>
          <div className="help-detail">
            <h3>Configuring tools</h3>
            <p>
              Click a tool chip to open its configuration panel. Each tool type
              has its own settings (index name, room ID, function name, etc.).
              The tool's configuration is independent of using the same type as a
              graph node.
            </p>
          </div>
          <div className="help-detail">
            <h3>Max iterations</h3>
            <p>
              The LLM's "Max iterations" setting (default: 10) limits how many
              tool-call rounds the model can make. If reached, the LLM is forced
              to produce a final response.
            </p>
          </div>
          <div className="help-detail">
            <h3>Tool vs. graph node</h3>
            <p>
              The same component (e.g., Vector Search) can exist in both forms in
              one graph — as a standalone node in a prescriptive path <em>and</em>{" "}
              as a tool on an LLM node. They are configured independently.
            </p>
          </div>
        </div>
      </section>

      {/* Graph execution */}
      <section className="home-section" id="graph">
        <h2>How the graph works</h2>
        <p className="home-section-desc">
          Understanding execution order helps you build reliable agents.
        </p>
        <div className="home-ref-grid">
          <div className="home-ref-item">
            <ArrowRight size={16} />
            <div>
              <strong>Entry point</strong>
              <span>
                Nodes with no incoming edges are entry nodes — they execute first
                when the graph runs. Typically you have one entry node.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Link size={16} />
            <div>
              <strong>Edge execution</strong>
              <span>
                After a node completes, execution follows its outgoing edges.
                Regular edges always fire. Router edges fire conditionally based
                on the state value.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <GitBranch size={16} />
            <div>
              <strong>Branching &amp; routing</strong>
              <span>
                Router nodes inspect a state field and direct flow to one of
                their output branches. Each route maps a value to a target node.
                A default/fallback route handles unmatched values.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <CircleDot size={16} />
            <div>
              <strong>Terminal nodes</strong>
              <span>
                Nodes with no outgoing edges are terminal — they end the graph
                execution. The final state after all terminal nodes complete is
                the agent's response.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <History size={16} />
            <div>
              <strong>Conversation history</strong>
              <span>
                When an LLM node has "Conversational" enabled, it includes prior
                message history. For multi-turn memory that persists across
                requests, connect a Lakebase database.
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Playground */}
      <section className="home-section" id="playground">
        <h2>Chat Playground</h2>
        <p className="home-section-desc">
          The Playground lets you test your agent with real conversations before
          deploying.
        </p>
        <div className="help-detail-grid">
          <div className="help-detail">
            <h3>Sending messages</h3>
            <p>
              Type a message and send it. Your graph is built and executed
              server-side. The response shows the agent's final output along with
              an execution trace of every step.
            </p>
          </div>
          <div className="help-detail">
            <h3>Execution trace</h3>
            <p>
              Expand the trace to see each node that executed, what it produced,
              any tool calls made, and the state at each step. This is invaluable
              for debugging.
            </p>
          </div>
          <div className="help-detail">
            <h3>Human input interrupts</h3>
            <p>
              If your graph includes a Human Input node, the playground will
              pause and present the question. Your response continues the graph
              from where it left off.
            </p>
          </div>
          <div className="help-detail">
            <h3>Iterating</h3>
            <p>
              Close the playground, adjust your graph or node configs, then
              re-open to test again. The playground always uses the latest graph
              state.
            </p>
          </div>
        </div>
      </section>

      {/* Toolbar */}
      <section className="home-section" id="toolbar">
        <h2>Toolbar actions</h2>
        <div className="home-ref-grid">
          <div className="home-ref-item">
            <Save size={16} />
            <div>
              <strong>Save JSON</strong>
              <span>
                Downloads your graph definition as a JSON file. Includes all
                nodes, edges, configurations, and state fields. Use this for
                version control, sharing, or backup.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Upload size={16} />
            <div>
              <strong>Load JSON</strong>
              <span>
                Import a previously saved graph JSON file. Restores all nodes,
                edges, configs, and state fields to the canvas.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <FileCode size={16} />
            <div>
              <strong>Export Python</strong>
              <span>
                Generates a standalone Python file that recreates your agent
                using LangGraph. You can run it outside this tool, customize it
                further, or use it as a starting point for production code.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <MessageSquare size={16} />
            <div>
              <strong>Chat Playground</strong>
              <span>
                Opens the interactive test environment. Send messages to your
                agent and inspect the full execution trace at every step.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Rocket size={16} />
            <div>
              <strong>Deploy</strong>
              <span>
                Logs your agent to MLflow, registers it in Unity Catalog, and
                creates a Model Serving endpoint. Requires a catalog, schema, and
                model name.
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Deploy */}
      <section className="home-section" id="deploy">
        <h2>Deploying your agent</h2>
        <p className="home-section-desc">
          Deployment is a one-click process that takes your graph from the
          canvas to a production serving endpoint.
        </p>
        <div className="home-steps">
          <div className="home-step">
            <div className="home-step-num">1</div>
            <h3>Log to MLflow</h3>
            <p>
              Your agent code and configuration are packaged and logged as an
              MLflow model artifact.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num">2</div>
            <h3>Register in Unity Catalog</h3>
            <p>
              The model is registered in UC at the catalog and schema you
              specify, making it discoverable and governed.
            </p>
          </div>
          <div className="home-step">
            <div className="home-step-num">3</div>
            <h3>Create serving endpoint</h3>
            <p>
              A Model Serving endpoint is created (or updated) so your agent can
              receive requests over REST.
            </p>
          </div>
        </div>
        <div className="help-callout" style={{ marginTop: "1rem" }}>
          <AlertTriangle size={16} />
          <div>
            <strong>Lakebase required for conversational agents</strong>
            <p>
              If any LLM node has "Conversational" enabled, you must provide a
              Lakebase connection in the deploy modal. This is required for
              multi-turn memory to persist across requests to the serving
              endpoint.
            </p>
          </div>
        </div>
      </section>

      {/* Tips */}
      <section className="home-section" id="tips">
        <h2>Tips &amp; troubleshooting</h2>
        <div className="home-ref-grid">
          <div className="home-ref-item">
            <Info size={16} />
            <div>
              <strong>Start simple</strong>
              <span>
                Begin with a single LLM node connected to START and END. Test
                that it works in the playground before adding complexity.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Info size={16} />
            <div>
              <strong>Use template variables in prompts</strong>
              <span>
                Reference state fields in LLM system prompts with{" "}
                <code>{"{{field_name}}"}</code> syntax. The value is injected at
                runtime.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <Info size={16} />
            <div>
              <strong>Check the execution trace</strong>
              <span>
                When your agent gives unexpected results, expand the execution
                trace in the playground. It shows exactly what each node
                received and produced.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <AlertTriangle size={16} />
            <div>
              <strong>Node must have "writes to"</strong>
              <span>
                Every node needs a target state field. If a node's output seems
                missing, check that its "writes to" dropdown is set to the
                correct field.
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <AlertTriangle size={16} />
            <div>
              <strong>Disconnected nodes are ignored</strong>
              <span>
                Nodes that aren't reachable from the entry point won't execute.
                Make sure all nodes have incoming edges (except the entry node).
              </span>
            </div>
          </div>
          <div className="home-ref-item">
            <AlertTriangle size={16} />
            <div>
              <strong>Router needs matching handles connected</strong>
              <span>
                After defining routes on a Router node, you must connect each
                output handle to a downstream node. Unconnected routes will cause
                validation errors.
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="home-hero" style={{ marginTop: "1rem" }}>
        <h1>Ready to build?</h1>
        <p>Jump into the builder and start creating your agent.</p>
        <button className="btn btn-primary btn-lg" onClick={onGoToBuilder}>
          Open Builder
          <ArrowRight size={16} />
        </button>
      </section>
    </div>
  );
}
