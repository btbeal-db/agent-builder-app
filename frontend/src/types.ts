export interface StateSubField {
  name: string;
  type: string;
  description: string;
}

export interface StateFieldDef {
  name: string;
  type: string;  // str | int | float | bool | list[str] | structured
  description: string;
  sub_fields: StateSubField[];
}

export interface NodeConfigField {
  name: string;
  label: string;
  field_type: string;
  required: boolean;
  default: unknown;
  options: string[] | null;
  placeholder: string;
  help_text: string;
  fetch_endpoint: string;
  advanced?: boolean;
}

export interface DiscoveryOption {
  value: string;
  label: string;
  description: string;
  provider?: string | null;
}

export interface DiscoveryResponse {
  options: DiscoveryOption[];
  error?: string | null;
}

export interface NodeTypeMetadata {
  type: string;
  display_name: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  config_fields: NodeConfigField[];
  tool_compatible: boolean;
}

export interface AttachedTool {
  id: string;
  type: string;          // "uc_function" | "vector_search" | "genie"
  display_name: string;
  icon: string;
  color: string;
  config: Record<string, unknown>;
}

export interface GraphNode {
  id: string;
  type: string;
  name: string;
  writes_to: string;
  config: Record<string, unknown>;
  position: { x: number; y: number };
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  source_handle?: string | null;
}

export interface GraphDef {
  nodes: GraphNode[];
  edges: GraphEdge[];
  state_fields: StateFieldDef[];
  output_fields: string[];
}

export interface TraceSpan {
  name: string;
  status: string;
  start_time_ms: number;
  end_time_ms: number;
  inputs?: unknown;
  outputs?: unknown;
}

export type ExecutionTrace = Array<{ role: string; content: string; node?: string }>;

export type PreviewEvent =
  | { type: "delta"; text: string }
  | {
      type: "done";
      thread_id: string;
      output: string;
      execution_trace: ExecutionTrace;
      state: Record<string, string>;
      mlflow_trace: TraceSpan[];
    }
  | {
      type: "interrupt";
      thread_id: string;
      prompt: string;
      execution_trace: ExecutionTrace;
      state: Record<string, string>;
      mlflow_trace: TraceSpan[];
    }
  | { type: "error"; message: string };

export interface ExportResponse {
  success: boolean;
  code: string;
  error: string | null;
}

export type DeployMode = "log_only" | "log_and_register" | "full";
export type AuthMode = "obo" | "passthrough";

/** Which deployment target the user picks in the deploy modal. */
export type DeployTarget = "model_serving" | "app";

export type DeployStepName =
  | "validate"
  | "provision_lakebase"
  | "log_model"
  | "register_model"
  | "create_endpoint"
  // Agents-on-apps steps:
  | "generate_project"
  | "upload_workspace_files"
  | "create_app"
  | "deploy_app"
  | "complete";
export type DeployStepStatus = "pending" | "running" | "done" | "error" | "skipped";

export interface DeployEvent {
  step: DeployStepName;
  status: DeployStepStatus;
  message: string;
  data?: {
    endpoint_url?: string;
    model_version?: string;
    run_id?: string;
    // Agents-on-apps result fields:
    app_url?: string;
    invocations_url?: string;
    app_name?: string;
    workspace_path?: string;
  };
}

export interface DeployRequest {
  graph: GraphDef;
  model_name: string;
  experiment_path: string;
  deploy_mode: DeployMode;
  auth_mode: AuthMode;
  pat: string;
  // Lakebase — option A: auto-provision a new project
  lakebase_project_id: string;
  // Lakebase — option B: use an existing project
  lakebase_existing_project_id: string;
  // Lakebase — option C: raw connection string (legacy)
  lakebase_conn_string: string;
}

export interface AppDeployRequest {
  graph: GraphDef;
  app_name: string;
  workspace_path: string;
  auth_mode: AuthMode;
  pat: string;
  lakebase_project_id: string;
  lakebase_existing_project_id: string;
  lakebase_conn_string: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  error?: string | null;
  execution_trace?: ExecutionTrace;
  mlflow_trace?: TraceSpan[];
  /** Rotating verb shown while the agent is mid-pause (e.g. tool call). */
  thinking?: string | null;
}

// ── Models listing ─────────────────────────────────────────────────────────

export interface ResourceLink {
  label: string;
  url: string;
}

export interface ModelInfo {
  name: string;
  experiment_id: string;
  latest_run_id: string | null;
  latest_run_time: string | null;
  deploy_mode: string | null;
  registered_model_name: string | null;
  endpoint_name: string | null;
  resources: ResourceLink[];
  has_graph_def: boolean;
  experiment_url: string;
}

export interface ModelsResponse {
  models: ModelInfo[];
  workspace_url: string;
}

// ── Setup (MLflow experiment one-time config) ──────────────────────────────

export interface SetupStatusResponse {
  setup_complete: boolean;
  user_email: string;
  sp_display_name: string;
  experiment_path: string | null;
}

export interface SetupInfoResponse {
  user_email: string;
  sp_display_name: string;
  sp_id: string;
}

export interface SetupValidateResponse {
  success: boolean;
  experiment_id: string | null;
  error: string | null;
}
