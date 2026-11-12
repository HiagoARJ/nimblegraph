---
name: nimblegraph
description: Use nimblegraph (`nimblegraph` CLI) in JS/TS, Python, Go, Java, Kotlin, C#, PHP, Ruby, Rust, C/C++ and Swift projects to understand features, routes and endpoints from a compact, token-budgeted index instead of opening many files; to write/refresh the per-file and per-chain notes; and for architecture governance (rules + baseline, DDD bounded contexts, naming conventions, test coverage, PR review). Use it whenever the project has a .nimblegraph/ folder or the user mentions graph/nimblegraph, before reading code to answer "what does X do", "where is Y", bug hunts on a route, impact of a change, architecture/DDD questions, before finishing a change (`nimblegraph check --git`), or when asked to /nimblegraph update or document the project.
---

# nimblegraph (`nimblegraph`)

The project is indexed: files, imports (barrels followed; for Go/Java/C#... the references resolved through
packages and namespaces), routes (Next, Express/Fastify/Hono/Koa, Nest, React Router, TanStack Router, FastAPI,
Flask, Django, Gin/Echo/Chi/net/http, Spring, ASP.NET Core, Laravel, Symfony, Rails, Sinatra, Axum, Actix, Rocket,
Ktor, Vapor), front-end API calls linked to backend routes, symbols, and notes ("fichas") written by you.
Every answer is compact text with a token cost footer: `[~380 tok | reading all: ~9.7k tok]`.
Queries rebuild the index incrementally first, so they are always current.

## Before reading code
1. `nimblegraph analyze <term>` for a feature/domain word (`user`, `checkout`, `loan`): routes, pages, the flow
   front -> API -> controller -> service -> model, shared files, files without notes, packages.
2. `nimblegraph endpoint "<METHOD /path>"` for a specific route (bug, change, question). A concrete URL works
   (`nimblegraph endpoint /api/users/42`). Order: MOUNT > MIDDLEWARE > VALIDATION > HANDLER > DOWN (what the
   handler really uses, by method) > FRONT callers > TESTS. Use `--files` when you only need the paths.
3. `nimblegraph flow <route|page|file>` for the end-to-end chain of one page or route.
   If analyze says "too broad", it lists the AREAS where the term lives: rerun with two words
   (`nimblegraph analyze "proposal email"`) or with a folder/file path.
4. Open ONLY the files these answers point to. Prefer the ones marked as handler/DOWN or seeds.

In Git Bash on Windows, route arguments like `/api/x` are handled (the CLI undoes MSYS path conversion).

## Keep the notes alive
- After reading or editing a file that has no note, or whose note is stale (`*` after the path, or
  `NO-NOTE` lines), save one:
  `nimblegraph describe <file> --title "..." --summary "..." --resp "..." --scope "..." --flow "..." --tags a,b`
- Many at once (preferred): `nimblegraph describe --json '[{"file": "src/x.ts", "title": "...", "summary": "..."}]'`
  (one line, single quotes outside; avoid single quotes inside the texts).
- Limits (tokens, about chars/3.5): title 12, summary 40, resp 30, scope 12, flow 40, tags <= 6. A note over
  a limit is rejected with how much it is over: shorten and retry.
- Field meaning: title = what the file is; summary = what it does; resp = what is and is not its job;
  scope = feature/domain word; flow = its steps (`req > validate > service.create > 201`).
- Chain notes describe a whole feature: `nimblegraph describe --chain user --title .. --summary .. --flow ..`
  (limits 12 / 60 / 80). `nimblegraph analyze user` shows it on top (`CHAIN user - ...`).
- Write notes in the language of the project's team (Brazilian Portuguese for this user) unless told otherwise.

## Architecture governance
- Before finishing a change: `nimblegraph check --git` (only the files you touched). An ERROR is a broken rule of
  `.nimblegraph/rules.toml` (or of the presets detected when there is none): fix the dependency instead of
  working around it. If the user says the violation is intended, `nimblegraph check --baseline` accepts it.
- The project may have the guard hook (`nimblegraph init --guard`): after an Edit/Write it tells you the new errors of
  that file. Treat that message as a failing test.
- Where code belongs: `nimblegraph ddd` (bounded contexts, aggregates, context map, leaks) and `nimblegraph ddd <context>`
  (its aggregates, commands/queries/handlers, ports -> adapters, routes). Put new code in the context and ring
  (domain / application / infrastructure / presentation) its neighbours use; reach another context only through
  its public API (index, module exports, DTOs, commands/queries/events).
- Names: `nimblegraph conventions` shows the naming each package+layer follows (inferred or from rules.toml). Name new
  files and classes the same way.
- Tests: `nimblegraph tests --git` lists the tests that reach your change and the command to run them; `nimblegraph tests`
  shows endpoints/use cases without tests.
- PR summary: `nimblegraph review --base main --format markdown`.

## /nimblegraph update <target>
1. Run `nimblegraph update <target>` (target: a term, a route like `GET /api/users`, or a file).
2. Read only the files listed under DESCRIBE (stale ones show what the old note said).
3. Save all of them with one `nimblegraph describe --json '[...]'`.
4. If the CHAIN line says missing or stale, write the chain note with `nimblegraph describe --chain "<target>" ...`.
5. Reply with how many notes were saved.
Without a target: `nimblegraph update` shows what changed since the last build and which notes/chains went stale.

## Notes without Claude (local model)
If the user wants notes written locally (Hugging Face model via Ollama/LM Studio): `nimblegraph local status`,
`nimblegraph local setup`, `nimblegraph local describe [--all | <term>]`, or `nimblegraph update <target> --local`. Do not write those
notes yourself then. Notes written by a local model carry `"by": "local:<model>"`; refine them with `nimblegraph describe`
when you have read the file (that clears the marker).

## /nimblegraph todo
`nimblegraph todo --limit 15` lists the most important files without notes (routes, controllers, pages first, by
importers) with exports and cost. Describe them in batches with `nimblegraph describe --json`.

## Command reference
| command | what you get |
|---|---|
| `nimblegraph status` | engine, files by layer, routes, notes coverage, unresolved imports |
| `nimblegraph analyze <term>` | the feature chain front <-> back (main command) |
| `nimblegraph endpoint <route> [--files]` | every file of an endpoint in request order |
| `nimblegraph flow <route\|page\|file>` | end-to-end chain |
| `nimblegraph file <path>` | note, exports, imports, importers, routes, API calls of one file |
| `nimblegraph deps <file>` / `nimblegraph rdeps <file>` | what it uses / who uses it, as a tree |
| `nimblegraph routes [filter]` | method, path, handler of every route and page |
| `nimblegraph find <text>` | paths, symbols, routes and notes |
| `nimblegraph impact <files...> \| --git` | pages, routes, callers and tests affected by a change |
| `nimblegraph cycles` / `nimblegraph orphans` / `nimblegraph entries` | import cycles / unused files / entry points |
| `nimblegraph todo` / `nimblegraph update [target]` | notes to write |
| `nimblegraph describe ...` | write notes (file, `--symbol`, `--chain`, `--json`, `--batch`) |
| `nimblegraph export --format mermaid\|dot\|json [target]` | diagram for humans |
| `nimblegraph ui` | the visual graph in the browser (live) |
| `nimblegraph init --claude-md` | set up a new project (.nimblegraph/, .gitignore, CLAUDE.md section) |
| `nimblegraph check [files] [--git\|--base REF] [--all] [--baseline] [--format text\|json\|markdown\|github]` | architecture rules: new violations vs the baseline (exit 1 on new errors) |
| `nimblegraph rules [--init [presets]] [--export depcruise]` | active presets/rules; write `.nimblegraph/rules.toml` |
| `nimblegraph ddd [context]` | bounded contexts, aggregates, building blocks, context map, leaks |
| `nimblegraph glossary [--init]` | ubiquitous language: domain terms per context, synonyms |
| `nimblegraph conventions` | naming conventions per package+layer and the files off them |
| `nimblegraph architecture [--by context\|folder\|layer\|package\|ring] [--dsm] [--format mermaid]` | Ca/Ce/I/A/D per module, god files, dependency matrix |
| `nimblegraph hotspots` / `nimblegraph trend` / `nimblegraph adr` / `nimblegraph deadcode` | churn x size, metric history, ADRs per file, unused exports |
| `nimblegraph tests [files\|--git]` / `nimblegraph contracts` | coverage by endpoint/use case, tests to run / front x back mismatches |
| `nimblegraph review [--base REF] [--format markdown]` | the whole picture of a change, with reviewers from CODEOWNERS |

Common options: `--budget N` (tokens, default 1500; answers shrink progressively: symbol lists, then notes to
titles, then deep levels to counts, then a cut with the next command to run), `--depth N`, `--root <dir>`.
