import { createContext, useContext } from "react";
import type { Meta } from "./api";

export interface AppState {
  meta: Meta;
  compare: string[];
  toggleCompare: (key: string) => void;
  refreshMeta: () => void;
}

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppContext");
  return ctx;
}
