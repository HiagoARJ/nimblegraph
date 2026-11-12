# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private report instead:
[Report a vulnerability](https://github.com/HiagoARJ/nimblegraph/security/advisories/new).

Say what you ran, on which version or commit (`nimblegraph --version`), and what happened. Fixes go into the
latest version; older versions are not patched.

## What nimblegraph does on your machine

- **It reads your code and does not run it.** Files are parsed (TypeScript compiler API, tree-sitter or regex);
  the project's code, build scripts and tests are never executed.
- **It writes inside `.nimblegraph/`** in the project (or in `NIMBLEGRAPH_INDEX_DIR`). `nimblegraph init` also
  edits `.gitignore`, and with `--claude-md`, `--hook` or `--guard` it edits `CLAUDE.md` and the project's Claude
  Code settings. `nimblegraph setup` writes the command shim and `~/.claude/skills/nimblegraph/`.
- **`nimblegraph ui` listens on `127.0.0.1` only** and has no authentication: anything running on the same
  machine can reach it while it is open. Do not expose its port to a network.
- **`nimblegraph update --ai`** and the "Update descriptions with Claude" button start Claude Code (`claude -p`)
  with the tools `Read`, `Grep`, `Glob` and `Bash(nimblegraph:*)`. Parts of your code are then sent to Claude
  under your own Claude Code account.
- **`nimblegraph local`** sends file contents to the server set in `[local_ai] url`, by default Ollama on
  `127.0.0.1`. If you point it to another machine, your code goes there.
- **Network access otherwise** is limited to installing: `nimblegraph setup` downloads Node (through fnm),
  `typescript` (npm) and the tree-sitter grammars, which also download on first use.
