# BlindSpot — Architecture

This document explains *why* the system is shaped the way it is. For setup and
usage, see the [README](../README.md).

---

## 1. The problem being solved

Engineering organisations already own the two artefacts needed to answer "was
this failure preventable?" — a test suite and an incident record. They are
almost never compared, because comparing them requires understanding both in the
same terms.

BlindSpot's entire design follows from one decision: **reduce tests and
incidents to the same normalised shape, using the same extraction rules, so that
comparing them is a structural operation rather than a judgement call.**

```
NormalizedTest                        NormalizedIncident
  feature   "Checkout"                  feature    "Checkout"
  inputs    {discount: "10%"}           conditions {discount: "100%"}
  signals   [...]                       signals    [boundary_max, error_handling]
                    \                  /
                     ScenarioComparator
                             |
                   condition-by-condition diff
                             |
                    Coverage + Gap + Evidence
```

Because both sides are produced by `intelligence/extraction.py`, a difference
between them is a real difference — not an artefact of two independently-tuned
heuristics. This is the single most important property of the design, and most
of the classifier's precision comes from it.

---

## 2. Layering

```
          React + TypeScript UI
                    |
              FastAPI  (api/)
                    |
            Services  (services/)          orchestration, transactions
              /             \
    Parsers (parsers/)   Intelligence (intelligence/)
              \             /
        Retrieval (retrieval/)             index once, query many
                    |
      Repositories (repositories/) → SQLite
                    |
        Providers (providers/)             LLM + embeddings, swappable
```

Dependencies point inward. `domain/` depends on nothing; `intelligence/` depends
on `domain/`; services depend on everything below them; nothing depends on the
API layer. A future Jira or GitHub integration becomes another parser and
another service — the analysis engine does not change.

---

## 3. The deterministic / AI split

The specification requires that AI must not blindly determine everything, and
that BlindSpot works without an external LLM. The split is enforced structurally
rather than by convention:

| Deterministic (always) | LLM (optional, additive only) |
| --- | --- |
| File parsing, test discovery | Recovering a feature or condition the rules missed |
| Condition and signal extraction | Rewriting the explanation more fluently |
| Retrieval scoring | |
| Comparison and classification | |
| Confidence and risk | |
| Evidence | |
| Persistence | *never* |

`LLMEnricher` can only **add** keys the deterministic extractor did not produce,
and may only choose from a fixed vocabulary of features and signals. Its rewritten
explanation is rejected outright if it mentions a test ID that is not in the
candidate set. When no provider is configured, every call site receives `None`
and the system behaves identically minus the prose polish.

The consequence: **every verdict is reproducible.** The same incident against the
same index always produces the same answer, which is what makes the evaluation
harness meaningful and the evidence trustworthy.

---

## 4. Retrieval

Indexing happens once (spec Principle 1). `TestIndex` owns three things —
embeddings, a BM25 inverted index, and the normalised test metadata — and
persists all of them so a restart does not require re-parsing.

Retrieval is hybrid because neither half is sufficient alone:

- **Embeddings** generalise: an incident about a *coupon* finds a test about a
  *discount*.
- **BM25** pins exact tokens: an error code, a field name, the literal `100`.

Scores are blended with configurable weights, then adjusted by small
multiplicative boosts for feature agreement, condition-key overlap and shared
behavioural signals. Candidates below a *relative* floor (35% of the best score)
are dropped — an absolute floor alone cannot distinguish "weakly related" from
"unrelated, but this corpus is small", and padding the evidence list with
near-miss tests makes the explanation harder to trust rather than easier.

**Why a local embedding model.** The default is a hashed TF-IDF embedder written
in ~120 lines. It needs no download, works offline, and produces byte-identical
vectors on every run. A neural embedding model would generalise better but would
make analyses non-reproducible across versions and would either require a large
download or send private test metadata to a third party. `EmbeddingProvider` is
an interface, so swapping one in is a single class.

**Why an exact vector index.** FAISS is used when installed, but as `IndexFlatIP`
— exact inner-product search. An approximate index would make results vary
between runs and undermine the evidence-first guarantee, for no benefit at POC
scale (a few thousand vectors is one matrix-vector product). If FAISS is absent,
a numpy store behind the same interface produces identical results.

---

## 5. The comparison and classification core

### Per-test, not per-suite

The most important subtlety. Coverage is decided against the **best single
test**; the union across tests is tracked only to detect the case where the
pieces are each tested but never together:

```
Production:  immediate retry after timeout
Tests:       payment timeout ✓      payment retry ✓      (never together)
Verdict:     NOT COVERED — combination gap
```

Judging against the union would have called this "covered", which is precisely
the blind spot the product exists to find.

### Same-feature judging

Coverage is judged only against tests in the incident's feature when any were
retrieved. A timeout test in *Authentication* is not timeout coverage for a
*Profile* incident. Before this rule existed, cross-feature signal leakage was
silently upgrading genuinely uncovered scenarios to "partially covered".

### Key-bound qualifier signals

When production says `name = null`, the null-handling signal belongs to `name`
specifically. A test that passes a null *email* does not cover it. Signals tied
to a named input are therefore only matched when the test exercises that
qualifier on the *same* input.

### Fuzzy condition-key alignment

Incident prose and test code name the same input differently: `tax_rate` versus
`tax`, `page_size` versus `size`. Exact match wins; otherwise a shared
significant token is enough. Without this, a tested input looks untested purely
because of wording.

### Documented thresholds

Every threshold is a named constant with a stated rationale
(`classifier.py`, `confidence.py`, `risk.py`). There are no magic numbers inline
and no fabricated scores.

---

## 6. Recurring blind spots

Individual gap types roll up into **families** (`BLIND_SPOT_FAMILIES` in
`domain/enums.py`), so `NULL_HANDLING`, `EMPTY_INPUT` and `MISSING_FIELD` become
one "Null / Empty Inputs" pattern. A family is reported once it spans at least
`BLINDSPOT_PATTERN_MIN_INCIDENTS` *distinct* incidents.

Patterns are derived data, recomputed from the current set of analyses rather
than mutated incrementally — so deleting an incident or re-analysing after
adding tests always produces a correct picture. Rows are updated in place so
their ids stay stable for the UI.

---

## 7. Data model

```
test_source 1──* test
incident    1──* incident_analysis 1──* gap 1──* recommendation
                                        gap *──1 blind_spot
analysis_run                                   (audit trail)
```

SQLite, with JSON columns for the extensible parts of the domain model so that
adding a field to `NormalizedTest` does not require a migration during the POC.
The database is the source of truth; the vector index is a derived cache that is
rebuilt from it whenever the two disagree.

Re-indexing a source **replaces** its tests rather than appending, so a deleted
test also disappears — otherwise the suite could only ever grow. Identical tests
imported twice (the same case in a CSV and in the repository) are stored once,
detected by a content fingerprint.

---

## 8. Error handling

The governing rule from the specification: *one malformed file must never abort
an indexing run.* Parsers therefore never raise on bad **content** — a broken
row, a syntax error, a corrupt workbook becomes an `IngestionIssue` that is
reported in the result and surfaced in the UI. The scanner additionally guards
against symlinks, oversized files, unreadable files and parser crashes.

The generated sample repository contains a deliberately malformed module to keep
this path exercised, and a test asserts it is reported and skipped.

---

## 9. Frontend

React + TypeScript with no routing, state-management or data-fetching library —
five screens do not justify them, and their absence keeps the request/response
shape visible to a reviewer.

The UI's core obligation is that a verdict never appears alone. Evidence and the
"Analysis details" panel (retrieval scores, per-condition comparison, the closest
test) are part of the result, so a reader can always check the reasoning instead
of trusting it.

There is one copy of the source. It builds with Vite for production, and the
same `.tsx` files are compiled in the browser by a small Babel-based loader when
Node.js is unavailable — a POC convenience that costs a one-off compile on load
and is skipped entirely once `dist/` exists.

---

## 10. Extension points

| To add | Implement | Nothing else changes |
| --- | --- | --- |
| A test framework | `TestParser` | Register it in `RepositoryScanner` |
| A test source (GitHub, TestRail) | A parser + a service method | Analysis engine untouched |
| An incident source (Sentry, Jira) | Produce a `NormalizedIncident` | Analysis engine untouched |
| An embedding model | `EmbeddingProvider` | Register in `providers/embeddings` |
| A vector database | `VectorStore` | Register in `build_vector_store` |
| An LLM provider | `LLMProvider` | Register in `providers/llm` |
| A gap category | Add to `GapType` + a family + a recommendation builder | Classifier rules are table-driven |

---

## 11. Known limitations

Stated plainly, because a POC that hides them is less useful than one that does
not:

- **Extraction is rule-based.** It handles the engineering English in the sample
  corpus well; unusual phrasing will produce fewer conditions and therefore a
  lower-confidence, more conservative verdict. It fails toward "not enough
  evidence" rather than toward a confident wrong answer.
- **The condition vocabulary is curated.** Domain nouns outside
  `_CONDITION_NOUNS` are not extracted. This is deliberate — a wrong condition
  produces a confidently false coverage claim, which is worse than a missing one.
- **Feature inference is keyword-driven** and single-label. A test spanning two
  features is filed under one.
- **No test-execution history.** BlindSpot reasons about what tests *represent*,
  not whether they ran or passed. `MISSING_EXECUTION` exists as a gap type for
  when CI data is integrated.
- **Topical-only incidents are judged conservatively.** With no extractable
  condition and no decisive signal, the system will not claim `COVERED`. This
  costs accuracy on that class and is the single evaluation mismatch — an
  intentional trade, since a false "covered" is the worst error this product can
  make.
- **The evaluation is not fully independent** — see the caveat in the README.
