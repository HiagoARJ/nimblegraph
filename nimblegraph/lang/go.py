"""Go: package clause, imports (alias, dot, blank), structs/interfaces/types, functions and methods (attached to
the receiver type; the receiver reads as `this`), struct fields (for `h.svc.Create()`), calls and the
`x := r.Group("/api")` assignments route detectors need. Exported = capitalized."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, new_symbol, text, unquote, line


def type_name(n) -> str | None:
    """`*users.Service` -> users.Service, `[]Item` -> Item, `Handler` -> Handler."""
    if n is None:
        return None
    if n.type in ("pointer_type", "slice_type", "array_type", "generic_type"):
        inner = [c for c in n.named_children if c.type in ("type_identifier", "qualified_type", "pointer_type",
                                                             "generic_type")]
        return type_name(inner[-1]) if inner else None
    if n.type in ("type_identifier", "qualified_type"):
        return text(n)
    return None


class GoExtractor(Extractor):
    lang = "go"
    grammar = "go"
    FUNCTIONS = frozenset()  # handled in visit (methods need the receiver)
    CALLS = frozenset({"call_expression"})
    IDENTS = frozenset({"identifier", "type_identifier"})
    STRINGS = frozenset({"interpreted_string_literal", "raw_string_literal"})
    LAMBDAS = frozenset({"func_literal"})
    MEMBERS = frozenset({"selector_expression"})
    ASSIGNS = frozenset({"const_spec", "var_spec", "short_var_declaration", "assignment_statement"})

    def assignment(self, node):
        left = node.child_by_field_name("left") or node.child_by_field_name("name")
        right = node.child_by_field_name("right") or node.child_by_field_name("value")
        if left is None or right is None:
            return None
        if left.type == "expression_list":
            left = left.named_children[0] if left.named_children else None
        if right.type == "expression_list":
            right = right.named_children[0] if right.named_children else None
        return (text(left), right) if left is not None and right is not None else None

    def format_call(self, n):
        """fmt.Sprintf("%s/users/%d", base, id) -> base value + /users/:param."""
        if n.type != "call_expression" or text(n.child_by_field_name("function")) != "fmt.Sprintf":
            return None
        al = n.child_by_field_name("arguments")
        args = list(al.named_children) if al is not None else []
        return _printf(self, args)

    def __init__(self, src: bytes, rel: str):
        super().__init__(src, rel)
        self.rec["assigns"] = []
        self._recv: str | None = None
        self._methods: list = []

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "package_clause":
            self.rec["package"] = text(child_of_type(node, "package_identifier"))
            return True
        if t == "import_spec":
            path = unquote(text(node.child_by_field_name("path")))
            alias = node.child_by_field_name("name")
            local = text(alias) if alias is not None else path.rsplit("/", 1)[-1]
            if local not in ("_",):
                self.add_import(path, "import", [["*", local]], node)
            return True
        if t == "type_spec":
            name = text(node.child_by_field_name("name"))
            ty = node.child_by_field_name("type")
            kind = "class" if ty is not None and ty.type == "struct_type" else "type"
            sym = new_symbol(name, kind, node, name[:1].isupper())
            sym["extends"], sym["implements"], sym["abstract"] = None, [], ty is not None and ty.type == "interface_type"
            sym["ctor_types"] = self._struct_fields(ty) if kind == "class" else {}
            if ty is not None and ty.type == "struct_type":  # embedded structs read as base types
                embedded = [type_name(c.child_by_field_name("type")) for c in self._field_decls(ty)
                            if c.child_by_field_name("name") is None]
                sym["implements"] = [e for e in embedded if e]
            self.rec["symbols"].append(sym)
            for c in node.named_children:
                self.walk(c, sym, None)
            return True
        if t == "function_declaration":
            name = text(node.child_by_field_name("name"))
            sym = new_symbol(name, "function", node, name[:1].isupper())
            pl = node.child_by_field_name("parameters")
            sym["params"] = [text(i) for pd in (children_of_type(pl, "parameter_declaration") if pl is not None else [])
                             for i in children_of_type(pd, "identifier")]
            for p in sym["params"]:
                self.local(p, sym)
            self.rec["symbols"].append(sym)
            if name == "main" and self.rec.get("package") == "main":
                self.rec["entry"] = True
            for c in node.named_children:
                self.walk(c, None, sym)
            return True
        if t == "method_declaration":
            name = text(node.child_by_field_name("name"))
            recv_pl = node.child_by_field_name("receiver")
            pd = child_of_type(recv_pl, "parameter_declaration") if recv_pl is not None else None
            recv_type = (type_name(pd.child_by_field_name("type")) or "") if pd is not None else ""
            recv_name = text(pd.child_by_field_name("name")) if pd is not None else ""
            m = {"name": name, "line": line(node), "refs": [], "members": [], "this_refs": [], "decorators": [],
                 "param_types": [], "_recv": recv_type.split(".")[-1]}
            self._methods.append((m, node))
            pl = node.child_by_field_name("parameters")
            for pd2 in children_of_type(pl, "parameter_declaration") if pl is not None else []:
                for i in children_of_type(pd2, "identifier"):
                    self.local(text(i), m)
            prev, self._recv = self._recv, recv_name or None
            for c in node.named_children:
                self.walk(c, None, m)
            self._recv = prev
            return True
        if t in ("short_var_declaration", "assignment_statement", "var_spec"):
            left = node.child_by_field_name("left") or node.child_by_field_name("name")
            right = node.child_by_field_name("right") or node.child_by_field_name("value")
            names = [text(c) for c in (left.named_children if left is not None and left.type == "expression_list"
                                       else [left] if left is not None else [])]
            if t == "short_var_declaration":
                for n in names:
                    self.local(n, fn)
            vals = right.named_children if right is not None and right.type == "expression_list" else \
                [right] if right is not None else []
            if names and vals and vals[0].type == "call_expression":
                c = self.call(vals[0], cls)
                if c is not None:
                    self.rec["assigns"].append({"name": names[0], **c, "line": line(node)})
        return False

    def _field_decls(self, struct):
        fl = child_of_type(struct, "field_declaration_list")
        return children_of_type(fl, "field_declaration") if fl is not None else []

    def _struct_fields(self, struct) -> dict[str, str]:
        out = {}
        for fd in self._field_decls(struct):
            tn = type_name(fd.child_by_field_name("type"))
            for n in children_of_type(fd, "field_identifier"):
                if tn:
                    out[text(n)] = tn
        return out

    def _obj(self, n) -> str | None:
        if n.type == "identifier":
            s = text(n)
            return "this" if s == self._recv else s
        if n.type == "selector_expression":
            base = self._obj(n.child_by_field_name("operand"))
            return f"{base}.{text(n.child_by_field_name('field'))}" if base else None
        return None

    def call(self, node, cls) -> dict | None:
        f = node.child_by_field_name("function")
        al = node.child_by_field_name("arguments")
        args = [self.arg(c) for c in al.named_children] if al is not None else []
        if f is None:
            return None
        if f.type == "selector_expression":
            obj = self._obj(f.child_by_field_name("operand"))
            return {"obj": obj, "prop": text(f.child_by_field_name("field")), "callee": None, "args": args}
        if f.type == "identifier":
            return {"obj": None, "prop": None, "callee": text(f), "args": args}
        return {"obj": None, "prop": None, "callee": None, "args": args}

    def arg(self, n) -> dict:
        if n.type == "selector_expression":
            obj = self._obj(n.child_by_field_name("operand"))
            if obj:
                return {"t": "member", "v": f"{obj}.{text(n.child_by_field_name('field'))}"}
        return super().arg(n)

    def walk(self, node, cls, fn) -> None:
        if node.type in ("selector_expression", "qualified_type"):
            op = node.child_by_field_name("operand") or node.child_by_field_name("package")
            field = node.child_by_field_name("field") or node.child_by_field_name("name")
            if op is not None and op.type in ("identifier", "package_identifier") and field is not None:
                self.ref(f"{text(op)}.{text(field)}", fn or cls)  # users.Service: resolved through the import
        super().walk(node, cls, fn)

    def finish(self) -> None:
        by_name = {s["name"]: s for s in self.rec["symbols"]}
        for m, node in self._methods:
            recv = m.pop("_recv")
            m["refs"] = [r for r in self._owner_refs.get(id(m), {})][:300]
            owner = by_name.get(recv)
            if owner is not None:
                owner["methods"].append(m)
            else:  # receiver type declared in another file of the package
                sym = new_symbol(f"{recv}.{m['name']}" if recv else m["name"], "function", node, m["name"][:1].isupper())
                sym["refs"], sym["members"] = m["refs"], m["members"]
                self.rec["symbols"].append(sym)


def extract(src: bytes, rel: str) -> dict:
    return GoExtractor(src, rel).run()


def _printf(ex, args) -> str | None:
    """A printf-style call: the format string with each %v/%s/%d replaced by its argument's value."""
    import re
    fmt = ex.eval_str(args[0]) if args else None
    if fmt is None:
        return None
    rest = iter(args[1:])

    def sub(_m):
        a = next(rest, None)
        v = ex.eval_str(a) if a is not None else None
        return v if v is not None else ex._hole(a) if a is not None else ":param"  # noqa: SLF001
    return re.sub(r"%[-+# 0-9.]*[a-zA-Z]", sub, fmt.replace("%%", "\x00")).replace("\x00", "%")
