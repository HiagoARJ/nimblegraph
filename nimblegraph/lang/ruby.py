"""Ruby: require/require_relative, modules and classes (qualified as Admin::UsersController for Rails
autoloading), methods, constants referenced (User, Billing::Invoice) and calls with symbol/keyword arguments and
blocks (their line range lets the Rails routes DSL nest `namespace :api do ... end`)."""

from __future__ import annotations

from nimblegraph.lang.base import Extractor, children_of_type, new_symbol, text

ASSOCIATIONS = {"has_many", "has_one", "belongs_to", "has_and_belongs_to_many"}


def _classify(name: str, kind: str) -> str:
    """:comments (has_many) -> Comment, :author (belongs_to) -> Author, :order_items -> OrderItem."""
    if kind in ("has_many", "has_and_belongs_to_many"):
        name = name[:-3] + "y" if name.endswith("ies") else name[:-1] if name.endswith("s") else name
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))


class RubyExtractor(Extractor):
    lang = "ruby"
    grammar = "ruby"
    FUNCTIONS = frozenset({"method", "singleton_method"})
    CALLS = frozenset({"call"})
    IDENTS = frozenset({"constant"})
    STRINGS = frozenset({"string"})
    LAMBDAS = frozenset({"do_block", "block", "lambda"})
    MEMBERS = frozenset({"scope_resolution"})
    ASSIGNS = frozenset({"assignment"})
    CONCATS = frozenset({"binary"})

    def assignment(self, node):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is not None and left.type in ("identifier", "constant", "instance_variable") and right is not None:
            return text(left), right
        return None

    def template_text(self, n):
        return "".join(text(c) if c.type == "string_content" else "{" + text(c)[2:-1] + "}"
                       for c in n.named_children if c.type in ("string_content", "interpolation"))

    def __init__(self, src: bytes, rel: str):
        super().__init__(src, rel)
        self._ns: list[str] = []

    def visit(self, node, cls, fn) -> bool:
        t = node.type
        if t == "call" and node.child_by_field_name("receiver") is None:
            meth = text(node.child_by_field_name("method"))
            if meth in ("require", "require_relative", "load", "autoload"):
                al = node.child_by_field_name("arguments")
                strs = [self.string_value(c) for c in children_of_type(al, "string")] if al is not None else []
                if strs and strs[-1]:
                    self.add_import(strs[-1], "require_relative" if meth == "require_relative" else "require", [], node)
                return True
        if t in ("module", "class"):
            name_n = node.child_by_field_name("name")
            short = text(name_n)
            qual = "::".join([*self._ns, short]) if self._ns else short
            sym = new_symbol(short.rsplit("::", 1)[-1], "class" if t == "class" else "type", node, True)
            sym["qualified"] = qual
            sup = node.child_by_field_name("superclass")
            sym["extends"] = text(sup.named_children[0]) if sup is not None and sup.named_children else None
            sym["implements"], sym["abstract"], sym["ctor_types"] = [], False, {}
            self.rec["symbols"].append(sym)
            self._ns.append(short)
            for c in node.named_children:
                if c != name_n:
                    self.walk(c, sym if t == "class" else cls, fn)
            self._ns.pop()
            if sup is not None:
                self.ref(sym["extends"], sym)
            return True
        if t == "scope_resolution":
            self.ref(text(node), fn or cls)
        if t == "call" and node.child_by_field_name("receiver") is None and \
                text(node.child_by_field_name("method")) in ASSOCIATIONS:  # has_many :comments -> Comment
            al = node.child_by_field_name("arguments")
            kids = al.named_children if al is not None else []
            target = None
            for c in kids:
                if c.type == "pair" and text(c.child_by_field_name("key")).strip(":") == "class_name":
                    target = self.string_value(c.child_by_field_name("value")) if \
                        c.child_by_field_name("value").type == "string" else None
            if target is None and kids and kids[0].type == "simple_symbol":
                target = _classify(text(kids[0]).lstrip(":"), text(node.child_by_field_name("method")))
            if target:
                self.ref(target, fn or cls)
        if t == "call":  # `include Concern` / `extend X` in a class body: mixins read as implemented types
            meth = text(node.child_by_field_name("method"))
            if cls is not None and fn is None and meth in ("include", "extend", "prepend") \
                    and node.child_by_field_name("receiver") is None:
                al = node.child_by_field_name("arguments")
                for c in al.named_children if al is not None else []:
                    if c.type in ("constant", "scope_resolution"):
                        cls["implements"].append(text(c))
        return False

    def func_name(self, node) -> str | None:
        return text(node.child_by_field_name("name")) or None

    def _obj(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "self":
            return "this"
        if n.type in ("identifier", "constant", "scope_resolution"):
            return text(n)
        if n.type == "instance_variable":
            return "this." + text(n).lstrip("@")
        return None

    def call(self, node, cls) -> dict | None:
        meth = text(node.child_by_field_name("method"))
        al = node.child_by_field_name("arguments")
        args, props = [], {}
        for c in al.named_children if al is not None else []:
            if c.type == "pair":
                k = c.child_by_field_name("key")
                props[text(k).lstrip(":").rstrip(":").strip("\"'")] = self.arg(c.child_by_field_name("value"))
            elif c.type == "block_argument":
                continue
            else:
                args.append(self.arg(c))
        if props:
            args.append({"t": "obj", "props": props})
        blk = node.child_by_field_name("block")
        if blk is not None:
            args.append(self.arg(blk))
        recv = node.child_by_field_name("receiver")
        if recv is None:
            return {"obj": None, "prop": None, "callee": meth, "args": args}
        obj = self._obj(recv)
        return {"obj": obj, "prop": meth, "callee": None, "args": args}

    def arg(self, n) -> dict:
        if n is None:
            return {"t": "other"}
        if n.type in ("simple_symbol", "hash_key_symbol"):
            return {"t": "str", "v": text(n).lstrip(":")}
        if n.type == "array":
            return {"t": "arr", "items": [self.arg(c) for c in n.named_children]}
        if n.type in ("constant", "scope_resolution"):
            return {"t": "id", "v": text(n)}
        return super().arg(n)

    def string_value(self, n) -> str | None:
        if any(c.type == "interpolation" for c in n.named_children):
            return None
        return "".join(text(c) for c in n.named_children if c.type == "string_content")

    def finish(self) -> None:
        if self.rel.endswith(("config.ru", "config/application.rb")) or self.rel.startswith("bin/"):
            self.rec["entry"] = True


def extract(src: bytes, rel: str) -> dict:
    return RubyExtractor(src, rel).run()
