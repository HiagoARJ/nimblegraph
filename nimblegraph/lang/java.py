"""Java: package, imports (single-type, on-demand, static), classes/interfaces/enums/records, annotations with
their arguments, fields (for `field.method()`), constructor injection and method calls."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, text

ENTRY_ANNOTATIONS = {"SpringBootApplication", "QuarkusMain", "MicronautApplication"}


def type_name(n) -> str | None:
    """`List<User>` -> List, `com.x.User` -> User, `User[]` -> User."""
    if n is None:
        return None
    if n.type in ("generic_type", "array_type", "annotated_type"):
        inner = child_of_type(n, "type_identifier", "scoped_type_identifier", "generic_type")
        return type_name(inner) if inner is not None else None
    if n.type == "scoped_type_identifier":
        return text(n).rsplit(".", 1)[-1]
    if n.type == "type_identifier":
        return text(n)
    return None


class JavaExtractor(Extractor):
    lang = "java"
    grammar = "java"
    CLASSES = {"class_declaration": "class", "record_declaration": "class", "enum_declaration": "class",
               "interface_declaration": "type", "annotation_type_declaration": "type"}
    FUNCTIONS = frozenset({"method_declaration", "constructor_declaration"})
    CALLS = frozenset({"method_invocation"})
    STRINGS = frozenset({"string_literal"})
    LAMBDAS = frozenset({"lambda_expression", "method_reference"})
    MEMBERS = frozenset({"field_access"})
    ASSIGNS = frozenset({"variable_declarator"})

    def assignment(self, node):
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        return (text(name), value) if name is not None and value is not None else None

    def format_call(self, n):
        """String.format("%s/users/%d", BASE, id)."""
        if n.type != "method_invocation" or text(n.child_by_field_name("name")) not in ("format", "formatted"):
            return None
        al = n.child_by_field_name("arguments")
        args = list(al.named_children) if al is not None else []
        if text(n.child_by_field_name("name")) == "formatted":
            args = [n.child_by_field_name("object"), *args]
        from nimblegraph.lang.go import _printf
        return _printf(self, args)

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "package_declaration":
            self.rec["package"] = text(child_of_type(node, "scoped_identifier", "identifier"))
            return True
        if t == "import_declaration":
            path = text(child_of_type(node, "scoped_identifier", "identifier"))
            static = any(c.type == "static" for c in node.children)
            star = any(c.type == "asterisk" for c in node.children)
            if star:
                self.add_import(path + ".*", "static" if static else "import", [], node)
            elif static:
                owner, _, member = path.rpartition(".")
                self.add_import(owner, "static", [[member, member]], node)
            else:
                last = path.rsplit(".", 1)[-1]
                self.add_import(path, "import", [[last, last]], node)
            return True
        return False

    def _modifiers(self, node):
        return child_of_type(node, "modifiers")

    def exported(self, node, name: str) -> bool:
        m = self._modifiers(node)
        return not (m is not None and "private" in text(m).split())

    def is_abstract(self, node) -> bool:
        m = self._modifiers(node)
        return node.type == "interface_declaration" or (m is not None and "abstract" in text(m).split())

    def decorators(self, node) -> list[dict]:
        m = self._modifiers(node)
        out = []
        for a in children_of_type(m, "marker_annotation", "annotation") if m is not None else []:
            name = text(a.child_by_field_name("name")).rsplit(".", 1)[-1]
            args = []
            al = a.child_by_field_name("arguments")
            if al is not None:
                props = {}
                for c in al.named_children:
                    if c.type == "element_value_pair":
                        props[text(c.child_by_field_name("key"))] = self.arg(c.child_by_field_name("value"))
                    else:
                        args.append(self.arg(c))
                if props:
                    args.append({"t": "obj", "props": props})
            out.append({"name": name, "args": args})
        return out

    def arg(self, n) -> dict:
        if n is not None and n.type == "element_value_array_initializer":
            return {"t": "arr", "items": [self.arg(c) for c in n.named_children]}
        if n is not None and n.type == "class_literal":  # UserDto.class
            return {"t": "id", "v": text(n).removesuffix(".class")}
        return super().arg(n) if n is not None else {"t": "other"}

    def bases(self, node):
        ext, impl = None, []
        sc = node.child_by_field_name("superclass")
        if sc is not None:
            ext = type_name(sc.named_children[0]) if sc.named_children else None
        for field in ("interfaces",):
            si = node.child_by_field_name(field)
            tl = child_of_type(si, "type_list") if si is not None else None
            impl += [n for n in (type_name(c) for c in (tl.named_children if tl is not None else [])) if n]
        ei = child_of_type(node, "extends_interfaces")
        tl = child_of_type(ei, "type_list") if ei is not None else None
        impl += [n for n in (type_name(c) for c in (tl.named_children if tl is not None else [])) if n]
        return ext, impl

    def fields(self, node) -> dict[str, str]:
        body = node.child_by_field_name("body")
        out = {}
        for f in children_of_type(body, "field_declaration") if body is not None else []:
            tn = type_name(f.child_by_field_name("type"))
            for d in children_of_type(f, "variable_declarator"):
                if tn:
                    out[text(d.child_by_field_name("name"))] = tn
        if node.type == "record_declaration":
            for p in children_of_type(node.child_by_field_name("parameters"), "formal_parameter") \
                    if node.child_by_field_name("parameters") is not None else []:
                tn = type_name(p.child_by_field_name("type"))
                if tn:
                    out[text(p.child_by_field_name("name"))] = tn
        return out

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return node.type == "constructor_declaration"

    def _params(self, node):
        ps = node.child_by_field_name("parameters")
        return children_of_type(ps, "formal_parameter", "spread_parameter") if ps is not None else []

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            tn = type_name(p.child_by_field_name("type"))
            if tn:
                out[text(p.child_by_field_name("name"))] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (type_name(p.child_by_field_name("type")) for p in self._params(node)) if tn]

    def call(self, node, cls) -> dict | None:
        name = text(node.child_by_field_name("name"))
        obj_n = node.child_by_field_name("object")
        al = node.child_by_field_name("arguments")
        args = [self.arg(c) for c in al.named_children] if al is not None else []
        if obj_n is None:
            return {"obj": None, "prop": None, "callee": name, "args": args}
        if obj_n.type in ("identifier", "field_access", "this"):
            return {"obj": self.member_obj(text(obj_n), cls), "prop": name, "callee": None, "args": args}
        return {"obj": None, "prop": name, "callee": None, "args": args}

    def finish(self) -> None:
        for s in self.rec["symbols"]:
            if any(d["name"] in ENTRY_ANNOTATIONS for d in s.get("decorators", [])) \
                    or any(m["name"] == "main" for m in s.get("methods", [])):
                self.rec["entry"] = True


def extract(src: bytes, rel: str) -> dict:
    return JavaExtractor(src, rel).run()
