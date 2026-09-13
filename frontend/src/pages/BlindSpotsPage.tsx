/** Screen 6, Recurring blind spots (spec §30). */
import React from "react";
import { api } from "../services/api";
import { useAction, useAsync } from "../hooks/useAsync";
import type { BlindSpotPattern, Coverage } from "../types";
import {
  Banner,
  Card,
  CoverageBadge,
  EmptyState,
  Loading,
  RiskBadge,
  Tag,
  titleize,
} from "../components/ui";

export function BlindSpotsPage({
  patternKey,
  navigate,
}: {
  patternKey?: string;
  navigate: (route: string) => void;
}) {
  if (patternKey) return <BlindSpotDetail patternKey={patternKey} navigate={navigate} />;
  return <BlindSpotList navigate={navigate} />;
}

function BlindSpotList({ navigate }: { navigate: (route: string) => void }) {
  const { data, error, loading, reload } = useAsync(() => api.listBlindSpots(), []);
  const recompute = useAction(api.recomputeBlindSpots);

  return (
    <div className="stack">
      <div className="page-head">
        <div className="page-head__row">
          <div>
            <h1>Blind spots</h1>
            <div className="page-head__sub">
              Gap types that have affected more than one production incident.
            </div>
          </div>
          <button
            className="btn"
            disabled={recompute.pending}
            onClick={async () => {
              const result = await recompute.run();
              if (result) reload();
            }}
          >
            Recompute
          </button>
        </div>
      </div>

      {recompute.error ? <Banner tone="error">{recompute.error}</Banner> : null}

      <Card title="Recurring patterns" flush>
        {loading ? (
          <Loading />
        ) : error ? (
          <div className="card__body">
            <Banner tone="error">{error}</Banner>
          </div>
        ) : data?.items.length ? (
          <div>
            {data.items.map((pattern: BlindSpotPattern) => (
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
                  <div className="row row--tight">
                    <span className="pattern__label">{pattern.label}</span>
                    <RiskBadge risk={pattern.risk} />
                  </div>
                  <div className="pattern__summary">{pattern.summary}</div>
                  <div className="row row--tight" style={{ marginTop: 6 }}>
                    {pattern.features.map((feature) => (
                      <Tag key={feature}>{feature}</Tag>
                    ))}
                  </div>
                </div>
                <div className="pattern__count">
                  <strong>{pattern.incident_count}</strong>
                  <span>incidents</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No recurring blind spots yet">
            A pattern is reported once several incidents share the same gap family.
          </EmptyState>
        )}
      </Card>
    </div>
  );
}

function BlindSpotDetail({
  patternKey,
  navigate,
}: {
  patternKey: string;
  navigate: (route: string) => void;
}) {
  const { data, error, loading } = useAsync(() => api.getBlindSpot(patternKey), [patternKey]);

  if (loading) return <Loading />;
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return null;

  const { pattern, incidents } = data;

  return (
    <div className="stack">
      <div className="row">
        <button className="btn btn--ghost btn--sm" onClick={() => navigate("blind-spots")}>
          Back to blind spots
        </button>
      </div>

      <div className={`verdict verdict--${pattern.risk === "HIGH" ? "missing" : pattern.risk === "MEDIUM" ? "partial" : "covered"}`}>
        <div className="verdict__main">
          <div className="row row--tight">
            <RiskBadge risk={pattern.risk} />
          </div>
          <h1 style={{ marginTop: 10 }}>{pattern.label}</h1>
          <p className="verdict__why">{pattern.summary}</p>
          <div className="row row--tight" style={{ marginTop: 8 }}>
            {pattern.gap_types.map((gapType) => (
              <Tag key={gapType}>{titleize(gapType)}</Tag>
            ))}
          </div>
        </div>
        <div className="verdict__side">
          <div>
            <div className="stat__label">Incidents</div>
            <div className="stat__value">{pattern.incident_count}</div>
          </div>
          <div>
            <div className="stat__label">Gaps</div>
            <div className="stat__value">{pattern.gap_count}</div>
          </div>
          <div>
            <div className="stat__label">Features</div>
            <div className="stat__value" style={{ fontSize: 14 }}>
              {pattern.features.join(", ") || ", "}
            </div>
          </div>
        </div>
      </div>

      <Card title={`Contributing incidents (${incidents.length})`} flush>
        {incidents.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Incident</th>
                <th>Feature</th>
                <th>Coverage</th>
                <th>Gap</th>
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
                    <div style={{ fontWeight: 550 }}>{String(incident.title)}</div>
                    <div className="mono" style={{ color: "var(--text-3)" }}>
                      {String(incident.id)}
                    </div>
                  </td>
                  <td className="nowrap">{String(incident.feature)}</td>
                  <td className="nowrap">
                    <CoverageBadge coverage={incident.coverage as Coverage} />
                  </td>
                  <td>{String(incident.gap_summary ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="No incidents linked" />
        )}
      </Card>
    </div>
  );
}
