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
}

export interface NodeTypeMetadata {
  type: string;
  display_name: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  config_fields: NodeConfigField[];
}

export interface GraphNode {
  id: string;
  type: string;
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
}

export interface PreviewResponse {
  success: boolean;
  output: string;
  error: string | null;
  execution_trace: Array<{ role: string; content: string; node?: string }>;
  state: Record<string, string>;
}

export interface ExportResponse {
  success: boolean;
  code: string;
  error: string | null;
}

export interface DeployRequest {
  graph: GraphDef;
  model_name: string;
  experiment_path: string;
  lakebase_conn_string: string;
}

export interface DeployResponse {
  success: boolean;
  endpoint_url: string;
  model_version: string;
  error: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  error?: string | null;
  execution_trace?: PreviewResponse["execution_trace"];
  state?: Record<string, string>;
  loading?: boolean;
}
