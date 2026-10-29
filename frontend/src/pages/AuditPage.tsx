import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, Spinner, useToast } from "../components/ui";
import { AUDIT_ACTIONS, formatDateTime } from "../format";
import type { AuditEvent } from "../types";

const PAGE = 100;

function detailsText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? (v.length > 8 ? `${v.length} шт.` : v.join(", ")) : String(v)}`)
    .join(" · ");
}

export default function AuditPage() {
  const toast = useToast();
  const [items, setItems] = useState<AuditEvent[] | null>(null);
  const [more, setMore] = useState(false);

  useEffect(() => {
    api
      .get<AuditEvent[]>(`/api/audit?limit=${PAGE}`)
      .then((data) => {
        setItems(data);
        setMore(data.length === PAGE);
      })
      .catch((e) => toast(e.message, "danger"));
  }, [toast]);

  async function loadMore() {
    if (!items?.length) return;
    const data = await api.get<AuditEvent[]>(`/api/audit?limit=${PAGE}&before_id=${items[items.length - 1].id}`);
    setItems([...items, ...data]);
    setMore(data.length === PAGE);
  }

  if (!items) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Журнал действий</h1>
          <p className="muted">Кто, когда и что сделал в панели.</p>
        </div>
      </header>
      {items.length === 0 ? (
        <Empty title="Событий пока нет" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Пользователь</th>
                <th>Действие</th>
                <th>Объект</th>
                <th>Детали</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td className="small nowrap">{formatDateTime(e.created_at)}</td>
                  <td>{e.actor_name}</td>
                  <td>{AUDIT_ACTIONS[e.action] ?? e.action}</td>
                  <td className="small muted">
                    {e.entity_type}
                    {e.entity_id ? ` #${e.entity_id}` : ""}
                  </td>
                  <td className="small muted break">{detailsText(e.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more && (
        <button className="btn" onClick={loadMore}>
          Показать ещё
        </button>
      )}
    </div>
  );
}
