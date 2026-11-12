"""Python engine (fallback): extracts the same per-file record as node/extract.mjs, using the
pure-Python tokenizer in lexer.py. Imports are left unresolved here; resolve.py handles them."""

from __future__ import annotations

import re
from pathlib import Path

from nimblegraph.parse.lexer import Tok, tokenize

BACKEND = "pure-python"

HTTP = {"get", "post", "put", "patch", "delete", "del", "head", "options", "all"}
ROUTE_PROPS = HTTP | {"use", "route", "register", "setGlobalPrefix", "addHook", "mount"}
CLIENT_OBJS = {"axios", "ky"}
OBJ_DENY = {"Promise", "Object", "Array", "Map", "Set", "WeakMap", "JSON", "Math", "Reflect", "console",
            "window", "document", "localStorage", "sessionStorage", "process", "searchParams", "headers",
            "cookies", "formData"}
CALLEES = {"fetch", "axios", "$fetch", "ky", "ofetch", "createBrowserRouter", "createHashRouter",
           "createMemoryRouter", "useRoutes", "createFileRoute", "createLazyFileRoute", "createRootRoute",
           "createRootRouteWithContext", "createRoute"}
ROUTE_KEYS = ("element", "Component", "children", "lazy")
MAX_ARGS = 8
MAX_DEPTH = 3

KEYWORDS = {
    "break", "case", "catch", "class", "const", "continue", "debugger", "default", "delete", "do", "else",
    "export", "extends", "finally", "for", "function", "if", "import", "in", "instanceof", "new", "return",
    "super", "switch", "this", "throw", "try", "typeof", "var", "void", "while", "with", "yield", "let",
    "await", "null", "true", "false", "enum", "static", "of",
}
NOT_IDENT_ARG = {"this", "super", "null", "true", "false", "new", "typeof", "void", "delete", "await",
                 "function", "class", "async", "import", "yield"}
BIN_OPS = {"&&", "||", "??", "?", ":", "=", "+", "-", "*", "/", "%", "**", "==", "===", "!=", "!==", "<=", ">=",
           "|", "&", "^", ",", "=>"}
PARAM_MODS = {"public", "private", "protected", "readonly", "override"}
MODIFIERS = {"public", "private", "protected", "static", "readonly", "async", "abstract", "override",
             "declare", "accessor", "get", "set"}
CONTINUE_P = {
    ".", "?.", "(", "[", ",", "?", ":", "=>", "=", "+", "-", "*", "/", "%", "**", "&&", "||", "??", "&", "|",
    "^", "==", "===", "!=", "!==", "<", ">", "<=", ">=", "<<", ">>", ">>>", "+=", "-=", "*=", "/=", "%=",
    "**=", "<<=", ">>=", ">>>=", "&=", "|=", "^=", "&&=", "||=", "??=",
}
STMT_KW = {"export", "import", "const", "let", "var", "function", "class", "interface", "type", "enum", "return",
           "if", "for", "while", "do", "switch", "try", "throw", "declare", "abstract", "async"}
CONTINUE_ID = {"instanceof", "in", "as", "satisfies", "extends", "implements"}
TYPE_LIT_BEFORE = {":", "|", "&", "<", ",", "=>", "(", "[", "?", "keyof", "typeof", "readonly"}

PRIMITIVE_TYPES = {"string", "number", "boolean", "any", "unknown", "never", "void", "object", "symbol", "bigint",
                   "undefined", "null", "this", "typeof", "keyof", "readonly", "unique", "infer", "asserts"}
RE_HOOK = re.compile(r"^use[A-Z0-9]")
OTHER = {"t": "other"}


def is_comp_tag(tag: str) -> bool:
    return bool(tag) and tag.split(".")[-1][:1].isupper()


class _El:
    __slots__ = ("idx", "tag", "parent", "in_attr", "state", "attrs", "children", "end", "_cur")

    def __init__(self, idx: int, tag: str, parent: "_El | None", in_attr: bool):
        self.idx = idx
        self.tag = tag
        self.parent = parent
        self.in_attr = in_attr
        self.state = "attrs"
        self.attrs: dict[str, tuple[int, int]] = {}
        self.children: list[_El] = []
        self.end = idx
        self._cur: tuple[str, int] | None = None

    def close_attr(self, idx: int) -> None:
        if self._cur:
            name, start = self._cur
            if name != "...":
                self.attrs.setdefault(name, (start, idx))
            self._cur = None


class Extractor:
    def __init__(self, src: str, rel: str):
        self.rel = rel
        ext = Path(rel).suffix.lower()
        self.jsx_file = ext in (".tsx", ".jsx")
        toks, self.tpl_parts = tokenize(src, jsx=ext not in (".ts", ".mts", ".cts"))
        self.toks: list[Tok] = toks
        self.n = len(toks)
        self._match()
        self._jsx_tree()
        self.imports: list[dict] = []
        self.list_exports: list[dict] = []
        self.exports: list[dict] = []
        self.symbols: list[dict] = []
        self.calls: list[dict] = []
        self.route_objects: list[dict] = []
        self.dyn_locals: dict[str, str] = {}
        self.vars: dict[str, str] = {}
        self.consts: dict[str, dict] = {}
        self.directives: list[str] = []
        self.import_locals: set[str] = set()
        self.top_names: set[str] = set()
        self.known: set[str] = set()
        self.def_names: set[int] = set()  # token indexes of method names (not references)
        self.sym_ranges: list[tuple[int, int, str]] = []

    # ------------------------------------------------------------ structure
    def _match(self) -> None:
        toks, n = self.toks, self.n
        m = [-1] * n
        opens = [-1] * n
        stack: list[int] = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for i, t in enumerate(toks):
            if t.t != "p":
                continue
            if t.v in ("(", "[", "{"):
                stack.append(i)
            elif t.v in pairs:
                want = pairs[t.v]
                while stack and toks[stack[-1]].v != want:
                    j = stack.pop()
                    m[j] = i - 1
                if stack:
                    j = stack.pop()
                    m[j] = i
                    m[i] = j
                    opens[i] = j
        for j in stack:
            m[j] = n - 1
        self.m = m
        self.open_of = opens

    def _jsx_tree(self) -> None:
        self.els: dict[int, _El] = {}
        stack: list[_El] = []
        for i, t in enumerate(self.toks):
            if t.t == "jsx_open":
                parent = stack[-1] if stack else None
                el = _El(i, t.v, parent, in_attr=bool(parent and parent.state == "attrs"))
                if parent and not el.in_attr:
                    parent.children.append(el)
                self.els[i] = el
                stack.append(el)
            elif t.t == "jsx_attr" and stack:
                stack[-1].close_attr(i)
                stack[-1]._cur = (t.v, i + 1)
            elif t.t == "jsx_body" and stack:
                stack[-1].close_attr(i)
                stack[-1].state = "body"
            elif t.t == "jsx_close" and stack:
                el = stack.pop()
                el.close_attr(i)
                el.end = i

    # ------------------------------------------------------------ token helpers
    def p(self, i: int, v: str) -> bool:
        return 0 <= i < self.n and self.toks[i].t == "p" and self.toks[i].v == v

    def idv(self, i: int, v: str | None = None) -> bool:
        if not (0 <= i < self.n) or self.toks[i].t != "id":
            return False
        return v is None or self.toks[i].v == v

    def ends_stmt(self, t: Tok) -> bool:
        if t.t in ("id", "num", "str", "tpl_end", "regex", "jsx_close"):
            return not (t.t == "id" and t.v in CONTINUE_ID)
        return t.t == "p" and t.v in (")", "]", "}", "++", "--")

    def asi(self, prev: Tok, t: Tok) -> bool:
        if prev.t == "p" and prev.v in (">", ">>", ">>>") and t.t == "id" and t.v in STMT_KW:
            return True  # `x as Foo<Bar>` / `type A = B<C>` followed by a new statement
        if not self.ends_stmt(prev):
            return False
        if t.t == "p" and t.v in CONTINUE_P:
            return False
        if t.t == "id" and t.v in CONTINUE_ID:
            return False
        return True

    def scan_end(self, i: int, limit: int | None = None, stop_comma: bool = False) -> int:
        """Index of the token that ends the expression/statement starting at i (';', ',', closer, ASI)."""
        toks, m = self.toks, self.m
        n = self.n if limit is None else limit
        j = i
        prev: Tok | None = None
        while j < n:
            t = toks[j]
            if prev is not None and t.nl and self.asi(prev, t):
                return j
            if t.t == "p":
                v = t.v
                if v == ";":
                    return j
                if v in ("(", "[", "{"):
                    j = m[j]
                    prev = toks[j] if j < n else t
                    j += 1
                    continue
                if v in (")", "]", "}"):
                    return j
                if stop_comma and v == ",":
                    return j
                if v == "<":
                    gt = self._generic_close(j)
                    if gt is not None:  # type arguments/parameters: f<A, B>(...), <T, U>(x) => ...
                        prev = toks[gt]
                        j = gt + 1
                        continue
            elif t.t == "id" and t.v in ("as", "satisfies") and prev is not None:
                te = self.type_end(j + 1, n)
                if te > j + 1:
                    prev = toks[te - 1]
                    j = te
                    continue
            prev = t
            j += 1
        return min(j, n)

    def _generic_close(self, lt: int) -> int | None:
        """At '<' after an identifier: index of the matching '>' when it is followed by '(' (a generic call)."""
        toks = self.toks
        depth = 0
        j = lt
        limit = min(self.n, lt + 80)
        while j < limit:
            t = toks[j]
            if t.t == "p":
                v = t.v
                if v == "<":
                    depth += 1
                elif v in (">", ">>", ">>>"):
                    depth -= len(v)
                    if depth <= 0:
                        return j if depth == 0 and (self.p(j + 1, "(") or self.p(j + 1, "=>")) else None
                elif v in ("(", "[", "{"):
                    j = self.m[j]
                elif v not in (",", ".", "|", "&", "?", ":", "=>", "[", "]", "?.", "="):
                    return None
            elif t.t not in ("id", "str", "num"):
                return None
            j += 1
        return None

    def stmt_end(self, i: int) -> int:
        j = self.scan_end(i)
        if self.p(j, ";"):
            j += 1
        return j if j > i else i + 1

    def sig_end(self, pclose: int) -> int:
        """End of a body-less signature (overload/abstract) whose parameter list closes at pclose."""
        k = pclose + 1
        if self.p(k, ":"):
            k = self.type_end(k + 1)
        return k + 1 if self.p(k, ";") else k

    def skip_angle(self, j: int) -> int:
        """At '<': returns the index after the matching '>'."""
        depth = 0
        while j < self.n:
            t = self.toks[j]
            if t.t == "p":
                if t.v == "<":
                    depth += 1
                elif t.v in (">", ">>", ">>>"):
                    depth -= len(t.v)
                    if depth <= 0:
                        return j + 1
                elif t.v in ("(", "[", "{"):
                    j = self.m[j]
                elif t.v in (";", ")", "]", "}"):
                    return j
            j += 1
        return j

    def type_end(self, j: int, limit: int | None = None) -> int:
        """Scans a type annotation; stops at '=', ',', ';', ')', or ASI at angle depth 0."""
        toks, n = self.toks, (self.n if limit is None else limit)
        angle = 0
        prev: Tok | None = None
        while j < n:
            t = toks[j]
            if prev is not None and t.nl and angle <= 0 and self.asi(prev, t):
                return j
            if t.t == "p":
                v = t.v
                if v == "<":
                    angle += 1
                elif v in (">", ">>", ">>>"):
                    angle -= len(v)
                elif v in ("(", "[", "{"):
                    j = self.m[j]
                    prev = toks[j]
                    j += 1
                    continue
                elif v in (")", "]", "}", ";"):
                    return j
                elif v in ("=", ",") and angle <= 0:
                    return j
            prev = t
            j += 1
        return j

    def find_body(self, j: int) -> int | None:
        """After a parameter list: index of the body '{' (skipping a return type), or None."""
        toks, n = self.toks, self.n
        if self.p(j, "{"):
            return j
        if not self.p(j, ":"):
            return None
        prev = ":"
        j += 1
        while j < n:
            t = toks[j]
            if t.t == "p":
                if t.v == "{":
                    if prev in TYPE_LIT_BEFORE:
                        j = self.m[j] + 1
                        prev = "}"
                        continue
                    return j
                if t.v in ("(", "["):
                    j = self.m[j] + 1
                    prev = ")"
                    continue
                if t.v in (";", ")", "]", "}"):
                    return None
            elif t.nl and t.t == "id" and t.v in ("export", "function", "const", "let", "var", "class", "import"):
                return None
            prev = t.v
            j += 1
        return None

    def split(self, a: int, e: int, sep: str = ",", angles: bool = False) -> list[tuple[int, int]]:
        out = []
        start = a
        j = a
        toks, m = self.toks, self.m
        angle = 0
        while j < e:
            t = toks[j]
            if t.t == "p":
                if t.v in ("(", "[", "{"):
                    j = m[j] + 1
                    continue
                if angles and t.v == "<":
                    angle += 1
                elif angles and t.v in (">", ">>", ">>>") and angle > 0:
                    angle = max(0, angle - len(t.v))
                elif angles and t.v in ("=", "=>"):
                    angle = 0
                elif t.v == sep and angle == 0:
                    out.append((start, j))
                    start = j + 1
            j += 1
        if start < e:
            out.append((start, e))
        return out

    def has_top(self, a: int, e: int, values: set[str]) -> bool:
        j = a
        while j < e:
            t = self.toks[j]
            if t.t == "p":
                if t.v in ("(", "[", "{"):
                    j = self.m[j] + 1
                    continue
                if t.v in values:
                    return True
            j += 1
        return False

    def line(self, i: int) -> int:
        return self.toks[min(i, self.n - 1)].line

    def end_line(self, e: int) -> int:
        return self.toks[max(0, min(e, self.n) - 1)].el

    # ------------------------------------------------------------ expressions
    def unwrap(self, a: int, e: int) -> tuple[int, int]:
        while a < e:
            if self.p(a, "(") and self.m[a] == e - 1:
                a, e = a + 1, e - 1
                continue
            if e - a > 1 and self.p(e - 1, "!"):
                e -= 1
                continue
            cut = self._top_as(a, e)
            if cut is not None:
                e = cut
                continue
            break
        return a, e

    def _top_as(self, a: int, e: int) -> int | None:
        j = a
        while j < e:
            t = self.toks[j]
            if t.t == "p" and t.v in ("(", "[", "{"):
                j = self.m[j] + 1
                continue
            if t.t == "p" and t.v == "=>":
                return None
            if t.t == "id" and t.v in ("as", "satisfies") and j > a:
                return j
            j += 1
        return None

    def chain_at(self, a: int, e: int) -> str | None:
        """Text of an identifier/this chain spanning exactly [a, e), else None."""
        toks = self.toks
        if a >= e or toks[a].t != "id" or toks[a].v in ("super", "import", "new", "typeof"):
            return None
        parts = [toks[a].v]
        j = a + 1
        while j < e:
            if toks[j].t == "p" and toks[j].v in (".", "?.") and j + 1 < e and toks[j + 1].t == "id":
                parts.append(toks[j + 1].v)
                j += 2
            else:
                return None
        if parts[0] in KEYWORDS and parts[0] != "this":
            return None
        if parts == ["this"]:
            return "this"
        return ".".join(parts)

    def is_fn(self, a: int, e: int) -> bool:
        toks = self.toks
        k = a
        if self.idv(k, "async") and k + 1 < e and not self.p(k + 1, "=>"):
            k += 1
        if self.idv(k, "function"):
            return True
        if self.p(k, "<"):
            k = self.skip_angle(k)
        if self.p(k, "("):
            k = self.m[k] + 1
            if self.p(k, ":"):
                while k < e and not self.p(k, "=>"):
                    if toks[k].t == "p" and toks[k].v in ("(", "[", "{"):
                        k = self.m[k]
                    k += 1
            return self.p(k, "=>") and k < e
        if k < e and toks[k].t == "id" and toks[k].v not in KEYWORDS and self.p(k + 1, "=>"):
            return True
        return False

    def tpl_text(self, a: int) -> str:
        head = self.toks[a].v
        quasis = [self.toks[k].v for k in self.tpl_parts.get(a, [])]
        if head == "" and quasis:
            return quasis[0] + "".join(":param" + q for q in quasis[1:])
        return head + "".join(":param" + q for q in quasis)

    def is_tpl_whole(self, a: int, e: int) -> bool:
        if self.toks[a].t != "tpl_start":
            return False
        parts = self.tpl_parts.get(a)
        return bool(parts) and parts[-1] == e - 1

    def call_parts(self, a: int, e: int) -> tuple[str, int] | None:
        """[a, e) is `chain [<..>] ( ... )`: returns (callee, open_paren_index)."""
        if not self.p(e - 1, ")"):
            return None
        o = self.open_of[e - 1]
        if o <= a:
            return None
        ce = o
        if self.p(o - 1, ">") or self.p(o - 1, ">>") or self.p(o - 1, ">>>"):
            lt = self._angle_open(o - 1, a)
            if lt is None:
                return None
            ce = lt
        c = self.chain_at(a, ce)
        return (c, o) if c else None

    def _angle_open(self, gt: int, lo: int) -> int | None:
        depth = 0
        j = gt
        while j >= lo:
            t = self.toks[j]
            if t.t == "p":
                if t.v in (">", ">>", ">>>"):
                    depth += len(t.v)
                elif t.v == "<":
                    depth -= 1
                    if depth <= 0:
                        return j
                elif t.v in (")", "]", "}"):
                    j = self.open_of[j]
                elif t.v in (";", "(", "[", "{", "=", "=>"):
                    return None
            j -= 1
        return None

    def args(self, o: int) -> list[dict]:
        return [self.to_arg(x, y) for x, y in self.split(o + 1, self.m[o])[:MAX_ARGS]]

    def to_arg(self, a: int, e: int, d: int = 0) -> dict:
        a, e = self.unwrap(a, e)
        if a >= e:
            return OTHER
        toks = self.toks
        t0 = toks[a]
        if e - a == 1 and t0.t == "str":
            return {"t": "str", "v": t0.v}
        if self.is_tpl_whole(a, e):
            return {"t": "tpl", "v": self.tpl_text(a)}
        if self.is_fn(a, e):
            return {"t": "fn"}
        if self.has_top(a, e, {"+"}) and not self.has_top(a, e, {"?", "&&", "||", "??", "=>", "-", "==", "===", "!=", "!==", "<", ">", "=", ","}):
            parts: list[str | None] = []
            for x, y in self.split(a, e, "+"):
                x, y = self.unwrap(x, y)
                if y - x == 1 and toks[x].t == "str":
                    parts.append(toks[x].v)
                elif x < y and self.is_tpl_whole(x, y):
                    parts.append(self.tpl_text(x))
                else:
                    parts.append(None)
            if all(p is not None for p in parts):
                return {"t": "str", "v": "".join(parts)}
            rest = parts[1:] if parts[0] is None else parts
            return {"t": "tpl", "v": "".join(":param" if p is None else p for p in rest)}
        if e - a == 1 and t0.t == "id":
            return OTHER if t0.v in NOT_IDENT_ARG else {"t": "id", "v": t0.v}
        c = self.chain_at(a, e)
        if c:
            return {"t": "member", "v": c} if c != "this" else OTHER
        cp = self.call_parts(a, e)
        if cp:
            callee, o = cp
            if callee == "require":
                first = self.split(o + 1, self.m[o])
                if first and first[0][1] - first[0][0] == 1 and toks[first[0][0]].t == "str":
                    return {"t": "require", "v": toks[first[0][0]].v}
            if d < MAX_DEPTH:
                return {"t": "call", "callee": callee,
                        "args": [self.to_arg(x, y, d + 1) for x, y in self.split(o + 1, self.m[o])[:MAX_ARGS]]}
            return OTHER
        if self.idv(a, "new"):
            q = a + 1
            while self.p(q + 1, ".") and self.idv(q + 2):
                q += 2
            callee = self.chain_at(a + 1, q + 1)
            rest = q + 1
            if self.p(rest, "<"):
                rest = self.skip_angle(rest)
            if callee and (rest >= e or self.p(rest, "(") and self.m[rest] == e - 1):
                if d >= MAX_DEPTH:
                    return OTHER
                args = [] if rest >= e else [self.to_arg(x, y, d + 1) for x, y in self.split(rest + 1, e - 1)[:MAX_ARGS]]
                return {"t": "new", "callee": callee, "args": args}
            return OTHER
        if self.p(a, "{") and self.m[a] == e - 1:
            if d >= MAX_DEPTH:
                return OTHER
            props: dict[str, dict] = {}
            for key, kind, vs, ve, _ks in self.obj_props(a):
                if kind == "assign":
                    props[key] = self.to_arg(vs, ve, d + 1)
                elif kind == "short":
                    props[key] = {"t": "id", "v": key}
                elif kind == "method":
                    props[key] = {"t": "fn"}
            return {"t": "obj", "props": props}
        if self.p(a, "[") and self.m[a] == e - 1:
            if d >= MAX_DEPTH:
                return OTHER
            items = []
            for x, y in self.split(a + 1, e - 1)[:16]:
                items.append(OTHER if self.p(x, "...") else self.to_arg(x, y, d + 1))
            return {"t": "arr", "items": items}
        return OTHER

    def obj_props(self, a: int) -> list[tuple[str, str, int, int, int]]:
        """Object literal at '{' -> [(key, kind, value_start, value_end, prop_start)];
        kind: assign | short | method | accessor."""
        out = []
        toks = self.toks
        for x, y in self.split(a + 1, self.m[a]):
            if x >= y or self.p(x, "..."):
                continue
            k = x
            kind_prefix = None
            while k + 1 < y and toks[k].t == "id" and toks[k].v in ("async", "get", "set") and not self.p(k + 1, "(") \
                    and not self.p(k + 1, ":") and not self.p(k + 1, ","):
                kind_prefix = toks[k].v
                k += 1
            if self.p(k, "*"):
                k += 1
            t = toks[k]
            if t.t not in ("id", "str", "num"):
                continue
            key = t.v
            if self.p(k + 1, ":"):
                out.append((key, "assign", k + 2, y, x))
            elif self.p(k + 1, "(") or self.p(k + 1, "<"):
                kind = "accessor" if kind_prefix in ("get", "set") else "method"
                out.append((key, kind, k + 1, y, x))
                self.def_names.add(k)
            elif k + 1 >= y or self.p(k + 1, "="):
                if t.t == "id":
                    out.append((key, "short", k, k + 1, x))
        return out

    # ------------------------------------------------------------ symbols
    def contains_jsx(self, a: int, e: int) -> bool:
        return any(self.toks[j].t == "jsx_open" for j in range(a, min(e, self.n)))

    def fn_kind(self, name: str | None, a: int, e: int) -> str:
        if not name:
            return "function"
        if RE_HOOK.match(name):
            return "hook"
        if name[:1].isupper() and (self.jsx_file or self.contains_jsx(a, e)):
            return "component"
        return "function"

    def var_kind(self, name: str, a: int, e: int) -> str:
        a, e = self.unwrap(a, e)
        if a >= e:
            return "const"
        if self.is_fn(a, e):
            return self.fn_kind(name, a, e)
        cp = self.call_parts(a, e)
        if cp is None and self.p(e - 1, ")"):
            # callee that is not a plain chain (e.g. styled.div<...>(...)) still counts, unless it is an operand
            o = self.open_of[e - 1]
            cp = ("?", o) if o > a and not self.has_top(a, o, BIN_OPS) else None
        if cp:
            first = self.split(cp[1] + 1, self.m[cp[1]])
            if first:
                x, y = self.unwrap(*first[0])
                if x < y and self.is_fn(x, y):
                    return self.fn_kind(name, a, e)
        return "const"

    def refs(self, a: int, e: int, self_name: str | None) -> list[str]:
        toks, known = self.toks, self.known
        out = set()
        for j in range(a, min(e, self.n)):
            t = toks[j]
            if t.t == "jsx_open":
                root = t.v.split(".")[0]
                if root in known and root != self_name:
                    out.add(root)
                continue
            if t.t != "id" or t.v not in known or t.v == self_name:
                continue
            if j > 0 and toks[j - 1].t == "p" and toks[j - 1].v in (".", "?."):
                continue
            if self.p(j + 1, ":") and (t.nl or j > 0 and toks[j - 1].t == "p" and toks[j - 1].v in ("{", ",", ";", "(")):
                continue
            if self.p(j + 1, "?") and self.p(j + 2, ":") and j > 0 and toks[j - 1].t == "p" and toks[j - 1].v in ("{", ",", ";", "("):
                continue
            if j in self.def_names:
                continue
            out.add(t.v)
        return sorted(out)

    def members(self, a: int, e: int, self_name: str | None) -> list[str]:
        """`x.y` on a known name and `this.x.y` (same rules as membersOf in extract.mjs)."""
        toks, known = self.toks, self.known
        out = set()
        end = min(e, self.n)
        for j in range(a, end - 2):
            t = toks[j]
            if t.t != "id" or not (toks[j + 1].t == "p" and toks[j + 1].v in (".", "?.")):
                continue
            if j > 0 and toks[j - 1].t == "p" and toks[j - 1].v in (".", "?."):
                continue
            nxt = toks[j + 2]
            if nxt.t != "id" or nxt.v.startswith("#"):
                continue
            if t.v == "this":
                if j + 4 < end + 1 and j + 4 <= self.n - 1 and toks[j + 3].t == "p" and toks[j + 3].v in (".", "?.")                         and toks[j + 4].t == "id" and not toks[j + 4].v.startswith("#") and j + 4 < end:
                    out.add(f"this.{nxt.v}.{toks[j + 4].v}")
            elif t.v in known and t.v != self_name:
                out.add(f"{t.v}.{nxt.v}")
        return sorted(out)

    def this_refs(self, a: int, e: int) -> list[str]:
        toks = self.toks
        out = set()
        for j in range(a, min(e, self.n) - 2):
            if toks[j].t == "id" and toks[j].v == "this" and self.p(j + 1, ".") and toks[j + 2].t == "id":
                out.add(toks[j + 2].v)
        return sorted(out)

    def type_name(self, a: int, e: int) -> str | None:
        toks = self.toks
        if a >= e or toks[a].t != "id" or toks[a].v in PRIMITIVE_TYPES:
            return None
        j = a
        last = toks[a].v
        while j + 2 < e + 1 and self.p(j + 1, ".") and j + 2 < e and toks[j + 2].t == "id":
            j += 2
            last = toks[j].v
        j += 1
        if self.p(j, "<"):
            j = self.skip_angle(j)
        while self.p(j, "[") and self.p(j + 1, "]"):
            j += 2
        return last if j >= e else None

    def params(self, o: int) -> list[tuple[str | None, str | None, int]]:
        """Parameter list at '(' -> [(name, type_name, name_index)]."""
        out = []
        toks = self.toks
        for x, y in self.split(o + 1, self.m[o], angles=True):
            k = x
            while self.p(k, "@"):
                _, k = self.decorator(k)
            while k < y:
                if toks[k].t == "id" and toks[k].v in PARAM_MODS and k + 1 < y and (toks[k + 1].t == "id" or self.p(k + 1, "{") or self.p(k + 1, "[")):
                    k += 1
                elif self.p(k, "..."):
                    k += 1
                else:
                    break
            name = toks[k].v if k < y and toks[k].t == "id" else None
            name_idx = k
            if k < y and toks[k].t == "p" and toks[k].v in ("{", "["):
                k = self.m[k]
            k += 1
            if self.p(k, "?"):
                k += 1
            tn = None
            if self.p(k, ":"):
                te = self.type_end(k + 1, y)
                tn = self.type_name(k + 1, te)
            out.append((name, tn, name_idx))
        return out

    def decorator(self, i: int) -> tuple[dict, int]:
        toks = self.toks
        k = i + 1
        start = k
        while k < self.n and toks[k].t == "id":
            if self.p(k + 1, ".") and k + 2 < self.n and toks[k + 2].t == "id":
                k += 2
            else:
                break
        name = self.chain_at(start, k + 1) or "?"
        k += 1
        args: list[dict] = []
        if self.p(k, "("):
            args = self.args(k)
            k = self.m[k] + 1
        return {"name": name, "args": args}, k

    def method_info(self, a: int, e: int, name: str, name_idx: int, params_open: int | None, decos: list[dict]) -> dict:
        ptypes = [tn for _, tn, _ in self.params(params_open)] if params_open is not None else []
        return {
            "name": name,
            "line": self.line(name_idx),
            "refs": self.refs(a, e, None), "members": self.members(a, e, None),
            "this_refs": self.this_refs(a, e),
            "decorators": decos,
            "param_types": [t for t in ptypes if t],
        }

    def fn_params_open(self, a: int, e: int) -> int | None:
        """For an arrow/function expression slice, index of its '(' (None for `x => ...`)."""
        k = a
        if self.idv(k, "async"):
            k += 1
        if self.idv(k, "function"):
            k += 1
            if self.p(k, "*"):
                k += 1
            if self.idv(k):
                k += 1
        if self.p(k, "<"):
            k = self.skip_angle(k)
        return k if self.p(k, "(") else None

    # ------------------------------------------------------------ statements
    def run(self) -> dict:
        toks = self.toks
        i = 0
        # directives
        while i < self.n and toks[i].t == "str" and not toks[i].tpl:
            nxt = i + 1
            if nxt < self.n and not (self.p(nxt, ";") or toks[nxt].nl):
                break
            self.directives.append(toks[i].v)
            i = nxt + 1 if self.p(nxt, ";") else nxt
        self._collect_top_names()
        i = 0
        while i < self.n:
            i = self.statement(i)
        exported_locals = {e.get("local") for e in self.list_exports + self.exports if e.get("local")}
        for s in self.symbols:
            if s["name"] in exported_locals:
                s["exported"] = True
        self._scan_calls()
        self._scan_vars()
        self._scan_routes()
        jsx: list[str] = []
        seen = set()
        for t in toks:
            if t.t == "jsx_open" and is_comp_tag(t.v) and t.v not in seen:
                seen.add(t.v)
                jsx.append(t.v)
        return {
            "path": self.rel,
            "imports": self.imports,
            "exports": self.list_exports + self.exports,
            "symbols": self.symbols,
            "jsx": jsx,
            "calls": self.calls,
            "route_objects": self.route_objects,
            "dyn_locals": self.dyn_locals,
            "vars": self.vars,
            "consts": self.consts,
            "directives": self.directives,
        }

    def _collect_top_names(self) -> None:
        """Pre-pass: import locals and top-level declaration names (refs are filtered by them)."""
        toks = self.toks
        i = 0
        while i < self.n:
            t = toks[i]
            k = i
            while self.p(k, "@"):
                _, k = self.decorator(k)
            if self.idv(k, "export"):
                k += 1
                if self.idv(k, "default"):
                    k += 1
            if self.idv(k, "declare"):
                i = self.stmt_end(k)
                continue
            if self.idv(k, "import") and not (self.p(k + 1, "(") or self.p(k + 1, ".")):
                end = self.stmt_end(k)
                for _imp, local in self._import_names(k, end):
                    self.import_locals.add(local)
                i = end
                continue
            j = k
            if self.idv(j, "async") or self.idv(j, "abstract"):
                j += 1
            if self.idv(j, "const") and self.idv(j + 1, "enum"):
                j += 1
            if toks[j].t == "id" if j < self.n else False:
                kw = toks[j].v
                if kw in ("function", "class", "interface", "enum") or (kw == "type" and self.idv(j + 1) and (self.p(j + 2, "=") or self.p(j + 2, "<"))):
                    q = j + 1
                    if self.p(q, "*"):
                        q += 1
                    if self.idv(q) and toks[q].v not in ("extends", "implements"):
                        self.top_names.add(toks[q].v)
                elif kw in ("const", "let", "var"):
                    q = j + 1
                    while q < self.n:
                        if self.idv(q):
                            self.top_names.add(toks[q].v)
                        q2 = q + 1 if not (toks[q].t == "p" and toks[q].v in ("{", "[")) else self.m[q] + 1
                        if self.p(q2, "!"):
                            q2 += 1
                        if self.p(q2, ":"):
                            q2 = self.type_end(q2 + 1)
                        e = self.scan_end(q2 + 1, stop_comma=True) if self.p(q2, "=") else q2
                        if self.p(e, ","):
                            q = e + 1
                            continue
                        break
            i = self.statement_skip(i)
        self.known = self.import_locals | self.top_names

    def statement_skip(self, i: int) -> int:
        """Like statement() but without side effects: just finds where the statement ends."""
        k = i
        while self.p(k, "@"):
            _, k = self.decorator(k)
        if self.idv(k, "export"):
            k += 1
            if self.idv(k, "default"):
                k += 1
        end = self._decl_end(k)
        return end if end is not None else self.stmt_end(k if k > i else i)

    def _decl_end(self, j: int) -> int | None:
        toks = self.toks
        k = j
        if self.idv(k, "async") and self.idv(k + 1, "function"):
            k += 1
        if self.idv(k, "function"):
            k += 1
            if self.p(k, "*"):
                k += 1
            if self.idv(k) and not self.p(k, "("):
                k += 1
            if self.p(k, "<"):
                k = self.skip_angle(k)
            if not self.p(k, "("):
                return None
            body = self.find_body(self.m[k] + 1)
            return self.m[body] + 1 if body is not None else self.sig_end(self.m[k])
        if self.idv(k, "abstract") and self.idv(k + 1, "class"):
            k += 1
        if self.idv(k, "class") or self.idv(k, "interface") or self.idv(k, "enum") or (self.idv(k, "const") and self.idv(k + 1, "enum")):
            body = self._class_body(k)
            return self.m[body] + 1 if body is not None else None
        if self.idv(k, "namespace") or self.idv(k, "module"):
            if k + 1 < self.n and toks[k + 1].t in ("id", "str") and self.p(k + 2, "{"):
                return self.m[k + 2] + 1
        return None

    def _class_body(self, k: int) -> int | None:
        """From the class/interface/enum keyword: index of the body '{'."""
        toks = self.toks
        j = k + 1
        angle = 0
        while j < self.n:
            t = toks[j]
            if t.t == "p":
                if t.v == "<":
                    angle += 1
                elif t.v in (">", ">>", ">>>"):
                    angle -= len(t.v)
                elif t.v in ("(", "["):
                    j = self.m[j]
                elif t.v == "{":
                    if angle <= 0:
                        return j
                    j = self.m[j]
                elif t.v in (";", ")", "]", "}"):
                    return None
            j += 1
        return None

    def statement(self, i: int) -> int:
        toks = self.toks
        start = i
        decos: list[dict] = []
        while self.p(i, "@"):
            d, i = self.decorator(i)
            decos.append(d)
        if i >= self.n:
            return i
        t = toks[i]
        if t.t == "id":
            v = t.v
            if v == "import" and not (self.p(i + 1, "(") or self.p(i + 1, ".")):
                return self.import_decl(i, start)
            if v == "export":
                return self.export_decl(i, start, decos)
            if v == "declare":
                return self.statement_skip(i + 1) if self._decl_end(i + 1) else self.stmt_end(i)
            r = self.declaration(i, start, decos, exported=False, default=False)
            if r is not None:
                return r
            if v in ("module", "exports"):
                return self.cjs_export(i, start)
        if t.t == "p" and t.v == ";":
            return i + 1
        return self.stmt_end(i)

    def _import_names(self, i: int, end: int) -> list[tuple[str, str]]:
        return self._parse_import(i, end)[1]

    def _parse_import(self, i: int, end: int) -> tuple[str | None, list[list[str]], str]:
        """Returns (spec, names, kind) for an import declaration starting at i."""
        toks = self.toks
        j = i + 1
        type_only = False
        if self.idv(j, "type") and j + 1 < end and (toks[j + 1].t == "id" and toks[j + 1].v != "from" or self.p(j + 1, "{") or self.p(j + 1, "*")):
            type_only = True
            j += 1
        if j < end and toks[j].t == "str":
            return toks[j].v, [], "side-effect"
        names: list[list[str]] = []
        any_value = False
        if self.idv(j) and toks[j].v != "from" or (self.idv(j, "from") and self.idv(j + 1, "from")):
            if self.p(j + 1, "="):
                # import x = require('y')
                k = j + 2
                if self.idv(k, "require") and self.p(k + 1, "(") and toks[k + 2].t == "str":
                    return toks[k + 2].v, [["*", toks[j].v]], "type" if type_only else "require"
                return None, [], "import"
            names.append(["default", toks[j].v])
            any_value = True
            j += 1
            if self.p(j, ","):
                j += 1
        if self.p(j, "*") and self.idv(j + 1, "as") and self.idv(j + 2):
            names.append(["*", toks[j + 2].v])
            any_value = True
            j += 3
        elif self.p(j, "{"):
            close = self.m[j]
            for x, y in self.split(j + 1, close):
                k = x
                el_type = False
                if self.idv(k, "type") and k + 1 < y and toks[k + 1].t in ("id", "str") and not self.idv(k + 1, "as"):
                    el_type = True
                    k += 1
                if k >= y or toks[k].t not in ("id", "str"):
                    continue
                imported = toks[k].v
                local = imported
                if self.idv(k + 1, "as") and k + 2 < y:
                    local = toks[k + 2].v
                names.append([imported, local])
                if not el_type:
                    any_value = True
            j = close + 1
        spec = None
        if self.idv(j, "from") and j + 1 < self.n and toks[j + 1].t == "str":
            spec = toks[j + 1].v
        kind = "type" if type_only or (not any_value and names) else "import"
        return spec, names, kind

    def import_decl(self, i: int, start: int) -> int:
        end = self.stmt_end(i)
        spec, names, kind = self._parse_import(i, end)
        if spec is not None:
            for _imp, local in names:
                self.import_locals.add(local)
            self.imports.append({"spec": spec, "kind": kind, "names": names, "line": self.line(start)})
        return end

    def export_decl(self, i: int, start: int, decos: list[dict]) -> int:
        toks = self.toks
        j = i + 1
        line = self.line(start)
        if self.idv(j, "default"):
            j += 1
            r = self.declaration(j, start, decos, exported=True, default=True)
            if r is not None:
                return r
            end = self.stmt_end(j)
            e = end - 1 if self.p(end - 1, ";") else end
            a, b = self.unwrap(j, e)
            if b - a == 1 and toks[a].t == "id" and toks[a].v not in NOT_IDENT_ARG:
                self.exports.append({"name": "default", "kind": "id", "local": toks[a].v, "line": line})
            elif a < b and self.is_fn(a, b):
                self.exports.append({"name": "default", "kind": "function", "line": line})
            elif self.idv(a, "class"):
                self.exports.append({"name": "default", "kind": "class", "line": line})
            else:
                self.exports.append({"name": "default", "kind": "expr", "line": line})
            return end
        if self.p(j, "="):
            end = self.stmt_end(j + 1)
            e = end - 1 if self.p(end - 1, ";") else end
            a, b = self.unwrap(j + 1, e)
            if b - a == 1 and toks[a].t == "id":
                self.exports.append({"name": "default", "kind": "id", "local": toks[a].v, "line": line})
            elif a < b and self.is_fn(a, b):
                self.exports.append({"name": "default", "kind": "function", "line": line})
            else:
                self.exports.append({"name": "default", "kind": "expr", "line": line})
            return end
        if self.p(j, "*"):
            end = self.stmt_end(j)
            k = j + 1
            names: list[list[str]] = []
            kind = "reexport-all"
            if self.idv(k, "as") and k + 1 < self.n:
                names = [["*", toks[k + 1].v]]
                kind = "reexport"
                k += 2
            if self.idv(k, "from") and toks[k + 1].t == "str":
                self.imports.append({"spec": toks[k + 1].v, "kind": kind, "names": names, "line": line})
            return end
        type_list = self.idv(j, "type") and self.p(j + 1, "{")
        if type_list:
            j += 1
        if self.p(j, "{"):
            close = self.m[j]
            pairs = []
            for x, y in self.split(j + 1, close):
                k = x
                if self.idv(k, "type") and k + 1 < y and toks[k + 1].t in ("id", "str") and not self.idv(k + 1, "as"):
                    k += 1
                if k >= y or toks[k].t not in ("id", "str"):
                    continue
                prop = toks[k].v
                name = toks[k + 2].v if self.idv(k + 1, "as") and k + 2 < y else prop
                pairs.append([prop, name])
            end = self.stmt_end(close + 1) if self.idv(close + 1, "from") else (close + 2 if self.p(close + 1, ";") else close + 1)
            if self.idv(close + 1, "from") and close + 2 < self.n and toks[close + 2].t == "str":
                self.imports.append({"spec": toks[close + 2].v, "kind": "reexport", "names": pairs, "line": line})
            else:
                for prop, name in pairs:
                    self.list_exports.append({"name": name, "kind": "local", "local": prop, "line": line})
            return end
        if self.idv(j, "declare") or self.idv(j, "import") or self.idv(j, "as"):
            return self.stmt_end(j)
        r = self.declaration(j, start, decos, exported=True, default=False)
        return r if r is not None else self.stmt_end(j)

    def declaration(self, i: int, start: int, decos: list[dict], exported: bool, default: bool) -> int | None:
        toks = self.toks
        if i >= self.n or toks[i].t != "id":
            return None
        v = toks[i].v
        if v == "async" and self.idv(i + 1, "function") or v == "function":
            return self.function_decl(i, start, exported, default)
        if v == "class" or (v == "abstract" and self.idv(i + 1, "class")):
            return self.class_decl(i, start, decos, exported, default)
        if v in ("const", "let", "var") and not self.idv(i + 1, "enum"):
            if self.idv(i + 1) or self.p(i + 1, "{") or self.p(i + 1, "["):
                return self.var_decl(i, start, exported)
            return None
        if v in ("interface", "enum") or (v == "const" and self.idv(i + 1, "enum")):
            k = i + 1 if v != "const" else i + 2
            if not self.idv(k):
                return None
            body = self._class_body(k - 1 if v != "const" else k - 1)
            end = self.m[body] + 1 if body is not None else self.stmt_end(i)
            self._type_symbol(toks[k].v, k, start, end, exported, self.line(start))
            if v == "interface" and body is not None:
                _ext, impl = self.heritage(k + 1, body, is_class=False)
                if impl:
                    self.symbols[-1]["implements"] = impl
            return end
        if v == "type" and self.idv(i + 1) and (self.p(i + 2, "=") or self.p(i + 2, "<")):
            end = self.stmt_end(i + 1)
            self._type_symbol(toks[i + 1].v, i + 1, start, end, exported, self.line(start))
            return end
        if v in ("namespace", "module") and i + 1 < self.n and toks[i + 1].t in ("id", "str") and self.p(i + 2, "{"):
            return self.m[i + 2] + 1
        return None

    def _type_symbol(self, name: str, name_idx: int, start: int, end: int, exported: bool, line: int) -> None:
        self.symbols.append({"name": name, "kind": "type", "line": self.line(name_idx), "end": self.end_line(end),
                             "exported": exported, "refs": self.refs(start, end, name), "members": self.members(start, end, name)})
        if exported:
            self.exports.append({"name": name, "kind": "type", "line": line})

    def function_decl(self, i: int, start: int, exported: bool, default: bool) -> int:
        toks = self.toks
        k = i + 1 if toks[i].v == "async" else i
        k += 1
        if self.p(k, "*"):
            k += 1
        name = None
        name_idx = k
        if self.idv(k):
            name = toks[k].v
            k += 1
        if self.p(k, "<"):
            k = self.skip_angle(k)
        if not self.p(k, "("):
            return self.stmt_end(i)
        body = self.find_body(self.m[k] + 1)
        if body is None:
            return self.sig_end(self.m[k])
        end = self.m[body] + 1
        kind = self.fn_kind(name, start, end)
        if name:
            self.symbols.append({"name": name, "kind": kind, "line": self.line(name_idx), "end": self.end_line(end),
                                 "exported": exported, "refs": self.refs(start, end, name), "members": self.members(start, end, name)})
            self.sym_ranges.append((start, end, name))
        if exported:
            ex = {"name": "default" if default else name, "kind": kind, "line": self.line(start)}
            if default and name:
                ex["local"] = name
            self.exports.append(ex)
        return end

    def class_decl(self, i: int, start: int, decos: list[dict], exported: bool, default: bool) -> int:
        toks = self.toks
        k = i + 1 if toks[i].v == "abstract" else i
        name = None
        name_idx = k + 1
        if self.idv(k + 1) and toks[k + 1].v not in ("extends", "implements"):
            name = toks[k + 1].v
        body = self._class_body(k)
        if body is None:
            return self.stmt_end(i)
        end = self.m[body] + 1
        ctor, methods, ranges = self.class_body(body, name or "")
        if name:
            ext, impl = self.heritage(name_idx + 1, body, is_class=True)
            self.symbols.append({
                "name": name, "kind": "class", "line": self.line(name_idx), "end": self.end_line(end),
                "exported": exported, "refs": self.refs(start, end, name), "members": self.members(start, end, name), "decorators": decos,
                "ctor_types": ctor, "methods": methods,
                "extends": ext, "implements": impl, "abstract": toks[i].v == "abstract",
            })
            self.sym_ranges.extend(ranges)
            self.sym_ranges.append((start, end, name))
        if exported:
            ex = {"name": "default" if default else name, "kind": "class", "line": self.line(start)}
            if default and name:
                ex["local"] = name
            self.exports.append(ex)
        return end

    def heritage(self, a: int, b: int, is_class: bool) -> tuple[str | None, list[str]]:
        """`<T> extends A.B<T> implements I, J` between the name and the body '{' -> ('B', ['I', 'J']);
        mixin calls (`extends Mixin(Base)`) are not names."""
        toks = self.toks
        ext, impl = None, []
        k = a
        if self.p(k, "<"):
            k = self.skip_angle(k)
        mode = None
        while k < b:
            if self.idv(k, "extends") or self.idv(k, "implements"):
                mode = "ext" if toks[k].v == "extends" and is_class else "impl"
                k += 1
                continue
            if mode and toks[k].t == "id":
                j = k
                while self.p(j + 1, ".") and j + 2 < b and toks[j + 2].t == "id":
                    j += 2
                name: str | None = toks[j].v
                k = j + 1
                if self.p(k, "<"):
                    k = self.skip_angle(k)
                if self.p(k, "("):
                    k = self.m[k] + 1
                    name = None
                if name:
                    if mode == "ext":
                        ext = ext if ext is not None else name
                    else:
                        impl.append(name)
                continue
            k += 1
        return ext, impl

    def class_body(self, body: int, cls: str) -> tuple[dict, list[dict], list[tuple[int, int, str]]]:
        toks = self.toks
        close = self.m[body]
        ctor: dict[str, str] = {}
        methods: list[dict] = []
        ranges: list[tuple[int, int, str]] = []
        k = body + 1
        while k < close:
            if self.p(k, ";") or self.p(k, ","):
                k += 1
                continue
            mstart = k
            mdecos = []
            while self.p(k, "@"):
                d, k = self.decorator(k)
                mdecos.append(d)
            accessor = False
            while k < close and toks[k].t == "id" and toks[k].v in MODIFIERS and k + 1 < close and \
                    (toks[k + 1].t in ("id", "str", "num") or self.p(k + 1, "[") or self.p(k + 1, "*") or self.p(k + 1, "{")) \
                    and not toks[k + 1].nl:
                if toks[k].v in ("get", "set"):
                    accessor = True
                k += 1
            if self.p(k, "*"):
                k += 1
            if self.p(k, "{"):  # static block
                k = self.m[k] + 1
                continue
            name = None
            name_idx = k
            if k < close and toks[k].t in ("id", "str", "num"):
                name = toks[k].v
                self.def_names.add(k)
                k += 1
            elif self.p(k, "["):
                k = self.m[k] + 1
            else:
                k += 1
                continue
            if self.p(k, "?") or self.p(k, "!"):
                k += 1
            if self.p(k, "<"):
                k = self.skip_angle(k)
            if self.p(k, "("):
                pclose = self.m[k]
                bodyi = self.find_body(pclose + 1)
                if bodyi is None:
                    end = self.sig_end(pclose)
                    end = min(end, close)
                else:
                    end = self.m[bodyi] + 1
                if name == "constructor":
                    for pname, tn, _ in self.params(k):
                        if pname and tn:
                            ctor[pname] = tn
                    ranges.append((mstart, end, f"{cls}.constructor"))
                elif name is not None:
                    if bodyi is not None and not accessor:
                        methods.append(self.method_info(mstart, end, name, name_idx, k, mdecos))
                    ranges.append((mstart, end, f"{cls}.{name}"))
                k = end
                continue
            # property
            tn = None
            if self.p(k, ":"):
                te = self.type_end(k + 1, close)
                tn = self.type_name(k + 1, te)
                k = te
            init = None
            if self.p(k, "="):
                e = self.scan_end(k + 1, limit=close)
                init = (k + 1, e)
                k = e
            end = k + 1 if self.p(k, ";") else k
            if end <= mstart:
                end = mstart + 1
            if name is not None:
                if init and self.is_fn(*self.unwrap(*init)):
                    ia, ib = self.unwrap(*init)
                    methods.append(self.method_info(mstart, end, name, name_idx, self.fn_params_open(ia, ib), mdecos))
                else:
                    if tn and mdecos:
                        ctor[name] = tn
                    if init:
                        ia, ib = self.unwrap(*init)
                        cp = self.call_parts(ia, ib)
                        if cp and cp[0] == "inject":
                            first = self.split(cp[1] + 1, self.m[cp[1]])
                            if first and first[0][1] - first[0][0] == 1 and toks[first[0][0]].t == "id":
                                ctor[name] = toks[first[0][0]].v
                ranges.append((mstart, end, f"{cls}.{name}"))
            k = end
        return ctor, methods, ranges

    def var_decl(self, i: int, start: int, exported: bool) -> int:
        toks = self.toks
        j = i + 1
        line = self.line(start)
        while j < self.n:
            name = None
            name_idx = j
            if self.idv(j):
                name = toks[j].v
                j += 1
            elif self.p(j, "{") or self.p(j, "["):
                j = self.m[j] + 1
            else:
                break
            if self.p(j, "!"):
                j += 1
            if self.p(j, ":"):
                j = self.type_end(j + 1)
            init = None
            if self.p(j, "="):
                e = self.scan_end(j + 1, stop_comma=True)
                init = (j + 1, e)
                j = e
            if name:
                self._var_symbol(name, name_idx, j, init, exported, line)
            if self.p(j, ","):
                j += 1
                continue
            break
        if self.p(j, ";"):
            j += 1
        return j if j > i else i + 1

    def _var_symbol(self, name: str, name_idx: int, end: int, init: tuple[int, int] | None, exported: bool, line: int) -> None:
        toks = self.toks
        kind = self.var_kind(name, *init) if init else "const"
        sym = {"name": name, "kind": kind, "line": self.line(name_idx), "end": self.end_line(end),
               "exported": exported, "refs": self.refs(name_idx, end, name), "members": self.members(name_idx, end, name)}
        if init:
            ia, ib = self.unwrap(*init)
            if self.p(ia, "{") and self.m[ia] == ib - 1:
                methods = []
                for key, kind2, vs, ve, ps in self.obj_props(ia):
                    if kind2 == "method":
                        o = vs if self.p(vs, "(") else (self.skip_angle(vs) if self.p(vs, "<") else None)
                        if o is not None and self.p(o, "(") and self.find_body(self.m[o] + 1) is not None:
                            methods.append(self.method_info(ps, ve, key, ps if toks[ps].v == key else vs - 1, o, []))
                    elif kind2 == "assign":
                        va, vb = self.unwrap(vs, ve)
                        if va < vb and self.is_fn(va, vb):
                            methods.append(self.method_info(ps, ve, key, ps if toks[ps].v == key else vs - 2,
                                                            self.fn_params_open(va, vb), []))
                    self.sym_ranges.append((ps, ve, f"{name}.{key}"))
                if methods:
                    sym["methods"] = methods
            if exported and (self.p(ia, "{") and self.m[ia] == ib - 1 or self.p(ia, "[") and self.m[ia] == ib - 1
                             or ib - ia == 1 and toks[ia].t == "str" or self.is_tpl_whole(ia, ib)):
                self.consts[name] = self.to_arg(ia, ib)
            for j in range(init[0], init[1] - 2):
                if self.idv(j, "import") and self.p(j + 1, "(") and toks[j + 2].t == "str" and not self.p(j - 1, "."):
                    self.dyn_locals[name] = toks[j + 2].v
                    break
        self.symbols.append(sym)
        self.sym_ranges.append((name_idx, end, name))
        if exported:
            self.exports.append({"name": name, "kind": kind, "line": line})

    def cjs_export(self, i: int, start: int) -> int:
        toks = self.toks
        end = self.stmt_end(i)
        line = self.line(start)
        k = i
        parts = []
        while k < end and toks[k].t == "id":
            parts.append(toks[k].v)
            if self.p(k + 1, "."):
                k += 2
            else:
                k += 1
                break
        if not self.p(k, "="):
            return end
        left = ".".join(parts)
        e = end - 1 if self.p(end - 1, ";") else end
        a, b = self.unwrap(k + 1, e)
        if left == "module.exports":
            if b - a == 1 and toks[a].t == "id":
                self.exports.append({"name": "default", "kind": "cjs", "local": toks[a].v, "line": line})
            elif self.p(a, "{") and self.m[a] == b - 1:
                for key, kind, vs, ve, _ in self.obj_props(a):
                    ex = {"name": key, "kind": "cjs", "line": line}
                    if kind == "short":
                        ex["local"] = key
                    elif kind == "assign":
                        va, vb = self.unwrap(vs, ve)
                        if vb - va == 1 and toks[va].t == "id" and toks[va].v not in NOT_IDENT_ARG:
                            ex["local"] = toks[va].v
                    if kind != "accessor":
                        self.exports.append(ex)
            else:
                self.exports.append({"name": "default", "kind": "cjs", "line": line})
        elif re.fullmatch(r"(module\.)?exports\.[\w$]+", left):
            ex = {"name": parts[-1], "kind": "cjs", "line": line}
            if b - a == 1 and toks[a].t == "id":
                ex["local"] = toks[a].v
            self.exports.append(ex)
        return end

    # ------------------------------------------------------------ whole-file scans
    def sym_at(self, idx: int) -> str | None:
        best = None
        for a, b, name in self.sym_ranges:
            if a <= idx < b and (best is None or b - a < best[1] - best[0]):
                best = (a, b, name)
        return best[2] if best else None

    def _callee_end(self, o: int) -> int:
        """Index of the last token of the callee for the call whose '(' is at o (skips type arguments)."""
        if self.p(o - 1, ">") or self.p(o - 1, ">>") or self.p(o - 1, ">>>"):
            lt = self._angle_open(o - 1, max(0, o - 40))
            if lt is not None and lt > 0 and self.toks[lt - 1].t == "id":
                return lt - 1
        return o - 1

    def _chain_back(self, k: int) -> tuple[list[str], int]:
        """Identifier chain ending at k -> (parts, start_index)."""
        toks = self.toks
        parts = [toks[k].v]
        while k - 2 >= 0 and toks[k - 1].t == "p" and toks[k - 1].v in (".", "?.") and toks[k - 2].t == "id":
            parts.insert(0, toks[k - 2].v)
            k -= 2
        return parts, k

    def _obj_text(self, e: int) -> tuple[str | None, int]:
        """chainText of the expression ending at e -> (text or None, start index)."""
        toks = self.toks
        if e < 0 or toks[e].t != "id":
            return None, e
        parts, s = self._chain_back(e)
        if s > 0 and toks[s - 1].t == "p" and toks[s - 1].v in (".", "?."):
            return None, s
        if s > 0 and self.idv(s - 1, "new"):
            return None, s
        if parts[0] in ("super", "import") or (parts[0] in KEYWORDS and parts[0] != "this"):
            return None, s
        return ".".join(parts), s

    def _expr_start(self, k: int) -> int:
        """Start of a member/call chain whose last callee token is k."""
        toks = self.toks
        while True:
            _, s = self._chain_back(k)
            if s >= 2 and toks[s - 1].t == "p" and toks[s - 1].v in (".", "?.") and self.p(s - 2, ")"):
                o = self.open_of[s - 2]
                if o <= 0:
                    return s
                ce = self._callee_end(o)
                if toks[ce].t != "id":
                    return o
                k = ce
                continue
            return s

    def _scan_calls(self) -> None:
        toks, m = self.toks, self.m
        for o in range(1, self.n):
            t = toks[o]
            if t.t != "p" or t.v != "(":
                continue
            ce = self._callee_end(o)
            c = toks[ce]
            if c.t != "id":
                continue
            before = toks[ce - 1] if ce > 0 else None
            member = before is not None and before.t == "p" and before.v in (".", "?.")
            if not member:
                if c.v == "import" and ce == o - 1:
                    first = self.split(o + 1, m[o])
                    if first and first[0][1] - first[0][0] == 1 and toks[first[0][0]].t == "str":
                        self.imports.append({"spec": toks[first[0][0]].v, "kind": "dynamic", "names": [], "line": c.line})
                    continue
                if before is not None and (self.idv(ce - 1, "function") or self.idv(ce - 1, "new") or (before.t == "p" and before.v == "@")):
                    continue
                if ce in self.def_names or self.p(m[o] + 1, "{"):
                    continue
                if c.v == "require" and ce == o - 1:
                    self._require(ce, o)
                    continue
                if c.v in CALLEES or c.v.startswith("useSWR"):
                    self.calls.append({"obj": None, "prop": None, "callee": c.v, "args": self.args(o),
                                       "line": c.line, "sym": self.sym_at(ce)})
                elif m[o] > o + 1:
                    # request('/me'), api(`/users/${id}`): calls to local HTTP wrappers (frameworks/api_calls)
                    args = self.args(o)
                    if args and args[0].get("t") in ("str", "tpl") and args[0]["v"].startswith("/"):
                        self.calls.append({"obj": None, "prop": None, "callee": c.v, "args": args,
                                           "line": c.line, "sym": self.sym_at(ce)})
                continue
            prop = c.v
            # the full callee must not be preceded by `new`
            _, cs = self._chain_back(ce)
            if cs > 0 and self.idv(cs - 1, "new"):
                continue
            pre: list[dict] = []
            e = ce - 2
            while self.p(e, ")"):
                io = self.open_of[e]
                ice = self._callee_end(io)
                if io <= 0 or toks[ice].t != "id" or not (ice > 0 and toks[ice - 1].t == "p" and toks[ice - 1].v in (".", "?.")):
                    break
                ip = toks[ice].v
                if ip == "route" and len(self.split(io + 1, m[io])) == 1:
                    pre = [self.args(io)[0]]
                    e = ice - 2
                    break
                if ip in HTTP:
                    e = ice - 2
                    continue
                break
            obj, _ = self._obj_text(e)
            if obj is None and prop in HTTP and self.p(e, ")") and self.open_of[e] > 0:
                # request(app).post('/x') (supertest): the object is the result of a call -> 'request()'
                ice = self._callee_end(self.open_of[e])
                cc, _ = self._obj_text(ice) if toks[ice].t == "id" else (None, 0)
                obj = f"{cc}()" if cc else None
            if not obj or obj.split(".")[-1] in OBJ_DENY:
                continue
            if prop in ROUTE_PROPS or prop.startswith("useGlobal") or obj in CLIENT_OBJS:
                start = self._expr_start(ce)
                self.calls.append({"obj": obj, "prop": prop, "callee": None, "args": pre + self.args(o),
                                   "line": toks[start].line, "sym": self.sym_at(start)})

    def _require(self, ce: int, o: int) -> None:
        toks, m = self.toks, self.m
        args = self.split(o + 1, m[o])
        if len(args) != 1 or args[0][1] - args[0][0] != 1 or toks[args[0][0]].t != "str":
            return
        spec = toks[args[0][0]].v
        names: list[list[str]] = []
        close = m[o]
        nxt = close + 1
        direct_end = nxt >= self.n or self.p(nxt, ";") or self.p(nxt, ",") or self.p(nxt, ")") or self.p(nxt, "}") \
            or toks[nxt].nl and self.asi(toks[close], toks[nxt])
        if self.p(ce - 1, "=") and direct_end:
            k = ce - 2
            if self.p(k, "}"):
                k = self.open_of[k]
            if k > 0 and toks[k - 1].t == "id" and toks[k - 1].v in ("const", "let", "var"):
                if self.idv(k):
                    names = [["*", toks[k].v]]
                elif self.p(k, "{"):
                    for x, y in self.split(k + 1, m[k]):
                        if self.p(x, "...") and self.idv(x + 1):
                            names.append([toks[x + 1].v, toks[x + 1].v])
                        elif self.idv(x) and self.p(x + 1, ":") and self.idv(x + 2) and (x + 3 >= y or self.p(x + 3, "=")):
                            names.append([toks[x].v, toks[x + 2].v])
                        elif toks[x].t in ("id",) and (x + 1 >= y or self.p(x + 1, "=")):
                            names.append([toks[x].v, toks[x].v])
        self.imports.append({"spec": spec, "kind": "require", "names": names, "line": toks[ce].line})

    def _scan_vars(self) -> None:
        toks = self.toks
        for i in range(self.n):
            t = toks[i]
            if t.t != "id" or t.v not in ("const", "let", "var"):
                continue
            j = i + 1
            while j < self.n:
                if not self.idv(j):
                    if self.p(j, "{") or self.p(j, "["):
                        k = self.m[j] + 1
                        if self.p(k, ":"):
                            k = self.type_end(k + 1)
                        if self.p(k, "="):
                            e = self.scan_end(k + 1, stop_comma=True)
                            if self.p(e, ","):
                                j = e + 1
                                continue
                    break
                name = toks[j].v
                k = j + 1
                if self.p(k, "!"):
                    k += 1
                if self.p(k, ":"):
                    k = self.type_end(k + 1)
                if not self.p(k, "="):
                    if self.p(k, ","):
                        j = k + 1
                        continue
                    break
                e = self.scan_end(k + 1, stop_comma=True)
                if name not in self.vars:
                    c = self._init_callee(k + 1, e)
                    if c:
                        self.vars[name] = c
                if self.p(e, ","):
                    j = e + 1
                    continue
                break

    def _init_callee(self, a: int, e: int) -> str | None:
        a, e = self.unwrap(a, e)
        if self.idv(a, "await"):
            a, e = self.unwrap(a + 1, e)
        if a >= e:
            return None
        if self.idv(a, "new"):
            k = a + 1
            q = k
            while self.p(q + 1, ".") and self.idv(q + 2):
                q += 2
            rest = q + 1
            if self.p(rest, "<"):
                rest = self.skip_angle(rest)
            if self.p(rest, "("):
                rest = self.m[rest] + 1
            return self.chain_at(k, q + 1) if rest >= e else None
        cp = self.call_parts(a, e)
        return cp[0] if cp else None

    # ------------------------------------------------------------ React Router
    def _obj_keys(self, a: int) -> set[str]:
        return {k for k, kind, *_ in self.obj_props(a)}

    def _is_route_array(self, a: int) -> bool:
        for x, y in self.split(a + 1, self.m[a]):
            if self.p(x, "{") and self.m[x] == y - 1:
                keys = self._obj_keys(x)
                if keys & {"path", "index"} and keys & set(ROUTE_KEYS):
                    return True
        return False

    def _comps_in(self, a: int, e: int) -> list[str]:
        tags = [self.toks[j].v for j in range(a, min(e, self.n)) if self.toks[j].t == "jsx_open" and is_comp_tag(self.toks[j].v)]
        return tags[::-1]

    def _import_in(self, a: int, e: int) -> str | None:
        toks = self.toks
        for j in range(a, min(e, self.n) - 2):
            if self.idv(j, "import") and self.p(j + 1, "(") and toks[j + 2].t == "str" and not self.p(j - 1, "."):
                return toks[j + 2].v
        return None

    def _route_from_obj(self, a: int) -> dict:
        toks = self.toks
        r: dict = {"path": None, "comps": [], "lazy": None, "children": []}
        el_comps: list[str] = []
        comp_comps: list[str] = []
        for key, kind, vs, ve, _ in self.obj_props(a):
            if kind != "assign":
                if kind == "short" and key == "Component":
                    comp_comps.append(key)
                continue
            va, vb = self.unwrap(vs, ve)
            if key == "path":
                x = self.to_arg(va, vb)
                if x["t"] in ("str", "tpl"):
                    r["path"] = x["v"]
            elif key == "index":
                r["index"] = True
            elif key == "element":
                el_comps.extend(self._comps_in(va, vb))
            elif key == "Component" and vb - va == 1 and toks[va].t == "id":
                comp_comps.append(toks[va].v)
            elif key == "lazy":
                r["lazy"] = self._import_in(va, vb)
            elif key == "children" and self.p(va, "[") and self.m[va] == vb - 1:
                r["children"] = [self._route_from_obj(x) for x, y in self.split(va + 1, vb - 1)
                                 if self.p(x, "{") and self.m[x] == y - 1]
        r["comps"] = el_comps + comp_comps
        return r

    def _attr_value(self, rng: tuple[int, int]) -> tuple[int, int]:
        a, e = rng
        if self.p(a, "{") and self.m[a] == e - 1:
            return self.unwrap(a + 1, e - 1)
        return a, e

    def _route_from_el(self, el: _El) -> dict:
        toks = self.toks
        r: dict = {"path": None, "comps": [], "lazy": None, "children": []}
        el_comps: list[str] = []
        comp_comps: list[str] = []
        for key, rng in el.attrs.items():
            va, vb = self._attr_value(rng)
            if key == "path" and va < vb:
                x = self.to_arg(va, vb)
                if x["t"] in ("str", "tpl"):
                    r["path"] = x["v"]
            elif key == "index":
                r["index"] = True
            elif key == "element":
                el_comps.extend(self._comps_in(va, vb))
            elif key == "Component" and vb - va == 1 and toks[va].t == "id":
                comp_comps.append(toks[va].v)
            elif key == "lazy":
                r["lazy"] = self._import_in(va, vb)
        r["comps"] = el_comps + comp_comps

        def visit(children: list[_El]) -> None:
            for c in children:
                if c.tag == "Route":
                    r["children"].append(self._route_from_el(c))
                else:
                    visit(c.children)

        visit(el.children)
        return r

    def _scan_routes(self) -> None:
        toks = self.toks
        j = 0
        while j < self.n:
            t = toks[j]
            if t.t == "p" and t.v == "[" and self._is_route_array(j):
                self.route_objects.extend(self._route_from_obj(x) for x, y in self.split(j + 1, self.m[j])
                                          if self.p(x, "{") and self.m[x] == y - 1)
                j = self.m[j] + 1
                continue
            if t.t == "jsx_open" and t.v == "Route" and j in self.els:
                el = self.els[j]
                self.route_objects.append(self._route_from_el(el))
                j = el.end + 1
                continue
            j += 1


def extract_source(src: str, rel: str) -> dict:
    return Extractor(src, rel).run()


def extract_path(root: Path, rel: str) -> dict:
    src = (root / rel).read_text(encoding="utf-8", errors="replace")
    return extract_source(src, rel)
