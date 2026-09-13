/** Settings. Read-only: configuration is environment-driven.
 *
 *  Its job is to make the privacy posture legible at a glance, so the first
 *  thing on the page answers the only question that matters to a user handing
 *  over their test suite: does any of this leave my machine?
 */
import React from "react";
import { api } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { Banner, Card, Loading } from "../components/ui";

/** One labelled row. Labels are written for people, not derived from keys. */
function Rows({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return (
    <dl className="kv">
      {rows.map(([label, value]) => (
        <React.Fragment key={label}>
          <dt>{label}</dt>
          <dd>{value === null || value === undefined || value === "" ? "Not set" : value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function megabytes(bytes: unknown): string {
  const value = Number(bytes);
  if (!Number.isFinite(value)) return "Not set";
  return `${(value / (1024 * 1024)).toFixed(0)} MB`;
}

function count(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : "Not set";
}

function yesNo(value: unknown): string {
  return value ? "Yes" : "No";
}

function percent(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "Not set";
}

export function SettingsPage() {
  const { data, error, loading } = useAsync(() => api.settings(), []);

  if (loading) return <Loading />;
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return null;

  const { ai, index, retrieval, limits, security } = data;
  const aiActive = Boolean(ai.llm_active);
  const aiConfiguredButInactive = Boolean(ai.external_ai_enabled) && !aiActive;
  const hosted = security.mode === "hosted";

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Settings</h1>
        <div className="page-head__sub">
          Configuration is set through environment variables and applied at startup.
        </div>
      </div>

      <Banner tone={aiActive ? "warn" : "ok"}>
        {aiActive ? (
          <>
            <strong>AI assistance is on.</strong> Incident text and test metadata are sent to{" "}
            {String(ai.llm_provider)}. Your source code is never sent. Coverage results stay
            fully deterministic.
          </>
        ) : aiConfiguredButInactive ? (
          <>
            <strong>Running locally.</strong> {String(ai.llm_provider)} is configured but has no
            API key, so no data leaves this machine.
          </>
        ) : (
          <>
            <strong>Running locally.</strong> No data leaves this machine. Search and analysis use
            a local model and deterministic rules.
          </>
        )}
      </Banner>

      <div className="grid grid--2">
        <Card
          title="AI assistance"
          note="Optional. Improves wording only; it cannot change a coverage result."
        >
          <Rows
            rows={[
              ["Status", aiActive ? "Active" : "Off"],
              ["Provider", aiActive ? String(ai.llm_provider) : "None"],
              ["Model", aiActive ? String(ai.llm_model ?? "Not set") : "None"],
              ["Sends data externally", yesNo(aiActive)],
            ]}
          />
        </Card>

        <Card title="Privacy and security">
          <Rows
            rows={[
              ["Deployment", hosted ? "Hosted" : "Local machine"],
              ["Repository indexing", security.repository_indexing ? "Available" : "Turned off"],
              ["Source code stored", yesNo(security.source_code_retained)],
              ["Delete actions", String(security.destructive_operations)],
              ["Rate limiting", security.rate_limited ? "On" : "Off"],
            ]}
          />
        </Card>

        <Card title="Test index">
          <Rows
            rows={[
              ["Tests indexed", count(index.tests)],
              ["Search model", String(index.embedding_model ?? "Not set")],
              ["Vector store", String(index.vector_store ?? "Not set")],
              ["Dimensions", count(index.dimensions)],
            ]}
          />
        </Card>

        <Card title="Search behaviour">
          <Rows
            rows={[
              ["Tests compared per incident", count(retrieval.top_k)],
              ["Minimum relevance", percent(retrieval.min_score)],
              ["Meaning match weight", percent(retrieval.vector_weight)],
              ["Keyword match weight", percent(retrieval.lexical_weight)],
            ]}
          />
        </Card>

        <Card title="Indexing limits">
          <Rows
            rows={[
              ["Largest file read", megabytes(limits.max_file_size_bytes)],
              ["Files per scan", count(limits.max_scanned_files)],
              ["Scan time limit", `${count(limits.scan_time_budget_seconds)} seconds`],
              [
                "Allowed directories",
                security.allowed_repository_roots
                  ? `${count(security.allowed_repository_roots)} configured`
                  : "Any project directory",
              ],
            ]}
          />
        </Card>

        <Card title="Request limits">
          <Rows
            rows={[
              ["Requests per minute", count(limits.rate_limit_per_minute)],
              ["Analyses per minute", count(limits.rate_limit_analyze_per_minute)],
              [
                "Blind spot threshold",
                `${count(limits.blind_spot_min_incidents)} incidents`,
              ],
            ]}
          />
        </Card>
      </div>

      <Card title="About">
        <Rows
          rows={[
            ["Version", data.version],
            ["Database", data.database],
            [
              "API reference",
              <a href="/docs" target="_blank" rel="noreferrer">
                Open
              </a>,
            ],
          ]}
        />
      </Card>
    </div>
  );
}
