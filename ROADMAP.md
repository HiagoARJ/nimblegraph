# Roadmap: more languages

nimblegraph started with JS/TS. The goal is the same compact answers (`analyze`, `endpoint`, `impact`, `check`...)
for the most used languages, ordered by how much a web back-end with routes benefits from it.

| wave | language | frameworks (routes) | status |
|---|---|---|---|
| — | JavaScript / TypeScript | Express, Fastify, Hono, Koa, Nest, Next.js, React Router, TanStack Router | done |
| 1 | Python | FastAPI, Flask, Django, DRF routers | done |
| 1 | Go | net/http (1.22 patterns), Gin, Echo, Chi, Fiber, gorilla/mux | done |
| 1 | Java | Spring MVC/Boot, JAX-RS | done |
| 1 | C# | ASP.NET Core (controllers, Minimal APIs, Razor Pages) | done |
| 2 | PHP | Laravel (routes files, groups, resources), Symfony attributes | done |
| 2 | Ruby | Rails routes DSL (resources, namespaces, member/collection), Sinatra | done |
| 2 | Rust | Axum (nest across functions), Actix Web, Rocket | done |
| 2 | Kotlin | Spring, Ktor routing DSL | done |
| 3 | C | — (includes via compile_commands.json/CMake, prototypes, symbols) | done |
| 3 | C++ | — (includes, classes and bases, out-of-class methods) | done |
| 3 | Swift | Vapor (grouped, RouteCollection, register(collection:)) | done |

## Design

1. **Language registry** (`nimblegraph/lang/__init__.py`): one entry per language with extensions, the manifests
   that make a folder a package, build folders skipped at a package root, the test-file pattern and whether its
   imports name packages (resolved through the declaration index). JS/TS is one entry of it.
2. **Parsing with tree-sitter** (`nimblegraph/lang/<language>.py`): py-tree-sitter with
   `tree-sitter-language-pack` (grammars download on first use; `nimblegraph setup` prefetches them). A generic walker
   (`lang/base.py`) builds the same per-file record as the JS engines from a per-language table of node types plus
   hooks, so everything downstream (queries, governance, UI) works unchanged. Without tree-sitter (no wheel, an
   offline first run, a native library blocked by the OS), a regex fallback extracts imports and top-level
   declarations (`partial: true`). JS/TS keeps its two engines (TypeScript compiler API and pure Python).
3. **Two-level resolution**: file-level for languages that import files (Python, PHP/PSR-4, Ruby, Rust `mod`,
   C/C++ `#include`), and symbol-level for languages that import packages or namespaces (Go, Java, Kotlin, C#,
   Swift, PHP, Ruby constants): a global `(package, name) → file` table turns the referenced types and identifiers
   into file edges, through explicit imports, the file's own package/namespace and the imported ones.
4. **Generic packages**: manifests of every ecosystem (`go.mod`, `Cargo.toml`, `*.csproj`, `pom.xml`, Gradle,
   `composer.json`, `Gemfile`, `pyproject.toml`, `Package.swift`) give package names, dependencies and externals.
5. **Framework registry**: each detector declares its languages and when it applies. Routes keep the same shape,
   so a React front end matches back-end routes in any language.

## Checked on real projects

spring-petclinic, eShopOnWeb, full-stack-fastapi-template, and the RealWorld apps of Gin, Django, Laravel, Rails,
Axum and Kotlin/Spring (each API's endpoints found with their handlers), jq (C), the Vapor template, and a
9.6k-file mixed C#/C/C++ repository (~45 s full build); then mealie (FastAPI class-based controllers), netbox
(DRF routers under include()), mastodon (Rails + Devise), dotnet/eShop (HttpClient calls between services) and
gitea (Go, 3.4k files). JS/TS graphs were compared before and after the multi-language core on 36 projects:
identical.

## Strings, constants and HTTP calls

Route prefixes and URLs are evaluated, not just read as literals: string variables and fields, module and class
constants (also imported from other files: `settings.API_V1_STR`, `ApiRoutes.Users`), Spring `${key:default}`
from application.properties/yml, interpolations (`f"{base}/x"`, `$"{base}x"`, `"#{base}"`, `"$base"`,
`"\(base)"`), `+` concatenation and `fmt.Sprintf` / `String.format` / `format!`. Unknown parts become
parameters; a prefix nobody can resolve (an environment variable) is dropped rather than invented.

HTTP calls of every language are linked to the routes they reach: clients (HttpClient, requests/httpx,
RestTemplate, net/http, Guzzle/Http::, reqwest, Faraday...) and tests (TestClient, Django's client, MockMvc,
httptest, Rails request specs, Laravel `getJson`), so `nimblegraph tests` and `impact` see them as e2e callers.

## Known gaps / next

- URLs assembled by helper functions (`GetAllCatalogItemsUri(base, page)`) or values read at runtime.
- Routes built at runtime (netbox's `include(get_model_urls(...))`), Laravel `->only()`/`->except()` chained after
  `Route::resource`, Actix `web::scope()` prefixes, Spring WebTestClient chains (`.get().uri("/x")`).
- Every build re-runs the assembly over all files; on a 3k-file repository an edit costs a few seconds.
- A WASM (web-tree-sitter) engine for platforms where the native tree-sitter wheels cannot load.
- More languages/frameworks: Elixir/Phoenix, Scala/Play, Dart/Flutter, Lua, NestJS-like DI in other ecosystems.

## Adding a language

- an entry in `nimblegraph/lang/__init__.py` (extensions, manifests, build folders, test pattern, `symbols`);
- `nimblegraph/lang/<language>.py`: an `Extractor` subclass (node types + hooks) and `extract()`;
- import/declaration regexes in `lang/fallback.py`;
- resolution in `lang/resolve.py` (`_links_<language>`) and the manifest in `lang/manifest.py`;
- a route detector in `nimblegraph/frameworks/` registered in `frameworks.DETECTORS`;
- a fixture in `tests/fixtures/<language>-<framework>/` and tests in `tests/test_langs.py`;
- a check on a real public project.
