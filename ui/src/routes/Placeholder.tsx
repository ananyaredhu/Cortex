// Screens not built yet. Each names the phase that builds it and the mockup it follows.
export function Placeholder({ title, phase, mockup }: { title: string; phase: string; mockup: string }) {
  return (
    <div className="page">
      <h1 className="h1">{title}</h1>
      <p className="muted">
        Not built yet ({phase}). Follows <code className="mono">ui/design-reference/screens/{mockup}</code>; see{" "}
        <code className="mono">ui/MOCKUP-DRIFT.md</code> for what changes from the mockup.
      </p>
    </div>
  );
}
