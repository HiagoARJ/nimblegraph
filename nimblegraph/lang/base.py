"""Generic tree-sitter extractor. Walks a syntax tree and builds the per-file record in the same shape as the JS
engines (imports, exports, symbols with methods/decorators/refs/members, calls with ARG arguments), driven by a
small per-language table of node types plus hooks that the language modules override."""

from __future__ import annotations

import functools
import re

try:  # missing wheel or a native library blocked by the OS: the regex fallback takes over
    from tree_sitter_language_pack import get_parser as _get_parser
except Exception:  # noqa: BLE001
    _get_parser = None

MAX_REFS = 300
RE_HOLE = re.compile(r"\{([^{}=][^{}]*)\}")  # {expr} in a normalized template
RE_NAME = re.compile(r"^[A-Za-z_$@][\w$@]*(\.[A-Za-z_]\w*)*$")  # base_url / settings.API_V1_STR / self.base
RE_QUOTED = re.compile(r"""^[A-Za-z$@]*(["'`]{1,3})(.*)\1$""", re.S)  # f"...", $"...", @"...", '...'


class GrammarUnavailable(Exception):
    """tree-sitter (or this language's grammar) cannot be loaded: the regex fallback takes over."""


@functools.lru_cache(maxsize=None)
def parser(grammar: str):
    if _get_parser is None:
        return None
    try:
        return _get_parser(grammar)
    except Exception:  # noqa: BLE001 - grammar not downloadable (offline) or not loadable
        return None


def text(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def line(n) -> int:
    return n.start_point[0] + 1


def unquote(s: str) -> str:
    for q in ('"""', "'''", '"', "'", "`"):
        if len(s) >= 2 * len(q) and s.startswith(q) and s.endswith(q):
            return s[len(q):-len(q)]
    return s


def child_of_type(n, *types):
    for c in n.named_children:
        if c.type in types:
            return c
    return None


def children_of_type(n, *types):
    return [c for c in n.named_children if c.type in types]


def descendants(n, *types, limit: int = 2000):
    """Breadth-first descendants of the given types (bounded)."""
    out, queue, seen = [], list(n.named_children), 0
    while queue and seen < limit:
        c = queue.pop(0)
        seen += 1
        if c.type in types:
            out.append(c)
        queue.extend(c.named_children)
    return out


def new_symbol(name: str, kind: str, node, exported: bool = True) -> dict:
    return {"name": name, "kind": kind, "line": line(node), "end": node.end_point[0] + 1, "exported": exported,
            "refs": [], "members": [], "decorators": [], "methods": []}


class Extractor:
    lang = ""
    grammar = ""
    CLASSES: dict[str, str] = {}  # node type -> symbol kind (class | type)
    FUNCTIONS: frozenset[str] = frozenset()  # top level -> function symbol, directly in a class -> method
    CALLS: frozenset[str] = frozenset()
    IDENTS: frozenset[str] = frozenset({"identifier", "type_identifier"})
    STRINGS: frozenset[str] = frozenset({"string", "string_literal"})
    LAMBDAS: frozenset[str] = frozenset({"lambda", "lambda_expression", "closure_expression", "func_literal"})
    MEMBERS: frozenset[str] = frozenset({"field_access", "member_access_expression", "selector_expression",
                                         "attribute", "field_expression", "navigation_expression"})
    SKIP: frozenset[str] = frozenset({"comment", "line_comment", "block_comment"})
    ASSIGNS: frozenset[str] = frozenset()  # node types `assignment()` reads
    CONCATS: frozenset[str] = frozenset({"binary_expression", "binary_operator", "additive_expression"})
    INTERPOLATED: frozenset[str] = frozenset()  # node types that are always interpolated strings
    INTERP_PARTS: frozenset[str] = frozenset({"interpolation", "string_interpolation", "interpolated_expression",
                                              "interpolated_identifier", "template_substitution"})

    def __init__(self, src: bytes, rel: str):
        self.src = src
        self.rel = rel
        self.rec: dict = {"path": rel, "lang": self.lang, "package": None, "imports": [], "exports": [],
                          "symbols": [], "calls": [], "jsx": []}
        self._refs: dict[str, None] = {}
        self._owner_refs: dict[int, dict[str, None]] = {}
        self._ptypes: dict[int, dict[str, str]] = {}  # function -> typed parameters (name -> type)
        self._locals: dict[int, set[str]] = {}  # function -> parameter and local names
        self._strs: dict[int, dict[str, str]] = {0: {}}  # scope (function/class id, 0 = module) -> name -> string
        self._cur: tuple = (None, None)  # (class, function) of the node being walked
        self.rec["strings"] = {}  # module constants and Class.FIELD strings, for other files to resolve

    # ------------------------------------------------------------ entry point
    def run(self) -> dict:
        p = parser(self.grammar)
        if p is None:
            raise GrammarUnavailable(f"tree-sitter grammar '{self.grammar}' not available")
        root = p.parse(self.src).root_node
        self.walk(root, None, None)
        for owner in self._all_owners():
            refs = self._owner_refs.get(id(owner), {})
            owner["refs"] = [r for r in refs if r != owner["name"]][:MAX_REFS]
        own = {s["name"] for s in self.rec["symbols"]}
        self.rec["refs"] = [r for r in self._refs if r not in own][:MAX_REFS * 3]
        for s in self.rec["symbols"]:
            if s.get("exported"):
                self.rec["exports"].append({"name": s["name"], "kind": s["kind"], "line": s["line"]})
        self.finish()
        return self.rec

    def _all_owners(self):
        for s in self.rec["symbols"]:
            yield s
            yield from s.get("methods", [])

    # ------------------------------------------------------------ walking
    def walk(self, node, cls: dict | None, fn: dict | None) -> None:
        t = node.type
        if t in self.SKIP:
            return
        self._cur = (cls, fn)
        if t in self.ASSIGNS:  # x = "..." / const X = ... / var uri = $"{base}items": strings for calls below
            a = self.assignment(node)
            if a is not None:
                name, value = a
                s = self.eval_str(value) if value is not None else None
                if name and s is not None:
                    self._remember(name, s, cls, fn)
        if self.visit(node, cls, fn):  # language hook handled the node and its children
            return
        if t in self.CLASSES:
            sym = self.class_symbol(node, cls)
            if sym is not None:
                self.rec["symbols"].append(sym)
                for c in node.named_children:
                    self.walk(c, sym, None)
                return
        if t in self.FUNCTIONS:
            name = self.func_name(node)
            if name and fn is None:
                if cls is not None:
                    m = {"name": name, "line": line(node), "refs": [], "members": [], "this_refs": [],
                         "decorators": self.decorators(node), "param_types": self.param_types(node)}
                    if self.is_constructor(node, name, cls):
                        cls.setdefault("ctor_types", {}).update(self.ctor_types(node))
                    cls["methods"].append(m)
                    fn = m
                else:
                    sym = new_symbol(name, "function", node, self.exported(node, name))
                    sym["decorators"] = self.decorators(node)
                    self.rec["symbols"].append(sym)
                    fn = sym
                    self.on_function(node, sym)
                self._ptypes[id(fn)] = {k: v for k, v in self.ctor_types(node).items() if "." not in v}
                for p in self.param_names(node):
                    self.local(p, fn)
        if t in self.CALLS:
            self._cur = (cls, fn)
            c = self.call(node, cls)
            if c is not None:
                c["args"] = [self._resolve_arg(a) for a in c.get("args") or []]
                owner = fn or cls
                c["sym"] = (cls["name"] if cls else fn["name"]) if (cls or fn) else None
                c["line"] = line(node)
                self.rec["calls"].append(c)
                if owner is not None and c.get("obj") and c.get("prop"):
                    if c["obj"] == "this":  # this.helper(): a call to a method of the same class
                        if fn is not None and "this_refs" in fn and c["prop"] not in fn["this_refs"]:
                            fn["this_refs"].append(c["prop"])
                    else:
                        root, _, rest = c["obj"].partition(".")
                        typed = (self._ptypes.get(id(fn)) or {}).get(root) if fn is not None else None
                        obj = f"{typed}.{rest}" if typed and rest else typed or c["obj"]
                        mem = f"{obj}.{c['prop']}"  # svc.create with `svc: UserService` -> UserService.create
                        if mem not in owner["members"]:
                            owner["members"].append(mem)
                        if c["obj"].startswith("this.") and fn is not None and "this_refs" in fn:
                            r = c["obj"].split(".")[1]
                            if r not in fn["this_refs"]:
                                fn["this_refs"].append(r)
        if t in self.IDENTS:
            self.ref(text(node), fn or cls)
        for c in node.named_children:
            self.walk(c, cls, fn)

    def ref(self, name: str, owner: dict | None) -> None:
        if not name or len(name) > 80:
            return
        if owner is not None and name in self._locals.get(id(owner), ()):
            return  # a parameter or local binding, not the same-named declaration elsewhere
        self._refs[name] = None
        if owner is not None:
            self._owner_refs.setdefault(id(owner), {})[name] = None

    # ------------------------------------------------------------ strings (route prefixes, URLs of HTTP calls)
    def _remember(self, name: str, value: str, cls: dict | None, fn: dict | None) -> None:
        name = self._plain(name)
        if fn is not None:
            self._strs.setdefault(id(fn), {})[name] = value
        elif cls is not None:
            self._strs.setdefault(id(cls), {})[name] = value
            self.rec["strings"][f"{cls['name']}.{name}"] = value
        else:
            self._strs[0][name] = value
            self.rec["strings"][name] = value

    @staticmethod
    def _plain(name: str) -> str:
        for p in ("self.", "this.", "@", "$this->", "$", "Self::", "self::", "static::"):
            if name.startswith(p):
                return name[len(p):]
        return name

    def lookup_str(self, name: str) -> str | None:
        """A string variable/constant visible here: function locals, class fields, module constants, Class.NAME."""
        cls, fn = self._cur
        plain = self._plain(name.strip())
        for scope in (fn, cls):
            if scope is not None:
                v = self._strs.get(id(scope), {}).get(plain)
                if v is not None:
                    return v
        return self._strs[0].get(plain) if plain in self._strs[0] else self.rec["strings"].get(plain.replace("::", "."))

    def _resolve_arg(self, a: dict) -> dict:
        t = a.get("t")
        if t in ("id", "member"):
            v = self.lookup_str(a["v"])
            if v is not None:
                return {"t": "tpl" if ":param" in v or "{=" in v else "str", "v": v, "via": a["v"]}
        elif t == "obj":
            return {**a, "props": {k: self._resolve_arg(v) for k, v in a["props"].items()}}
        elif t == "arr":
            return {**a, "items": [self._resolve_arg(i) for i in a["items"]]}
        return a

    def eval_str(self, n) -> str | None:
        """The string an expression evaluates to, as far as the file says: literals, known variables and constants,
        interpolations and `+` concatenations (unknown parts become :param), format calls."""
        if n is None:
            return None
        t = n.type
        if t in self.STRINGS or t in self.INTERPOLATED:
            if self.is_interpolated(n):
                return self.interpolate(n)
            return self.string_value(n)
        if t in self.IDENTS or t in self.MEMBERS or t in ("this", "self"):
            return self.lookup_str(text(n).replace("->", ".").replace("::", "."))
        if t in self.CONCATS:
            kids = n.named_children
            op = [text(c) for c in n.children if not c.is_named]
            if len(kids) == 2 and "+" in op or t in ("concatenation_expression",) or ("." in op and self.lang == "php"):
                parts = [self.eval_str(k) for k in kids]
                if any(p is not None for p in parts):
                    return "".join(p if p is not None else self._hole(k) for p, k in zip(parts, kids))
            return None
        if t in ("parenthesized_expression",) and n.named_children:
            return self.eval_str(n.named_children[0])
        return self.format_call(n)

    def is_interpolated(self, n) -> bool:
        return n.type in self.INTERPOLATED or any(c.type in self.INTERP_PARTS for c in n.named_children)

    def interpolate(self, n) -> str | None:
        """`$"{base}items/{id}"` -> 'api/catalog/items/:param' (known variables substituted)."""
        raw = self.template_text(n)
        if raw is None:
            return None

        def sub(m):
            expr = m.group(1).split(":", 1)[0].split(",", 1)[0].strip()
            v = self.lookup_str(expr)
            return v if v is not None else f"{{={expr}}}" if RE_NAME.match(expr) else ":param"
        return RE_HOLE.sub(sub, raw)

    def _hole(self, n) -> str:
        """An unknown part: {=settings.API_V1_STR} when it is a name another file may define, else :param."""
        s = text(n).replace("->", ".").replace("::", ".")
        return f"{{={s}}}" if RE_NAME.match(s) else ":param"

    def template_text(self, n) -> str | None:
        """The literal with each interpolation as {expr}; languages with other syntaxes override."""
        s = text(n)
        m = RE_QUOTED.search(s)
        return m.group(2) if m else None

    def format_call(self, n) -> str | None:
        """fmt.Sprintf / String.format / format!: languages override."""
        return None

    def assignment(self, node) -> tuple[str, object] | None:
        """(name, value node) of an assignment/declaration with an initializer; languages override."""
        return None

    def local(self, name: str, owner: dict | None) -> None:
        """A name bound inside a function (parameter, `x := ...`, `let x = ...`): never a reference."""
        if owner is not None and name:
            self._locals.setdefault(id(owner), set()).add(name)

    # ------------------------------------------------------------ hooks (override per language)
    def visit(self, node, cls, fn) -> bool:
        return False

    def class_symbol(self, node, outer: dict | None) -> dict | None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return None
        sym = new_symbol(name, self.CLASSES[node.type], node, self.exported(node, name))
        sym["decorators"] = self.decorators(node)
        sym["extends"], sym["implements"] = self.bases(node)
        sym["abstract"] = self.is_abstract(node)
        sym["ctor_types"] = self.fields(node)
        return sym

    def func_name(self, node) -> str | None:
        return text(node.child_by_field_name("name")) or None

    def exported(self, node, name: str) -> bool:
        return True

    def decorators(self, node) -> list[dict]:
        return []

    def bases(self, node) -> tuple[str | None, list[str]]:
        return None, []

    def is_abstract(self, node) -> bool:
        return False

    def fields(self, node) -> dict[str, str]:
        """name -> type of the class fields (lets `field.method()` resolve to the injected type)."""
        return {}

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return name == cls["name"]

    def ctor_types(self, node) -> dict[str, str]:
        return {}

    def param_types(self, node) -> list[str]:
        return []

    def param_names(self, node) -> list[str]:
        return list(self.ctor_types(node))

    def call(self, node, cls) -> dict | None:
        return None

    def on_function(self, node, sym: dict) -> None:
        pass

    def finish(self) -> None:
        pass

    # ------------------------------------------------------------ helpers for the hooks
    def add_import(self, spec: str, kind: str, names: list[list[str]], node) -> None:
        if spec:
            self.rec["imports"].append({"spec": spec, "kind": kind, "names": names, "line": line(node)})

    def member_obj(self, obj: str, cls: dict | None) -> str:
        """`service` that is a class field -> `this.service` (Java/C#/Kotlin call fields without `this.`)."""
        if cls is not None and obj in (cls.get("ctor_types") or {}) and not obj.startswith("this."):
            return "this." + obj
        return obj

    def arg(self, n) -> dict:
        t = n.type
        if t in self.STRINGS or t in self.INTERPOLATED or t in self.CONCATS:
            s = self.eval_str(n)
            if s is None:
                return {"t": "other"}
            return {"t": "tpl" if ":param" in s or "{=" in s else "str", "v": s}
        if t in self.LAMBDAS:  # the line range lets detectors nest the routes registered inside it
            return {"t": "fn", "line": line(n), "end": n.end_point[0] + 1}
        if t in self.IDENTS:
            return {"t": "id", "v": text(n)}
        if t in self.MEMBERS:
            return {"t": "member", "v": text(n).replace("::", ".").replace("->", ".")}
        if t in self.CALLS or t == "macro_invocation":
            s = self.format_call(n)  # String.format(...) / fmt.Sprintf(...) / format!(...) as an argument
            if s is not None:
                return {"t": "tpl" if ":param" in s or "{=" in s else "str", "v": s}
        if t in self.CALLS:
            c = self.call(n, None) or {}
            return {"t": "call", "callee": c.get("callee") or (f"{c['obj']}.{c['prop']}" if c.get("obj") else None),
                    "args": c.get("args", [])}
        return {"t": "other"}

    def string_value(self, n) -> str | None:
        s = text(n)
        if any(c.type in ("interpolation", "template_substitution", "string_interpolation", "interpolated_variable",
                          "escape_sequence_interpolation") for c in n.named_children):
            return None
        return unquote(s.lstrip("bBrRuUfF@$"))
