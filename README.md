# Quick Study

Quick Study is a self-hosted tutorial-generation system. The repository currently contains
the modular-monolith scaffold (a Next.js web process, a FastAPI control plane, a Celery
worker, MySQL and Redis), the domain layer (the database schema and migrations, local account
authentication, the idempotent run/step execution contract, content-addressed artifact
storage and the keyword retrieval interface) and the resumable generation workflow: the
LangGraph pipeline, its outline-approval interrupt and the MySQL checkpointer adapter. The
workflow's model and ingestion nodes are still stubs, and the reviewer interface is not
implemented yet.

## Repository layout

```text
apps/
  api/
    alembic/           Migrations; 0001 creates the whole domain schema
    app/
      auth/            Argon2id passwords, sessions, CSRF, project authorisation
      db/              Declarative base, portable column types, models, sessions
      retrieval/       Stable search interface over the MySQL FULLTEXT indexes
      runs/            Run state machine and the at-least-once step contract
      storage/         SHA-256 content-addressed artifact store
      workflows/       LangGraph generation graph and the checkpointer adapter
  web/                 Next.js App Router application
packages/
  contracts/           Reserved for OpenAPI-generated frontend types
  ts-analyzer/         Static JavaScript/TypeScript analysis, JSON out, spawned by the API
  diagram-renderer/    Mermaid validation and sanitized SVG rendering, spawned by the API
data/
  artifacts/           SHA-256-addressed artifact payloads (contents are ignored)
compose.yaml            Local container topology
```

## Docker Compose on Windows

Prerequisites: Docker Desktop with Compose, Git, Node.js 20.9 or newer, Python 3.12 or newer,
and `uv`.

From PowerShell:

```powershell
Copy-Item .env.example .env
# Replace the two password placeholders in .env before starting services.
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-WebRequest http://127.0.0.1:3000
```

Stop the stack without deleting persisted data:

```powershell
docker compose down
```

MySQL and Redis ports bind to `127.0.0.1` only. The API and worker containers run as the
unprivileged `app` user, and the bound `data/artifacts` directory is the only writable path.
Application secrets belong in the ignored `.env` file or a deployment secret store; never add
them to the repository.

## Local development

Install dependencies once:

```powershell
uv sync --project apps/api --dev
npm ci --prefix apps/web
npm ci --prefix packages/ts-analyzer
npm ci --prefix packages/diagram-renderer
```

Start MySQL and Redis in containers:

```powershell
Copy-Item .env.example .env
docker compose up -d mysql redis
```

Then run each process in a separate PowerShell terminal:

```powershell
npm run dev:api
npm run dev:worker
npm run dev:web
```

The API and the worker read the repository-root `.env` and resolve `data/artifacts` inside
this checkout no matter which working directory they run from, so they always share one
configuration and one artifact root. When `DATABASE_URL` is not supplied they
compose `mysql://MYSQL_USER:MYSQL_PASSWORD@MYSQL_HOST:MYSQL_PORT/MYSQL_DATABASE` from the
`.env` values. `REDIS_URL` and `ARTIFACTS_DIR` fall back to the published Compose port and
the in-repo artifact root. The checked-in defaults contain no credentials.

## Database migrations

Alembic reads the same `.env` as the application, so no separate database configuration is
needed:

```powershell
npm run db:upgrade                       # apply every migration
npm run db:history                       # show revisions and the current one
npm run db:revision -- -m "add x"        # generate a revision from the models
```

Revision `0001` creates the full domain schema: `users`, `sessions`, `projects`,
`project_members`, `sources`, `snapshots`, `documents`, `chunks`, `symbols`, `edges`, `runs`,
`steps`, `artifacts`, `outlines`, `chapters`, `claims`, `citations` and `approvals`, together
with the four MySQL `FULLTEXT` indexes used for retrieval. Every table is InnoDB and utf8mb4.
Generated revisions are formatted and linted by an Alembic post-write hook.

## Focused checks

```powershell
npm run check:scaffold      # API and web smoke tests, linting, Compose validation
npm run check:domain-auth   # API test suite and Python linting
npm run check:workflow      # graph, runner and checkpointer contract tests, plus linting
npm run check:packages      # type checks, builds and tests both analysis packages
npm run check               # the scaffold checks plus the analysis packages
```

The API suite runs against SQLite and an in-process checkpointer by default, so it needs no
services. Set `QUICKSTUDY_TEST_MYSQL_URL` to a scratch schema whose name contains `test` to
additionally run the MySQL-only checks — migrations against real MySQL 8.4, `MATCH ...
AGAINST` retrieval and the whole checkpointer contract. Those tests drop and recreate their
schemas on every run and skip when the variable is unset. None of these commands runs an
end-to-end tutorial-generation flow.

## Generation workflow

The tutorial is produced by a LangGraph graph in
[apps/api/app/workflows/tutorial](apps/api/app/workflows/tutorial):
`discover → snapshot → parse → index → analyze → outline → human_interrupt → chapters →
diagrams → validate → publish`. `human_interrupt` calls LangGraph's `interrupt()` and the
run stays suspended on its own thread until a reviewer's decision arrives as
`Command(resume=...)`; approval continues to `chapters` and rejection is the one step back
the pipeline allows, straight to `outline` for another version.

Each phase's body is one field of `TutorialNodes`, so ingestion, retrieval, generation,
diagrams and the quality gate each replace a function without touching the graph. The
defaults are deterministic stubs that call no model and fetch nothing. Every node goes
through the same wrapper: it claims a step by idempotency key before doing anything, so a
node that already succeeded or is leased by another worker does nothing, and it records
`pipeline_version`, `input_hash`, `prompt_hash`, `model`, `attempt`, tokens, cost and any
error. A chapter a reviewer locked is never replaced by a regeneration.

Celery only wakes a run up. `app.workflows.tasks` takes an identifier, hands it to the
runner and returns a status summary; the run's phase, attempts, cost and errors live in
`runs` and `steps`, so a lost message costs latency and a duplicated one costs nothing.
Each wake-up is itself a step, and a reviewer's decision is part of its key, so replaying an
approval is refused while a different decision is new work.

### MySQL checkpointer

LangGraph's first-party production checkpointer is the PostgreSQL one; MySQL is served by
the community package `langgraph-checkpoint-mysql`. It is confined to
[apps/api/app/workflows/checkpointing](apps/api/app/workflows/checkpointing) behind
`CheckpointerProvider`, which owns the connection settings the package requires
(`autocommit=True`, or `setup()` silently fails to persist its tables) and refuses to run on
a server it was not tested on — MySQL 8.0.19 or newer, and older than 9.6, which dropped
`MD5` from generated column expressions.

The tested version window for `langgraph`, `langgraph-checkpoint` and
`langgraph-checkpoint-mysql`, along with the migration level the package should reach, is
declared in `compatibility.py` and verified before the adapter touches a database, so an
upgrade fails loudly instead of writing a schema nobody has exercised.

`tests/test_checkpointer_contract.py` is what makes the dependency replaceable. It states
what any backend has to do — initialisation and migration, concurrent writes, recovery after
a hard kill, redelivery, interrupt and `Command(resume=...)`, and a database left at an
older migration level — and runs it against every provider. Replacing the store means
writing one provider and passing the same suite. Checkpoints hold execution state only, so
losing the checkpoint schema costs progress, never facts.

## Analysis packages

Two Node packages do work that has no good Python equivalent. The API calls both as
subprocesses, so their contract is a CLI plus a versioned JSON document rather than an import.
Each ships its own JSON Schema through `--print-schema`, and each is documented in its own
README.

[packages/ts-analyzer](packages/ts-analyzer) analyzes JavaScript and TypeScript with the
TypeScript Compiler API and emits files, symbols, imports, dependencies and call edges. It never
executes the repository it reads: the compiler host answers only from the collected sources plus
TypeScript's bundled `lib.*.d.ts`, so resolution cannot reach `node_modules` or anything else on
disk. Relationships that are not statically decidable — dynamic dispatch, computed members,
reflection, callbacks — are reported as `unresolved` with a machine-readable reason instead of
being guessed.

[packages/diagram-renderer](packages/diagram-renderer) validates a Mermaid source with
`mermaid.parse()` and renders it with a pinned Mermaid at `securityLevel: "strict"`, then
sanitizes the SVG: scripts, event handlers, embedded HTML and every non-fragment URL are removed.
A failure at any stage returns `svg: null` with a diagnosable error, so a broken or unsafe
diagram is never published.

Both bound their work with overridable limits that are clamped by hard ceilings (file counts and
sizes, input and output sizes, time budgets), and both write machine-readable JSON on failure.

## Authentication model

Local accounts only. The first administrator is created once through the bootstrap flow,
which a unique `users.bootstrap_slot` enforces at the database level rather than with a
read-then-write check. Passwords are Argon2id (64 MiB, 3 iterations, 4 lanes) and are
re-hashed on login when those parameters change. A session is an opaque random token
delivered in an HttpOnly, SameSite=Lax, Secure cookie; only its SHA-256 fingerprint is
stored, so a database dump cannot be replayed. State-changing requests must also present the
session's CSRF token in the `X-CSRF-Token` header. Project access is resolved from ownership,
`project_members` or deployment-administrator status, and a caller without access gets 404
rather than 403 so project ids cannot be enumerated.
