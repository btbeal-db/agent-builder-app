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
  default_field_template: { name: string; type: string; description: string } | null;
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

export interface PreviewResponse {
  success: boolean;
  output: string;
  error: string | null;
  execution_trace: Array<{ role: string; content: string; node?: string }>;
  state: Record<string, string>;
  thread_id: string | null;
  interrupt: string | null;
  mlflow_trace: TraceSpan[];
}

export interface ExportResponse {
  success: boolean;
  code: string;
  error: string | null;
}

export type DeployMode = "log_only" | "log_and_register" | "full";

export type DeployStepName = "validate" | "provision_lakebase" | "log_model" | "register_model" | "create_endpoint" | "complete";
export type DeployStepStatus = "pending" | "running" | "done" | "error" | "skipped";

export interface DeployEvent {
  step: DeployStepName;
  status: DeployStepStatus;
  message: string;
  data?: { endpoint_url?: string; model_version?: string; run_id?: string };
}

export interface DeployRequest {
  graph: GraphDef;
  model_name: string;
  experiment_path: string;
  deploy_mode: DeployMode;
  pat: string;
  // Lakebase — option A: auto-provision a new project
  lakebase_project_id: string;
  // Lakebase — option B: use an existing project
  lakebase_existing_project_id: string;
  // Lakebase — option C: raw connection string (legacy)
  lakebase_conn_string: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  error?: string | null;
  execution_trace?: PreviewResponse["execution_trace"];
  mlflow_trace?: TraceSpan[];
  loading?: boolean;
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
