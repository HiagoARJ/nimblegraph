"""The engine for every non-JS language: tree-sitter extractors (nimblegraph/lang/<module>.py), with the regex
fallback when tree-sitter is unavailable or a file does not parse."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from nimblegraph import lang as langs
from nimblegraph.lang import base, fallback


def grammar_ok(lg: langs.Language) -> bool:
    mod = _module(lg)
    return mod is not None and base.parser(_grammar(mod, lg)) is not None


def _module(lg: langs.Language):
    try:
        return importlib.import_module(f"nimblegraph.lang.{lg.module}")
    except ImportError:
        return None


def _grammar(mod, lg: langs.Language) -> str:
    g = getattr(mod, "GRAMMARS", None)
    return g.get(lg.name, lg.name) if g else lg.name


class TreeSitterEngine:
    name = "tree-sitter"

    def __init__(self):
        self.used: dict[str, str] = {}  # language -> "tree-sitter" | "regex"

    def describe(self) -> str:
        if not self.used:
            return "tree-sitter"
        return "tree-sitter (" + ", ".join(f"{langs.BY_NAME[k].label}{'' if v == 'tree-sitter' else ' regex'}"
                                           for k, v in sorted(self.used.items())) + ")"

    def extract(self, root: Path, files: list[str]):
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(limit, 20000))  # the walker recurses per tree level; generated code nests deep
        try:
            return self._extract(root, files)
        finally:
            sys.setrecursionlimit(limit)

    def _extract(self, root: Path, files: list[str]):
        from nimblegraph.engine import ExtractResult
        res = ExtractResult(header={"tree-sitter": True})
        failed_langs: set[str] = set()
        for rel in files:
            lg = langs.of(rel)
            if lg is None or lg.module is None:
                continue
            try:
                src = (root / rel).read_bytes()
            except OSError:
                res.failed.append(rel)
                continue
            mod = _module(lg)
            rec = None
            if mod is not None and lg.name not in failed_langs:
                try:
                    rec = mod.extract(src, rel, lg.name) if getattr(mod, "MULTI", False) else mod.extract(src, rel)
                    self.used.setdefault(lg.name, "tree-sitter")
                except base.GrammarUnavailable:  # regex for the rest of this language
                    failed_langs.add(lg.name)
                except Exception as e:  # noqa: BLE001 - an extractor bug (or a too-deep file) must not break the build
                    res.warning = f"tree-sitter extractor failed on {rel}: {type(e).__name__}: {e}"[:300]
            if rec is None:
                rec = fallback.extract(lg.name, src, rel)
                self.used[lg.name] = "regex" if lg.name in failed_langs else self.used.get(lg.name, "tree-sitter")
            res.records[rel] = rec
        if failed_langs:
            res.warning = ("tree-sitter not available for " + ", ".join(sorted(failed_langs)) +
                           " (pip install tree-sitter tree-sitter-language-pack; first use downloads the grammar); "
                           "used the regex fallback")
        return res
