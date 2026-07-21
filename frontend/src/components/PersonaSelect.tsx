import { Select } from '@base-ui/react/select';
import { PERSONAS, type PersonaId } from '../lib/mock-data';
import { usePersona } from '../lib/persona';

/* Persona switcher — the plan's "first-class control". Built on Base UI's
   Select primitive so focus, keyboard nav and ARIA are handled by the
   primitive layer rather than hand-rolled. */
export function PersonaSelect() {
  const { personaId, setPersonaId } = usePersona();
  return (
    <Select.Root
      value={personaId}
      onValueChange={(v) => setPersonaId(v as PersonaId)}
    >
      <Select.Trigger className="persona-select-trigger" aria-label="Persona">
        <Select.Value>
          {(value: PersonaId) => PERSONAS.find((p) => p.id === value)?.label}
        </Select.Value>
        <Select.Icon aria-hidden>▾</Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner align="start">
          <Select.Popup className="persona-select-popup">
            {PERSONAS.map((p) => (
              <Select.Item key={p.id} value={p.id} className="persona-select-item">
                <Select.ItemText>{p.label}</Select.ItemText>
                <Select.ItemIndicator aria-hidden>✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
