import { useEffect, useState } from "react";

// useState that survives leaving the page: the value is kept in this browser (localStorage),
// so filters and typed text are still there when you come back. Falls back to plain state
// when storage is blocked (private windows, previews).
export function useStored<T>(key: string, initial: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const full = `devicescout:${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(full);
      return raw == null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(full, JSON.stringify(value));
    } catch {
      /* storage unavailable: keep working without it */
    }
  }, [full, value]);
  return [value, setValue];
}
