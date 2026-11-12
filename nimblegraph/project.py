"""`nimblegraph init`: .nimblegraph/ + config.toml, graph.db in .gitignore, the CLAUDE.md snippet and the optional hook."""

from __future__ import annotations

import json
from pathlib import Path

from nimblegraph.config import CONFIG_TEMPLATE, Config

MARKER = "<!-- nimblegraph -->"
CLAUDE_MD_SNIPPET = """{marker}
## nimblegraph
Indexed by nimblegraph (`nimblegraph`). Before opening files to understand a feature, run `nimblegraph analyze <term>`
(or `nimblegraph endpoint "<METHOD /path>"`) and read only the files it points to. After reading or editing a file
with no note (or marked `*`), save one with `nimblegraph describe` (notes in {language}). Before finishing a change, run
`nimblegraph check --git` and fix new architecture errors (rules: `nimblegraph rules`; contexts: `nimblegraph ddd`). Details: /nimblegraph.
"""
GITIGNORE_LINES = (".nimblegraph/graph.db", ".nimblegraph/graph.db-*")
HOOK_COMMAND = "nimblegraph build --quiet"
GUARD_COMMAND = "nimblegraph check --hook"


def _append_once(path: Path, marker: str, text: str) -> bool:
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if marker in current:
        return False
    sep = "" if not current or current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
    path.write_text(current + sep + text, encoding="utf-8", newline="\n")
    return True


def init(cfg: Config, claude_md: bool = False, hook: bool = False, guard: bool = False) -> list[str]:
    done: list[str] = []
    cfg.dir.mkdir(parents=True, exist_ok=True)
    toml = cfg.dir / "config.toml"
    if not toml.is_file():
        toml.write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
        done.append(f"created {toml}")
    gi = cfg.root / ".gitignore"
    current = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    missing = [line for line in GITIGNORE_LINES if line not in current.splitlines()]
    if not (cfg.root / ".git").exists():
        missing = []
        done.append("not a git repository: .gitignore left untouched")
    if missing:
        sep = "" if not current or current.endswith("\n") else "\n"
        gi.write_text(current + sep + "# nimblegraph index (rebuilt by `nimblegraph build`; notes.json is versioned)\n"
                      + "\n".join(missing) + "\n", encoding="utf-8", newline="\n")
        done.append(f"added {', '.join(missing)} to .gitignore")
    if claude_md:
        md = cfg.root / "CLAUDE.md"
        snippet = CLAUDE_MD_SNIPPET.format(marker=MARKER, language=cfg["notes_language"])
        if _append_once(md, MARKER, snippet):
            done.append(f"appended the nimblegraph section to {md}")
        else:
            done.append("CLAUDE.md already has the nimblegraph section")
    if hook:
        done.append(install_hook(cfg.root))
    if guard:
        done.append(install_hook(cfg.root, GUARD_COMMAND, "architecture guard"))
    return done


def install_hook(root: Path, command: str = HOOK_COMMAND, label: str = "rebuild") -> str:
    """PostToolUse hook in <project>/.claude/settings.json: rebuild the index (or check the architecture rules)
    after Edit/Write."""
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    except ValueError:
        return f"{settings} is not valid JSON; hook not installed"
    hooks = data.setdefault("hooks", {}).setdefault("PostToolUse", [])
    for entry in hooks:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return f"the {label} hook is already installed"
    hooks.append({"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": command}]})
    settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return f"installed the {label} hook in {settings}"
