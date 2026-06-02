import { useState, useEffect, useCallback, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useReactFlow, useNodes } from "@xyflow/react";
import type { NodeTypeMetadata, NodeConfigField } from "../types";
import { useStateFields } from "../StateContext";
import RouteEditor, { type Route } from "./RouteEditor";
import SchemaEditor, { type SchemaField } from "./SchemaEditor";
import SearchableSelect from "./SearchableSelect";
import TemplatedTextarea from "./TemplatedTextarea";
import LocalInput from "./LocalInput";

interface Props {
  selectedNodeId: string;
  nodeTypes: NodeTypeMetadata[];
  stateVariables: string[];
}

const DEFAULT_ROUTE: Route = { label: "default", match_value: "" };

export default function ConfigPanel({ selectedNodeId, nodeTypes, stateVariables }: Props) {
  const { setNodes, setEdges } = useReactFlow();
  const stateFields = useStateFields();
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const nodes = useNodes();

  const node = useMemo(() => nodes.find((n) => n.id === selectedNodeId), [nodes, selectedNodeId]);

  const nodeType = (node?.data.nodeType as string) ?? node?.type ?? "";
  const meta = useMemo(() => nodeTypes.find((nt) => nt.type === nodeType), [nodeTypes, nodeType]);
  const isRouter = (node?.data.is_router as boolean) ?? false;
  const config = (node?.data.config ?? {}) as Record<string, unknown>;

  /** Remove all outgoing edges from this router node. */
  const clearRouterEdges = useCallback(() => {
    setEdges((eds) => eds.filter((e) => e.source !== selectedNodeId));
  }, [selectedNodeId, setEdges]);

  const updateConfig = useCallback(
    (fieldName: string, value: unknown) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== selectedNodeId) return n;
          return {
            ...n,
            data: {
              ...n.data,
              config: { ...(n.data.config as Record<string, unknown>), [fieldName]: value },
            },
          };
        })
      );
    },
    [selectedNodeId, setNodes]
  );

  // Look up the state field that the router evaluates
  const evaluatesName = (config.evaluates as string) ?? "";
  const evaluatedField = stateFields.find((f) => f.name === evaluatesName) ?? null;

  // Auto-sync bool routes when the evaluated field is bool
  const isBoolRoute = evaluatedField?.type === "bool" ||
    (evaluatedField?.type === "structured" &&
      evaluatedField.sub_fields.find(
        (s) => s.name === ((config._route_sub_field as string) ?? "")
      )?.type === "bool");

  useEffect(() => {
    if (!isBoolRoute || !isRouter || !node) return;
    const currentRoutes = config.routes_json;
    const boolRoutes = [
      { label: "True", match_value: "true" },
      { label: "False", match_value: "false" },
    ];
    if (
      Array.isArray(currentRoutes) &&
      currentRoutes.length === 2 &&
      currentRoutes[0]?.match_value === "true"
    ) return;
    updateConfig("routes_json", boolRoutes);
    clearRouterEdges();
  }, [isBoolRoute]); // eslint-disable-line react-hooks/exhaustive-deps

  // Early return AFTER all hooks
  if (!node || !meta) return null;

  const renderField = (field: NodeConfigField) => {
    // State variable — dropdown populated from derived state vars
    if (field.field_type === "state_variable") {
      const val = (config[field.name] as string) ?? (field.default as string) ?? (field.required ? stateVariables[0] : "") ?? "";
      return (
        <div key={field.name} className="config-field">
          <label>{field.label}</label>
          <select
            value={val}
            onChange={(e) => {
              updateConfig(field.name, e.target.value);
              if (isRouter) {
                updateConfig("_route_sub_field", "");
                updateConfig("routes_json", [DEFAULT_ROUTE]);
                clearRouterEdges();
              }
            }}
          >
            {!field.required && <option value="">— None —</option>}
            {stateVariables.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          {field.help_text && (
            <span className="config-hint">{field.help_text}</span>
          )}
        </div>
      );
    }

    // Route editor — state-field-aware
    if (field.field_type === "route_editor") {
      const raw = config[field.name];
      let routes: Route[];
      if (Array.isArray(raw)) {
        routes = raw as Route[];
      } else if (typeof raw === "string") {
        try { routes = JSON.parse(raw); } catch { routes = [DEFAULT_ROUTE]; }
      } else {
        routes = [DEFAULT_ROUTE];
      }

      const routeSubField = (config._route_sub_field as string) ?? "";

      return (
        <div key={field.name} className="config-field">
          <RouteEditor
            evaluatedField={evaluatedField}
            subField={routeSubField}
            onSubFieldChange={(sf) => {
              updateConfig("_route_sub_field", sf);
              clearRouterEdges();
              // Set routes based on sub-field type
              const subDef = evaluatedField?.sub_fields.find((s) => s.name === sf);
              if (subDef?.type === "bool") {
                updateConfig(field.name, [
                  { label: "True", match_value: "true" },
                  { label: "False", match_value: "false" },
                ]);
              } else {
                updateConfig(field.name, [DEFAULT_ROUTE]);
              }
            }}
            routes={routes}
            onChange={(updated) => {
              updateConfig(field.name, updated);
              // Clear edges when route count changes (handles added/removed)
              if (updated.length !== routes.length) clearRouterEdges();
            }}
          />
        </div>
      );
    }

    // Schema editor
    if (field.field_type === "schema_editor") {
      const raw = config[field.name];
      let fields: SchemaField[];
      if (typeof raw === "string") {
        try { fields = JSON.parse(raw); } catch { fields = []; }
      } else if (Array.isArray(raw)) {
        fields = raw as SchemaField[];
      } else {
        fields = [];
      }

      return (
        <div key={field.name} className="config-field">
          {field.label && <label>{field.label}</label>}
          <SchemaEditor
            fields={fields}
            onChange={(updated) => updateConfig(field.name, updated)}
          />
          {field.help_text && (
            <span className="config-hint">{field.help_text}</span>
          )}
        </div>
      );
    }

    // Searchable select — async-fetched dropdown with fallback to text
    if (field.field_type === "searchable_select" && field.fetch_endpoint) {
      const val = (config[field.name] ?? field.default ?? "") as string;
      return (
        <div key={field.name} className="config-field">
          <label>
            {field.label}
            {field.required && " *"}
          </label>
          <SearchableSelect
            value={val}
            onChange={(v) => updateConfig(field.name, v)}
            fetchEndpoint={field.fetch_endpoint}
            placeholder={field.placeholder}
            showProviderIcons={field.fetch_endpoint.includes("serving-endpoints")}
          />
          {field.help_text && (
            <span className="config-hint">{field.help_text}</span>
          )}
        </div>
      );
    }

    const val = (config[field.name] ?? field.default ?? "") as string;

    return (
      <div key={field.name} className="config-field">
        <label>
          {field.label}
          {field.required && " *"}
        </label>

        {field.field_type === "textarea" ? (
          <TemplatedTextarea
            value={val}
            placeholder={field.placeholder}
            variables={field.name === "system_prompt" ? stateVariables : []}
            onChange={(v) => updateConfig(field.name, v)}
          />
        ) : field.field_type === "select" && field.options ? (
          <select
            value={val}
            onChange={(e) => updateConfig(field.name, e.target.value)}
          >
            {field.options.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        ) : (
          <LocalInput
            type={field.field_type === "number" ? "number" : "text"}
            value={String(val)}
            placeholder={field.placeholder}
            onChange={(next) => {
              if (field.field_type !== "number") {
                updateConfig(field.name, next);
                return;
              }
              if (next === "") {
                updateConfig(field.name, "");
                return;
              }
              const parsed = parseFloat(next);
              updateConfig(field.name, Number.isNaN(parsed) ? next : parsed);
            }}
          />
        )}
        {field.help_text && (
          <span className="config-hint">{field.help_text}</span>
        )}
      </div>
    );
  };

  const basicFields = meta.config_fields.filter((f) => !f.advanced);
  const advancedFields = meta.config_fields.filter((f) => f.advanced);

  return (
    <div className="config-panel">
      <h3>{meta.display_name} Config</h3>

      {basicFields.map(renderField)}

      {advancedFields.length > 0 && (
        <div className="config-advanced">
          <button
            type="button"
            className="config-advanced-toggle"
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span>Advanced</span>
          </button>
          {advancedOpen && (
            <div className="config-advanced-body">
              {advancedFields.map(renderField)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
