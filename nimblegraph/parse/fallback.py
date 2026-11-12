"""Last-resort regex extractor: imports and export names only, used when the parser fails on a file."""

from __future__ import annotations

import re
from pathlib import Path

RE_COMMENT = re.compile(r"/\*[\s\S]*?\*/|(?<![:\\])//[^\n]*")
RE_IMPORT = re.compile(r"^\s*import\s+(type\s+)?([\s\S]*?)\s*from\s*['\"]([^'\"]+)['\"]", re.M)
RE_SIDE = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"]", re.M)
RE_REEXPORT = re.compile(r"^\s*export\s+(type\s+)?(\*(?:\s+as\s+\w+)?|\{[^}]*\})\s*from\s*['\"]([^'\"]+)['\"]", re.M)
RE_REQUIRE = re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)")
RE_DYNAMIC = re.compile(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)")
RE_EXPORT_DECL = re.compile(
    r"^\s*export\s+(default\s+)?(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
    r"(function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M)


def _line(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _names(clause: str) -> list[list[str]]:
    out = []
    clause = clause.strip()
    m = re.match(r"([A-Za-z_$][\w$]*)\s*(,|$)", clause)
    if m:
        out.append(["default", m.group(1)])
    m = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if m:
        out.append(["*", m.group(1)])
    m = re.search(r"\{([^}]*)\}", clause)
    if m:
        for part in m.group(1).split(","):
            part = re.sub(r"^\s*type\s+", "", part).strip()
            if not part:
                continue
            bits = re.split(r"\s+as\s+", part)
            out.append([bits[0].strip(), bits[-1].strip()])
    return out


def extract_source(src: str, rel: str) -> dict:
    code = RE_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), src)
    imports = []
    for m in RE_IMPORT.finditer(code):
        imports.append({"spec": m.group(3), "kind": "type" if m.group(1) else "import",
                        "names": _names(m.group(2)), "line": _line(code, m.start())})
    for m in RE_SIDE.finditer(code):
        imports.append({"spec": m.group(1), "kind": "side-effect", "names": [], "line": _line(code, m.start())})
    for m in RE_REEXPORT.finditer(code):
        what = m.group(2)
        if what.startswith("*"):
            ns = re.search(r"as\s+(\w+)", what)
            kind, names = ("reexport", [["*", ns.group(1)]]) if ns else ("reexport-all", [])
        else:
            kind, names = "reexport", _names(what)
        imports.append({"spec": m.group(3), "kind": kind, "names": names, "line": _line(code, m.start())})
    for m in RE_REQUIRE.finditer(code):
        imports.append({"spec": m.group(1), "kind": "require", "names": [], "line": _line(code, m.start())})
    for m in RE_DYNAMIC.finditer(code):
        imports.append({"spec": m.group(1), "kind": "dynamic", "names": [], "line": _line(code, m.start())})
    exports = []
    for m in RE_EXPORT_DECL.finditer(code):
        kind = {"interface": "type", "type": "type", "enum": "type", "class": "class"}.get(m.group(2), "function"
                                                                                           if m.group(2).startswith("function") else "const")
        ex = {"name": "default" if m.group(1) else m.group(3), "kind": kind, "line": _line(code, m.start())}
        if m.group(1):
            ex["local"] = m.group(3)
        exports.append(ex)
    return {
        "path": rel, "imports": imports, "exports": exports, "symbols": [], "jsx": [], "calls": [],
        "route_objects": [], "dyn_locals": {}, "vars": {}, "consts": {}, "directives": [], "partial": True,
    }


def extract_path(root: Path, rel: str) -> dict:
    return extract_source((root / rel).read_text(encoding="utf-8", errors="replace"), rel)
