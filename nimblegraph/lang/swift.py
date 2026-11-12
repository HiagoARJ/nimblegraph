"""Swift: module imports, classes/structs/enums/actors/extensions and protocols (inheritance clause: first is
the superclass or a protocol), stored properties (for `service.all()`), functions/inits, calls with labelled
arguments (`todos.get(use: index)`) and trailing closures, and `let x = routes.grouped("x")` assignments."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, line, new_symbol, text


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type == "user_type":
        ids = children_of_type(n, "type_identifier")
        return text(ids[-1]) if ids else None
    if n.type in ("optional_type", "array_type", "type_annotation"):
        inner = n.child_by_field_name("name") or n.child_by_field_name("wrapped") or child_of_type(
            n, "user_type", "optional_type", "array_type")
        return type_name(inner)
    if n.type == "type_identifier":
        return text(n)
    return None


class SwiftExtractor(Extractor):
    lang = "swift"
    grammar = "swift"
    CLASSES = {"class_declaration": "class", "protocol_declaration": "type"}
    FUNCTIONS = frozenset({"function_declaration", "init_declaration", "protocol_function_declaration"})
    CALLS = frozenset({"call_expression"})
    IDENTS = frozenset({"simple_identifier", "type_identifier"})
    STRINGS = frozenset({"line_string_literal", "multi_line_string_literal"})
    LAMBDAS = frozenset({"lambda_literal"})
    MEMBERS = frozenset({"navigation_expression"})
    ASSIGNS = frozenset({"property_declaration"})

    def assignment(self, node):
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        return (text(name), value) if name is not None and value is not None else None

    def template_text(self, n):
        return "".join(text(c) if c.type in ("line_str_text", "multi_line_str_text") else "{" + text(c) + "}"
                       for c in n.named_children
                       if c.type in ("line_str_text", "multi_line_str_text", "interpolated_expression"))

    def __init__(self, src: bytes, rel: str):
        super().__init__(src, rel)
        self.rec["assigns"] = []

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "import_declaration":
            mod = text(child_of_type(node, "identifier"))
            if mod:
                self.add_import(mod, "import", [], node)
            return True
        if t == "property_declaration" and fn is not None:  # let todos = routes.grouped("todos")
            pat = node.child_by_field_name("name")
            val = node.child_by_field_name("value")
            if pat is not None:
                self.local(text(pat), fn)
            if pat is not None and val is not None and val.type == "call_expression":
                c = self.call(val, cls)
                if c is not None:
                    self.rec["assigns"].append({"name": text(pat), **c, "line": line(node)})
        return False

    def class_symbol(self, node, outer):
        name_n = node.child_by_field_name("name")
        name = type_name(name_n) if name_n is not None and name_n.type != "type_identifier" else text(name_n)
        if not name:
            return None
        kind_kw = next((text(c) for c in node.children if not c.is_named and text(c) in
                        ("class", "struct", "enum", "actor", "extension")), "class")
        sym = new_symbol(name, "type" if node.type == "protocol_declaration" else "class", node,
                         self.exported(node, name))
        bases = [type_name(c.child_by_field_name("inherits_from") or child_of_type(c, "user_type"))
                 for c in children_of_type(node, "inheritance_specifier")]
        bases = [b for b in bases if b]
        if kind_kw == "class" and bases and not bases[0].endswith(("Protocol", "able", "ing")):
            sym["extends"], sym["implements"] = bases[0], bases[1:]
        else:
            sym["extends"], sym["implements"] = None, bases
        if kind_kw == "extension":
            sym["extension"] = True
        sym["abstract"] = node.type == "protocol_declaration"
        sym["decorators"] = self.decorators(node)
        sym["ctor_types"] = self.fields(node)
        return sym

    def fields(self, node) -> dict[str, str]:
        body = node.child_by_field_name("body")
        out = {}
        for p in children_of_type(body, "property_declaration") if body is not None else []:
            pat = p.child_by_field_name("name")
            ta = child_of_type(p, "type_annotation")
            tn = type_name(ta) if ta is not None else None
            if pat is not None and tn:
                out[text(pat)] = tn
        return out

    def exported(self, node, name: str) -> bool:
        mods = child_of_type(node, "modifiers")
        return mods is None or not any(w in ("private", "fileprivate") for w in text(mods).split())

    def decorators(self, node) -> list[dict]:
        mods = child_of_type(node, "modifiers")
        out = []
        for a in children_of_type(mods, "attribute") if mods is not None else []:
            out.append({"name": type_name(child_of_type(a, "user_type")) or text(a).lstrip("@"), "args": []})
        return out

    def func_name(self, node) -> str | None:
        if node.type == "init_declaration":
            return "init"
        n = node.child_by_field_name("name") or child_of_type(node, "simple_identifier")
        return text(n) if n is not None else None

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return node.type == "init_declaration"

    def _params(self, node):
        return children_of_type(node, "parameter")

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            names = children_of_type(p, "simple_identifier")
            tn = type_name(child_of_type(p, "user_type", "optional_type", "array_type"))
            if names and tn:
                out[text(names[-1])] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (type_name(child_of_type(p, "user_type", "optional_type")) for p in self._params(node))
                if tn]

    def on_function(self, node, sym: dict) -> None:
        if sym["name"] == "main" or self.rel.endswith("main.swift"):
            self.rec["entry"] = True

    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type in ("self_expression",) or text(n) == "self":
            return "this"
        if n.type == "simple_identifier":
            return text(n)
        if n.type == "navigation_expression":
            base = self._obj(n.child_by_field_name("target"))
            suf = n.child_by_field_name("suffix")
            name = text(suf.child_by_field_name("suffix") if suf is not None else None)
            return f"{base}.{name}" if base and name else None
        return None

    def call(self, node, cls) -> dict | None:
        kids = node.named_children
        if not kids:
            return None
        callee = kids[0]
        suffix = child_of_type(node, "call_suffix")
        args, props = [], {}
        va = child_of_type(suffix, "value_arguments") if suffix is not None else None
        for a in children_of_type(va, "value_argument") if va is not None else []:
            label = a.child_by_field_name("name")
            val = a.child_by_field_name("value")
            v = self.arg(val) if val is not None else {"t": "other"}
            if label is not None:
                props[text(label)] = v
            else:
                args.append(v)
        if props:
            args.append({"t": "obj", "props": props})
        lam = child_of_type(suffix, "lambda_literal") if suffix is not None else None
        if lam is not None:
            args.append(self.arg(lam))
        if callee.type == "navigation_expression":
            obj = self._obj(callee.child_by_field_name("target"))
            suf = callee.child_by_field_name("suffix")
            prop = text(suf.child_by_field_name("suffix")) if suf is not None else None
            return {"obj": self.member_obj(obj, cls) if obj else None, "prop": prop, "callee": None, "args": args}
        if callee.type == "simple_identifier":
            return {"obj": None, "prop": None, "callee": text(callee), "args": args}
        return {"obj": None, "prop": None, "callee": None, "args": args}

    def string_value(self, n) -> str | None:
        if any(c.type == "interpolated_expression" for c in n.named_children):
            return None
        return "".join(text(c) for c in n.named_children if c.type in ("line_str_text", "multi_line_str_text"))

    def finish(self) -> None:
        # extensions add methods to a type declared elsewhere: keep them as `Type.method` symbols
        out = []
        for s in self.rec["symbols"]:
            if s.pop("extension", False):
                for m in s.get("methods", []):
                    e = {"name": f"{s['name']}.{m['name']}", "kind": "function", "line": m["line"], "end": m["line"],
                         "exported": True, "refs": m["refs"], "members": m["members"], "decorators": []}
                    out.append(e)
                continue
            out.append(s)
        names = {s["name"] for s in out}
        self.rec["symbols"] = out
        self.rec["exports"] = [e for e in self.rec["exports"] if e["name"] in names]


def extract(src: bytes, rel: str) -> dict:
    return SwiftExtractor(src, rel).run()
