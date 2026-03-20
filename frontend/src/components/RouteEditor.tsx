import type { StateFieldDef } from "../types";

export interface Route {
  label: string;       // displayed on the handle and in the UI
  match_value: string; // what to match against (e.g. "true", "sales,revenue")
}

interface Props {
  evaluatedField: StateFieldDef | null;
  /** For structured fields — which sub-field to branch on */
  subField: string;
  onSubFieldChange: (name: string) => void;
  routes: Route[];
  onChange: (routes: Route[]) => void;
}

export default function RouteEditor({
  evaluatedField,
  subField,
  onSubFieldChange,
  routes,
  onChange,
}: Props) {
  const fieldType = resolveFieldType(evaluatedField, subField);

  // ── Bool: fixed True / False routes, no editing ──────────────
  if (fieldType === "bool") {
    return (
      <div className="route-editor">
        <div className="route-editor-header">
          Routes based on <strong>{evaluatedField?.name}{subField ? `.${subField}` : ""}</strong> (True / False)
        </div>
        <div className="route-list">
          <div className="route-row route-fixed">
            <span className="route-fixed-label route-true">True</span>
          </div>
          <div className="route-row route-fixed">
            <span className="route-fixed-label route-false">False</span>
          </div>
        </div>
      </div>
    );
  }

  // ── Structured: pick a sub-field first, then recurse on its type ──
  if (fieldType === "structured" && evaluatedField) {
    const subs = evaluatedField.sub_fields;
    return (
      <div className="route-editor">
        <div className="route-editor-header">
          Pick which field inside <strong>{evaluatedField.name}</strong> to branch on:
        </div>
        <div className="config-field">
          <select
            value={subField}
            onChange={(e) => onSubFieldChange(e.target.value)}
          >
            <option value="">select sub-field...</option>
            {subs.map((sf) => (
              <option key={sf.name} value={sf.name}>
                {sf.name} ({sf.type})
              </option>
            ))}
          </select>
        </div>
        {subField && (
          <RouteEditor
            evaluatedField={evaluatedField}
            subField={subField}
            onSubFieldChange={onSubFieldChange}
            routes={routes}
            onChange={onChange}
          />
        )}
      </div>
    );
  }

  // ── String / other: keyword-match routes + fallback ──────────
  const updateRoute = (index: number, updates: Partial<Route>) => {
    onChange(routes.map((r, i) => (i === index ? { ...r, ...updates } : r)));
  };

  const addRoute = () => {
    // Insert before the fallback (last route)
    const fallback = routes[routes.length - 1];
    onChange([
      ...routes.slice(0, -1),
      { label: "", match_value: "" },
      fallback,
    ]);
  };

  const removeRoute = (index: number) => {
    if (routes.length <= 1) return;
    onChange(routes.filter((_, i) => i !== index));
  };

  return (
    <div className="route-editor">
      <div className="route-editor-header">
        Match keywords in <strong>{evaluatedField?.name ?? "field"}</strong>.
        First match wins. Last route is the fallback.
      </div>

      <div className="route-list">
        {routes.map((route, i) => {
          const isLast = i === routes.length - 1;
          return (
            <div key={i} className={`route-row${isLast ? " route-fallback" : ""}`}>
              <div className="route-fields">
                <input
                  className="route-name"
                  value={route.label}
                  placeholder={isLast ? "fallback" : "route label"}
                  onChange={(e) => updateRoute(i, { label: e.target.value })}
                />
                {isLast ? (
                  <span className="route-fallback-label">fallback (always matches)</span>
                ) : (
                  <input
                    className="route-condition"
                    value={route.match_value}
                    placeholder="keywords: sales, revenue"
                    onChange={(e) => updateRoute(i, { match_value: e.target.value })}
                  />
                )}
              </div>
              {routes.length > 1 && (
                <button
                  className="route-remove"
                  onClick={() => removeRoute(i)}
                  title="Remove route"
                >
                  &times;
                </button>
              )}
            </div>
          );
        })}
      </div>

      <button className="btn btn-sm route-add" onClick={addRoute}>
        + Add Route
      </button>
    </div>
  );
}

/** Resolve the effective type to route on, following into structured sub-fields. */
function resolveFieldType(
  field: StateFieldDef | null,
  subField: string
): string {
  if (!field) return "str";
  if (field.type !== "structured" || !subField) return field.type;
  const sf = field.sub_fields.find((s) => s.name === subField);
  return sf?.type ?? "str";
}
