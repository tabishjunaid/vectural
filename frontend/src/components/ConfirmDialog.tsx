import { Dialog } from '@base-ui/react/dialog';

/* Review-action confirmation, on Base UI's Dialog primitive (focus trap +
   ARIA from the primitive). In UI-0 the actions are mock-only; UI-4 wires
   approve / request-changes / reject to the architect-review workflow. */
export function ConfirmDialog({
  open,
  onClose,
  title,
  message,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  message: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop className="dialog-backdrop" />
        <Dialog.Popup className="dialog-popup">
          <Dialog.Title render={<h4 />}>{title}</Dialog.Title>
          <Dialog.Description render={<p />}>{message}</Dialog.Description>
          <div className="dialog-actions">
            <button className="btn-approve" onClick={onClose}>
              OK
            </button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
