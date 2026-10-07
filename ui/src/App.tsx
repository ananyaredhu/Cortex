import { Navigate, Route, Routes } from "react-router-dom";
import { AdminPage } from "./admin/AdminPage";
import { AskPage } from "./ask/AskPage";
import { AuditPage } from "./audit/AuditPage";
import { usePersona } from "./auth/PersonaContext";
import { canAdmin, canAudit } from "./auth/personas";
import { ComparePage } from "./compare/ComparePage";
import { Sidebar } from "./components/Sidebar";
import { DemoControls } from "./demo/DemoControls";
import { DEMO_CONTROLS } from "./demo/sim";
import { Placeholder } from "./routes/Placeholder";

export function App() {
  const { persona } = usePersona();
  return (
    <div className="app">
      <Sidebar />
      <main className="app__main">
        <Routes>
          <Route path="/" element={<Placeholder title="My work" phase="Phase 4" mockup="Workspace.dc.html" />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route
            path="/audit"
            element={
              canAudit(persona) ? (
                <AuditPage />
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
          <Route
            path="/admin"
            element={
              canAdmin(persona) ? (
                <AdminPage />
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {DEMO_CONTROLS && <DemoControls />}
    </div>
  );
}
