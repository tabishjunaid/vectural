import { createContext, useContext, useState, type ReactNode } from 'react';
import { PERSONAS, type Persona, type PersonaId } from './mock-data';

/* Persona is the one genuinely global client control (R6, design-doc §5.6).
   The switcher writes here; every screen reads from here. When the answer
   endpoint lands (UI-3) the current persona is threaded into each request,
   exactly as the routing layer threads it into synthesis. */
interface PersonaContextValue {
  persona: Persona;
  personaId: PersonaId;
  setPersonaId: (id: PersonaId) => void;
}

const PersonaContext = createContext<PersonaContextValue | null>(null);

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [personaId, setPersonaId] = useState<PersonaId>('engineer');
  const persona = PERSONAS.find((p) => p.id === personaId) ?? PERSONAS[0];
  return (
    <PersonaContext.Provider value={{ persona, personaId, setPersonaId }}>
      {children}
    </PersonaContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePersona(): PersonaContextValue {
  const ctx = useContext(PersonaContext);
  if (!ctx) throw new Error('usePersona must be used within a PersonaProvider');
  return ctx;
}
