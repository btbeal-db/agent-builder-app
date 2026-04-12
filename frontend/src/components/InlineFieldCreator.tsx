import { useState } from "react";
import { Plus, Check, X } from "lucide-react";
import type { StateFieldDef } from "../types";

const FIELD_TYPES = [
  { value: "str", label: "Text" },
  { value: "int", label: "Integer" },
  { value: "float", label: "Number" },
  { value: "bool", label: "True / False" },
  { value: "list[str]", label: "List of Text" },
  { value: "structured", label: "Structured" },
];

interface Props {
  existingNames: string[];
  onAdd: (field: StateFieldDef) => void;
  onCancel: () => void;
}

export default function InlineFieldCreator({ existingNames, onAdd, onCancel }: Props) {
  const [name, setName] = useState("");
  const [type, setType] = useState("str");

  const sanitized = name.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_]/g, "");
  const duplicate = existingNames.includes(sanitized);
  const valid = sanitized.length > 0 && !duplicate;

  const handleSubmit = () => {
    if (!valid) return;
    onAdd({ name: sanitized, type, description: "", sub_fields: [] });
  };

  return (
    <div className="inline-field-creator" onClick={(e) => e.stopPropagation()}>
      <input
        className="inline-field-input"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
          if (e.key === "Escape") onCancel();
        }}
        placeholder="field_name"
        autoFocus
      />
      <select
        className="inline-field-type"
        value={type}
        onChange={(e) => setType(e.target.value)}
      >
        {FIELD_TYPES.map((ft) => (
          <option key={ft.value} value={ft.value}>{ft.label}</option>
        ))}
      </select>
      <button className="inline-field-btn" onClick={handleSubmit} disabled={!valid} title="Add field">
        <Check size={12} />
      </button>
      <button className="inline-field-btn" onClick={onCancel} title="Cancel">
        <X size={12} />
      </button>
      {duplicate && <span className="inline-field-error">Name taken</span>}
    </div>
  );
}
