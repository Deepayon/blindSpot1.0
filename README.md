# BlindSpot

**Find what production knows that your tests don't.**

Test coverage intelligence for production incidents. A proof of concept.

BlindSpot indexes your existing tests **once**, then answers one question for
every production incident:

> Was this failure actually protected against by our tests?

It is not an AI test-case generator. It is an SDLC feedback layer that turns
real-world failures into evidence about where your testing strategy does not
represent reality.

---

## What it does

```
Production incident
  -> understand the scenario        (feature, conditions, behaviour)
  -> retrieve candidate tests       (hybrid semantic + lexical search)
  -> compare, condition by condition
  -> classify coverage              COVERED / PARTIAL / NOT COVERED
                                    or INSUFFICIENT EVIDENCE, rather than guess
  -> explain why, with evidence
  -> detect recurring blind spots
  -> recommend specific coverage
```

The worked example from the specification, produced by the running system:

```
INC-1042   Checkout failed when a 100% discount coupon was applied

Coverage    PARTIALLY COVERED          Confidence  86% (High)      Risk  High
Gap         BOUNDARY_CONDITION         The 100% discount boundary condition is
                                       not covered by any existing test.

Why         Existing tests validate discount behaviour (10% and 20%) but do not
            represent the 100% value observed in production, which is what
            triggered the failure.

Evidence    Production condition: discount = 100%
            Closest test test_checkout::test_checkout_with_a_10_discount_coupon
              belongs to the same feature (Checkout)
            'discount' is tested at 10% and 20%, but production used 100%
            No related test exercises the upper boundary value

Recommend   Add a Checkout test with discount = 0% / 50% / 100%
            Add a negative-value test for discount
            Add an invalid-value test for discount
```

---

## Quick start

Requirements: **Python 3.11+**. Node.js is optional (see [Frontend](#frontend)).

```bash
pip install -r requirements.txt

# Generate the sample dataset (~1,500 tests, 56 incidents with ground truth)
python scripts/generate_dataset.py

# Index it and analyse every incident, so the UI opens with real data
python scripts/load_sample.py

# Run
cd backend
uvicorn app.main:app --reload
```

Open **http://localhost:8000**. API docs are at `/docs`.

> **Windows PowerShell:** run each line separately, `&&` is not a valid
> statement separator in Windows PowerShell 5.1. If `uvicorn` is not on your
> PATH, use `python -m uvicorn app.main:app --reload` instead.
>
> ```powershell
> cd backend
> python -m uvicorn app.main:app --reload
> ```

To start empty instead, skip `load_sample.py` and add a test source from the
**Tests** screen.

### Using it on your own project

1. **Tests -> Local project** -> enter a directory path -> *Index repository*.
   Python/pytest is the primary target; JavaScript/TypeScript (Jest, Vitest) is
   also detected. Your code never leaves the machine and is never executed.
2. Or **Tests -> CSV / Excel** -> drop a test export. Column names are matched
   flexibly (`Test ID`, `TestID`, `Key`, ... all work).
3. **Incidents** -> paste a production incident -> *Analyse incident*.

Tests are indexed once. Every later incident is analysed against the same index.

---

## How it works

BlindSpot is deliberately **deterministic first**. An LLM is optional and, when
enabled, may only enrich wording and fill blanks, it can never change a
verdict, invent a test ID, or introduce a claim that is not in the evidence.

| Concern | Owner |
| --- | --- |
| Parsing, test discovery, metadata | Deterministic code (AST, CSV/Excel readers) |
| Condition and signal extraction | Deterministic rules (`intelligence/extraction.py`) |
| Retrieval | Local embeddings + BM25, blended |
| Comparison, classification, scoring | Deterministic rules with documented thresholds |
| Explanation wording | Templates; optionally rewritten by an LLM |
| Database writes, file access, execution | Never the LLM |

### Coverage classification

The decision order, from `intelligence/classifier.py`:

1. No candidate clears the relevance floor -> **NOT COVERED**
2. The pieces are tested but never together -> **NOT COVERED** *(combination gap)*
3. One test matches every condition and behaviour -> **COVERED**
4. A related test exists but misses the decisive production condition -> **PARTIAL**
5. Otherwise -> **NOT COVERED**

The distinction that matters most: **PARTIAL** means the input *is* exercised
but not at the production value (the `10% / 20% / never 100%` case).
**NOT COVERED** means the input or behaviour is untested entirely.

Coverage is judged **per test, not per suite**. Three tests that separately
cover `timeout`, `retry` and `payment failure` do not cover "immediate retry
after timeout", that is reported as a combination gap, exactly as the
specification requires.

A `COVERED` verdict on an incident that still happened is reported as
**potentially ineffective coverage**: the scenario was represented, so the
problem is in the test's assertions or data.

### Confidence and risk

Both are computed from observable signals and documented in code, never
invented. Confidence combines retrieval strength, how many structured facts were
comparable, how clear-cut the verdict was, and whether the index is large enough
for an absence to mean anything. Risk combines incident severity, coverage
level, feature criticality and recurrence. See `intelligence/confidence.py` and
`intelligence/risk.py` for the point tables.

---

## Evaluation

The sample dataset ships with a ground-truth file, so the analyser can be
measured rather than demonstrated:

```bash
python scripts/evaluate.py
```

Current results on the 66-incident sample (1,500 indexed tests):

| Metric | Result |
| --- | --- |
| Coverage classification accuracy | **97.0%** (64/66) |
| Gap family (category) accuracy | **98.5%** (65/66) |
| Retrieval, a same-feature test found | **92.4%** (61/66) |
| Recurring blind spots detected | 10 |

Ten of those incidents are deliberately adversarial: wording that shares no
vocabulary with the tests, tests that are lexically similar but irrelevant,
several conditions at once, combination gaps, empty versus null, and reports too
vague to judge at all. They exist to make the number honest rather than
flattering, and two of them still fail (below).

**Read this number with the right caveat.** The ground-truth labels were written
before the engine was tuned, but several were revised during development where
the original label contradicted the specification's own definitions, or
contradicted another label in the same file. One example of each: an input that
*is* exercised at a different value was labelled `NOT_COVERED`, when the spec's
flagship 100%-discount example calls exactly that case `PARTIAL`; and
`invalid_email_format` was labelled `NOT_COVERED` while `null_email_profile`,
which has the identical structure (same field, same feature, exercised but never
at the failing value), was labelled `PARTIAL`. Each revised label carries its
justification in `data/evaluation/ground_truth.json`. Because labels and engine
were refined in the same effort, treat this as a development signal, not an
independent benchmark.

### The four verdicts

`COVERED`, `PARTIAL` and `NOT_COVERED` are the answers. `INSUFFICIENT_EVIDENCE`
is the refusal, and it is returned when the report gives nothing to compare:
no conditions, no behavioural signals, and no shared wording specific enough to
mean anything. "Customers reported that the orders page looked wrong" shares
`orders` and `page` with most of the suite, which is vocabulary, not evidence.

The distinction from `NOT_COVERED` is deliberate. "Not covered" is a positive
finding: the suite was searched and the scenario is missing. An incident that
names its area and finds nothing related there still gets `NOT_COVERED`. An
incident that never described a scenario gets `INSUFFICIENT_EVIDENCE`, records
no gap, and is kept out of the recurring blind spots, so an unclear report
cannot inflate a pattern. Its recommendations address the report rather than the
test suite.

### The two remaining mismatches

Both are the same limitation and both are kept in the dataset on purpose:
recognising that a "full-value voucher" is a 100% discount, or that signing in
with "the wrong secret" is the incorrect-password test, requires the meaning of
the words rather than the words themselves. The deterministic engine declines
instead of guessing, which is the intended behaviour. Semantic extraction is
what should close them; deleting or relabelling them would hide the one
limitation the evaluation most needs to report.

---

## Frontend

The UI is React + TypeScript, and there is exactly one copy of the source in
`frontend/src`. It can run two ways:

**With Node.js**, the production path:

```bash
cd frontend
npm install
npm run build       # emits dist/, which the backend serves automatically
npm run dev         # or: Vite dev server on :5173, proxying /api to :8000
```

**Without Node.js**, the POC path, and the default:
the backend serves `frontend/src` directly and the loader in `index.html`
compiles the same `.tsx` files in the browser with a vendored Babel. React,
ReactDOM and Babel are committed under `frontend/vendor/`, so this works with no
network access at all.

This is a development convenience, not the deployment story: it costs a one-off
compile on page load. Run `npm run build` and the backend prefers `dist/`
automatically, no code changes.

---

## Testing

```bash
python -m pytest          # 118 backend tests
python -m ruff check .    # lint
```

Covers CSV/Excel/pytest ingestion, malformed input, repository-scanning
security, retrieval, all three coverage classifications, the combination-gap
case, explainability guarantees, recommendations, pattern detection and the full
API surface.

Frontend tests (`npm test`) require Node.js.

---

## Configuration

All configuration is environment-driven. Copy `.env.example` to `.env`.

The defaults keep BlindSpot **fully local**: a local embedding model, a local
vector index, SQLite, and no external AI. The Settings screen states plainly
whether anything leaves the machine.

To enable an LLM:

```bash
BLINDSPOT_LLM_PROVIDER=openrouter    # or: gemini | anthropic | openai
BLINDSPOT_LLM_MODEL=nex-agi/nex-n2.5-mini:free
BLINDSPOT_LLM_API_KEY=...
```

Any OpenAI-compatible gateway works without new code, set
`BLINDSPOT_LLM_BASE_URL` (OpenRouter, Together, vLLM, LM Studio, Ollama).
Switching provider without changing the model is safe: a model belonging to
another provider is replaced with that provider's default rather than failing
every call.

Only normalised test metadata and incident text are sent. Source code and raw
test bodies never are. If the provider is unreachable, rate-limited or
misconfigured, analysis continues deterministically and the log says which.

**What the model is allowed to do** is deliberately narrow, and this was
tightened after measurement rather than assumed:

-  identify the **feature** when the deterministic rules found none
-  rewrite the **explanation** more fluently, using only the supplied facts , 
  an explanation citing a test ID that was not retrieved is rejected outright
-  supply conditions or signals, because those decide the verdict

That last rule is not caution for its own sake. Letting the model contribute
conditions and signals was measured against the sample dataset and changed two
correct verdicts out of eight into wrong ones; restricting it to a genuine blank
still cost one. With the current boundary both Gemini and OpenRouter match
deterministic analysis **8/8** while producing noticeably better prose, and
reproducibility is preserved: the same incident always yields the same verdict.

Provider behaviour observed while testing, in case it saves you time:

| Provider | Notes |
| --- | --- |
| OpenRouter (`nex-agi/nex-n2.5-mini:free`) | ~5 s/call, clean JSON, refined 8/8 explanations |
| Gemini (`gemini-3.6-flash`) | Works well; free-tier quota is easily exhausted (HTTP 429) |
| Reasoning-first free models | Spend the whole token budget thinking, return `{}`, can take minutes, avoid |

Failures are never fatal: a 429, a wrong model name or a timeout logs an
actionable reason and the analysis completes deterministically.

---

## Security and privacy

### Your source code

BlindSpot reads test files to extract metadata: the test name, its feature, its
inputs and its expected behaviour. That is all the analysis needs.

- **Your code is never uploaded.** Indexing happens in the process you started,
  against a directory you nominated.
- **Your code is never executed.** Python tests are read with the `ast` module.
  Nothing is imported, and no test is ever run.
- **Your code is never stored.** The raw test body is discarded after parsing
  and is not written to the database. `BLINDSPOT_STORE_SOURCE_CODE` can retain
  it on a machine you own; it cannot be enabled in a hosted deployment, and the
  API never returns it either way.
- **Only test files are opened.** A file no registered parser claims is not read.

### Deployment modes

| | `local` (default) | `hosted` |
| --- | --- | --- |
| Index a directory on the server | Available | Refused |
| Delete sources and incidents | Available | Requires `BLINDSPOT_ADMIN_TOKEN`, otherwise off |
| Retain source code | Opt-in | Blocked at startup |
| CORS | localhost only | Nothing unless an origin is named |
| Content-Security-Policy | Permits the in-browser compiler | Strict, no inline script |

An unrecognised `BLINDSPOT_MODE` is treated as `hosted`, so a typo makes the
deployment more restrictive rather than less.

### Directory policy

A path is refused unless it is plainly a project directory. Rejected: remote
URLs, network paths, relative paths, filesystem and drive roots, system
directories, credential directories such as `.ssh` and `.aws`, your home folder,
and personal folders such as Documents and Downloads. A project *inside* one of
those folders indexes normally.

Scans are bounded by file count, file size, tree depth and wall-clock time, so a
large or deeply nested directory cannot hold a request open.

### Other controls

Per-client rate limiting with a tighter budget for analysis endpoints; request
size limits; `X-Frame-Options`, `Content-Security-Policy`, `X-Content-Type-Options`,
`Referrer-Policy` and related headers on every response; constant-time admin
token comparison; and errors that never return a stack trace, file path or
driver detail.

These are verified by 56 tests in `backend/tests/test_security.py`.

The POC has no user accounts. It binds to `127.0.0.1` by default. Put
authentication in front of it before exposing it to a network.

Symlinks are not followed, and every candidate file is re-checked to still
resolve inside the nominated root. `BLINDSPOT_ALLOWED_REPOSITORY_ROOTS`
restricts indexing to an explicit allow-list.

---

## Docker

```bash
docker compose up --build      # http://localhost:8000
```

Mount repositories read-only to index them; see the comments in
`docker-compose.yml`.

---

## Project layout

```
backend/app/
  api/           FastAPI routes, schemas, frontend hosting
  config/        settings and structured logging
  db/            SQLAlchemy models and session management
  domain/        the normalised model, the stable core
  parsers/       CSV, Excel, pytest, Jest, repository scanner
  retrieval/     embeddings, vector stores, BM25, hybrid retriever
  intelligence/  extraction, comparison, classification, explanation,
                 recommendations, pattern detection, optional LLM
  repositories/  persistence
  services/      orchestration
  providers/     LLM and embedding abstractions
frontend/src/    React + TypeScript UI
scripts/         dataset generation, sample loading, evaluation
docs/            architecture
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design rationale and
extension points.

---

## Scope

This is a 2-3 week POC. Deliberately **not** built: Jira/GitHub/CI integrations,
authentication, multi-tenancy, test execution, automatic code modification, or
support for every framework. The domain model is kept independent of any
integration so those can be added later without touching the analysis engine.
