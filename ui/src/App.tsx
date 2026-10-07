import { Navigate, Route, Routes } from "react-router-dom";
import { AskPage } from "./ask/AskPage";
import { usePersona } from "./auth/PersonaContext";
import { canAdmin, canAudit } from "./auth/personas";
import { ComparePage } from "./compare/ComparePage";
import { Sidebar } from "./components/Sidebar";
import { DEMO_CONTROLS, DemoControls } from "./demo/DemoControls";
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
                <Placeholder title="Audit console" phase="Phase 3" mockup="Audit Console.dc.html" />
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
          <Route
            path="/admin"
            element={
              canAdmin(persona) ? (
                <Placeholder title="Admin" phase="Phase 3" mockup="Admin.dc.html" />
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
