import { createContext, useContext, useState, type ReactNode } from 'react';

/* Answer depth — how thorough an answer to produce. Orthogonal to persona:
   persona sets the *altitude* (engineer vs business owner), depth sets the
   *budget* (how much evidence is gathered and how long the answer may run).
   Mirrors backend/answer/depth.py; `deep` costs materially more per question,
   so `standard` is the default and `deep` is a deliberate choice. */

export type DepthId = 'brief' | 'standard' | 'deep';

export const DEPTHS: { id: DepthId; label: string; hint: string }[] = [
  { id: 'brief', label: 'Brief', hint: 'Short answer, least evidence' },
  { id: 'standard', label: 'Standard', hint: 'Balanced depth and cost' },
  { id: 'deep', label: 'Deep', hint: 'Most evidence, longest answer' },
];

interface DepthContextValue {
  depthId: DepthId;
  setDepthId: (id: DepthId) => void;
}

const DepthContext = createContext<DepthContextValue | null>(null);

export function DepthProvider({ children }: { children: ReactNode }) {
  const [depthId, setDepthId] = useState<DepthId>('standard');
  return <DepthContext.Provider value={{ depthId, setDepthId }}>{children}</DepthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDepth(): DepthContextValue {
  const ctx = useContext(DepthContext);
  if (!ctx) throw new Error('useDepth must be used within a DepthProvider');
  return ctx;
}
