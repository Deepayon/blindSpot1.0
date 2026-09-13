/** Screen 1 — Dashboard (spec §25). */
import React from "react";
import { api } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import type { BlindSpotPattern, Coverage } from "../types";
import {
  Banner,
  Card,
  CoverageBadge,
  EmptyState,
  Loading,
  Metric,
  formatDate,
  titleize,
} from "../components/ui";

export function DashboardPage({ navigate }: { navigate: (route: string) => void }) {
  const { data, error, loading } = useAsync(() => api.dashboard(), []);

  if (loading) return <Loading label="Loading dashboard…" />;
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return null;

  const { metrics, tests, top_blind_spots: patterns, recent_incidents: incidents } = data;
  const analysed = metrics.covered + metrics.partial + metrics.not_covered;

  return (
    <div className="stack">
      <div className="page-head">
        <div className="page-head__row">
          <div>
            <h1>Dashboard</h1>
            <div className="page-head__sub">Production → test intelligence across your suite.</div>
          </div>
          <button className="btn btn--primary" onClick={() => navigate("incidents")}>
            Analyse an incident
          </button>
        </div>
      </div>

      {metrics.tests_indexed === 0 ? (
        <Banner tone="warn">
          No tests are indexed yet.{" "}
          <a href="#/tests" onClick={() => navigate("tests")}>
            Add a test source
          </a>{" "}
          to start finding gaps.
        </Banner>
      ) : null}

      <div className="grid grid--metrics">
        <Metric label="Tests indexed" value={tests.total.toLocaleString()} hint={`${tests.sources} source(s)`} />
        <Metric label="Incidents" value={metrics.incidents} hint={`${analysed} analysed`} />
        <Metric label="Test gaps" value={metrics.test_gaps} />
        <Metric label="Blind spots" value={metrics.blind_spots} hint="recurring patterns" />
      </div>

      <div className="grid grid--metrics">
        <Metric label="Covered" value={metrics.covered} tone="covered" />
        <Metric label="Partially covered" value={metrics.partial} tone="partial" />
        <Metric label="Not covered" value={metrics.not_covered} tone="missing" />
        <Metric
          label="Last indexed"
          value={<span style={{ fontSize: 14, fontWeight: 500 }}>{formatDate(tests.last_indexed)}</span>}
        />
      </div>

      <div className="grid grid--2">
        <Card
          title="Top recurring blind spots"
          note="Gap categories that keep reappearing across incidents."
          flush
        >
          {patterns.length ? (
            <div>
              {patterns.map((pattern: BlindSpotPattern) => (
                <div
                  key={pattern.key}
                  className={`pattern pattern--${pattern.risk}`}
                  onClick={() => navigate(`blind-spots/${pattern.key}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") navigate(`blind-spots/${pattern.key}`);
                  }}
                >
                  <div className="pattern__bar" />
                  <div className="pattern__body">
                    <div className="pattern__label">{pattern.label}</div>
                    <div className="pattern__summary">{pattern.features.slice(0, 3).join(", ")}</div>
                  </div>
                  <div className="pattern__count">
                    <strong>{pattern.incident_count}</strong>
                    <span>incidents</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No recurring patterns yet">
              A pattern appears once several incidents share the same kind of gap.
            </EmptyState>
          )}
        </Card>

        <Card title="Recent incidents" flush>
          {incidents.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Feature</th>
                  <th>Coverage</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((incident) => (
                  <tr
                    key={String(incident.id)}
                    className="is-clickable"
                    onClick={() => navigate(`incidents/${incident.id}`)}
                  >
                    <td>
                      <div style={{ fontWeight: 550 }}>{String(incident.title || incident.id)}</div>
                      <div className="mono" style={{ color: "var(--text-3)" }}>
                        {String(incident.id)} · {titleize(String(incident.severity ?? ""))}
                      </div>
                    </td>
                    <td className="nowrap">{String(incident.feature)}</td>
                    <td className="nowrap">
                      <CoverageBadge coverage={incident.coverage as Coverage | null} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No incidents analysed yet">
              Paste a production incident on the Incidents screen to get started.
            </EmptyState>
          )}
        </Card>
      </div>

      <Card title="Indexed coverage by feature" flush>
        {Object.keys(tests.by_feature).length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Feature</th>
                <th>Tests</th>
                <th>Share</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tests.by_feature)
                .sort((a, b) => b[1] - a[1])
                .map(([feature, count]) => (
                  <tr key={feature}>
                    <td>{feature}</td>
                    <td className="num">{count.toLocaleString()}</td>
                    <td style={{ width: "45%" }}>
                      <div className="meter" style={{ width: "100%", marginTop: 0 }}>
                        <div
                          className="meter__fill"
                          style={{ width: `${tests.total ? (count / tests.total) * 100 : 0}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="Nothing indexed yet" />
        )}
      </Card>
    </div>
  );
}
