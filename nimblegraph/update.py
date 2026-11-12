"""`nimblegraph todo` / `nimblegraph update`: which notes are missing or stale, as a batch ready for `nimblegraph describe --batch`,
and `--ai`, which runs Claude Code headless (`claude -p`) to write them."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from nimblegraph import tokens as tk
from nimblegraph.notes import SKIP_LAYERS, Notes
from nimblegraph.query import Graph
from nimblegraph.render import Out

IMPORTANCE = {
    "route": 0, "api-route": 0, "controller": 0, "page": 0, "entry": 0,
    "layout": 1, "service": 1, "middleware": 1, "hook": 1, "api-client": 1,
    "component": 2, "store": 2, "repository": 2, "model": 2,
    "util": 3, "module": 3, "type": 4,
}
BATCH_FORMAT = ('[{"file": "...", "title": "...", "summary": "...", "resp": "...", "scope": "...", '
                '"flow": "...", "tags": ["..."]}]')

AI_PROMPT = """You are writing nimblegraph notes ("fichas") for this project.
1. Run: {first}
2. Read ONLY the files it lists (Read tool). Do not explore anything else.
3. Save the notes of all of them in ONE command, the JSON on a single line in single quotes:
   nimblegraph describe --json '{fmt}'
   (inside the texts avoid single quotes; use plain double quotes only as JSON delimiters)
   Limits in tokens (about chars/3.5): title 12, summary 40, resp 30, scope 12, flow 40, at most 6 tags.
   title = what the file is; summary = what it does; resp = what is and is not its responsibility;
   scope = the feature/domain in one or two words; flow = its steps, e.g. "req > validate > service.create > 201".
{chain}4. If a note is rejected, shorten it and run describe again for that file only.
Write the note texts in {language}. Finish with one line: how many notes were saved."""

CHAIN_STEP = """   Then, if the CHAIN line says missing or stale, describe the whole chain:
   nimblegraph describe --chain "{target}" --title "..." --summary "..." --flow "..."   (limits 12 / 60 / 80)
"""


def todo_items(g: Graph, notes: Notes, files=None) -> list[str]:
    """Files without a note or with a stale one, most important first (routes, controllers, pages, by importers)."""
    pool = files if files is not None else g.files
    items = [p for p in pool if g.layer(p) not in SKIP_LAYERS and (p not in notes.files or notes.stale(p))]
    return sorted(items, key=lambda p: (IMPORTANCE.get(g.layer(p), 3), -len(g.importers(p)), p))


def batch_lines(o: Out, g: Graph, notes: Notes, items: list[str], detail: int) -> None:
    for p in items:
        f = g.files[p]
        state = ""
        if p in notes.files:
            state = f" STALE was: {notes.files[p].get('title', '')}" if detail >= 1 else " STALE"
        o.add(f" {o.path(p)} [{g.layer(p)}] ~{tk.fmt(f.tokens)} tok, {len(g.importers(p))} importers{state}")
        if detail >= 2:
            bits = []
            exported = [s["name"] for s in g.symbols.get(p, []) if s["exported"] and not s["parent"]]
            if exported:
                bits.append("exports " + ",".join(exported[:8]) + ("..." if len(exported) > 8 else ""))
            routes = g.routes_of_file(p)
            if routes:
                bits.append("routes " + ",".join(r.label for r in routes[:4]) + ("..." if len(routes) > 4 else ""))
            deps = sorted({e.dst for e in g.out.get(p, []) if e.kind in ("import", "dynamic")})
            if deps and detail >= 3:
                bits.append("uses " + ", ".join(o.path(d) for d in deps[:6]) + ("..." if len(deps) > 6 else ""))
            if bits:
                o.add("  " + " | ".join(bits))


def todo_view(g: Graph, notes: Notes, limit: int, detail: int = 3) -> Out:
    items = todo_items(g, notes)
    total = len([p for p in g.files if g.layer(p) not in SKIP_LAYERS])
    stale = sum(1 for p in items if p in notes.files)
    o = Out("todo", stats=f"{total - len(items)}/{total} described | {len(items) - stale} missing, {stale} stale")
    shown = items[:limit]
    if not shown:
        o.add("every file has an up-to-date note")
        return o
    o.add(f"DESCRIBE {len(shown)}" + (f" of {len(items)}" if len(items) > len(shown) else ""))
    batch_lines(o, g, notes, shown, detail)
    o.add(f"NEXT read them, then: nimblegraph describe --json '{BATCH_FORMAT}'")
    o.read_cost = sum(g.files[p].tokens for p in shown)
    return o


def changes_view(g: Graph, notes: Notes, changes: dict, fresh: bool, chain_states: dict[str, str], detail: int = 3) -> Out:
    added, removed, changed = changes.get("added", []), changes.get("removed", []), changes.get("changed", [])
    o = Out("update", stats=(f"+{len(added)} -{len(removed)} ~{len(changed)}" if fresh else "no changes since last build"))
    if not fresh and (added or removed or changed):
        o.add("LAST BUILD WITH CHANGES")
    for label, items in (("ADDED", added), ("REMOVED", removed), ("CHANGED", changed)):
        if items:
            o.add(f"{label} " + ", ".join(o.path(p) for p in items[:40]) + (f" +{len(items) - 40}" if len(items) > 40 else ""))
    ra, rr = changes.get("routes_added", []), changes.get("routes_removed", [])
    if ra or rr:
        o.add("ROUTES " + ", ".join([f"+{r}" for r in ra] + [f"-{r}" for r in rr]))
    stale_now = sorted(p for p in changed + added if p in notes.files and notes.stale(p))
    if stale_now:
        o.add("STALE NOTES " + ", ".join(o.path(p) for p in stale_now))
    gone = sorted(p for p in removed if p in notes.files)
    if gone:
        o.add("NOTES OF REMOVED FILES " + ", ".join(gone))
    bad_chains = {k: s for k, s in chain_states.items() if s != "ok"}
    if bad_chains:
        o.add("STALE CHAINS " + ", ".join(f"{k} {s}" for k, s in sorted(bad_chains.items())))
    todo = todo_items(g, notes)
    o.add(f"NOTES {len(notes.files)} | {len(todo)} files missing or stale -> nimblegraph todo | nimblegraph update <target>")
    o.read_cost = sum(g.files[p].tokens for p in changed + added if p in g.files)
    return o


def target_view(g: Graph, notes: Notes, target: str, files: set[str], chain_key: str, chain_state: str,
                detail: int = 3) -> Out:
    items = todo_items(g, notes, files)
    o = Out(f"update {target}", stats=f"{len(files)} files in chain | {len(items)} to describe | chain note: {chain_state}")
    if items:
        o.add(f"DESCRIBE {len(items)}")
        batch_lines(o, g, notes, items, detail)
        o.add(f"NEXT read them, then: nimblegraph describe --json '{BATCH_FORMAT}'")
    else:
        o.add("every file of the chain has an up-to-date note")
    if chain_state != "ok":
        o.add(f"CHAIN {chain_key}: {chain_state} -> nimblegraph describe --chain \"{chain_key}\" --title .. --summary .. --flow ..")
    o.read_cost = sum(g.files[p].tokens for p in items)
    return o


# ---------------------------------------------------------------------- Claude Code headless

def _version_key(p: Path) -> tuple:
    m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", str(p))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_claude() -> str | None:
    """`claude` on PATH, then the VS Code extension binary (newest), then ~/.local/bin."""
    exe = shutil.which("claude")
    if exe:
        return exe
    home = Path.home()
    names = ("claude.exe", "claude") if os.name == "nt" else ("claude",)
    cands = []
    for ext_dir in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        base = home / ext_dir / "extensions"
        if base.is_dir():
            for d in base.glob("anthropic.claude-code-*"):
                for n in names:
                    b = d / "resources" / "native-binary" / n
                    if b.is_file():
                        cands.append(b)
    if cands:
        return str(max(cands, key=_version_key))
    for n in names:
        b = home / ".local" / "bin" / n
        if b.is_file():
            return str(b)
    return None


def ai_command(claude: str, target: str | None, language: str, limit: int = 15) -> list[str]:
    first = f'nimblegraph update "{target}"' if target else f"nimblegraph todo --limit {limit}"
    prompt = AI_PROMPT.format(first=first, fmt=BATCH_FORMAT, language=language,
                              chain=CHAIN_STEP.format(target=target) if target else "")
    return [claude, "-p", prompt, "--allowedTools", "Read", "Grep", "Glob", "Bash(nimblegraph:*)"]


def manual_instruction(target: str | None) -> str:
    t = f" {target}" if target else ""
    return (f"Claude Code was not found. In a Claude Code session in this project, run: /nimblegraph update{t}\n"
            f"(or ask: run `nimblegraph update{t}`, read the listed files and save their notes with nimblegraph describe --batch)")


def run_ai(root: Path, target: str | None, language: str, dry_run: bool = False) -> int:
    claude = find_claude()
    if not claude:
        print(manual_instruction(target))
        return 3
    cmd = ai_command(claude, target, language)
    if dry_run:
        print(subprocess.list2cmdline(cmd))
        return 0
    print(f"running Claude Code headless in {root} ...", flush=True)
    env = dict(os.environ)
    env.setdefault("MSYS_NO_PATHCONV", "1")
    try:
        return subprocess.run(cmd, cwd=root, env=env, check=False).returncode
    except OSError as e:
        print(f"could not start Claude Code: {e}")
        return 3
