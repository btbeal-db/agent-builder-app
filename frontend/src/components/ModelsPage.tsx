import { useState, useEffect, useCallback } from "react";
import { ExternalLink, Upload, Loader, Package, Brain, GitBranch, Search, BarChart3, FunctionSquare, User } from "lucide-react";
import { fetchModels, fetchModelGraph } from "../api";
import type { ModelInfo, GraphDef, StateFieldDef } from "../types";

interface Props {
  graphImporter: ((g: GraphDef) => void) | null;
  setStateFields: (fields: StateFieldDef[]) => void;
  onSwitchToBuilder: () => void;
}

const NODE_TYPE_ICONS: Record<string, typeof Brain> = {
  llm: Brain,
  router: GitBranch,
  vector_search: Search,
  genie: BarChart3,
  uc_function: FunctionSquare,
  human_input: User,
};

const DEPLOY_MODE_LABELS: Record<string, string> = {
  full: "Deployed",
  log_and_register: "Registered",
  log_only: "Logged",
};

function formatTime(raw: string | null): string {
  if (!raw) return "--";
  const d = new Date(raw);
  if (isNaN(d.getTime())) return "--";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function ModelsPage({ graphImporter, setStateFields, onSwitchToBuilder }: Props) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingGraph, setLoadingGraph] = useState<string | null>(null);

  useEffect(() => {
    fetchModels()
      .then((res) => setModels(res.models))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const handleLoadToCanvas = useCallback(async (model: ModelInfo) => {
    if (!model.latest_run_id || !model.has_graph_def || !graphImporter) return;
    setLoadingGraph(model.experiment_id);
    try {
      const graph = await fetchModelGraph(model.latest_run_id);
      if (graph.state_fields?.length) {
        setStateFields(graph.state_fields);
      }
      graphImporter(graph);
      onSwitchToBuilder();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to load graph");
    } finally {
      setLoadingGraph(null);
    }
  }, [graphImporter, setStateFields, onSwitchToBuilder]);

  if (loading) {
    return (
      <div className="models-page">
        <div className="models-loading">
          <Loader size={24} className="spinning" />
          <span>Loading models...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="models-page">
        <div className="models-header">
          <h1>Models</h1>
        </div>
        <div className="models-empty">
          <p>Failed to load models: {error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="models-page">
      <div className="models-header">
        <h1>Models</h1>
        <p>
          Agents you've deployed from this app. Click a model to open it in
          Databricks, or load it back to the canvas to iterate.
        </p>
      </div>

      {models.length === 0 ? (
        <div className="models-empty">
          <Package size={40} strokeWidth={1.2} />
          <h3>No models deployed yet</h3>
          <p>Build an agent in the Builder and deploy it to see it here.</p>
        </div>
      ) : (
        <div className="models-table-wrapper">
          <table className="models-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Last Deployed</th>
                <th>Status</th>
                <th>Nodes</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.experiment_id}>
                  <td>
                    <div className="models-name-cell">
                      <span className="models-name">{m.name}</span>
                      {m.registered_model_name && (
                        <span className="models-uc-name">{m.registered_model_name}</span>
                      )}
                    </div>
                  </td>
                  <td className="models-time">{formatTime(m.latest_run_time)}</td>
                  <td>
                    {m.deploy_mode ? (
                      <span className={`models-badge models-badge--${m.deploy_mode}`}>
                        {DEPLOY_MODE_LABELS[m.deploy_mode] ?? m.deploy_mode}
                      </span>
                    ) : (
                      <span className="models-badge">Unknown</span>
                    )}
                  </td>
                  <td>
                    {m.node_types.length > 0 ? (
                      <div className="models-nodes">
                        {m.node_types.map((t) => {
                          const Icon = NODE_TYPE_ICONS[t] ?? Brain;
                          return <span key={t} title={t}><Icon size={14} /></span>;
                        })}
                        <span className="models-node-count">{m.node_count}</span>
                      </div>
                    ) : (
                      "--"
                    )}
                  </td>
                  <td>
                    <div className="models-actions">
                      {m.has_graph_def && (
                        <button
                          className="btn btn-sm btn-ghost btn-with-icon"
                          onClick={() => handleLoadToCanvas(m)}
                          disabled={loadingGraph === m.experiment_id}
                          title="Load this graph to the builder canvas"
                        >
                          {loadingGraph === m.experiment_id ? (
                            <Loader size={12} className="spinning" />
                          ) : (
                            <Upload size={12} />
                          )}
                          Load
                        </button>
                      )}
                      {m.experiment_url && (
                        <a
                          href={m.experiment_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn btn-sm btn-ghost btn-with-icon"
                          title="Open experiment in Databricks"
                        >
                          <ExternalLink size={12} />
                          Databricks
                        </a>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
