import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

export interface ChartPoint {
  key: string;
  label: string;
  value: number;
}

const HEIGHT = 220;
const M = { top: 14, right: 56, bottom: 26, left: 52 };
const BAR_MAX = 24;

const compactFormat = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const fullFormat = new Intl.NumberFormat("ru-RU");

export function formatNumber(value: number): string {
  return Math.abs(value) >= 10_000 ? compactFormat.format(value) : fullFormat.format(value);
}

function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return nice * power;
}

function scaleY(points: ChartPoint[], fromZero: boolean) {
  const values = points.map((p) => p.value);
  const max = Math.max(...values, 0);
  const min = fromZero ? 0 : Math.min(...values);
  if (max === min) {
    const step = max === 0 ? 1 : niceStep(Math.abs(max) / 2);
    const lo = fromZero ? 0 : Math.max(0, Math.floor(min / step) * step - step);
    return { lo, hi: lo + step * 4, ticks: [0, 1, 2, 3, 4].map((i) => lo + step * i) };
  }
  const step = niceStep((max - min) / 4);
  const lo = fromZero ? 0 : Math.floor(min / step) * step;
  const count = Math.ceil((max - lo) / step);
  const ticks = Array.from({ length: count + 1 }, (_, i) => lo + step * i);
  return { lo, hi: ticks[ticks.length - 1], ticks };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Подписи по оси X: не больше ~6, равномерно. */
function xLabelIndexes(n: number, plotWidth: number): number[] {
  const max = Math.max(2, Math.min(7, Math.floor(plotWidth / 70)));
  if (n <= max) return Array.from({ length: n }, (_, i) => i);
  const step = (n - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => Math.round(i * step));
}

function Tooltip({ x, y, width, title, value }: { x: number; y: number; width: number; title: string; value: string }) {
  const left = Math.min(Math.max(x - 70, 0), width - 140);
  return (
    <div className="chart-tooltip" style={{ left, top: Math.max(y - 64, 0) }} role="status">
      <strong>{value}</strong>
      <span>
        <i className="tooltip-key" />
        {title}
      </span>
    </div>
  );
}

function Axes({ ticks, lo, hi, plotW, plotH }: { ticks: number[]; lo: number; hi: number; plotW: number; plotH: number }) {
  const y = (v: number) => M.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line className="chart-grid" x1={M.left} x2={M.left + plotW} y1={y(t)} y2={y(t)} />
          <text className="chart-axis-text" x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
            {formatNumber(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

/** Столбцы: величина за каждый день. */
export function ColumnChart({ points, seriesName }: { points: ChartPoint[]; seriesName: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const plotW = width - M.left - M.right;
  const plotH = HEIGHT - M.top - M.bottom;
  const { lo, hi, ticks } = scaleY(points, true);
  const band = plotW / Math.max(points.length, 1);
  const barW = Math.max(2, Math.min(BAR_MAX, band - 2));
  const y = (v: number) => M.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const labels = xLabelIndexes(points.length, plotW);

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={HEIGHT} role="img" aria-label={`${seriesName}: столбчатая диаграмма по дням`}>
        <Axes ticks={ticks} lo={lo} hi={hi} plotW={plotW} plotH={plotH} />
        <line className="chart-baseline" x1={M.left} x2={M.left + plotW} y1={y(0)} y2={y(0)} />
        {points.map((p, i) => {
          const x = M.left + band * i + (band - barW) / 2;
          const top = y(p.value);
          const h = y(0) - top;
          const r = Math.min(4, h, barW / 2);
          return (
            <g key={p.key} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              {/* Цель для наведения — вся полоса дня, а не только столбец. */}
              <rect x={M.left + band * i} y={M.top} width={band} height={plotH} fill="transparent" />
              {h > 0 && (
                <path
                  className={`chart-bar ${hover === i ? "is-hover" : ""}`}
                  d={`M${x},${y(0)} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${y(0)} Z`}
                />
              )}
            </g>
          );
        })}
        {labels.map((i) => (
          <text key={i} className="chart-axis-text" x={M.left + band * i + band / 2} y={HEIGHT - 6} textAnchor="middle">
            {points[i].label}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <Tooltip
          x={M.left + band * hover + band / 2}
          y={y(points[hover].value)}
          width={width}
          title={`${seriesName}, ${points[hover].label}`}
          value={formatNumber(points[hover].value)}
        />
      )}
    </div>
  );
}

/** Линия: значение на конец каждого дня, с перекрестием и подписью последнего значения. */
export function LineChart({ points, seriesName }: { points: ChartPoint[]; seriesName: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const plotW = width - M.left - M.right;
  const plotH = HEIGHT - M.top - M.bottom;
  const { lo, hi, ticks } = scaleY(points, false);
  const x = (i: number) => M.left + (points.length <= 1 ? plotW / 2 : (plotW * i) / (points.length - 1));
  const y = (v: number) => M.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
  const area = points.length > 1 ? `${path} L${x(points.length - 1)},${y(lo)} L${x(0)},${y(lo)} Z` : "";
  const labels = xLabelIndexes(points.length, plotW);
  const last = points.length - 1;

  function onMove(e: ReactPointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const rel = e.clientX - box.left - M.left;
    const i = points.length <= 1 ? 0 : Math.round((rel / plotW) * (points.length - 1));
    setHover(Math.max(0, Math.min(last, i)));
  }

  return (
    <div className="chart" ref={ref}>
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`${seriesName}: линия по дням`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <Axes ticks={ticks} lo={lo} hi={hi} plotW={plotW} plotH={plotH} />
        {area && <path className="chart-area" d={area} />}
        <path className="chart-line" d={path} />
        {hover !== null && <line className="chart-crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + plotH} />}
        {last >= 0 && (
          <>
            <circle className="chart-dot" cx={x(last)} cy={y(points[last].value)} r={4} />
            <text className="chart-end-label" x={x(last) + 8} y={y(points[last].value)} dy="0.32em">
              {formatNumber(points[last].value)}
            </text>
          </>
        )}
        {hover !== null && hover !== last && <circle className="chart-dot" cx={x(hover)} cy={y(points[hover].value)} r={4} />}
        {labels.map((i) => (
          <text key={i} className="chart-axis-text" x={x(i)} y={HEIGHT - 6} textAnchor="middle">
            {points[i].label}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <Tooltip
          x={x(hover)}
          y={y(points[hover].value)}
          width={width}
          title={`${seriesName}, ${points[hover].label}`}
          value={formatNumber(points[hover].value)}
        />
      )}
    </div>
  );
}

/** Карточка графика с переключателем «график / таблица»: значения доступны и без наведения. */
export function ChartCard({
  title,
  subtitle,
  points,
  valueTitle,
  children,
}: {
  title: string;
  subtitle?: string;
  points: ChartPoint[];
  valueTitle: string;
  children: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="card chart-card">
      <div className="chart-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="muted small">{subtitle}</p>}
        </div>
        <button className="link-btn small" onClick={() => setAsTable((v) => !v)}>
          {asTable ? "Показать график" : "Показать таблицей"}
        </button>
      </div>
      {asTable ? (
        <div className="chart-table">
          <table className="table">
            <thead>
              <tr>
                <th>День</th>
                <th className="num">{valueTitle}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.key}>
                  <td>{p.label}</td>
                  <td className="num">{fullFormat.format(p.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </section>
  );
}
