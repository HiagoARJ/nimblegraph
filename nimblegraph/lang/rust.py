"""Rust: `mod x;` and `use` trees (nested lists, aliases, globs), structs/enums/traits, `impl` blocks (methods
attach to the type, `impl Trait for T` reads as implements), attributes (`#[get("/x")]`), calls (`self` reads as
`this`, `a::b()` as obj a / prop b) and Axum/Actix method routers (`get(h).post(h2)`, `web::get().to(h)`)."""

from __future__ import annotations

import re

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, line, new_symbol, text

HTTP = {"get", "post", "put", "patch", "delete", "head", "options", "any", "trace", "connect"}
RE_KV = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
RE_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type in ("type_identifier", "identifier"):
        return text(n)
    if n.type in ("scoped_type_identifier", "scoped_identifier"):
        return text(n.child_by_field_name("name"))
    if n.type in ("generic_type", "reference_type", "pointer_type"):
        inner = n.child_by_field_name("type") or child_of_type(n, "type_identifier", "scoped_type_identifier")
        return type_name(inner)
    return None


class RustExtractor(Extractor):
    lang = "rust"
    grammar = "rust"
    CLASSES = {"struct_item": "class", "enum_item": "class", "union_item": "class", "trait_item": "type",
               "type_item": "type"}
    FUNCTIONS = frozenset({"function_item", "function_signature_item"})
    CALLS = frozenset({"call_expression"})
    IDENTS = frozenset({"identifier", "type_identifier"})
    STRINGS = frozenset({"string_literal", "raw_string_literal"})
    LAMBDAS = frozenset({"closure_expression"})
    MEMBERS = frozenset({"field_expression", "scoped_identifier"})
    ASSIGNS = frozenset({"const_item", "static_item", "let_declaration"})

    def assignment(self, node):
        name = node.child_by_field_name("name") or node.child_by_field_name("pattern")
        value = node.child_by_field_name("value")
        if name is not None and name.type == "identifier" and value is not None:
            return text(name), value
        return None

    def format_call(self, n):
        """format!("{}/users/{id}", base) -> the base value + /users/:param."""
        if n.type != "macro_invocation" or text(n.child_by_field_name("macro") or n.named_children[0]) != "format":
            return None
        tt = child_of_type(n, "token_tree")
        kids = [c for c in tt.named_children] if tt is not None else []
        if not kids or kids[0].type != "string_literal":
            return None
        fmt = self.string_value(kids[0]) or ""
        args = iter(kids[1:])

        def sub(m):
            inner = m.group(1).split(":", 1)[0]
            if inner and not inner.isdigit():  # {name} inline
                v = self.lookup_str(inner)
                return v if v is not None else f"{{={inner}}}"
            a = next(args, None)
            v = self.eval_str(a) if a is not None else None
            return v if v is not None else ":param"
        return re.sub(r"\{([^{}]*)\}", sub, fmt.replace("{{", "\x00").replace("}}", "\x01")).replace(
            "\x00", "{").replace("\x01", "}")

    def __init__(self, src: bytes, rel: str):
        super().__init__(src, rel)
        self._attrs: list[dict] = []
        self._impls: list[tuple[str, list[dict], list]] = []

    # ------------------------------------------------------------ items
    def visit(self, node, cls, fn) -> bool:
        t = node.type
        parent = node.parent.type if node.parent is not None else ""
        if t == "attribute_item":
            a = child_of_type(node, "attribute")
            if a is not None:
                self._attrs.append(self._attribute(a))
            return True
        if parent in ("source_file", "declaration_list") and t not in self.CLASSES and t not in self.FUNCTIONS \
                and t not in ("impl_item", "line_comment", "block_comment"):
            self._attrs = []
        if t == "use_declaration":
            for path, alias in self._use_paths(node.child_by_field_name("argument"), []):
                spec = "::".join(path)
                last = path[-1]
                self.add_import(spec, "use", [[last, alias or last]] if last != "*" else [["*", "*"]], node)
            return True
        if t == "mod_item":
            name = text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            if body is None:
                self.add_import(name, "mod", [], node)
                return True
            for c in body.named_children:  # inline module: its items read as this file's
                self.walk(c, cls, fn)
            return True
        if t == "impl_item":
            owner = type_name(node.child_by_field_name("type")) or "?"
            trait = type_name(node.child_by_field_name("trait"))
            self._attrs = []
            methods: list[dict] = []
            body = node.child_by_field_name("body")
            for item in body.named_children if body is not None else []:
                if item.type == "attribute_item":
                    self.visit(item, cls, fn)
                    continue
                if item.type != "function_item":
                    self.walk(item, cls, fn)
                    continue
                name = text(item.child_by_field_name("name"))
                m = {"name": name, "line": line(item), "refs": [], "members": [], "this_refs": [],
                     "decorators": self._take_attrs(), "param_types": self.param_types(item)}
                self._ptypes[id(m)] = {k: v for k, v in self.ctor_types(item).items()}
                for c in item.named_children:
                    self.walk(c, None, m)
                methods.append(m)
            self._impls.append((owner, methods, [trait] if trait else [], node))
            self.ref(owner, None)
            if trait:
                self.ref(trait, None)
            return True
        if t in ("scoped_identifier", "scoped_type_identifier") and parent not in ("scoped_identifier", "scoped_type_identifier"):
            self.ref(text(node).removeprefix("crate::"), fn or cls)
        return False

    def _use_paths(self, n, prefix: list[str]):
        if n is None:
            return
        t = n.type
        if t in ("identifier", "crate", "self", "super", "metavariable"):
            yield [*prefix, text(n)], None
        elif t == "scoped_identifier":
            yield [*prefix, *text(n).split("::")], None
        elif t == "use_as_clause":
            path = n.child_by_field_name("path")
            for p, _ in self._use_paths(path, prefix):
                yield p, text(n.child_by_field_name("alias"))
        elif t == "use_wildcard":
            inner = n.named_children[0] if n.named_children else None
            base = [*prefix, *text(inner).split("::")] if inner is not None else prefix
            yield [*base, "*"], None
        elif t == "scoped_use_list":
            path = n.child_by_field_name("path")
            base = [*prefix, *text(path).split("::")] if path is not None else prefix
            yield from self._use_paths(n.child_by_field_name("list"), base)
        elif t == "use_list":
            for c in n.named_children:
                if c.type == "self":
                    yield list(prefix), None
                else:
                    yield from self._use_paths(c, prefix)

    def _attribute(self, a) -> dict:
        path = a.named_children[0] if a.named_children else None
        name = text(path).rsplit("::", 1)[-1] if path is not None else ""
        tt = a.child_by_field_name("arguments") or child_of_type(a, "token_tree")
        args: list[dict] = []
        if tt is not None:
            raw = text(tt)
            kv = dict(RE_KV.findall(raw))
            positional = RE_STR.findall(RE_KV.sub("", raw))
            args = [{"t": "str", "v": s} for s in positional]
            if kv:
                args.append({"t": "obj", "props": {k: {"t": "str", "v": v} for k, v in kv.items()}})
            if not positional and not kv:  # derive(Debug, Clone)
                args = [{"t": "id", "v": i} for i in re.findall(r"\b[A-Za-z_]\w*\b", raw)]
        return {"name": name, "args": args}

    def _take_attrs(self) -> list[dict]:
        out, self._attrs = self._attrs, []
        return out

    def decorators(self, node) -> list[dict]:
        return self._take_attrs()

    def exported(self, node, name: str) -> bool:
        return child_of_type(node, "visibility_modifier") is not None

    def fields(self, node) -> dict[str, str]:
        out = {}
        body = node.child_by_field_name("body")
        for f in children_of_type(body, "field_declaration") if body is not None else []:
            tn = type_name(f.child_by_field_name("type"))
            if tn:
                out[text(f.child_by_field_name("name"))] = tn
        return out

    def _params(self, node):
        ps = node.child_by_field_name("parameters")
        return children_of_type(ps, "parameter") if ps is not None else []

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            tn = _inner_type(p.child_by_field_name("type"))
            pat = p.child_by_field_name("pattern")
            if tn and pat is not None and pat.type == "identifier":
                out[text(pat)] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (_inner_type(p.child_by_field_name("type")) for p in self._params(node)) if tn]

    def on_function(self, node, sym: dict) -> None:
        if sym["name"] == "main" and (self.rel.endswith("src/main.rs") or "/bin/" in self.rel or self.rel == "main.rs"):
            self.rec["entry"] = True

    # ------------------------------------------------------------ calls
    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "self":
            return "this"
        if n.type == "identifier":
            return text(n)
        if n.type == "field_expression":
            base = self._obj(n.child_by_field_name("value"))
            return f"{base}.{text(n.child_by_field_name('field'))}" if base else None
        if n.type in ("scoped_identifier",):
            return text(n).replace("::", ".")
        return None

    def call(self, node, cls) -> dict | None:
        f = node.child_by_field_name("function")
        al = node.child_by_field_name("arguments")
        argn = list(al.named_children) if al is not None else []
        args = [self.arg(c) for c in argn]
        if f is None:
            return None
        if f.type == "generic_function":
            f = f.child_by_field_name("function") or f
        out: dict
        if f.type == "field_expression":
            out = {"obj": self._obj(f.child_by_field_name("value")), "prop": text(f.child_by_field_name("field")),
                   "callee": None, "args": args}
        elif f.type == "scoped_identifier":
            path = f.child_by_field_name("path")
            out = {"obj": text(path).replace("::", ".") if path is not None else None,
                   "prop": text(f.child_by_field_name("name")), "callee": None, "args": args}
        else:
            out = {"obj": None, "prop": None, "callee": text(f), "args": args}
        if out.get("prop") in ("route", "service", "to") and len(argn) >= 2:
            out["methods"] = self._method_router(argn[1])
        return out

    def _method_router(self, n) -> list[list]:
        """get(list).post(create) / web::get().to(h) / routing::get(h) -> [[GET, 'list'], [POST, 'create']]."""
        if n is None or n.type != "call_expression":
            return []
        f = n.child_by_field_name("function")
        al = n.child_by_field_name("arguments")
        first = al.named_children[0] if al is not None and al.named_children else None
        h = text(first).replace("::", ".") if first is not None else None
        name = text(f).rsplit("::", 1)[-1] if f is not None and f.type in ("identifier", "scoped_identifier") else None
        if name in HTTP:
            return [[name.upper(), h]]
        if f is not None and f.type == "field_expression":
            field = text(f.child_by_field_name("field"))
            inner = self._method_router(f.child_by_field_name("value"))
            if field in HTTP:
                return [*inner, [field.upper(), h]]
            if field == "to" and inner:  # web::get().to(handler)
                return [[inner[-1][0], h]]
            return inner
        return []

    def arg(self, n) -> dict:
        if n.type == "macro_invocation":  # routes![a, b] / services![...]
            tt = child_of_type(n, "token_tree")
            ids = re.findall(r"[A-Za-z_][\w:]*", text(tt)) if tt is not None else []
            return {"t": "arr", "items": [{"t": "id", "v": i.replace("::", ".")} for i in ids]}
        if n.type == "reference_expression":
            inner = n.named_children[-1] if n.named_children else None
            return self.arg(inner) if inner is not None else {"t": "other"}
        if n.type == "scoped_identifier":
            return {"t": "member", "v": text(n).replace("::", ".")}
        return super().arg(n)

    def string_value(self, n) -> str | None:
        return "".join(text(c) for c in n.named_children if c.type == "string_content") if n.named_children \
            else text(n).strip('r#"')

    def finish(self) -> None:
        by_name = {s["name"]: s for s in self.rec["symbols"]}
        for owner, methods, traits, node in self._impls:
            for m in methods:
                m["refs"] = list(self._owner_refs.get(id(m), {}))[:300]
            sym = by_name.get(owner)
            if sym is not None:
                sym["methods"].extend(methods)
                sym.setdefault("implements", [])
                sym["implements"] += [t for t in traits if t not in sym["implements"]]
                continue
            for m in methods:  # impl of a type declared in another file
                s = new_symbol(f"{owner}.{m['name']}", "function", node, True)
                s["refs"], s["members"], s["decorators"] = m["refs"], m["members"], m["decorators"]
                self.rec["symbols"].append(s)


def _inner_type(n) -> str | None:
    """web::Data<AppState> -> AppState (extractors wrap the real type); &Pool -> Pool."""
    if n is None:
        return None
    if n.type == "generic_type":
        ta = n.child_by_field_name("type_arguments")
        outer = type_name(n.child_by_field_name("type"))
        if ta is not None and outer in ("Data", "State", "Arc", "Box", "Rc", "Extension", "Json", "Path", "Query",
                                        "Form", "Option", "Mutex", "RwLock"):
            inner = [c for c in ta.named_children]
            return _inner_type(inner[0]) if inner else outer
        return outer
    return type_name(n)


def extract(src: bytes, rel: str) -> dict:
    return RustExtractor(src, rel).run()
