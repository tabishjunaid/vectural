import { Select } from '@base-ui/react/select';
import { useModel, type ModelId } from '../lib/model';

/* Answer-model switcher, sitting beside PersonaSelect/DepthSelect in the composer.
   Same Base UI Select primitive so focus/keyboard/ARIA match the other controls.
   Options are fetched from the backend (GET /models via the model context), so
   the dropdown lists only models whose provider gateway is wired. Renders nothing
   until the list has loaded (or if none are available). */
export function ModelSelect() {
  const { models, modelId, setModelId } = useModel();
  if (!models.length || modelId === null) return null;
  return (
    <Select.Root value={modelId} onValueChange={(v) => setModelId(v as ModelId)}>
      <Select.Trigger className="persona-select-trigger" aria-label="Answer model">
        <Select.Value>
          {(value: ModelId) => models.find((m) => m.id === value)?.label ?? value}
        </Select.Value>
        <Select.Icon aria-hidden>▾</Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner align="start">
          <Select.Popup className="persona-select-popup">
            {models.map((m) => (
              <Select.Item key={m.id} value={m.id} className="persona-select-item">
                <Select.ItemText>{m.label}</Select.ItemText>
                <Select.ItemIndicator aria-hidden>✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
