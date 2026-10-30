import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api";

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (needCode) {
        await api.post("/api/auth/login/2fa", { code });
        onLogin();
        return;
      }
      const result = await api.post<{ two_factor_required: boolean }>("/api/auth/login", { username, password });
      if (result.two_factor_required) setNeedCode(true);
      else onLogin();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Не удалось войти";
      setError(message);
      // Истёк срок ввода кода — начинаем вход заново.
      if (needCode && message.includes("войдите заново")) {
        setNeedCode(false);
        setCode("");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-screen">
      <form className="card login" onSubmit={submit}>
        <h1>Очередь публикаций</h1>
        {needCode ? (
          <>
            <p className="muted">Введите 6-значный код из приложения-аутентификатора.</p>
            <label className="field">
              <span>Код</span>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
              />
            </label>
          </>
        ) : (
          <>
            <p className="muted">Войдите, чтобы управлять аккаунтами и кампаниями.</p>
            <label className="field">
              <span>Логин</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
          </>
        )}
        {error && <div className="alert alert-danger">{error}</div>}
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Проверка…" : needCode ? "Подтвердить" : "Войти"}
        </button>
        {needCode && (
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              setNeedCode(false);
              setCode("");
              setError(null);
            }}
          >
            Войти под другим пользователем
          </button>
        )}
      </form>
    </div>
  );
}
