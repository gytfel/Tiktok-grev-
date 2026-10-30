import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Empty, Spinner, useToast } from "../components/ui";
import type { Role, User } from "../types";

const ROLE_LABELS: Record<Role, string> = {
  admin: "Администратор — всё, включая пользователей",
  operator: "Оператор — аккаунты, видео и кампании",
  viewer: "Наблюдатель — только просмотр",
};

export default function UsersPage() {
  const { user: me } = useSession();
  const toast = useToast();
  const [users, setUsers] = useState<User[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("operator");

  const load = useCallback(() => api.get<User[]>("/api/users").then(setUsers), []);

  useEffect(() => {
    if (me.role === "admin") load().catch((e) => toast(e.message, "danger"));
  }, [load, me.role, toast]);

  async function create(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/users", { username, password, role });
      setUsername("");
      setPassword("");
      await load();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  async function resetTwoFactor(user: User) {
    if (!confirm(`Сбросить двухфакторный вход для ${user.username}? Его сессии будут завершены, 2FA нужно будет настроить заново.`)) return;
    try {
      await api.post(`/api/users/${user.id}/reset-2fa`);
      await load();
      toast(`2FA для ${user.username} сброшен`, "success");
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  async function remove(user: User) {
    if (!confirm(`Удалить пользователя ${user.username}?`)) return;
    try {
      await api.del(`/api/users/${user.id}`);
      await load();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  if (me.role !== "admin") return <Empty title="Только для администратора" />;
  if (!users) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Пользователи панели</h1>
          <p className="muted">Доступ к панели. Не связано с аккаунтами TikTok.</p>
        </div>
      </header>
      <div className="form-grid">
        <div className="card">
          <h2>Пользователи</h2>
          <ul className="plain-list">
            {users.map((u) => (
              <li key={u.id}>
                <span>
                  <strong>{u.username}</strong> <span className="muted small">{ROLE_LABELS[u.role].split(" —")[0]}</span>{" "}
                  {u.totp_enabled && <Badge tone="success">2FA</Badge>}
                </span>
                {u.id !== me.id && (
                  <span className="row-actions">
                    {u.totp_enabled && (
                      <button className="link-btn" onClick={() => resetTwoFactor(u)}>
                        Сбросить 2FA
                      </button>
                    )}
                    <button className="link-btn text-danger" onClick={() => remove(u)}>
                      Удалить
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
        <form className="card" onSubmit={create}>
          <h2>Новый пользователь</h2>
          <label className="field">
            <span>Логин</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} minLength={2} required />
          </label>
          <label className="field">
            <span>Пароль (от 10 символов)</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} required autoComplete="new-password" />
          </label>
          <fieldset className="field">
            <legend>Роль</legend>
            <div className="checks vertical">
              {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
                <label key={r}>
                  <input type="radio" checked={role === r} onChange={() => setRole(r)} /> {ROLE_LABELS[r]}
                </label>
              ))}
            </div>
          </fieldset>
          <button className="btn btn-primary">Создать</button>
        </form>
      </div>
    </div>
  );
}
