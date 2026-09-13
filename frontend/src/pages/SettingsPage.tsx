/** Settings — read-only in the POC (spec §31: "Settings can be minimal").
 *
 *  Its job is to make the privacy posture legible: whether any data leaves the
 *  machine, and exactly which providers are in use.
 */
import React from "react";
import { api } from "../services/api";
import { useAsync } from "../hooks/useAsync";
import { Banner, Card, Loading } from "../components/ui";

export function SettingsPage() {
  const { data, error, loading } = useAsync(() => api.settings(), []);

  if (loading) return <Loading />;
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return null;

  // `llm_active` is what actually matters: a provider named without an API key
  // falls back to local analysis, so nothing leaves the machine.
  const external = Boolean(data.ai.llm_active);
  const configuredButInactive = Boolean(data.ai.external_ai_enabled) && !external;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Settings</h1>
        <div className="page-head__sub">
          Configuration is environment-driven — edit <code>.env</code> and restart.
        </div>
      </div>

      <Banner tone={external ? "warn" : "ok"}>
        {external ? (
          <>
            <strong>External AI is enabled.</strong> Normalised test metadata and incident text are
            sent to <code>{String(data.ai.llm_provider)}</code> ({String(data.ai.llm_model)}). Source
            code and raw test bodies are never transmitted.
          </>
        ) : configuredButInactive ? (
          <>
            <strong>Fully local.</strong> <code>{String(data.ai.llm_provider)}</code> is configured
            but has no API key, so BlindSpot is running deterministically and no data leaves this
            machine.
          </>
        ) : (
          <>
            <strong>Fully local.</strong> No data leaves this machine. Retrieval and analysis run on
            local embeddings and deterministic rules.
          </>
        )}
      </Banner>

      <div className="grid grid--2">
        <Card title="AI providers">
          <KeyValues values={data.ai} />
        </Card>
        <Card title="Index">
          <KeyValues values={data.index} />
        </Card>
        <Card title="Retrieval">
          <KeyValues values={data.retrieval} />
        </Card>
        <Card title="Limits">
          <KeyValues values={data.limits} />
        </Card>
      </div>

      <Card title="About">
        <dl className="kv">
          <dt>Version</dt>
          <dd>{data.version}</dd>
          <dt>Database</dt>
          <dd>{data.database}</dd>
          <dt>API docs</dt>
          <dd>
            <a href="/docs" target="_blank" rel="noreferrer">
              /docs
            </a>
          </dd>
        </dl>
      </Card>
    </div>
  );
}

function KeyValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values || {});
  if (!entries.length) return <span style={{ color: "var(--text-3)" }}>—</span>;
  return (
    <dl className="kv">
      {entries.map(([key, value]) => (
        <React.Fragment key={key}>
          <dt>{key.replace(/_/g, " ")}</dt>
          <dd>{renderValue(value)}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}
