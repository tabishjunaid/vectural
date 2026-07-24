import { Select } from '@base-ui/react/select';
import { DEPTHS, useDepth, type DepthId } from '../lib/depth';

/* Answer-depth switcher, sitting beside PersonaSelect in the composer. Built on
   the same Base UI Select primitive so focus, keyboard nav and ARIA behave
   identically to the persona control rather than being hand-rolled twice. */
export function DepthSelect() {
  const { depthId, setDepthId } = useDepth();
  return (
    <Select.Root value={depthId} onValueChange={(v) => setDepthId(v as DepthId)}>
      <Select.Trigger className="persona-select-trigger" aria-label="Answer depth">
        <Select.Value>
          {(value: DepthId) => DEPTHS.find((d) => d.id === value)?.label}
        </Select.Value>
        <Select.Icon aria-hidden>▾</Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner align="start">
          <Select.Popup className="persona-select-popup">
            {DEPTHS.map((d) => (
              <Select.Item key={d.id} value={d.id} className="persona-select-item">
                <Select.ItemText>{d.label}</Select.ItemText>
                <Select.ItemIndicator aria-hidden>✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
