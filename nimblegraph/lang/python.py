"""Python: imports (absolute, relative, aliased, star), classes with bases and decorators, functions and methods
(`self` reads as `this`), typed `__init__` injection, calls with keyword arguments and the module-level
`app = FastAPI()` / `router = APIRouter(prefix=...)` assignments route detectors need."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, child_of_type, children_of_type, text, line


def type_name(n) -> str | None:
    if n is None:
        return None
    if n.type == "type":
        return type_name(n.named_children[0]) if n.named_children else None
    if n.type == "identifier":
        return text(n)
    if n.type == "attribute":
        return text(n).rsplit(".", 1)[-1]
    if n.type in ("generic_type", "subscript"):  # Optional[Repo] / list[Item]
        inner = [c for c in n.named_children if c.type in ("identifier", "attribute", "type", "type_parameter")]
        return type_name(inner[-1]) if inner else None
    if n.type == "type_parameter":
        return type_name(n.named_children[0]) if n.named_children else None
    if n.type == "string":
        return text(n).strip("'\"") or None
    return None


class PythonExtractor(Extractor):
    lang = "python"
    grammar = "python"
    CLASSES = {"class_definition": "class"}
    FUNCTIONS = frozenset({"function_definition"})
    CALLS = frozenset({"call"})
    IDENTS = frozenset({"identifier"})
    STRINGS = frozenset({"string"})
    LAMBDAS = frozenset({"lambda"})
    MEMBERS = frozenset({"attribute"})
    ASSIGNS = frozenset({"assignment"})

    def assignment(self, node):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is not None and left.type in ("identifier", "attribute") and right is not None:
            return text(left), right
        return None

    def __init__(self, src: bytes, rel: str):
        super().__init__(src, rel)
        self.rec["assigns"] = []
        self._decos: list[dict] = []

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "import_statement":
            for c in node.named_children:
                if c.type == "dotted_name":
                    mod = text(c)
                    self.add_import(mod, "import", [["*", mod.split(".")[0]]], node)
                elif c.type == "aliased_import":
                    self.add_import(text(c.child_by_field_name("name")), "import",
                                    [["*", text(c.child_by_field_name("alias"))]], node)
            return True
        if t == "import_from_statement":
            mod_n = node.child_by_field_name("module_name")
            mod = text(mod_n).replace(" ", "")
            names = []
            for c in node.named_children:
                if c == mod_n:
                    continue
                if c.type == "dotted_name":
                    names.append([text(c), text(c)])
                elif c.type == "aliased_import":
                    names.append([text(c.child_by_field_name("name")), text(c.child_by_field_name("alias"))])
                elif c.type == "wildcard_import":
                    names.append(["*", "*"])
            self.add_import(mod, "from", names, node)
            return True
        if t == "decorated_definition":
            self._decos = [self._decorator(d) for d in children_of_type(node, "decorator")]
            for d in children_of_type(node, "decorator"):  # decorator expressions are references/calls too
                for c in d.named_children:
                    self.walk(c, cls, fn)
            defn = node.child_by_field_name("definition")
            if defn is not None:
                self.walk(defn, cls, fn)
            self._decos = []
            return True
        if t == "if_statement" and cls is None and fn is None:
            cond = text(node.child_by_field_name("condition"))
            if "__name__" in cond and "__main__" in cond:
                self.rec["entry"] = True
        if t == "assignment" and fn is not None:
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                self.local(text(left), fn)
        if t in ("assignment",) and cls is None and fn is None:
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                c = self.call(right, None)
                if c is not None:
                    self.rec["assigns"].append({"name": text(left), **c, "line": line(node)})
            if left is not None and left.type == "identifier" and text(left) == "urlpatterns":
                self.rec["urlpatterns"] = True
        return False

    def _decorator(self, d) -> dict:
        expr = d.named_children[0] if d.named_children else None
        args: list = []
        if expr is not None and expr.type == "call":
            c = self.call(expr, None) or {}
            args = c.get("args", [])
            expr = expr.child_by_field_name("function")
        name = text(expr)
        return {"name": name.rsplit(".", 1)[-1], "full": name, "args": args}

    def decorators(self, node) -> list[dict]:
        out, self._decos = self._decos, []  # consumed by the definition they precede
        return out

    def exported(self, node, name: str) -> bool:
        return not name.startswith("_")

    def bases(self, node):
        al = node.child_by_field_name("superclasses")
        names = [type_name(c) for c in al.named_children if c.type in ("identifier", "attribute")] if al is not None else []
        names = [n for n in names if n and n != "object"]
        return (names[0] if names else None), names[1:]

    def is_abstract(self, node) -> bool:
        al = node.child_by_field_name("superclasses")
        return al is not None and any(text(c).rsplit(".", 1)[-1] in ("ABC", "Protocol") for c in al.named_children)

    def is_constructor(self, node, name: str, cls: dict) -> bool:
        return name == "__init__"

    def _params(self, node):
        ps = node.child_by_field_name("parameters")
        return [p for p in (ps.named_children if ps is not None else [])
                if p.type in ("typed_parameter", "typed_default_parameter")]

    def ctor_types(self, node) -> dict[str, str]:
        out = {}
        for p in self._params(node):
            name_n = p.child_by_field_name("name") or child_of_type(p, "identifier")
            tn = type_name(p.child_by_field_name("type"))
            if name_n is not None and tn:
                out[text(name_n)] = tn
        return out

    def param_types(self, node) -> list[str]:
        return [tn for tn in (type_name(p.child_by_field_name("type")) for p in self._params(node)) if tn]

    def param_names(self, node) -> list[str]:
        ps = node.child_by_field_name("parameters")
        out = []
        for p in ps.named_children if ps is not None else []:
            n = p if p.type == "identifier" else p.child_by_field_name("name") or child_of_type(p, "identifier")
            if n is not None and n.type == "identifier":
                out.append(text(n))
        return out

    def _obj(self, n) -> str | None:
        if n.type == "identifier":
            s = text(n)
            return "this" if s in ("self", "cls") else s
        if n.type == "attribute":
            base = self._obj(n.child_by_field_name("object"))
            return f"{base}.{text(n.child_by_field_name('attribute'))}" if base else None
        return None

    def call(self, node, cls) -> dict | None:
        f = node.child_by_field_name("function")
        al = node.child_by_field_name("arguments")
        args, props = [], {}
        for c in al.named_children if al is not None and al.type == "argument_list" else []:
            if c.type == "keyword_argument":
                props[text(c.child_by_field_name("name"))] = self.arg(c.child_by_field_name("value"))
            elif c.type not in ("comment",):
                args.append(self.arg(c))
        if props:
            args.append({"t": "obj", "props": props})
        if f is None:
            return None
        if f.type == "attribute":
            obj = self._obj(f.child_by_field_name("object"))
            return {"obj": obj, "prop": text(f.child_by_field_name("attribute")), "callee": None, "args": args}
        if f.type == "identifier":
            return {"obj": None, "prop": None, "callee": text(f), "args": args}
        return {"obj": None, "prop": None, "callee": None, "args": args}

    def arg(self, n) -> dict:
        if n is not None and n.type in ("list", "tuple"):
            return {"t": "arr", "items": [self.arg(c) for c in n.named_children]}
        if n is not None and n.type == "attribute":
            obj = self._obj(n)
            return {"t": "member", "v": obj} if obj else {"t": "other"}
        return super().arg(n) if n is not None else {"t": "other"}

    def string_value(self, n) -> str | None:
        if any(c.type == "interpolation" for c in n.named_children):
            return None
        content = [text(c) for c in n.named_children if c.type == "string_content"]
        return "".join(content)

    def walk(self, node, cls, fn) -> None:
        if node.type == "attribute":  # users.UserService / self.repo: dotted refs
            obj = self._obj(node)
            if obj and not obj.startswith("this"):
                self.ref(obj, fn or cls)
        super().walk(node, cls, fn)


def extract(src: bytes, rel: str) -> dict:
    return PythonExtractor(src, rel).run()
