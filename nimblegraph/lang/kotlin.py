"""Kotlin: package, imports (alias, wildcard), classes/interfaces/objects with annotations, primary-constructor
injection (`class C(private val svc: Service)`), functions (top-level ones too, as in Ktor modules) and calls
whose trailing lambdas keep their line range (Ktor `routing { route("/x") { get { } } }`)."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, new_symbol, text

ENTRY_ANNOTATIONS = {"SpringBootApplication"}


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type == "user_type":
        ids = children_of_type(n, "type_identifier")
        return text(ids[-1]) if ids else None
    if n.type in ("nullable_type", "type_reference"):
        return type_name(child_of_type(n, "user_type", "nullable_type", "type_reference"))
    if n.type == "type_identifier":
        return text(n)
    return None


class KotlinExtractor(Extractor):
    lang = "kotlin"
    grammar = "kotlin"
    CLASSES = {"class_declaration": "class", "object_declaration": "class"}
    FUNCTIONS = frozenset({"function_declaration", "secondary_constructor"})
    CALLS = frozenset({"call_expression"})
    IDENTS = frozenset({"simple_identifier", "type_identifier"})
    STRINGS = frozenset({"string_literal"})
    LAMBDAS = frozenset({"lambda_literal", "annotated_lambda", "anonymous_function"})
    MEMBERS = frozenset({"navigation_expression"})
    ASSIGNS = frozenset({"property_declaration"})

    def assignment(self, node):
        vd = child_of_type(node, "variable_declaration")
        n = child_of_type(vd, "simple_identifier") if vd is not None else None
        vals = [c for c in node.named_children if c.type not in ("variable_declaration", "modifiers",
                                                                 "binding_pattern_kind", "user_type")]
        return (text(n), vals[-1]) if n is not None and vals else None

    def template_text(self, n):
        import re
        parts = []
        for c in n.named_children:
            if c.type == "string_content":
                parts.append(re.sub(r"\$(\w+)", r"{\1}", text(c)))
            elif c.type in ("interpolated_identifier", "interpolated_expression"):
                parts.append("{" + text(c) + "}")
        return "".join(parts)

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "property_declaration" and fn is not None:  # val service = UserService(): a local binding
            vd = child_of_type(node, "variable_declaration")
            n = child_of_type(vd, "simple_identifier") if vd is not None else None
            if n is not None:
                self.local(text(n), fn)
        if t == "package_header":
            self.rec["package"] = text(child_of_type(node, "identifier"))
            return True
        if t == "import_header":
            path = text(child_of_type(node, "identifier"))
            alias = child_of_type(node, "import_alias")
            if child_of_type(node, "wildcard_import") is not None:
                self.add_import(path + ".*", "import", [], node)
            else:
                last = path.rsplit(".", 1)[-1]
                local = text(child_of_type(alias, "type_identifier", "simple_identifier")) if alias is not None else last
                self.add_import(path, "import", [[last, local]], node)
            return True
        return False

    def class_symbol(self, node, outer):
        name = text(child_of_type(node, "type_identifier"))
        if not name:
            return None
        is_iface = any(c.type == "interface" or text(c) == "interface" for c in node.children if not c.is_named)
        sym = new_symbol(name, "type" if is_iface else "class", node, self.exported(node, name))
        sym["decorators"] = self.decorators(node)
        ext, impl = None, []
        for d in children_of_type(node, "delegation_specifier"):
            ci = child_of_type(d, "constructor_invocation")
            tn = type_name(child_of_type(ci, "user_type")) if ci is not None else type_name(child_of_type(d, "user_type"))
            if ci is not None and ext is None:
                ext = tn
            elif tn:
                impl.append(tn)
        sym["extends"], sym["implements"] = ext, impl
        mods = text(child_of_type(node, "modifiers")).split()
        sym["abstract"] = is_iface or "abstract" in mods or "sealed" in mods
        sym["ctor_types"] = {**self._primary(node), **self.fields(node)}
        return sym

    def _primary(self, node) -> dict[str, str]:
        pc = child_of_type(node, "primary_constructor")
        out = {}
        for p in children_of_type(pc, "class_parameter") if pc is not None else []:
            n, tn = child_of_type(p, "simple_identifier"), type_name(child_of_type(p, "user_type", "nullable_type"))
            if n is not None and tn:
                out[text(n)] = tn
        return out

    def fields(self, node) -> dict[str, str]:
        body = child_of_type(node, "class_body")
        out = {}
        for p in children_of_type(body, "property_declaration") if body is not None else []:
            vd = child_of_type(p, "variable_declaration")
            n = child_of_type(vd, "simple_identifier") if vd is not None else None
            tn = type_name(child_of_type(vd, "user_type", "nullable_type")) if vd is not None else None
            if n is not None and tn:
                out[text(n)] = tn
        return out

    def func_name(self, node) -> str | None:
        if node.type == "secondary_constructor":
            return "constructor"
        n = child_of_type(node, "simple_identifier")
        return text(n) if n is not None else None

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return node.type == "secondary_constructor"

    def exported(self, node, name: str) -> bool:
        return "private" not in text(child_of_type(node, "modifiers")).split()

    def decorators(self, node) -> list[dict]:
        mods = child_of_type(node, "modifiers")
        out = []
        for a in children_of_type(mods, "annotation") if mods is not None else []:
            ci = child_of_type(a, "constructor_invocation")
            ut = child_of_type(ci, "user_type") if ci is not None else child_of_type(a, "user_type")
            name = type_name(ut) or ""
            args = self._value_args(child_of_type(ci, "value_arguments")) if ci is not None else []
            out.append({"name": name, "args": args})
        return out

    def _value_args(self, va) -> list[dict]:
        args, props = [], {}
        for a in children_of_type(va, "value_argument") if va is not None else []:
            kids = a.named_children
            if len(kids) >= 2 and kids[0].type == "simple_identifier" and any(text(c) == "=" for c in a.children):
                props[text(kids[0])] = self.arg(kids[-1])
            elif kids:
                args.append(self.arg(kids[-1]))
        if props:
            args.append({"t": "obj", "props": props})
        return args

    def _params(self, node):
        fvp = child_of_type(node, "function_value_parameters")
        return children_of_type(fvp, "parameter") if fvp is not None else []

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            n, tn = child_of_type(p, "simple_identifier"), type_name(child_of_type(p, "user_type", "nullable_type"))
            if n is not None and tn:
                out[text(n)] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (type_name(child_of_type(p, "user_type", "nullable_type")) for p in self._params(node)) if tn]

    def on_function(self, node, sym: dict) -> None:
        if sym["name"] == "main":
            self.rec["entry"] = True

    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "this_expression":
            return "this"
        if n.type == "simple_identifier":
            return text(n)
        if n.type == "navigation_expression":
            kids = n.named_children
            base = self._obj(kids[0]) if kids else None
            suf = child_of_type(n, "navigation_suffix")
            name = text(child_of_type(suf, "simple_identifier")) if suf is not None else ""
            return f"{base}.{name}" if base and name else None
        return None

    def call(self, node, cls) -> dict | None:
        kids = node.named_children
        if not kids:
            return None
        callee = kids[0]
        suffix = child_of_type(node, "call_suffix")
        args = self._value_args(child_of_type(suffix, "value_arguments")) if suffix is not None else []
        lam = child_of_type(suffix, "annotated_lambda") if suffix is not None else None
        if lam is not None:
            args.append(self.arg(child_of_type(lam, "lambda_literal") or lam))
        if callee.type == "navigation_expression":
            nk = callee.named_children
            obj = self._obj(nk[0]) if nk else None
            suf = child_of_type(callee, "navigation_suffix")
            prop = text(child_of_type(suf, "simple_identifier")) if suf is not None else None
            return {"obj": self.member_obj(obj, cls) if obj else None, "prop": prop, "callee": None, "args": args}
        if callee.type == "simple_identifier":
            return {"obj": None, "prop": None, "callee": text(callee), "args": args}
        return {"obj": None, "prop": None, "callee": None, "args": args}

    def string_value(self, n) -> str | None:
        if any(c.type.startswith("interpolat") for c in n.named_children):
            return None
        return "".join(text(c) for c in n.named_children if c.type == "string_content")

    def finish(self) -> None:
        for s in self.rec["symbols"]:
            if any(d["name"] in ENTRY_ANNOTATIONS for d in s.get("decorators", [])):
                self.rec["entry"] = True


def extract(src: bytes, rel: str) -> dict:
    return KotlinExtractor(src, rel).run()
