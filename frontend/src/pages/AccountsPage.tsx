import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Avatar, Badge, Empty, Modal, Spinner, useToast } from "../components/ui";
import { ACCOUNT_STATUS, PRIVACY_LABELS, formatRelative, plural } from "../format";
import type { Account, AccountStatus, Group } from "../types";

function connectUrl(accountId?: number) {
  return `/api/oauth/tiktok/start${accountId ? `?account_id=${accountId}` : ""}`;
}

export default function AccountsPage() {
  const { canWrite, meta } = useSession();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<AccountStatus | "">("");
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [showGroups, setShowGroups] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [a, g] = await Promise.all([api.get<Account[]>("/api/accounts"), api.get<Group[]>("/api/groups")]);
    setAccounts(a);
    setGroups(g);
  }, []);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  // Результат возврата из OAuth.
  useEffect(() => {
    const connected = params.get("connected");
    const error = params.get("oauth_error");
    if (connected) toast("Аккаунт TikTok подключён", "success");
    if (error) toast(`Подключение не удалось: ${error}`, "danger");
    if (connected || error) setParams({}, { replace: true });
  }, [params, setParams, toast]);

  const filtered = useMemo(() => {
    if (!accounts) return [];
    const q = search.trim().toLowerCase();
    return accounts.filter((a) => {
      if (statusFilter && a.connection_status !== statusFilter) return false;
      if (groupFilter === "none" && a.groups.length) return false;
      if (groupFilter && groupFilter !== "none" && !a.groups.some((g) => String(g.id) === groupFilter)) return false;
      if (!q) return true;
      return [a.label, a.username, a.display_name, a.notes].some((v) => v?.toLowerCase().includes(q));
    });
  }, [accounts, search, groupFilter, statusFilter]);

  const stats = useMemo(() => {
    const s = { total: accounts?.length ?? 0, connected: 0, needs_reauth: 0, not_connected: 0 };
    accounts?.forEach((a) => (s[a.connection_status] += 1));
    return s;
  }, [accounts]);

  const allFilteredSelected = filtered.length > 0 && filtered.every((a) => selected.has(a.id));

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) filtered.forEach((a) => next.delete(a.id));
      else filtered.forEach((a) => next.add(a.id));
      return next;
    });
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulk(action: string, groupId?: number) {
    const ids = [...selected];
    if (action === "delete" && !confirm(`Удалить ${ids.length} ${plural(ids.length, "аккаунт", "аккаунта", "аккаунтов")}? Доступ приложения будет отозван.`)) return;
    try {
      await api.post("/api/accounts/bulk", { account_ids: ids, action, group_id: groupId });
      if (action === "check") toast("Проверка поставлена в очередь — обновите страницу через минуту", "info");
      if (action === "delete") setSelected(new Set());
      await load();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function checkOne(account: Account) {
    setBusyId(account.id);
    try {
      const updated = await api.post<Account>(`/api/accounts/${account.id}/check`);
      setAccounts((list) => list?.map((a) => (a.id === updated.id ? updated : a)) ?? null);
      if (updated.last_error) toast(updated.last_error, "danger");
      else toast(`${updated.label}: данные обновлены`, "success");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusyId(null);
    }
  }

  async function disconnect(account: Account) {
    if (!confirm(`Отключить «${account.label}» от TikTok? Доступ приложения будет отозван.`)) return;
    try {
      await api.post(`/api/accounts/${account.id}/disconnect`);
      await load();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function remove(account: Account) {
    if (!confirm(`Удалить «${account.label}» из реестра?`)) return;
    try {
      await api.del(`/api/accounts/${account.id}`);
      await load();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  if (!accounts) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Аккаунты</h1>
          <p className="muted">
            {stats.total} в реестре · {stats.connected} подключено
            {stats.needs_reauth > 0 && <> · <span className="text-warning">{stats.needs_reauth} требуют авторизации</span></>}
          </p>
        </div>
        {canWrite && (
          <div className="actions">
            <button className="btn" onClick={() => setShowGroups(true)}>
              Группы
            </button>
            <button className="btn" onClick={() => setShowImport(true)}>
              Импорт CSV
            </button>
            <button className="btn" onClick={() => setEditing("new")}>
              Добавить запись
            </button>
            <a
              className={`btn btn-primary ${meta.tiktok_configured ? "" : "disabled"}`}
              href={meta.tiktok_configured ? connectUrl() : undefined}
            >
              Подключить аккаунт TikTok
            </a>
          </div>
        )}
      </header>

      <p className="hint">
        Каждый аккаунт подключается через официальный вход TikTok: владелец входит в свой аккаунт и разрешает
        приложению публикацию. Чтобы подключить заранее созданную запись, нажмите «Подключить» в её строке —
        система проверит, что вход выполнен именно в нужный аккаунт.
      </p>

      <div className="toolbar">
        <input
          className="search"
          placeholder="Поиск по названию, @username, заметкам"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} aria-label="Группа">
          <option value="">Все группы</option>
          <option value="none">Без группы</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as AccountStatus | "")} aria-label="Статус">
          <option value="">Любой статус</option>
          {Object.entries(ACCOUNT_STATUS).map(([key, v]) => (
            <option key={key} value={key}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      {canWrite && selected.size > 0 && (
        <BulkBar
          count={selected.size}
          groups={groups}
          onAction={bulk}
          onClear={() => setSelected(new Set())}
        />
      )}

      {accounts.length === 0 ? (
        <Empty title="Аккаунтов пока нет">Подключите аккаунт TikTok или импортируйте список из CSV.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                {canWrite && (
                  <th className="col-check">
                    <input type="checkbox" checked={allFilteredSelected} onChange={toggleAll} aria-label="Выбрать все" />
                  </th>
                )}
                <th>Аккаунт</th>
                <th>Группы</th>
                <th>Статус</th>
                <th>Доступная видимость</th>
                <th>Проверен</th>
                {canWrite && <th className="col-actions" />}
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => (
                <tr key={a.id} className={selected.has(a.id) ? "row-selected" : ""}>
                  {canWrite && (
                    <td className="col-check">
                      <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggle(a.id)} aria-label={`Выбрать ${a.label}`} />
                    </td>
                  )}
                  <td>
                    <div className="who">
                      <Avatar name={a.display_name || a.label} url={a.avatar_url} />
                      <div>
                        <button className="link-btn strong" onClick={() => canWrite && setEditing(a)} disabled={!canWrite}>
                          {a.label}
                        </button>
                        <div className="muted small">
                          {a.username ? `@${a.username}` : "не подключён"}
                          {a.display_name && a.display_name !== a.label ? ` · ${a.display_name}` : ""}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="chips">
                      {a.groups.map((g) => (
                        <span className="chip" key={g.id}>
                          {g.name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <Badge tone={ACCOUNT_STATUS[a.connection_status].tone}>{ACCOUNT_STATUS[a.connection_status].label}</Badge>
                    {a.last_error && <div className="small text-danger">{a.last_error}</div>}
                  </td>
                  <td className="small">
                    {a.creator_info?.privacy_level_options?.map((p) => PRIVACY_LABELS[p] ?? p).join(", ") || "—"}
                    {a.creator_info?.max_video_post_duration_sec && (
                      <div className="muted">до {a.creator_info.max_video_post_duration_sec} с</div>
                    )}
                  </td>
                  <td className="small muted">{formatRelative(a.creator_info_checked_at)}</td>
                  {canWrite && (
                    <td className="col-actions">
                      <div className="row-actions">
                        {a.connection_status === "connected" ? (
                          <button className="btn btn-sm" onClick={() => checkOne(a)} disabled={busyId === a.id}>
                            {busyId === a.id ? "…" : "Проверить"}
                          </button>
                        ) : (
                          <a className="btn btn-sm btn-primary" href={meta.tiktok_configured ? connectUrl(a.id) : undefined}>
                            {a.connection_status === "needs_reauth" ? "Переподключить" : "Подключить"}
                          </a>
                        )}
                        <RowMenu
                          items={[
                            ...(a.connection_status !== "not_connected"
                              ? [{ label: "Отключить от TikTok", onClick: () => disconnect(a) }]
                              : []),
                            { label: "Удалить из реестра", onClick: () => remove(a), danger: true },
                          ]}
                        />
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && <Empty title="Ничего не найдено">Измените фильтры.</Empty>}
        </div>
      )}

      {editing && (
        <AccountEditor
          account={editing === "new" ? null : editing}
          groups={groups}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
      {showImport && (
        <ImportDialog
          onClose={() => setShowImport(false)}
          onDone={() => {
            setShowImport(false);
            load();
          }}
        />
      )}
      {showGroups && <GroupsDialog groups={groups} onClose={() => setShowGroups(false)} onChanged={load} />}
    </div>
  );
}

function BulkBar({
  count,
  groups,
  onAction,
  onClear,
}: {
  count: number;
  groups: Group[];
  onAction: (action: string, groupId?: number) => void;
  onClear: () => void;
}) {
  const [groupId, setGroupId] = useState<string>("");
  return (
    <div className="bulk-bar">
      <strong>
        Выбрано: {count}
      </strong>
      <select value={groupId} onChange={(e) => setGroupId(e.target.value)} aria-label="Группа для действия">
        <option value="">Группа…</option>
        {groups.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </select>
      <button className="btn btn-sm" disabled={!groupId} onClick={() => onAction("add_to_group", Number(groupId))}>
        Добавить в группу
      </button>
      <button className="btn btn-sm" disabled={!groupId} onClick={() => onAction("remove_from_group", Number(groupId))}>
        Убрать из группы
      </button>
      <button className="btn btn-sm" onClick={() => onAction("check")}>
        Проверить
      </button>
      <button className="btn btn-sm btn-danger-ghost" onClick={() => onAction("delete")}>
        Удалить
      </button>
      <button className="link-btn" onClick={onClear}>
        Снять выделение
      </button>
    </div>
  );
}

function RowMenu({ items }: { items: { label: string; onClick: () => void; danger?: boolean }[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button className="icon-btn" onClick={() => setOpen((v) => !v)} aria-label="Ещё действия">
        ⋯
      </button>
      {open && (
        <div className="menu-list">
          {items.map((item) => (
            <button
              key={item.label}
              className={item.danger ? "text-danger" : ""}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AccountEditor({
  account,
  groups,
  onClose,
  onSaved,
}: {
  account: Account | null;
  groups: Group[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [label, setLabel] = useState(account?.label ?? "");
  const [notes, setNotes] = useState(account?.notes ?? "");
  const [groupIds, setGroupIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      if (account) await api.patch(`/api/accounts/${account.id}`, { label, notes });
      else await api.post("/api/accounts", { label, notes, group_ids: groupIds });
      onSaved();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={account ? "Аккаунт" : "Новая запись"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !label.trim()}>
            Сохранить
          </button>
        </>
      }
    >
      {!account && (
        <p className="muted small">
          Запись без подключения. После сохранения нажмите «Подключить» в её строке и войдите в нужный аккаунт TikTok.
        </p>
      )}
      <label className="field">
        <span>Название во внутреннем реестре</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={128} autoFocus />
      </label>
      <label className="field">
        <span>Заметки</span>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
      </label>
      {!account && groups.length > 0 && (
        <fieldset className="field">
          <span>Группы</span>
          <div className="chips">
            {groups.map((g) => (
              <label key={g.id} className="chip chip-toggle">
                <input
                  type="checkbox"
                  checked={groupIds.includes(g.id)}
                  onChange={(e) =>
                    setGroupIds((ids) => (e.target.checked ? [...ids, g.id] : ids.filter((id) => id !== g.id)))
                  }
                />
                {g.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {account?.open_id && <p className="muted small">open_id: {account.open_id}</p>}
    </Modal>
  );
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload() {
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    try {
      setResult(await api.post("/api/accounts/import", form));
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Импорт из CSV"
      onClose={result ? onDone : onClose}
      footer={
        result ? (
          <button className="btn btn-primary" onClick={onDone}>
            Готово
          </button>
        ) : (
          <>
            <button className="btn" onClick={onClose}>
              Отмена
            </button>
            <button className="btn btn-primary" onClick={upload} disabled={!file || busy}>
              Импортировать
            </button>
          </>
        )
      }
    >
      {result ? (
        <div>
          <p>
            Создано: <strong>{result.created}</strong>, пропущено (уже есть): <strong>{result.skipped}</strong>
          </p>
          {result.errors.length > 0 && (
            <ul className="error-list">
              {result.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <p className="muted small">Импортированные записи нужно подключить к TikTok через кнопку «Подключить».</p>
        </div>
      ) : (
        <>
          <p className="muted small">
            Колонки: <code>label</code> (обязательно), <code>groups</code> (несколько групп через <code>|</code>),{" "}
            <code>notes</code>. Разделитель — запятая или точка с запятой. Записи с уже существующим label пропускаются.
          </p>
          <pre className="code-sample">{"label;groups;notes\nshop-01;Магазин|Россия;основной\nshop-02;Магазин;"}</pre>
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </>
      )}
    </Modal>
  );
}

function GroupsDialog({ groups, onClose, onChanged }: { groups: Group[]; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");

  async function create() {
    try {
      await api.post("/api/groups", { name });
      setName("");
      onChanged();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function remove(group: Group) {
    if (!confirm(`Удалить группу «${group.name}»? Аккаунты останутся в реестре.`)) return;
    try {
      await api.del(`/api/groups/${group.id}`);
      onChanged();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  return (
    <Modal title="Группы аккаунтов" onClose={onClose}>
      <div className="inline-form">
        <input placeholder="Название новой группы" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        <button className="btn btn-primary" onClick={create} disabled={!name.trim()}>
          Создать
        </button>
      </div>
      {groups.length === 0 ? (
        <p className="muted">Групп пока нет.</p>
      ) : (
        <ul className="plain-list">
          {groups.map((g) => (
            <li key={g.id}>
              <span>{g.name}</span>
              <button className="link-btn text-danger" onClick={() => remove(g)}>
                Удалить
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
