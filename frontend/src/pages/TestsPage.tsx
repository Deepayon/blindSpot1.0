/** Screen 2, Test source & catalogue (spec §26).
 *
 *  Principle 1: tests are indexed once. This screen manages sources; incidents
 *  never re-upload anything.
 */
import React, { useRef, useState } from "react";
import { api } from "../services/api";
import { useAction, useAsync } from "../hooks/useAsync";
import type { IngestionResult, TestSourceInfo } from "../types";
import {
  Banner,
  Card,
  ConditionList,
  EmptyState,
  Loading,
  Spinner,
  formatDate,
} from "../components/ui";

type Mode = "repository" | "file";

export function TestsPage() {
  const [mode, setMode] = useState<Mode>("repository");
  const [repoPath, setRepoPath] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const sources = useAsync(() => api.listSources(), []);
  const [search, setSearch] = useState("");
  const [feature, setFeature] = useState("");
  const [queryKey, setQueryKey] = useState(0);
  const tests = useAsync(
    () => api.listTests({ q: search, feature, limit: 100 }),
    [queryKey],
  );

  const indexRepo = useAction(api.indexRepository);
  const indexFile = useAction(api.indexFile);
  const reindex = useAction(api.reindex);

  const busy = indexRepo.pending || indexFile.pending || reindex.pending;
  const lastResult: IngestionResult | null = indexRepo.result ?? indexFile.result;
  const lastError = indexRepo.error ?? indexFile.error ?? reindex.error;

  function refresh() {
    sources.reload();
    setQueryKey((n) => n + 1);
  }

  async function submitRepo(event: React.FormEvent) {
    event.preventDefault();
    if (!repoPath.trim()) return;
    indexFile.reset();
    const result = await indexRepo.run(repoPath.trim());
    if (result) refresh();
  }

  async function submitFile(file: File | undefined) {
    if (!file) return;
    indexRepo.reset();
    const result = await indexFile.run(file);
    if (result) refresh();
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div className="page-head__row">
          <div>
            <h1>Tests</h1>
            <div className="page-head__sub">
              Add your tests once. Every incident is checked against them.
            </div>
          </div>
          <button
            className="btn"
            disabled={busy}
            onClick={async () => {
              const result = await reindex.run();
              if (result) refresh();
            }}
          >
            {reindex.pending ? <Spinner /> : null} Rebuild index
          </button>
        </div>
      </div>

      <Card title="Add a test source">
        <div className="seg" style={{ marginBottom: 14 }}>
          <button
            className={`seg__btn${mode === "repository" ? " seg__btn--active" : ""}`}
            onClick={() => setMode("repository")}
          >
            Local project
          </button>
          <button
            className={`seg__btn${mode === "file" ? " seg__btn--active" : ""}`}
            onClick={() => setMode("file")}
          >
            CSV / Excel
          </button>
        </div>

        {mode === "repository" ? (
          <form onSubmit={submitRepo}>
            <div className="field">
              <label className="label" htmlFor="repo-path">
                Project directory
              </label>
              <input
                id="repo-path"
                className="input"
                placeholder="C:\Projects\my-project  or  /home/me/my-project"
                value={repoPath}
                onChange={(event) => setRepoPath(event.target.value)}
                disabled={busy}
              />
              <div className="card__note" style={{ marginTop: 6 }}>
                Your code stays on this machine. BlindSpot reads test files only. It never
                uploads, runs, or stores your source code.
              </div>
            </div>
            <button className="btn btn--primary" type="submit" disabled={busy || !repoPath.trim()}>
              {indexRepo.pending ? <Spinner /> : null} Index repository
            </button>
          </form>
        ) : (
          <>
            <div
              className={`drop${dragging ? " drop--over" : ""}`}
              onClick={() => fileInput.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                submitFile(event.dataTransfer.files?.[0]);
              }}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter") fileInput.current?.click();
              }}
            >
              {indexFile.pending ? (
                <Spinner label="Parsing and indexing..." />
              ) : (
                <>
                  <div style={{ fontWeight: 550, color: "var(--text-2)" }}>
                    Drop a .csv or .xlsx export here
                  </div>
                  <div style={{ marginTop: 4 }}>
                    or click to browse. Column names are matched flexibly, <code>Test ID</code>,{" "}
                    <code>TestID</code> and <code>Key</code> all work.
                  </div>
                </>
              )}
            </div>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.xlsx,.xlsm"
              style={{ display: "none" }}
              onChange={(event) => submitFile(event.target.files?.[0])}
            />
          </>
        )}

        {lastError ? (
          <div style={{ marginTop: 12 }}>
            <Banner tone="error">{lastError}</Banner>
          </div>
        ) : null}

        {lastResult ? (
          <div style={{ marginTop: 12 }}>
            <IngestionSummary result={lastResult} />
          </div>
        ) : null}
      </Card>

      <Card title="Indexed sources" flush>
        {sources.loading ? (
          <Loading />
        ) : sources.data?.items.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Type</th>
                <th>Tests</th>
                <th>Last indexed</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sources.data.items.map((source: TestSourceInfo) => (
                <tr key={source.id}>
                  <td>
                    <div style={{ fontWeight: 550 }}>{source.label}</div>
                    <div className="mono" style={{ color: "var(--text-3)", overflowWrap: "anywhere" }}>
                      {source.location}
                    </div>
                  </td>
                  <td className="nowrap">{source.kind}</td>
                  <td className="num">{source.test_count.toLocaleString()}</td>
                  <td className="nowrap">{formatDate(source.indexed_at)}</td>
                  <td>
                    <span className="badge badge--covered">{source.status}</span>
                  </td>
                  <td className="nowrap">
                    <button
                      className="btn btn--sm btn--ghost"
                      onClick={async () => {
                        await api.deleteSource(source.id);
                        refresh();
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="No test sources yet">
            Add a local project, a CSV export, or an Excel workbook above.
          </EmptyState>
        )}
      </Card>

      <Card
        title="Test catalogue"
        actions={
          <>
            <input
              className="input"
              style={{ width: 220 }}
              placeholder="Search tests..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") setQueryKey((n) => n + 1);
              }}
            />
            <select
              className="select"
              style={{ width: 150 }}
              value={feature}
              onChange={(event) => {
                setFeature(event.target.value);
                setQueryKey((n) => n + 1);
              }}
            >
              <option value="">All features</option>
              {["Authentication", "Checkout", "Payments", "Orders", "Profile", "Search", "Notifications", "Unknown"].map(
                (name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ),
              )}
            </select>
            <button className="btn btn--sm" onClick={() => setQueryKey((n) => n + 1)}>
              Search
            </button>
          </>
        }
        flush
      >
        {tests.loading ? (
          <Loading />
        ) : tests.data?.items.length ? (
          <>
            <div className="table__scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Test</th>
                    <th>Feature</th>
                    <th>Inputs</th>
                    <th>Expected</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {tests.data.items.map((test) => (
                    <tr key={`${test.source}-${test.id}`}>
                      <td>
                        <div style={{ fontWeight: 550 }}>{test.scenario || test.name}</div>
                        <div className="mono" style={{ color: "var(--text-3)" }}>
                          {test.id}
                        </div>
                      </td>
                      <td className="nowrap">{test.feature}</td>
                      <td>
                        <ConditionList conditions={test.inputs} />
                      </td>
                      <td style={{ maxWidth: 240 }}>{test.expected_behavior}</td>
                      <td className="mono" style={{ color: "var(--text-3)", overflowWrap: "anywhere" }}>
                        {test.source}
                        {test.line_number ? `:${test.line_number}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card__head" style={{ borderBottom: 0, borderTop: "1px solid var(--border)" }}>
              <span className="card__note">
                Showing {tests.data.items.length} of {tests.data.total.toLocaleString()} tests
              </span>
            </div>
          </>
        ) : (
          <EmptyState title="No tests match" />
        )}
      </Card>
    </div>
  );
}

function IngestionSummary({ result }: { result: IngestionResult }) {
  const errors = result.issues.filter((issue) => issue.severity === "ERROR");
  return (
    <div className="stack">
      <Banner tone="ok">
        Indexed <strong>{result.tests_indexed.toLocaleString()}</strong> tests from{" "}
        <strong>{result.source.label}</strong> in {result.duration_ms} ms
        {result.duplicates_skipped ? ` - ${result.duplicates_skipped} duplicate(s) skipped` : ""}
        {result.files_scanned ? ` - ${result.files_scanned} file(s) scanned` : ""}
        {result.files_skipped ? ` - ${result.files_skipped} skipped` : ""}
      </Banner>
      {result.issues.length ? (
        <details className="details">
          <summary>
            {result.issues.length} ingestion note{result.issues.length === 1 ? "" : "s"}
            {errors.length ? ` (${errors.length} error${errors.length === 1 ? "" : "s"})` : ""}
          </summary>
          <div className="issue-list" style={{ marginTop: 8 }}>
            {result.issues.map((issue, index) => (
              <div key={index}>
                <strong>{issue.severity}</strong> {issue.location}, {issue.reason}
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
