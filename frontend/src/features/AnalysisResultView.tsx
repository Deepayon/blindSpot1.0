/** The analysis result: verdict, why, evidence, gap, related tests,
 *  recommendations, and the raw signals behind the decision.
 *
 *  Spec §5 and §64: the verdict is never shown on its own. Evidence and the
 *  "Analysis Details" panel are part of the result, not an afterthought, so a
 *  reviewer can always check the reasoning rather than trust it.
 */
import React, { useState } from "react";
import { api } from "../services/api";
import type { AnalysisResult, Evidence, Recommendation, RecommendationStatus } from "../types";
import {
  Banner,
  Card,
  ConditionList,
  CoverageBadge,
  Meter,
  RiskBadge,
  Stat,
  Tag,
  coverageTone,
  formatDate,
  titleize,
} from "../components/ui";

const EFFECTIVENESS_NOTE: Record<string, string> = {
  NO_COVERAGE: "No relevant test exists for this scenario.",
  INSUFFICIENT_COVERAGE: "A related test exists but does not represent the production condition.",
  POTENTIALLY_INEFFECTIVE:
    "A test covers this scenario, yet production still failed, its assertions or data may not protect the behaviour.",
  ADEQUATE: "Existing coverage appears adequate.",
};

function evidenceClass(kind: string): string {
  if (kind === "condition_matched") return "is-match";
  if (
    kind === "condition_absent" ||
    kind === "condition_difference" ||
    kind === "signal_absent" ||
    kind === "combination" ||
    kind === "effectiveness"
  ) {
    return "is-gap";
  }
  return "";
}

export function AnalysisResultView({
  result,
  onChanged,
}: {
  result: AnalysisResult;
  onChanged?: () => void;
}) {
  const { incident, gaps, retrieval, comparison } = result;
  const gap = gaps[0];
  const usedLlm = result.reasoning_source.includes("llm");

  return (
    <div className="stack">
      {result.coverage === "INSUFFICIENT_EVIDENCE" ? (
        <Banner tone="warn">
          BlindSpot has no indexed tests to compare against. Add a test source before drawing a
          conclusion, reporting "not covered" against an empty index would not be evidence.
        </Banner>
      ) : null}

      {/* ---------------- verdict ---------------- */}
      <div className={`verdict verdict--${coverageTone(result.coverage)}`}>
        <div className="verdict__main">
          <div className="row row--tight">
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {incident.id}
            </span>
            <CoverageBadge coverage={result.coverage} large />
            <RiskBadge risk={result.risk} />
            {usedLlm ? <span className="badge badge--accent">AI-assisted</span> : null}
          </div>
          <h1 style={{ marginTop: 10 }}>{incident.title || incident.description.slice(0, 110)}</h1>
          <p className="verdict__why">{result.explanation}</p>
          <p className="card__note" style={{ marginTop: 8 }}>
            {EFFECTIVENESS_NOTE[result.effectiveness] ?? titleize(result.effectiveness)}
          </p>
        </div>
        <div className="verdict__side">
          <Stat label="Confidence" value={`${Math.round(result.confidence * 100)}%`}>
            <Meter value={result.confidence} />
            <div className="card__note" style={{ marginTop: 4 }}>
              {titleize(result.confidence_level)}
            </div>
          </Stat>
          <Stat label="Related tests" value={result.relevant_tests.length} />
          <Stat label="Searched" value={retrieval.candidate_count} />
        </div>
      </div>

      {/* ---------------- scenario + gap ---------------- */}
      <div className="grid grid--2">
        <Card title="Production scenario">
          <dl className="kv">
            <dt>Feature</dt>
            <dd>{incident.feature}</dd>
            <dt>Conditions</dt>
            <dd>
              <ConditionList conditions={incident.conditions} />
            </dd>
            <dt>Behaviour</dt>
            <dd>
              {incident.signals.length ? (
                <span className="row row--tight">
                  {incident.signals.map((signal) => (
                    <Tag key={signal}>{signal.replace(/_/g, " ")}</Tag>
                  ))}
                </span>
              ) : (
                <span style={{ color: "var(--text-3)" }}>none detected</span>
              )}
            </dd>
            {incident.failure ? (
              <>
                <dt>Failure</dt>
                <dd>{incident.failure}</dd>
              </>
            ) : null}
            {incident.root_cause ? (
              <>
                <dt>Root cause</dt>
                <dd>{incident.root_cause}</dd>
              </>
            ) : null}
            <dt>Severity</dt>
            <dd>{titleize(incident.severity)}</dd>
          </dl>
        </Card>

        {gap ? (
          <Card title="Identified gap">
            <div className="gap-box" style={{ border: 0, padding: 0 }}>
              <div className="gap-box__type">{titleize(gap.gap_type)}</div>
              <div className="gap-box__summary">{gap.summary}</div>
              {gap.detail ? <div className="gap-box__detail">{gap.detail}</div> : null}
              {Object.keys(gap.missing_conditions || {}).length ? (
                <>
                  <div className="stat__label" style={{ marginTop: 12 }}>
                    Not represented by any test
                  </div>
                  <div className="row row--tight" style={{ marginTop: 5 }}>
                    {Object.entries(gap.missing_conditions).map(([key, value]) => (
                      <Tag key={key}>
                        {key.startsWith("signal:") ? value : `${key} = ${value}`}
                      </Tag>
                    ))}
                  </div>
                </>
              ) : null}
              <div className="card__note" style={{ marginTop: 12 }}>
                Blind spot family: {gap.family_label}
              </div>
            </div>
          </Card>
        ) : (
          <Card title="Identified gap">
            <div style={{ color: "var(--text-3)" }}>No gap was recorded for this analysis.</div>
          </Card>
        )}
      </div>

      {/* ---------------- evidence ---------------- */}
      <Card title="Evidence" note="The facts this result is based on.">
        {result.evidence.length ? (
          <ul className="evidence">
            {result.evidence.map((item: Evidence, index: number) => (
              <li key={index} className={evidenceClass(item.kind)}>
                {item.statement}
              </li>
            ))}
          </ul>
        ) : (
          <div style={{ color: "var(--text-3)" }}>No evidence was gathered.</div>
        )}
      </Card>

      {/* ---------------- related tests ---------------- */}
      <Card
        title={`Related tests (${result.relevant_tests.length})`}
        note="The indexed tests closest to this scenario."
        flush
      >
        {result.relevant_tests.length ? (
          <div className="table__scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Test</th>
                  <th>Feature</th>
                  <th>Inputs</th>
                  <th>Source</th>
                  <th className="nowrap">Score</th>
                </tr>
              </thead>
              <tbody>
                {result.relevant_tests.map((item) => (
                  <tr key={item.test.id}>
                    <td>
                      <div style={{ fontWeight: 550 }}>{item.test.scenario || item.test.name}</div>
                      <div className="mono" style={{ color: "var(--text-3)", marginTop: 2 }}>
                        {item.test.id}
                      </div>
                    </td>
                    <td className="nowrap">
                      {item.test.feature}
                      {item.feature_match ? (
                        <span className="badge badge--covered" style={{ marginLeft: 6 }}>
                          match
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <ConditionList conditions={item.test.inputs} />
                    </td>
                    <td className="mono" style={{ color: "var(--text-3)", maxWidth: 220, overflowWrap: "anywhere" }}>
                      {item.test.source}
                      {item.test.line_number ? `:${item.test.line_number}` : ""}
                    </td>
                    <td className="num nowrap">{item.score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card__body" style={{ color: "var(--text-3)" }}>
            No indexed test was relevant to this scenario.
          </div>
        )}
      </Card>

      {/* ---------------- recommendations ---------------- */}
      {result.recommendations.length ? (
        <Card
          title="Recommended coverage"
          note="Tests to add for the gap identified above."
        >
          <RecommendationList recommendations={result.recommendations} onChanged={onChanged} />
        </Card>
      ) : null}

      {/* ---------------- details ---------------- */}
      <Card title="Analysis details">
        <details className="details" style={{ border: 0, paddingTop: 0 }}>
          <summary>Why this verdict, retrieval and comparison signals</summary>
          <div className="grid grid--2" style={{ marginTop: 12 }}>
            <dl className="kv">
              <dt>Strategy</dt>
              <dd>{retrieval.strategy}</dd>
              <dt>Vector store</dt>
              <dd>{retrieval.vector_store}</dd>
              <dt>Embeddings</dt>
              <dd>{retrieval.embedding_model}</dd>
              <dt>Candidates</dt>
              <dd>{retrieval.candidate_count}</dd>
              <dt>Considered</dt>
              <dd>{retrieval.considered_count}</dd>
              <dt>Top score</dt>
              <dd>{retrieval.top_score.toFixed(4)}</dd>
            </dl>
            <dl className="kv">
              <dt>Feature match</dt>
              <dd>{String(comparison.feature_match)}</dd>
              <dt>Condition match</dt>
              <dd>{String(comparison.condition_match)}</dd>
              <dt>Signal match</dt>
              <dd>{String(comparison.signal_match)}</dd>
              <dt>Matched</dt>
              <dd>{comparison.matched_conditions.join(", ") || ", "}</dd>
              <dt>Unmatched</dt>
              <dd>{comparison.unmatched_conditions.join(", ") || ", "}</dd>
              <dt>Missing behaviour</dt>
              <dd>{comparison.unmatched_signals.join(", ") || ", "}</dd>
              <dt>Closest test</dt>
              <dd>{comparison.best_test_id || ", "}</dd>
            </dl>
          </div>
          <div className="card__note" style={{ marginTop: 12 }}>
            Reasoning source: {result.reasoning_source} - analysed {formatDate(result.analyzed_at)}
          </div>
        </details>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- recommendations

const ACTIONS: Array<{ status: RecommendationStatus; label: string }> = [
  { status: "ACCEPTED", label: "Accept" },
  { status: "IGNORED", label: "Ignore" },
  { status: "ALREADY_COVERED", label: "Already covered" },
];

function RecommendationList({
  recommendations,
  onChanged,
}: {
  recommendations: Recommendation[];
  onChanged?: () => void;
}) {
  const [statuses, setStatuses] = useState<Record<number, RecommendationStatus>>({});
  const [error, setError] = useState<string | null>(null);

  async function setStatus(recommendation: Recommendation, status: RecommendationStatus) {
    if (recommendation.id == null) {
      // Unpersisted analysis (persist=false): keep it as local state only.
      setStatuses((prev) => ({ ...prev, [-recommendations.indexOf(recommendation) - 1]: status }));
      return;
    }
    try {
      await api.setRecommendationStatus(recommendation.id, status);
      setStatuses((prev) => ({ ...prev, [recommendation.id as number]: status }));
      setError(null);
      onChanged?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      {error ? <Banner tone="error">{error}</Banner> : null}
      {recommendations.map((recommendation, index) => {
        const key = recommendation.id ?? -index - 1;
        const status = statuses[key] ?? recommendation.status;
        const done = status !== "PROPOSED";
        return (
          <div className={`rec${done ? " rec--done" : ""}`} key={key}>
            <div className="rec__body">
              <div className="rec__title">{recommendation.title}</div>
              {recommendation.rationale ? <div className="rec__why">{recommendation.rationale}</div> : null}
              {Object.keys(recommendation.suggested_inputs || {}).length ? (
                <div className="rec__inputs">
                  {Object.entries(recommendation.suggested_inputs).map(([k, v]) => (
                    <Tag key={k}>
                      {k} = {String(v)}
                    </Tag>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="row row--tight">
              {done ? (
                <span className="badge badge--neutral">{titleize(status)}</span>
              ) : (
                ACTIONS.map((action) => (
                  <button
                    key={action.status}
                    className="btn btn--sm"
                    onClick={() => setStatus(recommendation, action.status)}
                  >
                    {action.label}
                  </button>
                ))
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}
