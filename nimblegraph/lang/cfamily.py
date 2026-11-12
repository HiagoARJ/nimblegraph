"""C and C++: #include ("local" and <system>), structs/classes/unions/enums and typedefs, C++ base classes and
namespaces, functions and prototypes (headers declare what .c/.cpp files define), out-of-class method
definitions (`void Player::update()` attaches to Player) and calls. `.h` uses the C++ grammar when the file looks
like C++ (classes, namespaces, templates), else C."""

from __future__ import annotations

import re

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, line, new_symbol, text

MULTI = True  # extract(src, rel, lang): one module for both languages
RE_CPP_HEADER = re.compile(rb"^\s*(class|namespace|template\s*<|using\s+namespace)\b|\bstd::", re.M)


def _declarator_name(d) -> tuple[str | None, str | None]:
    """function_declarator / pointer_declarator ... -> (scope, name): `Player::update` -> (Player, update)."""
    while d is not None and d.type in ("pointer_declarator", "reference_declarator", "function_declarator",
                                       "parenthesized_declarator", "attributed_declarator"):
        d = d.child_by_field_name("declarator") or (d.named_children[0] if d.named_children else None)
    if d is None:
        return None, None
    if d.type == "qualified_identifier":
        scope = d.child_by_field_name("scope")
        name = d.child_by_field_name("name")
        while name is not None and name.type == "qualified_identifier":  # a::B::f
            scope, name = name.child_by_field_name("scope"), name.child_by_field_name("name")
        return (text(scope) if scope is not None else None), text(name) if name is not None else None
    if d.type in ("identifier", "field_identifier", "destructor_name", "operator_name", "type_identifier"):
        return None, text(d)
    return None, None


def _is_function(d) -> bool:
    while d is not None:
        if d.type == "function_declarator":
            return True
        if d.type not in ("pointer_declarator", "reference_declarator", "parenthesized_declarator",
                          "attributed_declarator"):
            return False
        d = d.child_by_field_name("declarator")
    return False


class CFamilyExtractor(Extractor):
    CLASSES = {"struct_specifier": "class", "class_specifier": "class", "union_specifier": "class",
               "enum_specifier": "type"}
    FUNCTIONS = frozenset()  # handled in visit (qualified definitions)
    CALLS = frozenset({"call_expression"})
    IDENTS = frozenset({"identifier", "type_identifier"})
    STRINGS = frozenset({"string_literal", "raw_string_literal"})
    LAMBDAS = frozenset({"lambda_expression"})
    MEMBERS = frozenset({"field_expression", "qualified_identifier"})

    def __init__(self, src: bytes, rel: str, lang: str):
        self.lang = lang
        cpp = lang == "cpp" or (rel.endswith(".h") and RE_CPP_HEADER.search(src) is not None)
        self.grammar = "cpp" if cpp else "c"
        super().__init__(src, rel)
        self._out_of_class: list[tuple[str, dict, object]] = []

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "preproc_include":
            p = node.child_by_field_name("path")
            if p is not None:
                raw = text(p)
                self.add_import(raw, "include", [], node)  # keeps "x.h" / <x.h>: the resolver needs the form
            return True
        if t in self.CLASSES and node.child_by_field_name("body") is None:
            return False  # `struct X *p;` is a reference, not a declaration
        if t == "type_definition":  # typedef struct {...} Point;
            name = _declarator_name(node.child_by_field_name("declarator"))[1]
            inner = node.child_by_field_name("type")
            if name:
                sym = new_symbol(name, "class" if inner is not None and inner.type in self.CLASSES else "type", node,
                                 True)
                sym["extends"], sym["implements"], sym["abstract"], sym["ctor_types"] = None, [], False, {}
                self.rec["symbols"].append(sym)
            for c in node.named_children:
                if c.type != "type_identifier":
                    self.walk(c, cls, fn)
            return True
        if t == "function_definition" and fn is None:
            scope, name = _declarator_name(node.child_by_field_name("declarator"))
            if not name:
                return False
            static = any(text(c) == "static" for c in children_of_type(node, "storage_class_specifier"))
            if cls is not None and scope is None:  # inline method in a class body
                m = {"name": name, "line": line(node), "refs": [], "members": [], "this_refs": [], "decorators": [],
                     "param_types": []}
                cls["methods"].append(m)
                for c in node.named_children:
                    self.walk(c, None, m)
                return True
            sym = new_symbol(f"{scope.rsplit('::', 1)[-1]}.{name}" if scope else name, "function", node, not static)
            if scope:
                self._out_of_class.append((scope.rsplit("::", 1)[-1], sym, node))
            self.rec["symbols"].append(sym)
            if name == "main" and not scope:
                self.rec["entry"] = True
            for c in node.named_children:
                self.walk(c, None, sym)
            return True
        if t in ("declaration", "field_declaration") and fn is None:
            d = node.child_by_field_name("declarator")
            if d is not None and _is_function(d):  # prototype: int foo(int);  /  void update(float); in a class
                scope, name = _declarator_name(d)
                if name and cls is None and not scope:
                    sym = new_symbol(name, "function", node, True)
                    sym["prototype"] = True
                    self.rec["symbols"].append(sym)
                elif name and cls is not None:
                    cls["methods"].append({"name": name, "line": line(node), "refs": [], "members": [],
                                           "this_refs": [], "decorators": [], "param_types": []})
        if t == "namespace_definition":
            if not self.rec["package"]:
                self.rec["package"] = text(node.child_by_field_name("name")) or None
        if t in ("qualified_identifier",) and fn is not None:
            self.ref(text(node), fn)
        return False

    def class_symbol(self, node, outer):
        sym = super().class_symbol(node, outer)
        if sym is not None and node.type == "class_specifier":
            sym["ctor_types"] = self.fields(node)
        return sym

    def bases(self, node):
        bc = child_of_type(node, "base_class_clause")
        names = [text(c).rsplit("::", 1)[-1] for c in bc.named_children
                 if c.type in ("type_identifier", "qualified_identifier", "template_type")] if bc is not None else []
        names = [n.split("<", 1)[0] for n in names]
        return (names[0] if names else None), names[1:]

    def fields(self, node) -> dict[str, str]:
        body = node.child_by_field_name("body")
        out = {}
        for f in children_of_type(body, "field_declaration") if body is not None else []:
            ty = f.child_by_field_name("type")
            d = f.child_by_field_name("declarator")
            if ty is not None and d is not None and not _is_function(d) and ty.type in ("type_identifier",
                                                                                            "qualified_identifier"):
                name = _declarator_name(d)[1]
                if name:
                    out[name] = text(ty).rsplit("::", 1)[-1]
        return out

    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "this":
            return "this"
        if n.type == "identifier":
            return text(n)
        if n.type == "field_expression":
            base = self._obj(n.child_by_field_name("argument"))
            return f"{base}.{text(n.child_by_field_name('field'))}" if base else None
        return None

    def call(self, node, cls) -> dict | None:
        f = node.child_by_field_name("function")
        al = node.child_by_field_name("arguments")
        args = [self.arg(c) for c in al.named_children] if al is not None else []
        if f is None:
            return None
        if f.type == "field_expression":
            return {"obj": self._obj(f.child_by_field_name("argument")), "prop": text(f.child_by_field_name("field")),
                    "callee": None, "args": args}
        if f.type == "qualified_identifier":
            scope, name = text(f).rsplit("::", 1) if "::" in text(f) else (None, text(f))
            return {"obj": scope.replace("::", ".") if scope else None, "prop": name, "callee": None, "args": args}
        return {"obj": None, "prop": None, "callee": text(f), "args": args}

    def string_value(self, n) -> str | None:
        return "".join(text(c) for c in n.named_children if c.type == "string_content")

    def finish(self) -> None:
        by_name = {s["name"]: s for s in self.rec["symbols"]}
        merged: set[int] = set()
        for owner, sym, _node in self._out_of_class:  # void Player::update() defined next to class Player
            cls = by_name.get(owner)
            if cls is not None and cls.get("kind") == "class":
                method = sym["name"].split(".", 1)[1]
                existing = next((m for m in cls["methods"] if m["name"] == method), None)
                if existing is None:
                    cls["methods"].append({"name": method, "line": sym["line"], "refs": sym["refs"],
                                           "members": sym["members"], "this_refs": [], "decorators": [],
                                           "param_types": []})
                else:
                    existing["refs"], existing["members"] = sym["refs"], sym["members"]
                merged.add(id(sym))
        if merged:
            gone = {s["name"] for s in self.rec["symbols"] if id(s) in merged}
            self.rec["symbols"] = [s for s in self.rec["symbols"] if id(s) not in merged]
            self.rec["exports"] = [e for e in self.rec["exports"] if e["name"] not in gone]


def extract(src: bytes, rel: str, lang: str = "c") -> dict:
    return CFamilyExtractor(src, rel, lang).run()
