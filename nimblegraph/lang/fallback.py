"""Regex fallback for the tree-sitter languages (no wheel, a blocked native library, an offline first run or a
parser crash): imports and top-level declarations only, marked `partial`."""

from __future__ import annotations

import re

_I = re.M
IMPORTS: dict[str, list[tuple[re.Pattern, str]]] = {
    "python": [(re.compile(r"^\s*from\s+([.\w]+)\s+import\b", _I), "from"),
               (re.compile(r"^\s*import\s+([\w.]+)", _I), "import")],
    "go": [(re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([\w./-]+)"\s*$', _I), "import")],
    "java": [(re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", _I), "import")],
    "kotlin": [(re.compile(r"^\s*import\s+([\w.]+(?:\.\*)?)", _I), "import")],
    "csharp": [(re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;", _I), "using")],
    "php": [(re.compile(r"^\s*use\s+([\w\\]+)", _I), "use"),
            (re.compile(r"""^\s*(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+)['"]""", _I), "require")],
    "ruby": [(re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", _I), "require_relative"),
             (re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", _I), "require")],
    "rust": [(re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)", _I), "use"),
             (re.compile(r"^\s*(?:pub(?:\([\w ]+\))?\s+)?mod\s+(\w+)\s*;", _I), "mod")],
    "c": [(re.compile(r'^\s*#\s*include\s*([<"][^>"]+[>"])', _I), "include")],
    "cpp": [(re.compile(r'^\s*#\s*include\s*([<"][^>"]+[>"])', _I), "include")],
    "swift": [(re.compile(r"^\s*import\s+(\w+)", _I), "import")],
}
DECLS: dict[str, re.Pattern] = {
    "python": re.compile(r"^(?:async\s+)?(def|class)\s+(\w+)", _I),
    "go": re.compile(r"^(func|type)\s+(?:\([^)]*\)\s*)?(\w+)", _I),
    "java": re.compile(r"^\s*(?:public\s+|abstract\s+|final\s+)*(class|interface|enum|record)\s+(\w+)", _I),
    "kotlin": re.compile(r"^\s*(?:\w+\s+)*(class|interface|object|fun)\s+(\w+)", _I),
    "csharp": re.compile(r"^\s*(?:\w+\s+)*(class|interface|struct|record|enum)\s+(\w+)", _I),
    "php": re.compile(r"^\s*(?:abstract\s+|final\s+)*(class|interface|trait|function)\s+(\w+)", _I),
    "ruby": re.compile(r"^\s*(class|module|def)\s+([\w:]+)", _I),
    "rust": re.compile(r"^\s*(?:pub(?:\([\w ]+\))?\s+)?(fn|struct|enum|trait|type)\s+(\w+)", _I),
    "c": re.compile(r"^(struct|enum|typedef)\s+(?:struct\s+)?(\w+)", _I),
    "cpp": re.compile(r"^\s*(class|struct|namespace)\s+(\w+)", _I),
    "swift": re.compile(r"^\s*(?:\w+\s+)*(class|struct|protocol|enum|func|actor)\s+(\w+)", _I),
}
PACKAGE: dict[str, re.Pattern] = {
    "go": re.compile(r"^package\s+(\w+)", _I),
    "java": re.compile(r"^\s*package\s+([\w.]+)", _I),
    "kotlin": re.compile(r"^\s*package\s+([\w.]+)", _I),
    "csharp": re.compile(r"^\s*namespace\s+([\w.]+)", _I),
    "php": re.compile(r"^\s*namespace\s+([\w\\]+)", _I),
}
TYPE_KINDS = {"interface", "trait", "protocol", "type", "typedef", "enum", "module", "namespace"}


def extract(lang: str, src: bytes, rel: str) -> dict:
    text = src.decode("utf-8", errors="replace")
    rec: dict = {"path": rel, "lang": lang, "package": None, "imports": [], "exports": [], "symbols": [],
                 "calls": [], "jsx": [], "refs": [], "partial": True}
    m = PACKAGE.get(lang) and PACKAGE[lang].search(text)
    if m:
        rec["package"] = m.group(1)
    for rx, kind in IMPORTS.get(lang, []):
        for m in rx.finditer(text):
            spec = m.group(1)
            ln = text.count("\n", 0, m.start()) + 1
            names: list = []
            if lang in ("java", "kotlin") and not spec.endswith(".*"):
                names = [[spec.rsplit(".", 1)[-1]] * 2]
            elif lang == "go":
                names = [["*", spec.rsplit("/", 1)[-1]]]
            rec["imports"].append({"spec": spec, "kind": kind, "names": names, "line": ln})
    rx = DECLS.get(lang)
    for m in rx.finditer(text) if rx else []:
        kw, name = m.group(1), m.group(2)
        kind = "function" if kw in ("def", "func", "fn", "function", "fun") else "type" if kw in TYPE_KINDS else "class"
        ln = text.count("\n", 0, m.start()) + 1
        exported = not name.startswith("_") and (lang != "go" or name[:1].isupper())
        rec["symbols"].append({"name": name, "kind": kind, "line": ln, "end": ln, "exported": exported, "refs": [],
                               "members": [], "decorators": [], "methods": []})
        if exported:
            rec["exports"].append({"name": name, "kind": kind, "line": ln})
    return rec
