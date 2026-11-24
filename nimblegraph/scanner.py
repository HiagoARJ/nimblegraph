"""Walks the project, honours .gitignore files (nested too) and hashes source files."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from nimblegraph import lang as langs
from nimblegraph.config import Config
from nimblegraph.tokens import from_size


def content_hash(data: bytes) -> str:
    """sha1 of the content with CRLF read as LF: checkouts with and without core.autocrlf give the same hash,
    so notes committed from one OS are not stale on another."""
    return hashlib.sha1(data.replace(b"\r\n", b"\n")).hexdigest()

# files that change how imports resolve; any change invalidates the parse cache
CONFIG_NAMES = ("package.json", "pnpm-workspace.yaml")
CONFIG_PREFIXES = ("tsconfig", "jsconfig")
PACKAGE_LEVEL_DIRS = {"dist", "build", "out", "coverage"}
LANG_BUILD_DIRS = langs.build_dirs() - PACKAGE_LEVEL_DIRS  # target/, vendor/, bin/, obj/...: only at their package root


@dataclass
class FileInfo:
    path: str  # root-relative, posix
    hash: str
    mtime: int
    size: int
    loc: int
    tokens: int


@dataclass
class ScanResult:
    files: dict[str, FileInfo]
    configs: dict[str, str]  # path -> sha1 of tsconfig/jsconfig/package.json/pnpm-workspace
    packages: list[str]  # dirs (posix, '' = root) that contain a package.json
    manifests: dict[str, list[str]] = None  # dir -> manifest files of the other languages (go.mod, pom.xml...)

    def config_fingerprint(self) -> str:
        h = hashlib.sha1()
        for p in sorted(self.configs):
            h.update(f"{p}={self.configs[p]}\n".encode())
        return h.hexdigest()


def _is_source(name: str, exts: tuple[str, ...]) -> bool:
    low = name.lower()
    if not low.endswith(exts):
        return False
    return not (low.endswith((".d.ts", ".d.mts", ".d.cts")) or low.endswith(".min.js"))


def _read_ignore(path: Path) -> pathspec.PathSpec | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines) if lines else None


def scan(cfg: Config, prev: dict[str, tuple[int, int, str, int, int]] | None = None) -> ScanResult:
    """prev: path -> (mtime_ns, size, hash, loc, tokens) from the last build; unchanged files are not re-read."""
    root = cfg.root
    prev = prev or {}
    exts = tuple(e.lower() for e in cfg["extensions"])
    ignore_dirs = set(cfg["ignore_dirs"])
    max_size = int(cfg["max_file_kb"]) * 1024
    extra = pathspec.GitIgnoreSpec.from_lines(cfg["ignore"]) if cfg["ignore"] else None

    files: dict[str, FileInfo] = {}
    configs: dict[str, str] = {}
    packages: list[str] = []
    manifests: dict[str, list[str]] = {}
    # stack of (dir_rel, spec) for .gitignore files that apply to the current directory
    specs: dict[str, pathspec.PathSpec] = {}

    def ignored(rel: str, is_dir: bool) -> bool:
        target = rel + "/" if is_dir else rel
        if extra and extra.match_file(target):
            return True
        # check every .gitignore from root to the parent directory
        parts = rel.split("/")
        for i in range(len(parts)):
            base = "/".join(parts[:i])
            spec = specs.get(base)
            if spec is None:
                continue
            sub = "/".join(parts[i:]) + ("/" if is_dir else "")
            if spec.match_file(sub):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        if ".gitignore" in filenames:
            spec = _read_ignore(Path(dirpath) / ".gitignore")
            if spec:
                specs[rel_dir] = spec
        keep = []
        lang_out = langs.build_dirs_at(filenames) if LANG_BUILD_DIRS & set(dirnames) else set()
        for d in dirnames:
            if d in ignore_dirs or d.startswith(".") and d not in (".storybook",):
                # build/test output folders only at a package root: src/products/coverage is source code
                if not (d in PACKAGE_LEVEL_DIRS and rel_dir and not (Path(dirpath) / "package.json").is_file()
                        and d not in lang_out):
                    continue
            elif d in lang_out:
                continue
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if not ignored(rel, True):
                keep.append(d)
        dirnames[:] = sorted(keep)

        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            low = name.lower()
            is_cfg = low in CONFIG_NAMES or (low.startswith(CONFIG_PREFIXES) and low.endswith(".json"))
            is_manifest = not is_cfg and bool(langs.manifest_langs(name))
            is_src = _is_source(name, exts)
            if not (is_cfg or is_manifest or is_src):
                continue
            if ignored(rel, False):
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            if is_cfg or is_manifest:
                try:
                    configs[rel] = hashlib.sha1(full.read_bytes()).hexdigest()
                except OSError:
                    pass
                if low == "package.json":
                    packages.append(rel_dir)
                elif is_manifest:
                    manifests.setdefault(rel_dir, []).append(name)
                if not is_src:  # setup.py is a manifest and a source file
                    continue
            if st.st_size > max_size:
                continue
            old = prev.get(rel)
            if old and old[0] == st.st_mtime_ns and old[1] == st.st_size:
                files[rel] = FileInfo(rel, old[2], old[0], old[1], old[3], old[4])
                continue
            try:
                data = full.read_bytes()
            except OSError:
                continue
            text = data.decode("utf-8", errors="replace")
            files[rel] = FileInfo(
                path=rel,
                hash=content_hash(data),
                mtime=st.st_mtime_ns,
                size=st.st_size,
                loc=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
                tokens=from_size(len(text)),
            )
    return ScanResult(files=files, configs=configs, packages=packages, manifests=manifests)
