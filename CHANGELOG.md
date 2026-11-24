# Changelog

All notable changes to nimblegraph are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- File hashes ignore line endings (CRLF is read as LF), so a `notes.json` committed from a Windows checkout with
  `core.autocrlf` is not stale on Linux/macOS, and back. `notes.json` goes to version 2: notes written against the
  current content are upgraded in place on the first load; the index is rebuilt once (schema 4).

## [0.1.0] - 2026-11-12

First public release.

### Added

- **Index and queries for JS/TS**: `analyze`, `endpoint`, `flow`, `file`, `deps`, `rdeps`, `routes`, `find`,
  `impact`, `cycles`, `orphans`, `entries`, `status` and `export`, with compact answers that respect a token
  budget (`--budget`). Two engines produce the same per-file record: the TypeScript compiler API on Node and a
  pure-Python parser.
- **Routes** of Express, Fastify, Hono, Koa, Nest, Next.js, React Router and TanStack Router, with mounts and
  prefixes across files, middlewares, guards and DTOs; front-end `fetch`/axios calls linked to back-end routes.
- **Notes** per file, symbol and chain (`describe`, `todo`, `update [--ai]`), with token limits and staleness by
  file hash.
- **Notes with a local model** (`nimblegraph local`, `update --local`) through Ollama, LM Studio or llama-server.
- **Visual interface** (`nimblegraph ui`): live graph, endpoints in request order, colors by layer, bounded
  context, ring or language, light theme and 3D mode.
- **Claude Code integration**: the `/nimblegraph` skill, `init --claude-md`, `init --hook` and `init --guard`.
- **Architecture governance**: `check` with presets (`layered`, `nest`, `clean`/`hexagonal`, `ddd`, `react`,
  `deps`, `naming`, `fullstack`), custom rules in `rules.toml`, baseline, and `rules`, `ddd`, `glossary`,
  `conventions`, `architecture`, `hotspots`, `trend`, `adr`, `deadcode`, `tests`, `contracts` and `review`.
- **More languages** through tree-sitter, with a regex fallback: Python, Go, Java, C#, PHP, Ruby, Rust, Kotlin,
  C, C++ and Swift, and the routes of FastAPI, Flask, Django, DRF, net/http, Gin, Echo, Chi, Fiber, gorilla/mux,
  Spring, JAX-RS, ASP.NET Core, Laravel, Symfony, Rails, Sinatra, Axum, Actix Web, Rocket, Ktor and Vapor.
- **String evaluation** for route prefixes and URLs (constants across files, Spring properties, interpolation,
  concatenation, format calls) and **HTTP calls of every language** linked to the routes they reach, from clients
  and from tests.

### Changed

- The project was called codegraph (command `graph`, folder `.codegraph/`) before this release. Everything is now
  `nimblegraph`: package, command, skill, `.nimblegraph/` and `NIMBLEGRAPH_INDEX_DIR`.

[Unreleased]: https://github.com/HiagoARJ/nimblegraph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/HiagoARJ/nimblegraph/releases/tag/v0.1.0
