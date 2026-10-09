import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AdminPage } from "./admin/AdminPage";
import { AskPage } from "./ask/AskPage";
import logo from "./assets/logo/cortex-mark.svg";
import { AuditPage } from "./audit/AuditPage";
import { usePersona } from "./auth/PersonaContext";
import { canAdmin, canAudit } from "./auth/personas";
import { ComparePage } from "./compare/ComparePage";
import { Avatar } from "./components/Avatar";
import { Sidebar } from "./components/Sidebar";
import { DemoControls } from "./demo/DemoControls";
import { DEMO_CONTROLS } from "./demo/sim";
import { HomePage } from "./home/HomePage";

export function App() {
  const { persona } = usePersona();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [location.key, persona.id]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      {/* Phones: a top bar opens the sidebar as a drawer. Hidden on wider screens. */}
      <header className="mobilebar">
        <button type="button" className="icon-button" aria-label="Open menu" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
          <Menu size={20} />
        </button>
        <img src={logo} alt="" width={18} height={18} />
        <span className="wordmark">cortex</span>
        <Avatar src={persona.avatar} name={persona.name} size={28} />
      </header>
      {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} aria-hidden />}
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <main className="app__main" id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/ask/:conversationId" element={<AskPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/audit" element={canAudit(persona) ? <AuditPage /> : <Navigate to="/" replace />} />
          <Route path="/admin" element={canAdmin(persona) ? <AdminPage /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {DEMO_CONTROLS && <DemoControls />}
    </div>
  );
}
