import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { getModels, type ModelOption } from './api';

/* Answer model — which LLM synthesises the answer. Orthogonal to persona (altitude)
   and depth (budget): this picks *who writes it*. The list is fetched from the
   backend (GET /models) rather than hardcoded, because which models are available
   depends on the wired gateways and the account — the dropdown only ever shows
   models whose provider is actually reachable. `modelId` is null until the list
   loads (and stays null if no models are available), in which case the request
   sends no override and the backend uses its configured default tier. */

export type ModelId = string;

interface ModelContextValue {
  models: ModelOption[];
  modelId: ModelId | null;
  setModelId: (id: ModelId) => void;
}

const ModelContext = createContext<ModelContextValue | null>(null);

export function ModelProvider({ children }: { children: ReactNode }) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState<ModelId | null>(null);

  useEffect(() => {
    let cancelled = false;
    getModels()
      .then((list) => {
        if (cancelled) return;
        setModels(list);
        // Default to the first available model (the configured "large" tier for
        // the primary provider). Leave null if the backend offers none.
        setModelId((current) => current ?? list[0]?.id ?? null);
      })
      .catch(() => {
        /* No dropdown if /models is unreachable — requests just omit the override. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ModelContext.Provider value={{ models, modelId, setModelId }}>
      {children}
    </ModelContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useModel(): ModelContextValue {
  const ctx = useContext(ModelContext);
  if (!ctx) throw new Error('useModel must be used within a ModelProvider');
  return ctx;
}
