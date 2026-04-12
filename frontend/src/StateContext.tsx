import { createContext, useContext } from "react";
import type { StateFieldDef } from "./types";

interface StateContextValue {
  names: string[];
  fields: StateFieldDef[];
  addField: (field: StateFieldDef) => void;
  renameField: (oldName: string, newName: string) => void;
}

const noop = () => {};

const StateContext = createContext<StateContextValue>({
  names: [],
  fields: [],
  addField: noop,
  renameField: noop,
});

export const StateProvider = StateContext.Provider;

export function useStateVars(): string[] {
  return useContext(StateContext).names;
}

export function useStateFields(): StateFieldDef[] {
  return useContext(StateContext).fields;
}

export function useStateField(name: string): StateFieldDef | undefined {
  return useContext(StateContext).fields.find((f) => f.name === name);
}

export function useAddField(): (field: StateFieldDef) => void {
  return useContext(StateContext).addField;
}

export function useRenameField(): (oldName: string, newName: string) => void {
  return useContext(StateContext).renameField;
}
