"""Notes ("fichas") written by Claude Code: per file (and per symbol, `file#Symbol`) and per chain.
Stored in .nimblegraph/notes.json (versionable); a note is stale when the file hash changed since it was written,
a chain note when a file of the chain changed or files entered/left the chain."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from nimblegraph import tokens as tk

FILE_LIMITS = {"title": 12, "summary": 40, "resp": 30, "scope": 12, "flow": 40}
CHAIN_LIMITS = {"title": 12, "summary": 60, "flow": 80}
MAX_TAGS = 6
FILE_FIELDS = ("title", "summary", "resp", "scope", "flow", "tags")
CHAIN_FIELDS = ("title", "summary", "flow")
SKIP_LAYERS = {"test", "config"}


class NoteError(ValueError):
    pass


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


def chain_key(term: str) -> str:
    t = " ".join(term.strip().split())
    m = re.match(r"^([A-Za-z]+)\s+(/\S*)$", t)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    return t if t.startswith("/") else t.lower()


def validate(fields: dict, limits: dict) -> list[str]:
    """Problems with a note, e.g. 'summary: 52 tok > 40 (12 over)'."""
    errors = []
    for k, limit in limits.items():
        v = fields.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            errors.append(f"{k}: must be text")
            continue
        n = tk.estimate(v)
        if n > limit:
            errors.append(f"{k}: ~{n} tok > {limit} ({n - limit} over)")
    tags = fields.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append("tags: must be a list of words")
        elif len(tags) > MAX_TAGS:
            errors.append(f"tags: {len(tags)} > {MAX_TAGS}")
    unknown = set(fields) - set(limits) - {"tags"}
    if unknown:
        errors.append("unknown fields: " + ", ".join(sorted(unknown)))
    return errors


def note_cost(note: dict) -> int:
    return tk.estimate(" ".join(str(note.get(k, "")) for k in FILE_FIELDS if note.get(k)))


class Notes:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = path
        data = data or {}
        self.files: dict[str, dict] = data.get("files", {})
        self.chains: dict[str, dict] = data.get("chains", {})
        self.hashes: dict[str, str] = {}  # current file hashes (from the graph)

    @classmethod
    def load(cls, path: Path) -> "Notes":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "files": dict(sorted(self.files.items())), "chains": dict(sorted(self.chains.items()))}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".notes.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)

    def bind(self, graph) -> "Notes":
        """Current hashes, so staleness can be computed."""
        self.hashes = {p: f.hash for p, f in graph.files.items()}
        self.graph = graph
        return self

    # ------------------------------------------------------------ file notes
    def get(self, path: str, symbol: str | None = None) -> dict | None:
        if symbol:
            for key in (f"{path}#{symbol}", f"{path}#{symbol.split('.')[0]}"):
                if key in self.files:
                    return self.files[key]
            return None
        return self.files.get(path)

    def stale(self, path: str, symbol: str | None = None) -> bool:
        note = self.get(path, symbol) if symbol else self.files.get(path)
        if not note:
            return False
        cur = self.hashes.get(path)
        return bool(cur) and note.get("hash") != cur

    def set_file(self, path: str, fields: dict, current_hash: str | None, symbol: str | None = None,
                 by: str | None = None) -> dict:
        """`by` records who wrote the note (e.g. "local:<model>"); notes written by Claude have none."""
        fields = {k: v for k, v in fields.items() if v is not None}
        if "tags" in fields and isinstance(fields["tags"], str):
            fields["tags"] = [t.strip() for t in fields["tags"].split(",") if t.strip()]
        errors = validate(fields, FILE_LIMITS)
        if errors:
            raise NoteError("; ".join(errors))
        key = f"{path}#{symbol}" if symbol else path
        note = dict(self.files.get(key, {}))
        note.update(fields)
        if not note.get("title"):
            raise NoteError("title is required")
        note["hash"] = current_hash
        note["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        note.pop("by", None)
        if by:
            note["by"] = by
        self.files[key] = note
        return note

    def missing(self, paths, graph=None) -> list[str]:
        g = graph or getattr(self, "graph", None)
        out = []
        for p in paths:
            if g is not None and g.layer(p) in SKIP_LAYERS:
                continue
            if p not in self.files or self.stale(p):
                out.append(p)
        return out

    # ------------------------------------------------------------ chains
    def set_chain(self, key: str, fields: dict, files: dict[str, str], by: str | None = None) -> dict:
        fields = {k: v for k, v in fields.items() if v is not None}
        errors = validate(fields, CHAIN_LIMITS)
        if errors:
            raise NoteError("; ".join(errors))
        note = dict(self.chains.get(key, {}))
        note.update(fields)
        if not note.get("title"):
            raise NoteError("title is required")
        note["files"] = dict(sorted(files.items()))
        note["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        note.pop("by", None)
        if by:
            note["by"] = by
        self.chains[key] = note
        return note

    def find_chain(self, term: str) -> tuple[str, dict] | None:
        key = chain_key(term)
        if key in self.chains:
            return key, self.chains[key]
        for k in (key + "s", key[:-1] if key.endswith("s") else None, key + "es"):
            if k and k in self.chains:
                return k, self.chains[k]
        return None

    def chain_state(self, key: str, current_files: set[str]) -> str:
        """'ok' | 'missing' | 'stale (2 changed, 1 added)'."""
        note = self.chains.get(key)
        if not note:
            return "missing"
        stored = note.get("files", {})
        changed = sum(1 for f, h in stored.items() if f in self.hashes and self.hashes[f] != h)
        removed = sum(1 for f in stored if f not in current_files)
        added = len(current_files - set(stored))
        parts = [f"{n} {w}" for n, w in ((changed, "changed"), (added, "added"), (removed, "removed")) if n]
        return f"stale ({', '.join(parts)})" if parts else "ok"

    # ------------------------------------------------------------ views interface
    def text(self, note: dict, detail: int) -> str:
        title = note.get("title", "")
        if detail >= 2 and note.get("summary"):
            return f"{title}: {note['summary']}"
        return title

    def suffix(self, path: str, symbol: str | None = None, detail: int = 3) -> str:
        """'* - title: summary' for the line of a file (star = stale)."""
        note = self.get(path, symbol) if symbol else None
        note = note or self.files.get(path)
        if not note:
            return ""
        star = "*" if self.stale(path) else ""
        return f"{star} - {self.text(note, detail)}"

    def search(self, text: str) -> list[tuple[str, dict]]:
        low = text.lower()
        out = []
        for key, note in sorted(self.files.items()):
            blob = " ".join(str(note.get(k, "")) for k in FILE_FIELDS).lower()
            if low in blob:
                out.append((key, note))
        return out

    def with_scope(self, words: list[set[str]]) -> set[str]:
        """Files whose note scope/tags match every word (any singular/plural variant)."""
        out = set()
        for key, note in self.files.items():
            raw = [str(note.get("scope", ""))] + [str(t) for t in note.get("tags", []) or []]
            # "propostas pme" matches the term "propostas pme" and also "pme": compare word by word
            from nimblegraph.query import fold, tokens
            vals = {fold(v) for v in raw} | {w for v in raw for w in tokens(v)}
            if words and all(any(v in vals for v in vs) for vs in words):
                out.add(key.split("#")[0])
        return out

    def counts(self, files) -> dict:
        files = list(files)
        described = [p for p in files if p in self.files]
        return {"described": len(described), "stale": sum(1 for p in described if self.stale(p)),
                "orphans": sum(1 for k in self.files if k.split("#")[0] not in self.hashes)}


def load_for(cfg, graph=None) -> Notes:
    n = Notes.load(cfg.notes_path)
    if graph is not None:
        n.bind(graph)
    return n
