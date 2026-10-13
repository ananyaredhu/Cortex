import { LoaderCircle } from "lucide-react";

/** Says a routine's run is under way: a label and a spinner, never colour or motion alone. */
export function Running({ label = "Running now" }: { label?: string }) {
  return (
    <span className="routine-running" role="status">
      <LoaderCircle size={12} className="spin" aria-hidden /> {label}
    </span>
  );
}
