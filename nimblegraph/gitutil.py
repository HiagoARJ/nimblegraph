"""Git helpers: changed files (working tree or since a base ref) and change frequency, across the git
repositories found at the project root and in its sub-folders (a folder of several repos, like a portal)."""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

SKIP = {"node_modules", ".git", ".nimblegraph", "dist", "build"}


def _git(repo: Path, *args: str, timeout: int = 60) -> list[str] | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def repos(root: Path, depth: int = 2) -> list[Path]:
    """The repository that contains root (if any) plus nested repositories up to `depth` levels below."""
    out: list[Path] = []
    top = _git(root, "rev-parse", "--show-toplevel")
    home = Path.home().resolve()
    # a dotfiles repository at the home folder is not the project's repository
    if top and Path(top[0]).resolve() not in (home, *home.parents):
        out.append(root)
    frontier = [root]
    for _ in range(depth):
        nxt = []
        for d in frontier:
            try:
                children = [c for c in d.iterdir() if c.is_dir() and c.name not in SKIP and not c.name.startswith(".")]
            except OSError:
                continue
            for c in children:
                if (c / ".git").exists():
                    out.append(c)
                nxt.append(c)
        frontier = nxt
    return sorted(set(out), key=lambda p: len(p.parts))


def _rel(root: Path, repo: Path, name: str) -> str:
    prefix = repo.relative_to(root).as_posix() if repo != root else ""
    return f"{prefix}/{name}" if prefix else name


def changed_files(root: Path, base: str | None = None) -> list[str]:
    """Files changed in the working tree (diff against HEAD + untracked), or since `base` (base...HEAD plus the
    working tree), as paths relative to root."""
    out: list[str] = []
    for repo in repos(root):
        cmds = [["diff", "--name-only", "--relative", "HEAD"], ["ls-files", "--others", "--exclude-standard"]]
        if base:
            cmds.insert(0, ["diff", "--name-only", "--relative", f"{base}...HEAD"])
        for cmd in cmds:
            for name in _git(repo, *cmd) or []:
                rel = _rel(root, repo, name)
                if rel not in out:
                    out.append(rel)
    return out


def churn(root: Path, months: int = 6) -> tuple[Counter, list[str]]:
    """Commits touching each file in the last `months` months (paths relative to root) and the repos read."""
    counts: Counter = Counter()
    used = []
    for repo in repos(root):
        lines = _git(repo, "log", f"--since={months} months ago", "--name-only", "--format=", "--no-renames",
                     "--relative", timeout=120)
        if lines is None:
            continue
        used.append(repo.relative_to(root).as_posix() if repo != root else ".")
        for name in lines:
            counts[_rel(root, repo, name)] += 1
    return counts, used


def head(root: Path) -> str | None:
    lines = _git(root, "rev-parse", "--short", "HEAD")
    return lines[0] if lines else None
