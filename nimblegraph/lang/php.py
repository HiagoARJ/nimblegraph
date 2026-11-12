"""PHP: namespace, `use` (plain, grouped, aliased), require/include, classes/interfaces/traits/enums with
attributes, promoted constructor properties, member/static/function calls (with the `Route::prefix()->group()`
chain kept) and fully-qualified references."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, text, unquote


def _name(n) -> str:
    return text(n).lstrip("\\")


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type in ("named_type", "optional_type", "nullable_type"):
        inner = child_of_type(n, "name", "qualified_name", "named_type")
        return type_name(inner) if inner is not None else None
    if n.type in ("name", "qualified_name"):
        return _name(n).rsplit("\\", 1)[-1]
    if n.type in ("union_type", "intersection_type"):
        for c in n.named_children:
            t = type_name(c)
            if t:
                return t
    return None


class PhpExtractor(Extractor):
    lang = "php"
    grammar = "php"
    CLASSES = {"class_declaration": "class", "enum_declaration": "class", "interface_declaration": "type",
               "trait_declaration": "type"}
    FUNCTIONS = frozenset({"method_declaration", "function_definition"})
    CALLS = frozenset({"member_call_expression", "scoped_call_expression", "function_call_expression",
                       "nullsafe_member_call_expression"})
    IDENTS = frozenset({"name", "qualified_name"})
    STRINGS = frozenset({"string", "encapsed_string"})
    LAMBDAS = frozenset({"anonymous_function", "arrow_function", "anonymous_function_creation_expression"})
    MEMBERS = frozenset({"member_access_expression", "class_constant_access_expression"})
    ASSIGNS = frozenset({"assignment_expression", "const_element", "property_element"})
    INTERP_PARTS = frozenset({"variable_name", "member_access_expression", "dynamic_variable_name"})

    def assignment(self, node):
        if node.type == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            return (text(left).replace("->", "."), right) if left is not None and right is not None else None
        kids = node.named_children
        if len(kids) >= 2:
            val = kids[-1]
            if val.type == "property_initializer" and val.named_children:
                val = val.named_children[-1]
            return text(kids[0]), val
        return None

    def template_text(self, n):
        import re
        if n.type != "encapsed_string":
            return None
        raw = text(n)[1:-1]
        raw = re.sub(r"\{\$([^}]+)\}", lambda m: "{" + m.group(1).replace("->", ".") + "}", raw)
        return re.sub(r"\$(\w+(?:->\w+)*)", lambda m: "{" + m.group(1).replace("->", ".") + "}", raw)

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "namespace_definition":
            self.rec["package"] = _name(node.child_by_field_name("name")) or None
            body = node.child_by_field_name("body")
            if body is not None:
                for c in body.named_children:
                    self.walk(c, cls, fn)
            return True
        if t == "namespace_use_declaration":
            prefix_n = child_of_type(node, "namespace_name")
            group = node.child_by_field_name("body")
            prefix = _name(prefix_n) if prefix_n is not None and group is not None else ""
            clauses = children_of_type(group if group is not None else node, "namespace_use_clause")
            for cl in clauses:
                target = child_of_type(cl, "qualified_name", "name")
                alias = cl.child_by_field_name("alias")
                full = _name(target)
                if prefix:
                    full = f"{prefix}\\{full}"
                last = full.rsplit("\\", 1)[-1]
                self.add_import(full, "use", [[last, text(alias) if alias is not None else last]], node)
            return True
        if t in ("require_expression", "require_once_expression", "include_expression", "include_once_expression"):
            s = next((self.string_value(c) for c in self._strings(node)), None)
            if s:
                self.add_import(s, "require", [], node)
            return True
        if t in ("qualified_name",) and "\\" in text(node):
            self.ref(_name(node), fn or cls)  # \App\Models\User / Models\User: resolved by namespace
        return False

    def _strings(self, node):
        out, stack = [], [node]
        while stack:
            n = stack.pop()
            if n.type in ("string", "encapsed_string"):
                out.append(n)
            else:
                stack.extend(n.named_children)
        return out

    def exported(self, node, name: str) -> bool:
        vis = child_of_type(node, "visibility_modifier")
        return vis is None or text(vis) != "private"

    def is_abstract(self, node) -> bool:
        return node.type == "interface_declaration" or any(text(c) == "abstract" for c in node.children
                                                          if c.type in ("abstract_modifier", "abstract"))

    def decorators(self, node) -> list[dict]:
        al = node.child_by_field_name("attributes") or child_of_type(node, "attribute_list")
        out = []
        for g in children_of_type(al, "attribute_group") if al is not None else []:
            for a in children_of_type(g, "attribute"):
                name = type_name(child_of_type(a, "name", "qualified_name")) or ""
                args, props = [], {}
                params = a.child_by_field_name("parameters") or child_of_type(a, "arguments")
                for arg in children_of_type(params, "argument") if params is not None else []:
                    key = arg.child_by_field_name("name")
                    vals = [c for c in arg.named_children if c != key]
                    v = self.arg(vals[-1]) if vals else {"t": "other"}
                    if key is not None:
                        props[text(key)] = v
                    else:
                        args.append(v)
                if props:
                    args.append({"t": "obj", "props": props})
                out.append({"name": name, "args": args})
        return out

    def bases(self, node):
        bc = child_of_type(node, "base_clause")
        ic = child_of_type(node, "class_interface_clause")
        ext = [type_name(c) for c in bc.named_children] if bc is not None else []
        impl = [type_name(c) for c in ic.named_children] if ic is not None else []
        if node.type == "interface_declaration":  # interface A extends B, C
            return None, [n for n in ext + impl if n]
        return (ext[0] if ext else None), [n for n in impl if n]

    def fields(self, node) -> dict[str, str]:
        out = {}
        body = node.child_by_field_name("body")
        for p in children_of_type(body, "property_declaration") if body is not None else []:
            tn = type_name(p.child_by_field_name("type") or child_of_type(p, "named_type", "optional_type"))
            for el in children_of_type(p, "property_element"):
                vn = child_of_type(el, "variable_name")
                if tn and vn is not None:
                    out[text(vn).lstrip("$")] = tn
        return out

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return name == "__construct"

    def _params(self, node):
        fp = node.child_by_field_name("parameters")
        return children_of_type(fp, "simple_parameter", "property_promotion_parameter") if fp is not None else []

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            tn = type_name(p.child_by_field_name("type"))
            vn = p.child_by_field_name("name")
            if tn and vn is not None:
                out[text(vn).lstrip("$")] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (type_name(p.child_by_field_name("type")) for p in self._params(node)) if tn]

    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "variable_name":
            v = text(n).lstrip("$")
            return "this" if v == "this" else v
        if n.type in ("name", "qualified_name", "relative_scope"):
            s = _name(n)
            return "this" if s in ("self", "static") else s.rsplit("\\", 1)[-1]
        if n.type in ("member_access_expression", "nullsafe_member_access_expression"):
            base = self._obj(n.child_by_field_name("object"))
            return f"{base}.{text(n.child_by_field_name('name'))}" if base else None
        return None

    def _args(self, node) -> list[dict]:
        al = node.child_by_field_name("arguments") or child_of_type(node, "arguments")
        args, props = [], {}
        for a in children_of_type(al, "argument") if al is not None else []:
            key = a.child_by_field_name("name")
            vals = [c for c in a.named_children if c != key]
            v = self.arg(vals[-1]) if vals else {"t": "other"}
            if key is not None:
                props[text(key)] = v
            else:
                args.append(v)
        if props:
            args.append({"t": "obj", "props": props})
        return args

    def call(self, node, cls) -> dict | None:
        t = node.type
        args = self._args(node)
        if t == "function_call_expression":
            f = node.child_by_field_name("function")
            return {"obj": None, "prop": None, "callee": _name(f).rsplit("\\", 1)[-1] if f is not None else None,
                    "args": args}
        name = text(node.child_by_field_name("name"))
        objn = node.child_by_field_name("object") if t != "scoped_call_expression" else node.child_by_field_name("scope")
        obj = self._obj(objn)
        out = {"obj": self.member_obj(obj, cls) if obj else None, "prop": name, "callee": None, "args": args}
        chain = []  # Route::prefix('admin')->middleware('auth')->group(...): the calls before this one
        while objn is not None and objn.type in self.CALLS:
            inner = self.call(objn, cls) or {}
            chain.insert(0, {"obj": inner.get("obj"), "prop": inner.get("prop"), "args": inner.get("args", [])})
            objn = objn.child_by_field_name("object") if objn.type != "scoped_call_expression" else None
        if chain:
            out["chain"] = chain
        return out

    def arg(self, n) -> dict:
        if n is None:
            return {"t": "other"}
        if n.type == "variable_name":  # $url: resolved to its string when the function assigned one
            return {"t": "id", "v": text(n)}
        if n.type == "class_constant_access_expression":  # UserController::class
            parts = [c for c in n.named_children]
            if len(parts) == 2 and text(parts[1]) == "class":
                return {"t": "id", "v": type_name(parts[0]) or text(parts[0])}
        if n.type == "array_creation_expression":
            items, props = [], {}
            for el in children_of_type(n, "array_element_initializer"):
                vals = el.named_children
                if len(vals) == 2:  # ['prefix' => 'admin']
                    k = self.arg(vals[0])
                    if k.get("t") == "str":
                        props[k["v"]] = self.arg(vals[1])
                        continue
                items.append(self.arg(vals[-1]) if vals else {"t": "other"})
            return {"t": "obj", "props": props} if props and not items else {"t": "arr", "items": items}
        if n.type == "member_access_expression":
            o = self._obj(n)
            return {"t": "member", "v": o} if o else {"t": "other"}
        return super().arg(n)

    def string_value(self, n) -> str | None:
        if n.type == "encapsed_string" and any(c.type not in ("string_content", "string_value", "escape_sequence")
                                               for c in n.named_children):
            return None
        content = [text(c) for c in n.named_children if c.type in ("string_content", "string_value")]
        return "".join(content) if content or n.named_children else unquote(text(n))

    def finish(self) -> None:
        if self.rel.endswith(("public/index.php", "artisan")) or self.rel == "index.php":
            self.rec["entry"] = True


def extract(src: bytes, rel: str) -> dict:
    return PhpExtractor(src, rel).run()
