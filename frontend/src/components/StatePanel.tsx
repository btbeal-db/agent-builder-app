import { useState } from "react";
import { ChevronDown, ChevronRight, Plus, X, Check, Maximize2 } from "lucide-react";
import type { StateFieldDef, StateSubField } from "../types";
import { useRenameField } from "../StateContext";

const FIELD_TYPES = [
  { value: "str", label: "Text" },
  { value: "int", label: "Integer" },
  { value: "float", label: "Number" },
  { value: "bool", label: "True / False" },
  { value: "list[str]", label: "List of Text" },
  { value: "structured", label: "Structured" },
  { value: "vector_search_filter", label: "VS Filter" },
];

const SUB_FIELD_TYPES = FIELD_TYPES.filter((ft) => ft.value !== "structured");

const DEFAULT_DESCRIPTIONS: Record<string, string> = {
  vector_search_filter:
    'JSON filter for Vector Search. Exact match uses just the column name: {"department": "cardiology"}. ' +
    'Comparison operators go in the key: {"year >=": 2020, "id NOT": 5}. ' +
    'Multiple values: {"department": ["cardiology", "neurology"]}. ' +
    'Return {} if no filters are needed.',
};

const TYPE_LABELS: Record<string, string> = Object.fromEntries(
  FIELD_TYPES.map((ft) => [ft.value, ft.label])
);

interface Props {
  fields: StateFieldDef[];
  onChange: (fields: StateFieldDef[]) => void;
  onOpenModal?: () => void;
}

export default function StatePanel({ fields, onChange, onOpenModal }: Props) {
  const cascadeRename = useRenameField();
  const [collapsed, setCollapsed] = useState(false);
  const [expandedField, setExpandedField] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  // Local name buffer — committed on blur/Enter so typing is responsive
  const [editingName, setEditingName] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("str");
  const [newDesc, setNewDesc] = useState("");
  const [newSubFields, setNewSubFields] = useState<StateSubField[]>([]);

  const updateField = (index: number, updates: Partial<StateFieldDef>) => {
    onChange(fields.map((f, i) => (i === index ? { ...f, ...updates } : f)));
  };

  const removeField = (index: number) => {
    if (fields[index].name === "input") return;
    setExpandedField(null);
    setEditingName(null);
    onChange(fields.filter((_, i) => i !== index));
  };

  const addField = () => {
    const name = newName.trim().replace(/\s+/g, "_").toLowerCase();
    if (!name || fields.some((f) => f.name === name)) return;
    onChange([...fields, { name, type: newType, description: newDesc, sub_fields: newType === "structured" ? newSubFields : [] }]);
    setNewName("");
    setNewType("str");
    setNewDesc("");
    setNewSubFields([]);
    setAdding(false);
    setExpandedField(fields.length);
  };

  const commitName = (index: number) => {
    if (editingName !== null && editingName !== fields[index].name) {
      const oldName = fields[index].name;
      const sanitized = editingName.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_]/g, "");
      if (sanitized && !fields.some((f) => f.name === sanitized && f.name !== oldName)) {
        cascadeRename(oldName, sanitized);
      }
      setEditingName(null);
    } else {
      setEditingName(null);
    }
  };

  const updateSubField = (fieldIdx: number, subIdx: number, updates: Partial<StateSubField>) => {
    const field = fields[fieldIdx];
    const newSubs = field.sub_fields.map((sf, i) => (i === subIdx ? { ...sf, ...updates } : sf));
    updateField(fieldIdx, { sub_fields: newSubs });
  };

  const addSubField = (fieldIdx: number) => {
    const field = fields[fieldIdx];
    updateField(fieldIdx, {
      sub_fields: [...field.sub_fields, { name: "", type: "str", description: "" }],
    });
  };

  const removeSubField = (fieldIdx: number, subIdx: number) => {
    const field = fields[fieldIdx];
    updateField(fieldIdx, { sub_fields: field.sub_fields.filter((_, i) => i !== subIdx) });
  };

  const toggleField = (index: number) => {
    setExpandedField((prev) => (prev === index ? null : index));
    setEditingName(null);
    setAdding(false);
  };

  return (
    <div className="state-panel">
      <div className="state-panel-header-row">
        <button className="state-panel-header" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          <h2>State Model</h2>
        </button>
        {onOpenModal && (
          <button
            className="state-panel-expand-btn"
            onClick={onOpenModal}
            title="Open full editor"
          >
            <Maximize2 size={12} />
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="state-panel-body">
          {/* Built-in messages field */}
          <div className="state-panel-field state-panel-field-locked">
            <div className="state-panel-field-row">
              <span className="state-panel-name">messages</span>
              <span className="state-panel-type-badge">list[Msg]</span>
            </div>
          </div>

          {/* User-defined fields */}
          {fields.map((field, i) => {
            const isExpanded = expandedField === i;
            return (
              <div
                key={i}
                className={`state-panel-field${isExpanded ? " state-panel-field-expanded" : ""}`}
              >
                <div
                  className="state-panel-field-row"
                  onClick={() => toggleField(i)}
                >
                  <span className="state-panel-name">{field.name}</span>
                  <span className="state-panel-type-badge">
                    {TYPE_LABELS[field.type] ?? field.type}
                  </span>
                </div>

                {isExpanded && (
                  <div className="state-panel-field-editor">
                    {/* Name — buffered locally, committed on blur/Enter */}
                    {field.name === "input" ? (
                      <div className="state-panel-editor-row">
                        <label>Name</label>
                        <span className="state-panel-locked-value">{field.name}</span>
                      </div>
                    ) : (
                      <div className="state-panel-editor-row">
                        <label>Name</label>
                        <input
                          value={editingName !== null ? editingName : field.name}
                          placeholder="field_name"
                          onFocus={() => setEditingName(field.name)}
                          onChange={(e) =>
                            setEditingName(e.target.value.replace(/\s+/g, "_").toLowerCase())
                          }
                          onBlur={() => commitName(i)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.currentTarget.blur();
                          }}
                        />
                      </div>
                    )}

                    {/* Type — updates immediately */}
                    <div className="state-panel-editor-row">
                      <label>Type</label>
                      <select
                        value={field.type}
                        onChange={(e) => {
                          const t = e.target.value;
                          const prevDefault = DEFAULT_DESCRIPTIONS[field.type];
                          const shouldAutoFill =
                            t in DEFAULT_DESCRIPTIONS &&
                            (!field.description || field.description === prevDefault);
                          updateField(i, {
                            type: t,
                            sub_fields: t === "structured" ? field.sub_fields : [],
                            ...(shouldAutoFill ? { description: DEFAULT_DESCRIPTIONS[t] } : {}),
                          });
                        }}
                      >
                        {FIELD_TYPES.map((ft) => (
                          <option key={ft.value} value={ft.value}>
                            {ft.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Description — updates immediately */}
                    <div className="state-panel-editor-row">
                      <label>Desc</label>
                      <input
                        value={field.description}
                        placeholder="What is this field for?"
                        onChange={(e) => updateField(i, { description: e.target.value })}
                      />
                    </div>

                    {/* Sub-fields for structured type */}
                    {field.type === "structured" && (
                      <div className="state-panel-subfields">
                        <label className="state-panel-subfield-label">Sub-fields</label>
                        {field.sub_fields.map((sf, si) => (
                          <div key={si} className="state-panel-subfield-row">
                            <div className="state-panel-sub-top">
                              <input
                                className="state-panel-sub-name"
                                value={sf.name}
                                placeholder="name"
                                onChange={(e) => updateSubField(i, si, { name: e.target.value })}
                              />
                              <select
                                className="state-panel-sub-type"
                                value={sf.type}
                                onChange={(e) => {
                                  const t = e.target.value;
                                  const prevDefault = DEFAULT_DESCRIPTIONS[sf.type];
                                  const shouldAutoFill =
                                    t in DEFAULT_DESCRIPTIONS &&
                                    (!sf.description || sf.description === prevDefault);
                                  updateSubField(i, si, {
                                    type: t,
                                    ...(shouldAutoFill ? { description: DEFAULT_DESCRIPTIONS[t] } : {}),
                                  });
                                }}
                              >
                                {SUB_FIELD_TYPES.map((ft) => (
                                  <option key={ft.value} value={ft.value}>
                                    {ft.label}
                                  </option>
                                ))}
                              </select>
                              <button
                                className="state-panel-sub-remove"
                                onClick={() => removeSubField(i, si)}
                              >
                                <X size={12} />
                              </button>
                            </div>
                            <input
                              className="state-panel-sub-desc"
                              value={sf.description}
                              placeholder="Description"
                              onChange={(e) => updateSubField(i, si, { description: e.target.value })}
                            />
                          </div>
                        ))}
                        <button
                          className="state-panel-sub-add"
                          onClick={() => addSubField(i)}
                        >
                          <Plus size={12} /> Sub-field
                        </button>
                      </div>
                    )}

                    {/* Delete */}
                    {field.name !== "input" && (
                      <button
                        className="state-panel-delete"
                        onClick={() => removeField(i)}
                      >
                        <X size={12} /> Remove field
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* Add field form */}
          {adding ? (
            <div className="state-panel-add-form">
              <input
                className="state-panel-add-name"
                value={newName}
                placeholder="field_name"
                autoFocus
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addField()}
              />
              <select
                className="state-panel-add-type"
                value={newType}
                onChange={(e) => {
                  const t = e.target.value;
                  setNewType(t);
                  if (t !== "structured") setNewSubFields([]);
                  if (t in DEFAULT_DESCRIPTIONS && (!newDesc || newDesc === DEFAULT_DESCRIPTIONS[newType])) {
                    setNewDesc(DEFAULT_DESCRIPTIONS[t]);
                  }
                }}
              >
                {FIELD_TYPES.map((ft) => (
                  <option key={ft.value} value={ft.value}>
                    {ft.label}
                  </option>
                ))}
              </select>
              <input
                className="state-panel-add-desc"
                value={newDesc}
                placeholder="Description"
                onChange={(e) => setNewDesc(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addField()}
              />
              {newType === "structured" && (
                <div className="state-panel-subfields">
                  <label className="state-panel-subfield-label">Sub-fields</label>
                  {newSubFields.map((sf, si) => (
                    <div key={si} className="state-panel-subfield-row">
                      <div className="state-panel-sub-top">
                        <input
                          className="state-panel-sub-name"
                          value={sf.name}
                          placeholder="name"
                          onChange={(e) => {
                            const updated = newSubFields.map((s, j) => j === si ? { ...s, name: e.target.value } : s);
                            setNewSubFields(updated);
                          }}
                        />
                        <select
                          className="state-panel-sub-type"
                          value={sf.type}
                          onChange={(e) => {
                            const updated = newSubFields.map((s, j) => j === si ? { ...s, type: e.target.value } : s);
                            setNewSubFields(updated);
                          }}
                        >
                          {SUB_FIELD_TYPES.map((ft) => (
                            <option key={ft.value} value={ft.value}>
                              {ft.label}
                            </option>
                          ))}
                        </select>
                        <button
                          className="state-panel-sub-remove"
                          onClick={() => setNewSubFields(newSubFields.filter((_, j) => j !== si))}
                        >
                          <X size={12} />
                        </button>
                      </div>
                      <input
                        className="state-panel-sub-desc"
                        value={sf.description}
                        placeholder="Description"
                        onChange={(e) => {
                          const updated = newSubFields.map((s, j) => j === si ? { ...s, description: e.target.value } : s);
                          setNewSubFields(updated);
                        }}
                      />
                    </div>
                  ))}
                  <button
                    className="state-panel-sub-add"
                    onClick={() => setNewSubFields([...newSubFields, { name: "", type: "str", description: "" }])}
                  >
                    <Plus size={12} /> Sub-field
                  </button>
                </div>
              )}
              <div className="state-panel-add-actions">
                <button className="state-panel-add-confirm" onClick={addField}>
                  <Check size={12} /> Add
                </button>
                <button className="state-panel-add-cancel" onClick={() => setAdding(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button className="state-panel-add-btn" onClick={() => { setAdding(true); setExpandedField(null); }}>
              <Plus size={13} /> Add field
            </button>
          )}
        </div>
      )}
    </div>
  );
}
