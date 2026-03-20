import { useState } from "react";
import type { StateFieldDef, StateSubField } from "../types";

// ── Presets ─────────────────────────────────────────────────────────
interface Preset {
  label: string;
  fields: StateFieldDef[];
}

const PRESETS: Preset[] = [
  {
    label: "Joke Agent",
    fields: [
      { name: "user_input", type: "str", description: "The user's initial message", sub_fields: [] },
      { name: "is_funny", type: "bool", description: "Whether the joke is genuinely funny", sub_fields: [] },
      {
        name: "verdict",
        type: "structured",
        description: "The judge's verdict on the joke",
        sub_fields: [
          { name: "is_funny", type: "bool", description: "Whether the joke is genuinely funny" },
          { name: "reasoning", type: "str", description: "Brief explanation of the verdict" },
        ],
      },
      {
        name: "rewrite",
        type: "structured",
        description: "Critique and rewrite of a bad joke",
        sub_fields: [
          { name: "critique", type: "str", description: "What makes the original joke fall flat" },
          { name: "rewritten_joke", type: "str", description: "An improved version of the joke" },
        ],
      },
    ],
  },
];

const FIELD_TYPES = [
  { value: "str", label: "Text" },
  { value: "int", label: "Integer" },
  { value: "float", label: "Number" },
  { value: "bool", label: "True / False" },
  { value: "list[str]", label: "List of Text" },
  { value: "structured", label: "Structured" },
];

const SUB_FIELD_TYPES = [
  { value: "str", label: "Text" },
  { value: "int", label: "Integer" },
  { value: "float", label: "Number" },
  { value: "bool", label: "True / False" },
  { value: "list[str]", label: "List of Text" },
];

interface Props {
  fields: StateFieldDef[];
  onChange: (fields: StateFieldDef[]) => void;
  onClose: () => void;
}

export default function StateModelModal({ fields, onChange, onClose }: Props) {
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("str");
  const [newDesc, setNewDesc] = useState("");

  const updateField = (index: number, updates: Partial<StateFieldDef>) => {
    onChange(fields.map((f, i) => (i === index ? { ...f, ...updates } : f)));
  };

  const addField = () => {
    const name = newName.trim().replace(/\s+/g, "_").toLowerCase();
    if (!name || fields.some((f) => f.name === name)) return;
    onChange([...fields, { name, type: newType, description: newDesc, sub_fields: [] }]);
    setNewName("");
    setNewType("str");
    setNewDesc("");
  };

  const removeField = (index: number) => {
    if (fields[index].name === "user_input") return;
    onChange(fields.filter((_, i) => i !== index));
  };

  // Sub-field helpers
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

  return (
    <div className="modal-overlay" onKeyDown={(e) => e.stopPropagation()}>
      <div className="modal-card">
        <div className="modal-header">
          <h1>Define Your Agent's State</h1>
          <p>
            The state model is the shared memory your agent works with. Every
            node reads the full state and updates a specific field. Use the
            <strong> Structured</strong> type for fields that need a defined
            shape (e.g. a judgment with score and critique).
          </p>
          <div className="modal-presets">
            <label>Load a preset:</label>
            <div className="modal-preset-buttons">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  className="btn btn-sm btn-preset"
                  onClick={() => onChange(p.fields)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="modal-body">
          <div className="modal-table-header">
            <span className="modal-col-name">Field Name</span>
            <span className="modal-col-type">Type</span>
            <span className="modal-col-desc">Description</span>
            <span className="modal-col-action"></span>
          </div>

          <div className="modal-field-list">
            {/* Built-in MessagesState fields */}
            <div className="modal-field-group modal-builtin">
              <div className="modal-field-row">
                <div className="modal-col-name">
                  <span className="modal-field-name locked">messages</span>
                </div>
                <div className="modal-col-type">
                  <span className="modal-field-type-label">list[BaseMessage]</span>
                </div>
                <div className="modal-col-desc">
                  <span className="modal-builtin-desc">Chat history (from MessagesState)</span>
                </div>
                <div className="modal-col-action" />
              </div>
            </div>

            {/* User-defined fields */}
            {fields.map((field, i) => (
              <div key={i} className="modal-field-group">
                <div className="modal-field-row">
                  <div className="modal-col-name">
                    {field.name === "user_input" ? (
                      <span className="modal-field-name locked">{field.name}</span>
                    ) : (
                      <input
                        className="modal-field-name-input"
                        value={field.name}
                        placeholder="field_name"
                        onChange={(e) => {
                          const sanitized = e.target.value.replace(/\s+/g, "_").toLowerCase();
                          updateField(i, { name: sanitized });
                        }}
                      />
                    )}
                  </div>
                  <div className="modal-col-type">
                    <select
                      className="modal-field-type"
                      value={field.type}
                      onChange={(e) => {
                        const newType = e.target.value;
                        updateField(i, {
                          type: newType,
                          sub_fields: newType === "structured" ? field.sub_fields : [],
                        });
                      }}
                    >
                      {FIELD_TYPES.map((ft) => (
                        <option key={ft.value} value={ft.value}>{ft.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="modal-col-desc">
                    <input
                      className="modal-field-desc"
                      value={field.description}
                      placeholder="What is this field for?"
                      onChange={(e) => updateField(i, { description: e.target.value })}
                    />
                  </div>
                  <div className="modal-col-action">
                    {field.name !== "user_input" && (
                      <button className="modal-field-remove" onClick={() => removeField(i)}>
                        &times;
                      </button>
                    )}
                  </div>
                </div>

                {/* Sub-fields for structured type */}
                {field.type === "structured" && (
                  <div className="modal-sub-fields">
                    {field.sub_fields.map((sf, si) => (
                      <div key={si} className="modal-sub-row">
                        <input
                          className="modal-sub-name"
                          value={sf.name}
                          placeholder="sub_field"
                          onChange={(e) => updateSubField(i, si, { name: e.target.value })}
                        />
                        <select
                          className="modal-sub-type"
                          value={sf.type}
                          onChange={(e) => updateSubField(i, si, { type: e.target.value })}
                        >
                          {SUB_FIELD_TYPES.map((ft) => (
                            <option key={ft.value} value={ft.value}>{ft.label}</option>
                          ))}
                        </select>
                        <input
                          className="modal-sub-desc"
                          value={sf.description}
                          placeholder="description"
                          onChange={(e) => updateSubField(i, si, { description: e.target.value })}
                        />
                        <button className="modal-field-remove" onClick={() => removeSubField(i, si)}>
                          &times;
                        </button>
                      </div>
                    ))}
                    <button className="btn btn-sm modal-sub-add" onClick={() => addSubField(i)}>
                      + Add Sub-field
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="modal-add-row">
            <div className="modal-col-name">
              <input
                className="modal-field-name-input"
                value={newName}
                placeholder="field_name"
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addField()}
              />
            </div>
            <div className="modal-col-type">
              <select
                className="modal-field-type"
                value={newType}
                onChange={(e) => setNewType(e.target.value)}
              >
                {FIELD_TYPES.map((ft) => (
                  <option key={ft.value} value={ft.value}>{ft.label}</option>
                ))}
              </select>
            </div>
            <div className="modal-col-desc">
              <input
                className="modal-field-desc"
                value={newDesc}
                placeholder="What is this field for?"
                onChange={(e) => setNewDesc(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addField()}
              />
            </div>
            <div className="modal-col-action">
              <button className="btn btn-sm" onClick={addField}>+</button>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn btn-primary btn-lg" onClick={onClose}>
            Continue to Builder
          </button>
        </div>
      </div>
    </div>
  );
}
