"""C#: namespaces (block and file-scoped), using directives (plain, static, alias), classes/structs/records/
interfaces/enums, attributes with arguments, fields/properties and constructor injection (primary too),
invocations (`app.MapGet("/x", ...)`) and top-level statements."""

from __future__ import annotations

import re

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, text

RE_IFACE = re.compile(r"^I[A-Z]")


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type == "identifier":
        return text(n)
    if n.type == "qualified_name":
        return text(n.child_by_field_name("name")) or text(n).rsplit(".", 1)[-1]
    if n.type == "generic_name":
        return text(child_of_type(n, "identifier"))
    if n.type in ("nullable_type", "array_type"):
        return type_name(n.named_children[0]) if n.named_children else None
    return None


class CSharpExtractor(Extractor):
    lang = "csharp"
    grammar = "csharp"
    CLASSES = {"class_declaration": "class", "struct_declaration": "class", "record_declaration": "class",
               "record_struct_declaration": "class", "enum_declaration": "class", "interface_declaration": "type"}
    FUNCTIONS = frozenset({"method_declaration", "constructor_declaration"})
    CALLS = frozenset({"invocation_expression"})
    IDENTS = frozenset({"identifier"})
    STRINGS = frozenset({"string_literal", "verbatim_string_literal", "raw_string_literal"})
    LAMBDAS = frozenset({"lambda_expression", "anonymous_method_expression"})
    MEMBERS = frozenset({"member_access_expression"})
    ASSIGNS = frozenset({"variable_declarator"})
    INTERPOLATED = frozenset({"interpolated_string_expression"})

    def assignment(self, node):
        name = node.child_by_field_name("name") or child_of_type(node, "identifier")
        vals = [c for c in node.named_children if c != name and c.type not in ("bracketed_argument_list",)]
        value = vals[-1] if vals else None
        if value is not None and value.type == "equals_value_clause":
            value = value.named_children[-1] if value.named_children else None
        return (text(name), value) if name is not None and value is not None else None

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "using_directive":
            alias = node.child_by_field_name("name")
            target = [c for c in node.named_children if c.type in ("identifier", "qualified_name", "generic_name")]
            static = any(c.type == "static" for c in node.children)
            glob = any(c.type == "global" or text(c) == "global" for c in node.children if not c.is_named)
            if alias is not None and len(target) >= 2:
                ns = text(target[-1])
                self.add_import(ns, "import", [[ns.rsplit(".", 1)[-1], text(alias)]], node)
            elif target:
                self.add_import(text(target[-1]), "static" if static else "global" if glob else "using", [], node)
            return True
        if t in ("namespace_declaration", "file_scoped_namespace_declaration"):
            name_n = node.child_by_field_name("name")
            name = text(name_n)
            if name and not self.rec["package"]:
                self.rec["package"] = name
            for c in node.named_children:
                if c != name_n:
                    self.walk(c, cls, fn)
            return True
        if t == "global_statement":
            self.rec["entry"] = True
        if t == "variable_declarator":  # var api = app.MapGroup("/api");
            inv = child_of_type(node, "invocation_expression")
            name = node.child_by_field_name("name")
            if inv is not None and name is not None:
                c = self.call(inv, cls)
                if c is not None:
                    self.rec.setdefault("assigns", []).append({"name": text(name), **c, "line": node.start_point[0] + 1})
        return False

    def _mods(self, node) -> list[str]:
        return [text(c) for c in children_of_type(node, "modifier")]

    def exported(self, node, name: str) -> bool:
        return "private" not in self._mods(node)

    def is_abstract(self, node) -> bool:
        return node.type == "interface_declaration" or "abstract" in self._mods(node)

    def decorators(self, node) -> list[dict]:
        out = []
        for al in children_of_type(node, "attribute_list"):
            for a in children_of_type(al, "attribute"):
                name = text(a.child_by_field_name("name")).rsplit(".", 1)[-1]
                name = name[:-9] if name.endswith("Attribute") and len(name) > 9 else name
                args, props = [], {}
                aal = child_of_type(a, "attribute_argument_list")
                for arg in children_of_type(aal, "attribute_argument") if aal is not None else []:
                    key = child_of_type(arg, "name_equals", "name_colon")
                    val = [c for c in arg.named_children if c is not key]
                    v = self.arg(val[-1]) if val else {"t": "other"}
                    if key is not None:
                        props[text(child_of_type(key, "identifier"))] = v
                    else:
                        args.append(v)
                if props:
                    args.append({"t": "obj", "props": props})
                out.append({"name": name, "args": args})
        return out

    def bases(self, node):
        bl = child_of_type(node, "base_list")
        names = [n for n in (type_name(c) for c in (bl.named_children if bl is not None else [])) if n]
        if node.type == "interface_declaration":
            return None, names
        ext = next((n for n in names if not RE_IFACE.match(n)), None)
        return ext, [n for n in names if n != ext]

    def fields(self, node) -> dict[str, str]:
        out = {}
        body = node.child_by_field_name("body")
        for f in children_of_type(body, "field_declaration") if body is not None else []:
            vd = child_of_type(f, "variable_declaration")
            tn = type_name(vd.child_by_field_name("type")) if vd is not None else None
            for d in children_of_type(vd, "variable_declarator") if vd is not None else []:
                if tn:
                    out[text(d.child_by_field_name("name") or child_of_type(d, "identifier"))] = tn
        for p in children_of_type(body, "property_declaration") if body is not None else []:
            tn = type_name(p.child_by_field_name("type"))
            if tn:
                out[text(p.child_by_field_name("name"))] = tn
        out.update(self.ctor_types(node))  # primary constructor: class Svc(IRepo repo)
        return out

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return node.type == "constructor_declaration"

    def _params(self, node):
        pl = node.child_by_field_name("parameters") or child_of_type(node, "parameter_list")
        return children_of_type(pl, "parameter") if pl is not None else []

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
        f = node.child_by_field_name("function")
        al = node.child_by_field_name("arguments")
        args = []
        for a in children_of_type(al, "argument") if al is not None else []:
            val = [c for c in a.named_children if c.type not in ("name_colon",)]
            args.append(self.arg(val[-1]) if val else {"t": "other"})
        if f is None:
            return None
        if f.type == "member_access_expression":
            obj = f.child_by_field_name("expression")
            prop = type_name(f.child_by_field_name("name")) or text(f.child_by_field_name("name"))
            if obj is not None and obj.type in ("identifier", "member_access_expression", "this_expression", "this"):
                o = text(obj).replace("this.", "this.", 1)
                return {"obj": self.member_obj(o, cls), "prop": prop, "callee": None, "args": args}
            return {"obj": None, "prop": prop, "callee": None, "args": args}
        return {"obj": None, "prop": None, "callee": type_name(f) or text(f), "args": args}

    def finish(self) -> None:
        for s in self.rec["symbols"]:
            if any(m["name"] == "Main" for m in s.get("methods", [])):
                self.rec["entry"] = True


def extract(src: bytes, rel: str) -> dict:
    return CSharpExtractor(src, rel).run()
