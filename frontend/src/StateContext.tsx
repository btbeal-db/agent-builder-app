import { createContext, useContext } from "react";
import type { StateFieldDef } from "./types";

interface StateContextValue {
  names: string[];
  fields: StateFieldDef[];
}

const StateContext = createContext<StateContextValue>({ names: [], fields: [] });

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
