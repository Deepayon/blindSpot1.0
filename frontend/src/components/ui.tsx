/** Shared presentational primitives.
 *
 * Kept in one module because each is a handful of lines; splitting them across
 * a dozen files would add navigation cost without adding clarity.
 */
import React from "react";
import type { Coverage, Risk } from "../types";

// ---------------------------------------------------------------- labels

const COVERAGE_LABEL: Record<Coverage, string> = {
  COVERED: "Covered",
  PARTIAL: "Partially Covered",
  NOT_COVERED: "Not Covered",
  INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
};

const COVERAGE_TONE: Record<Coverage, string> = {
  COVERED: "covered",
  PARTIAL: "partial",
  NOT_COVERED: "missing",
  INSUFFICIENT_EVIDENCE: "neutral",
};

export function coverageLabel(coverage: Coverage | null | undefined): string {
  return coverage ? COVERAGE_LABEL[coverage] ?? coverage : "Not analysed";
}

export function coverageTone(coverage: Coverage | null | undefined): string {
  return coverage ? COVERAGE_TONE[coverage] ?? "neutral" : "neutral";
}

/** `BOUNDARY_CONDITION` -> `Boundary Condition` */
export function titleize(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------- pieces

export function CoverageBadge({ coverage, large }: { coverage: Coverage | null | undefined; large?: boolean }) {
  return (
    <span className={`badge badge--${coverageTone(coverage)}${large ? " badge--lg" : ""}`}>
      {coverageLabel(coverage)}
    </span>
  );
}

const RISK_TONE: Record<Risk, string> = { HIGH: "missing", MEDIUM: "partial", LOW: "covered" };

export function RiskBadge({ risk }: { risk: Risk | null | undefined }) {
  if (!risk) return null;
  return <span className={`badge badge--${RISK_TONE[risk] ?? "neutral"}`}>{titleize(risk)} risk</span>;
}

export function Tag({ children }: { children: React.ReactNode }) {
  return <span className="tag">{children}</span>;
}

export function Card({
  title,
  actions,
  children,
  note,
  flush,
}: {
  title?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  note?: string;
  flush?: boolean;
}) {
  if (!title) return <div className="card">{children}</div>;
  return (
    <section className="card card--flush">
      <header className="card__head">
        <div>
          <div className="card__title">{title}</div>
          {note ? <div className="card__note">{note}</div> : null}
        </div>
        {actions ? <div className="row row--tight">{actions}</div> : null}
      </header>
      {flush ? children : <div className="card__body">{children}</div>}
    </section>
  );
}

export function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "covered" | "partial" | "missing";
}) {
  return (
    <div className={`metric${tone ? ` metric--${tone}` : ""}`}>
      <div className="metric__label">{label}</div>
      <div className="metric__value">{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row row--tight">
      <span className="spinner" aria-hidden="true" />
      {label ? <span style={{ color: "var(--text-3)", fontSize: 13 }}>{label}</span> : null}
    </span>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty">
      <Spinner label={label} />
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      {children ? <div>{children}</div> : null}
    </div>
  );
}

export function Banner({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "ok" | "warn";
  children: React.ReactNode;
}) {
  const suffix = tone === "info" ? "" : ` banner--${tone}`;
  return <div className={`banner${suffix}`}>{children}</div>;
}

export function Meter({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="meter" role="presentation">
      <div className="meter__fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Stat({ label, value, children }: { label: string; value: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div>
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {children}
    </div>
  );
}

/** Renders `{discount: "100%"}` as readable `discount = 100%` pairs. */
export function ConditionList({ conditions }: { conditions: Record<string, unknown> }) {
  const entries = Object.entries(conditions || {});
  if (!entries.length) return <span style={{ color: "var(--text-3)" }}>none extracted</span>;
  return (
    <span className="row row--tight">
      {entries.map(([key, value]) => (
        <Tag key={key}>
          {key} = {String(value)}
        </Tag>
      ))}
    </span>
  );
}
