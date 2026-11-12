"""Project root discovery and settings (defaults + .nimblegraph/config.toml)."""

from __future__ import annotations

import copy
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from nimblegraph.lang import ALL_EXTS

DIR_NAME = ".nimblegraph"

DEFAULTS: dict = {
    "ignore_dirs": [
        "node_modules", "dist", "build", ".next", "out", "coverage", ".turbo", ".expo",
        ".git", DIR_NAME, ".vercel", ".output", ".nuxt", ".svelte-kit", ".cache",
        ".parcel-cache", ".yarn", ".pnpm-store",
        "__pycache__", "venv", "site-packages", "Pods", "DerivedData", "cmake-build-debug", "cmake-build-release",
    ],
    "ignore": [],  # extra gitignore-style patterns
    "extensions": list(ALL_EXTS),  # every supported language; narrow it in config.toml (e.g. only JS/TS)
    "max_file_kb": 1500,
    "budget": 1500,
    "notes_language": "Brazilian Portuguese",  # language Claude writes notes in (nimblegraph update --ai)
    "depth": 4,
    "entries": [],
    "aliases": {},  # {"@x": "src/x"}
    "layers": {},  # {"glob": "layer"}
}

CONFIG_TEMPLATE = """\
# nimblegraph settings (all optional)
# budget = 1500          # default token budget for answers
# depth = 4              # default traversal depth
# ignore = ["scripts/legacy/**"]
# entries = ["src/worker.ts"]

# [aliases]               # import aliases the tsconfig does not know about
# "@app" = "src/app"

# [layers]                # glob -> layer override
# "src/lib/http/**" = "api-client"
"""


@dataclass
class Config:
    root: Path
    settings: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))

    @property
    def dir(self) -> Path:
        # NIMBLEGRAPH_INDEX_DIR keeps the index outside the project (read-only checkouts, tests)
        override = os.environ.get("NIMBLEGRAPH_INDEX_DIR")
        if override:
            safe = re.sub(r"[^\w.-]+", "_", str(self.root)).strip("_")
            return Path(override) / safe
        return self.root / DIR_NAME

    @property
    def db_path(self) -> Path:
        return self.dir / "graph.db"

    @property
    def notes_path(self) -> Path:
        return self.dir / "notes.json"

    def __getitem__(self, key):
        return self.settings[key]


class RootError(Exception):
    pass


WORKSPACE_MARKERS = ("pnpm-workspace.yaml", "turbo.json", "lerna.json", "nx.json", "rush.json")


def _too_broad(p: Path) -> bool:
    """Never pick the home folder or a drive root as a project (dotfiles repos live there)."""
    home = Path.home().resolve()
    return p == home or p.parent == p or p in home.parents


def _is_workspace_root(p: Path) -> bool:
    if any((p / m).is_file() for m in WORKSPACE_MARKERS):
        return True
    pj = p / "package.json"
    if pj.is_file():
        try:
            return "workspaces" in json.loads(pj.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
    return False


def find_root(start: Path | None = None) -> Path:
    """.nimblegraph upwards; else the git root; else the monorepo workspace root; else the nearest
    package.json; else start. The home folder and drive roots are never chosen."""
    start = (start or Path.cwd()).resolve()
    chain = [p for p in (start, *start.parents) if not _too_broad(p)]
    if not chain:
        raise RootError(f"{start} is not inside a project; cd into one or pass --root")
    for p in chain:
        if (p / DIR_NAME).is_dir():
            return p
    git = next((p for p in chain if (p / ".git").exists()), None)
    if git:
        return git
    workspace = next((p for p in reversed(chain) if _is_workspace_root(p)), None)
    if workspace:
        return workspace
    pkg = next((p for p in chain if (p / "package.json").is_file()), None)
    if pkg:
        return pkg
    from nimblegraph.lang import MANIFESTS, MANIFEST_EXTS
    other = next((p for p in chain if any((p / m).is_file() for m in MANIFESTS)
                  or any(next(p.glob(f"*{e}"), None) for e in MANIFEST_EXTS)), None)
    return other or start


def load(root: Path | str | None = None) -> Config:
    root = Path(root).resolve() if root else find_root()
    settings = copy.deepcopy(DEFAULTS)
    cfg_file = Config(root=root).dir / "config.toml"
    if not cfg_file.is_file():
        cfg_file = root / DIR_NAME / "config.toml"
    if cfg_file.is_file():
        with cfg_file.open("rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(settings.get(k), dict):
                settings[k].update(v)
            elif k == "ignore_dirs" and isinstance(v, list):
                settings[k] = list(dict.fromkeys(settings[k] + v))
            else:
                settings[k] = v
    return Config(root=root, settings=settings)


def user_cache_dir() -> Path:
    d = Path.home() / DIR_NAME
    d.mkdir(exist_ok=True)
    return d
