# Contributing

Thanks for helping! Issues and pull requests are welcome, in English or Portuguese.

## Setup

```
git clone https://github.com/HiagoARJ/nimblegraph && cd nimblegraph
pip install -e .[dev]
nimblegraph setup          # Node LTS + typescript for the Node engine, shim on PATH, /nimblegraph skill
python -m pytest -q
```

Tests that need the Node engine are skipped when Node or `typescript` is missing (`tests/conftest.py`).

## Rules of thumb

- **Other languages** are parsed by the tree-sitter extractors in `nimblegraph/lang/` (one module per language on top
  of `lang/base.py`); they produce the same per-file record as the JS engines. See "Adding a language" in
  [ROADMAP.md](ROADMAP.md).
- **Engine parity.** JS/TS files are parsed by two engines that must produce the same record per file:
  `nimblegraph/node/extract.mjs` (TypeScript compiler API) and `nimblegraph/parse/` (pure Python). Change both together;
  `tests/test_parse.py::test_parity_node_python` and `tests/test_resolve.py::test_engines_build_same_graph` check it.
- **Bump `EXTRACTOR_VERSION`** in `nimblegraph/build.py` whenever the per-file record changes, so cached parses are
  discarded.
- **Fixtures.** Each scenario is a small project in `tests/fixtures/<name>/`. Prefer adding a fixture over mocking.
- **Do not write into real projects while testing.** Set `NIMBLEGRAPH_INDEX_DIR` to a temp folder and the index goes
  there instead of `<project>/.nimblegraph/`.
- **UI.** The UI lives in `nimblegraph/ui` (React + Vite). After changing it run `npm run build` there and commit
  `nimblegraph/ui/dist/`: it ships prebuilt so users don't need Node.
- **Output is for an LLM.** CLI answers are compact ASCII with English labels and must respect `--budget`.

## Pull requests

- Keep them focused; include a test for behavior changes.
- `python -m pytest -q` must pass; CI runs it on Linux and Windows.
- New languages and frameworks: see [ROADMAP.md](ROADMAP.md) for the checklist.
- User-visible changes get a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
- Be kind: the [Code of Conduct](CODE_OF_CONDUCT.md) applies to issues, pull requests and reviews.
