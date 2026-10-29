import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api";
import { Spinner, ToastProvider } from "./components/ui";
import type { Meta, User } from "./types";
import LoginPage from "./pages/LoginPage";
import AccountsPage from "./pages/AccountsPage";
import MediaPage from "./pages/MediaPage";
import CampaignsPage from "./pages/CampaignsPage";
import NewCampaignPage from "./pages/NewCampaignPage";
import CampaignPage from "./pages/CampaignPage";
import AuditPage from "./pages/AuditPage";
import UsersPage from "./pages/UsersPage";

interface Session {
  user: User;
  meta: Meta;
  canWrite: boolean;
  logout: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside of session");
  return session;
}

function Shell({ session }: { session: Session }) {
  const { user, meta } = session;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ▶
          </span>
          <span>Очередь публикаций</span>
        </div>
        <nav>
          <NavLink to="/campaigns" end>
            Кампании
          </NavLink>
          {session.canWrite && <NavLink to="/campaigns/new">Новая кампания</NavLink>}
          <NavLink to="/accounts">Аккаунты</NavLink>
          <NavLink to="/media">Видео</NavLink>
          <NavLink to="/audit">Журнал</NavLink>
          {user.role === "admin" && <NavLink to="/users">Пользователи</NavLink>}
        </nav>
        <div className="sidebar-foot">
          <div className="muted small">
            {user.username} · {user.role === "admin" ? "админ" : user.role === "operator" ? "оператор" : "просмотр"}
          </div>
          <button className="link-btn" onClick={session.logout}>
            Выйти
          </button>
        </div>
      </aside>
      <main className="content">
        {meta.emulator && (
          <div className="banner banner-warning">
            Режим эмулятора: панель подключена к локальному эмулятору TikTok API, а не к настоящему TikTok.
          </div>
        )}
        {!meta.tiktok_configured && (
          <div className="banner banner-danger">
            Не заданы TIKTOK_CLIENT_KEY и TIKTOK_CLIENT_SECRET — подключение аккаунтов недоступно.
          </div>
        )}
        <Routes>
          <Route path="/" element={<Navigate to="/campaigns" replace />} />
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/campaigns/new" element={<NewCampaignPage />} />
          <Route path="/campaigns/:id" element={<CampaignPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/media" element={<MediaPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="*" element={<Navigate to="/campaigns" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Root() {
  const [state, setState] = useState<{ user: User; meta: Meta } | null | "loading">("loading");
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const user = await api.get<User>("/api/auth/me");
      const meta = await api.get<Meta>("/api/meta");
      setState({ user, meta });
    } catch {
      setState(null);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setState(null));
    load();
  }, [load]);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    setState(null);
    navigate("/");
  }, [navigate]);

  if (state === "loading") {
    return (
      <div className="center-screen">
        <Spinner />
      </div>
    );
  }
  if (state === null) return <LoginPage onLogin={load} />;
  const session: Session = { ...state, canWrite: state.user.role !== "viewer", logout };
  return (
    <SessionContext.Provider value={session}>
      <Shell session={session} />
    </SessionContext.Provider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <Root />
      </ToastProvider>
    </BrowserRouter>
  );
}
