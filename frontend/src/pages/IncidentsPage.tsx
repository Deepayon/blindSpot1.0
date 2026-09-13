/** Screens 3 & 4, incident submission and analysis result (spec §27-§29). */
import React, { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import { useAction, useAsync } from "../hooks/useAsync";
import { AnalysisResultView } from "../features/AnalysisResultView";
import type { AnalysisResult, Coverage, IncidentListItem } from "../types";
import {
  Banner,
  Card,
  CoverageBadge,
  EmptyState,
  Loading,
  RiskBadge,
  formatDate,
  titleize,
} from "../components/ui";

const EXAMPLE =
  "Checkout failed when a customer applied a 100% discount coupon during a promotion.\n" +
  "Root cause: division by zero in the discount calculation when the payable total reached zero.";

const PROGRESS = [
  "Understanding the incident...",
  "Searching indexed tests...",
  "Comparing behaviour...",
  "Classifying coverage...",
];

export function IncidentsPage({
  incidentId,
  navigate,
}: {
  incidentId?: string;
  navigate: (route: string) => void;
}) {
  if (incidentId) return <IncidentDetail incidentId={incidentId} navigate={navigate} />;
  return <IncidentWorkbench navigate={navigate} />;
}

// ---------------------------------------------------------------- submit

function IncidentWorkbench({ navigate }: { navigate: (route: string) => void }) {
  const [text, setText] = useState("");
  const [severity, setSeverity] = useState("");
  const [feature, setFeature] = useState("");
  const [listKey, setListKey] = useState(0);
  const [step, setStep] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const analyze = useAction(api.analyze);
  const upload = useAction(api.uploadIncident);
  const pending = analyze.pending || upload.pending;
  const result = analyze.result ?? upload.result;
  const error = analyze.error ?? upload.error;

  // Walk the progress labels while the request is in flight. Cosmetic, but it
  // names the pipeline stages so the wait is legible rather than opaque.
  useEffect(() => {
    if (!pending) {
      setStep(0);
      return;
    }
    const timer = setInterval(() => setStep((s) => Math.min(s + 1, PROGRESS.length - 1)), 320);
    return () => clearInterval(timer);
  }, [pending]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (text.trim().length < 3) return;
    upload.reset();
    const extra: Record<string, unknown> = {};
    if (severity) extra.severity = severity;
    if (feature) extra.feature = feature;
    const outcome = await analyze.run(text.trim(), extra);
    if (outcome) setListKey((n) => n + 1);
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Incidents</h1>
        <div className="page-head__sub">
          Check whether a production failure was covered by your existing tests.
        </div>
      </div>

      <Card title="Production incident">
        <form onSubmit={submit}>
          <div className="field">
            <textarea
              className="textarea"
              placeholder="Paste production incident here..."
              value={text}
              onChange={(event) => setText(event.target.value)}
              disabled={pending}
            />
          </div>
          <div className="row">
            <select
              className="select"
              style={{ width: 160 }}
              value={feature}
              onChange={(event) => setFeature(event.target.value)}
              disabled={pending}
            >
              <option value="">Detect feature</option>
              {["Authentication", "Checkout", "Payments", "Orders", "Profile", "Search", "Notifications"].map(
                (name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ),
              )}
            </select>
            <select
              className="select"
              style={{ width: 150 }}
              value={severity}
              onChange={(event) => setSeverity(event.target.value)}
              disabled={pending}
            >
              <option value="">Detect severity</option>
              {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((level) => (
                <option key={level} value={level}>
                  {titleize(level)}
                </option>
              ))}
            </select>
            <span className="spacer" />
            <button
              type="button"
              className="btn"
              disabled={pending}
              onClick={() => setText(EXAMPLE)}
            >
              Use example
            </button>
            <button
              type="button"
              className="btn"
              disabled={pending}
              onClick={() => fileInput.current?.click()}
            >
              Upload file
            </button>
            <button className="btn btn--primary" type="submit" disabled={pending || text.trim().length < 3}>
              Analyse incident
            </button>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept=".txt,.md,.log,.json"
            style={{ display: "none" }}
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              analyze.reset();
              const outcome = await upload.run(file);
              if (outcome) setListKey((n) => n + 1);
            }}
          />
        </form>

        {pending ? (
          <ul className="steps" style={{ marginTop: 14 }}>
            {PROGRESS.map((label, index) => (
              <li key={label} className={index < step ? "is-done" : index === step ? "is-active" : ""}>
                {index < step ? "yes " : index === step ? "> " : "  "}
                {label}
              </li>
            ))}
          </ul>
        ) : null}

        {error ? (
          <div style={{ marginTop: 12 }}>
            <Banner tone="error">{error}</Banner>
          </div>
        ) : null}
      </Card>

      {result ? <AnalysisResultView result={result as AnalysisResult} /> : null}

      <IncidentList key={listKey} navigate={navigate} />
    </div>
  );
}

// ---------------------------------------------------------------- list

function IncidentList({ navigate }: { navigate: (route: string) => void }) {
  const [coverage, setCoverage] = useState("");
  const { data, error, loading } = useAsync(
    () => api.listIncidents({ coverage: coverage || undefined, limit: 100 }),
    [coverage],
  );

  return (
    <Card
      title="Analysed incidents"
      actions={
        <div className="seg">
          {[
            { value: "", label: "All" },
            { value: "COVERED", label: "Covered" },
            { value: "PARTIAL", label: "Partial" },
            { value: "NOT_COVERED", label: "Not covered" },
          ].map((option) => (
            <button
              key={option.value}
              className={`seg__btn${coverage === option.value ? " seg__btn--active" : ""}`}
              onClick={() => setCoverage(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      }
      flush
    >
      {loading ? (
        <Loading />
      ) : error ? (
        <div className="card__body">
          <Banner tone="error">{error}</Banner>
        </div>
      ) : data?.items.length ? (
        <div className="table__scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Incident</th>
                <th>Feature</th>
                <th>Coverage</th>
                <th>Gap</th>
                <th>Risk</th>
                <th className="nowrap">Confidence</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((incident: IncidentListItem) => (
                <tr
                  key={incident.id}
                  className="is-clickable"
                  onClick={() => navigate(`incidents/${incident.id}`)}
                >
                  <td>
                    <div style={{ fontWeight: 550 }}>{incident.title || incident.description.slice(0, 80)}</div>
                    <div className="mono" style={{ color: "var(--text-3)" }}>
                      {incident.id}
                    </div>
                  </td>
                  <td className="nowrap">{incident.feature}</td>
                  <td className="nowrap">
                    <CoverageBadge coverage={incident.coverage as Coverage | null} />
                  </td>
                  <td className="nowrap">{titleize(incident.gap_type ?? "") || ", "}</td>
                  <td className="nowrap">
                    <RiskBadge risk={incident.risk ?? null} />
                  </td>
                  <td className="num nowrap">
                    {incident.confidence != null ? `${Math.round(incident.confidence * 100)}%` : ", "}
                  </td>
                  <td className="nowrap" style={{ color: "var(--text-3)" }}>
                    {formatDate(incident.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No incidents yet">
          Analyse an incident above and it will appear here.
        </EmptyState>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- detail

function IncidentDetail({
  incidentId,
  navigate,
}: {
  incidentId: string;
  navigate: (route: string) => void;
}) {
  const { data, error, loading, reload } = useAsync(() => api.getIncident(incidentId), [incidentId]);
  const reanalyze = useAction(api.reanalyze);

  if (loading) return <Loading label="Loading analysis..." />;
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return null;

  return (
    <div className="stack">
      <div className="row">
        <button className="btn btn--ghost btn--sm" onClick={() => navigate("incidents")}>
          Back to incidents
        </button>
        <span className="spacer" />
        <button
          className="btn btn--sm"
          disabled={reanalyze.pending}
          onClick={async () => {
            const result = await reanalyze.run(incidentId);
            if (result) reload();
          }}
        >
          Re-analyse against current index
        </button>
      </div>
      {reanalyze.error ? <Banner tone="error">{reanalyze.error}</Banner> : null}
      <AnalysisResultView result={reanalyze.result ?? data} onChanged={reload} />
    </div>
  );
}
