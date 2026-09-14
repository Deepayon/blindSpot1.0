/** The analysis result, laid out as the reasoning that produced it.
 *
 *  The order is the argument: what production did (incident), what the suite
 *  offered (relevant tests), the judgement (verdict), the facts behind it
 *  (evidence), what is missing (gap), what to do (recommendations), and whether
 *  this has happened before (recurring pattern). A reader can stop at any point
 *  and the next section answers "how do you know?".
 *
 *  Spec §5 and §64: the verdict is never shown on its own. Evidence and the
 *  "Analysis Details" panel are part of the result, not an afterthought, so a
 *  reviewer can always check the reasoning rather than trust it.
 */
import React, { useEffect, useState } from "react";
import { api } from "../services/api";
import type {
  AnalysisResult,
  BlindSpotPattern,
  Evidence,
  Recommendation,
  RecommendationStatus,
} from "../types";
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
  // Neither a match nor a gap: the dimension is touched but no value is
  // recorded, so it cannot be compared either way.
  if (kind === "condition_mentioned" || kind === "insufficient_evidence") return "is-unknown";
  return "";
}

/** A short label for where an evidence item's cited test lives. */
function evidenceLocation(item: Evidence): string | null {
  if (!item.test_source) return null;
  return item.test_line ? `${item.test_source}:${item.test_line}` : item.test_source;
}

function orNone(values: string[]): string {
  return values.length ? values.join(", ") : "None";
}

/** The recurring blind spot this incident's gap belongs to, if there is one.
 *
 *  Returns nothing when the gap has not recurred often enough to be a pattern.
 *  A single incident is a bug, not a blind spot, and the threshold that decides
 *  that lives on the server, so this only ever reads what the server published.
 */
function useRecurringPattern(
  familyKey: string | undefined,
  incidentId: string,
): BlindSpotPattern | null {
  const [pattern, setPattern] = useState<BlindSpotPattern | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!familyKey) {
      setPattern(null);
      return;
    }
    api
      .listBlindSpots()
      .then((response) => {
        if (cancelled) return;
        const match = response.items.find(
          (item) => item.key === familyKey && item.example_incident_ids.includes(incidentId),
        );
        setPattern(match ?? null);
      })
      // A pattern is supporting context. If it cannot be loaded the verdict and
      // its evidence are still complete, so this fails quietly rather than
      // taking down the result the reader came for.
      .catch(() => {
        if (!cancelled) setPattern(null);
      });
    return () => {
      cancelled = true;
    };
  }, [familyKey, incidentId]);

  return pattern;
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
  const pattern = useRecurringPattern(gap?.family_key, incident.id);
  const nothingIndexed = result.relevant_tests.length === 0;

  return (
    <div className="stack">
      {result.coverage === "INSUFFICIENT_EVIDENCE" ? (
        <Banner tone="warn">
          {nothingIndexed
            ? "No tests are indexed yet, so coverage cannot be judged. Add a test source first."
            : "Coverage cannot be determined from this report. See what it needs, below."}
        </Banner>
      ) : null}

      {/* ---------------- 1. incident ---------------- */}
      <Card
        title="Production incident"
        note="What happened, reduced to the scenario that was exercised."
      >
        <div className="row row--tight" style={{ marginBottom: 10 }}>
          <span className="mono" style={{ color: "var(--text-3)" }}>
            {incident.id}
          </span>
          <Tag>{incident.feature}</Tag>
          <Tag>{titleize(incident.severity)}</Tag>
        </div>
        <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>
          {incident.title || incident.description.slice(0, 110)}
        </h2>
        <dl className="kv" style={{ marginTop: 12 }}>
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
              <span style={{ color: "var(--text-3)" }}>None detected</span>
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
        </dl>
      </Card>

      {/* ---------------- 2. relevant tests ---------------- */}
      <Card
        title={`Relevant tests (${result.relevant_tests.length})`}
        note={`The indexed tests closest to this scenario, out of ${retrieval.candidate_count} searched.`}
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
                    <td
                      className="mono"
                      style={{ color: "var(--text-3)", maxWidth: 220, overflowWrap: "anywhere" }}
                    >
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

      {/* ---------------- 3. verdict ---------------- */}
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
            {result.coverage === "INSUFFICIENT_EVIDENCE"
              ? // The effectiveness note would read "no relevant test exists",
                // which contradicts the related tests listed above. Nothing is
                // being claimed about coverage here, so say that instead.
                "No conclusion is drawn either way."
              : (EFFECTIVENESS_NOTE[result.effectiveness] ?? titleize(result.effectiveness))}
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

      {/* ---------------- 4. evidence ---------------- */}
      <Card
        title="Evidence"
        note="Each fact below was extracted from the report or read from an indexed test. None is written by a model."
      >
        {result.evidence.length ? (
          <ul className="evidence">
            {result.evidence.map((item: Evidence, index: number) => {
              const location = evidenceLocation(item);
              return (
                <li key={index} className={evidenceClass(item.kind)}>
                  <div>{item.statement}</div>
                  {location ? (
                    <div className="mono card__note" style={{ marginTop: 3 }}>
                      {item.test_id ? `${item.test_id} - ` : ""}
                      {location}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div style={{ color: "var(--text-3)" }}>No evidence was gathered.</div>
        )}
      </Card>

      {/* ---------------- 5. gap ---------------- */}
      <div className="grid grid--2">
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
            <div style={{ color: "var(--text-3)" }}>
              {result.coverage === "INSUFFICIENT_EVIDENCE"
                ? "No gap can be named until the report says what failed. Recording one from an unclear report would put a guess into the recurring blind spots."
                : "No gap was recorded for this analysis."}
            </div>
          </Card>
        )}
      </div>


      {/* ---------------- 6. recommendations ---------------- */}
      {result.recommendations.length ? (
        <Card
          title={
            result.coverage === "INSUFFICIENT_EVIDENCE"
              ? "What this report needs"
              : "Recommended coverage"
          }
          note={
            result.coverage === "INSUFFICIENT_EVIDENCE"
              ? "Coverage cannot be judged until the report states what triggered the failure."
              : "Tests to add for the gap identified above."
          }
        >
          <RecommendationList recommendations={result.recommendations} onChanged={onChanged} />
        </Card>
      ) : null}

      {/* ---------------- 7. recurring pattern ---------------- */}
      {pattern ? (
        <Card
          title="Recurring blind spot"
          note="This gap is not isolated. The same kind of gap has appeared in other incidents."
        >
          <div className="row row--tight" style={{ marginBottom: 10 }}>
            <span className="badge badge--accent">{pattern.label}</span>
            <RiskBadge risk={pattern.risk} />
            <Tag>{pattern.incident_count} incidents</Tag>
          </div>
          <p style={{ marginTop: 0 }}>{pattern.summary}</p>
          {pattern.concentrated_in && pattern.concentration >= 0.6 ? (
            <p className="card__note">
              Concentrated in {pattern.concentrated_in}, which accounts for{" "}
              {Math.round(pattern.concentration * 100)}% of the gaps in this family. Treating it as
              an organisation-wide weakness would point the work at the wrong place.
            </p>
          ) : null}
          <div className="stat__label" style={{ marginTop: 12 }}>
            Supporting incidents
          </div>
          <div className="row row--tight" style={{ marginTop: 5 }}>
            {pattern.example_incident_ids.map((id) => (
              <Tag key={id}>
                {id}
                {id === incident.id ? " (this one)" : ""}
              </Tag>
            ))}
          </div>
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
              <dd>{orNone(comparison.matched_conditions)}</dd>
              <dt>Unmatched</dt>
              <dd>{orNone(comparison.unmatched_conditions)}</dd>
              <dt>Missing behaviour</dt>
              <dd>{orNone(comparison.unmatched_signals)}</dd>
              <dt>Closest test</dt>
              <dd>{comparison.best_test_id || "None"}</dd>
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
