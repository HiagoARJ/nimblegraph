"""Pure-Python JS/TS/JSX tokenizer used by the Python engine.

Token kinds:
  id, num, str (string or no-substitution template; .tpl marks the latter), regex, p (punctuator),
  tpl_start / tpl_mid / tpl_end (template with substitutions; expression tokens sit between them),
  jsx_open (v=tag), jsx_attr (v=name, '...' for spread), jsx_body ('>' of an opening tag), jsx_close (v=tag).
"""

from __future__ import annotations

import re

KW_BEFORE_EXPR = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do",
    "else", "yield", "await", "extends",
}

PUNCTS = sorted(
    """>>>= ... === !== **= <<= >>= >>> &&= ||= ??= => == != <= >= && || ?? ?. ++ -- += -= *= /= %= &= |= ^= ** << >>
    { } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = @ .""".split(),
    key=len, reverse=True,
)
RE_PUNCT = re.compile("|".join(re.escape(p) for p in PUNCTS))
RE_WS = re.compile(r"[ \t\r\f\v\u00a0\ufeff\u2028\u2029]+")
RE_ID = re.compile(r"#?[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*")
RE_NUM = re.compile(r"0[xXoObB][\da-fA-F_]+n?|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?n?")
RE_SQ = re.compile(r"'((?:[^'\\\n]|\\[\s\S])*)'?")
RE_DQ = re.compile(r'"((?:[^"\\\n]|\\[\s\S])*)"?')
RE_TPL_CHUNK = re.compile(r"[^`\\$\n]+")
RE_JSX_NAME = re.compile(r"[A-Za-z_$][\w$\-]*(?:[.:][A-Za-z_$][\w$\-]*)*")
RE_JSX_ATTR = re.compile(r"[A-Za-z_$][\w$\-]*(?::[A-Za-z_$][\w$\-]*)?")
RE_JSX_TEXT = re.compile(r"[^<{\n]+")
RE_GENERIC_ARROW = re.compile(r"<\s*[A-Za-z_$][\w$]*\s*(?:,|extends\b)")
RE_ESC = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")
_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def unescape(s: str) -> str:
    if "\\" not in s:
        return s

    def rep(m: re.Match) -> str:
        e = m.group(1)
        if e[0] == "u":
            code = e[2:-1] if e[1] == "{" else e[1:]
            try:
                return chr(int(code, 16))
            except ValueError:
                return e
        if e[0] == "x":
            return chr(int(e[1:], 16))
        if e in ("\n", "\r\n", "\r", "\u2028", "\u2029"):
            return ""
        return _SIMPLE_ESC.get(e, e)

    return RE_ESC.sub(rep, s)


class Tok:
    __slots__ = ("t", "v", "line", "el", "nl", "tpl")

    def __init__(self, t: str, v: str, line: int, el: int, nl: bool, tpl: bool = False):
        self.t = t
        self.v = v
        self.line = line
        self.el = el
        self.nl = nl
        self.tpl = tpl

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.t}:{self.v!r}@{self.line}"


class Lexer:
    def __init__(self, src: str, jsx: bool):
        if src.startswith("\ufeff"):
            src = src[1:]
        if src.startswith("#!"):
            nl = src.find("\n")
            src = " " * (nl if nl >= 0 else len(src)) + (src[nl:] if nl >= 0 else "")
        self.s = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.jsx = jsx
        self.nl = True
        self.toks: list[Tok] = []
        self.tpl_parts: dict[int, list[int]] = {}  # tpl_start index -> [mid..., end] indexes
        self.in_jsx = 0

    # ------------------------------------------------------------ helpers
    def emit(self, t: str, v: str, line: int | None = None, el: int | None = None, tpl: bool = False) -> int:
        line = self.line if line is None else line
        nl = self.nl and not self.in_jsx
        self.toks.append(Tok(t, v, line, self.line if el is None else el, nl, tpl))
        self.nl = False
        return len(self.toks) - 1

    def expr_allowed(self) -> bool:
        if not self.toks:
            return True
        p = self.toks[-1]
        if p.t == "p":
            return p.v not in (")", "]", "}")
        if p.t == "id":
            if len(self.toks) > 1 and self.toks[-2].t == "p" and self.toks[-2].v in (".", "?."):
                return False  # obj.delete<T>(...), obj.return / 2: property names, not keywords
            return p.v in KW_BEFORE_EXPR
        return p.t in ("jsx_attr", "jsx_body")

    def skip_ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "\n":
                self.line += 1
                self.nl = True
                self.i += 1
            elif c in " \t\r\f\v\u00a0\ufeff\u2028\u2029":
                m = RE_WS.match(s, self.i)
                self.i = m.end()
            elif c == "/" and self.i + 1 < n and s[self.i + 1] == "/":
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j
            elif c == "/" and self.i + 1 < n and s[self.i + 1] == "*":
                j = s.find("*/", self.i + 2)
                j = n if j < 0 else j + 2
                k = s.count("\n", self.i, j)
                if k:
                    self.line += k
                    self.nl = True
                self.i = j
            else:
                return

    # ------------------------------------------------------------ JS
    def lex(self) -> list[Tok]:
        self.lex_js(stop_brace=False)
        return self.toks

    def lex_js(self, stop_brace: bool) -> None:
        s, n = self.s, self.n
        depth = 0
        while True:
            self.skip_ws()
            if self.i >= n:
                return
            c = s[self.i]
            if c == "'" or c == '"':
                m = (RE_SQ if c == "'" else RE_DQ).match(s, self.i)
                k = s.count("\n", self.i, m.end())
                line = self.line
                self.line += k
                self.emit("str", unescape(m.group(1)), line)
                self.i = m.end()
            elif c == "`":
                self.lex_template()
            elif c.isalpha() or c in "_$#" or ord(c) > 127:
                m = RE_ID.match(s, self.i)
                if not m:
                    self.i += 1
                    continue
                self.emit("id", m.group())
                self.i = m.end()
            elif c.isdigit() or (c == "." and self.i + 1 < n and s[self.i + 1].isdigit()):
                m = RE_NUM.match(s, self.i)
                self.emit("num", m.group())
                self.i = m.end()
            elif c == "/" and self.expr_allowed() and self.lex_regex():
                pass
            elif c == "<" and self.jsx and self.expr_allowed() and self.looks_like_jsx():
                self.lex_jsx_element()
            else:
                m = RE_PUNCT.match(s, self.i)
                if not m:
                    self.i += 1
                    continue
                v = m.group()
                if v == "{":
                    depth += 1
                elif v == "}":
                    if depth == 0 and stop_brace:
                        self.i += 1
                        return
                    depth -= 1
                self.emit("p", v)
                self.i = m.end()

    def lex_regex(self) -> bool:
        s, n = self.s, self.n
        j = self.i + 1
        in_class = False
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return False
            if in_class:
                if c == "]":
                    in_class = False
            elif c == "[":
                in_class = True
            elif c == "/":
                break
            j += 1
        else:
            return False
        j += 1
        while j < n and (s[j].isalnum() or s[j] in "_$"):
            j += 1
        self.emit("regex", s[self.i:j])
        self.i = j
        return True

    def lex_template(self) -> None:
        s, n = self.s, self.n
        start_line = self.line
        self.i += 1
        buf: list[str] = []
        start_idx: int | None = None
        while self.i < n:
            c = s[self.i]
            if c == "`":
                self.i += 1
                text = unescape("".join(buf))
                if start_idx is None:
                    self.emit("str", text, start_line, tpl=True)
                else:
                    self.tpl_parts[start_idx].append(self.emit("tpl_end", text))
                return
            if c == "\\":
                if s[self.i + 1:self.i + 2] == "\n":
                    self.line += 1
                buf.append(s[self.i:self.i + 2])
                self.i += 2
                continue
            if c == "$" and s[self.i + 1:self.i + 2] == "{":
                text = unescape("".join(buf))
                buf = []
                if start_idx is None:
                    start_idx = self.emit("tpl_start", text, start_line)
                    self.tpl_parts[start_idx] = []
                else:
                    self.tpl_parts[start_idx].append(self.emit("tpl_mid", text))
                self.i += 2
                saved = self.in_jsx
                self.in_jsx = 0
                self.lex_js(stop_brace=True)
                self.in_jsx = saved
                continue
            if c == "\n":
                self.line += 1
                buf.append(c)
                self.i += 1
                continue
            if c == "$":
                buf.append(c)
                self.i += 1
                continue
            m = RE_TPL_CHUNK.match(s, self.i)
            buf.append(m.group())
            self.i = m.end()
        # unterminated template
        if start_idx is None:
            self.emit("str", unescape("".join(buf)), start_line, tpl=True)
        else:
            self.tpl_parts[start_idx].append(self.emit("tpl_end", unescape("".join(buf))))

    # ------------------------------------------------------------ JSX
    def looks_like_jsx(self) -> bool:
        s = self.s
        nxt = s[self.i + 1:self.i + 2]
        if not nxt or not (nxt == ">" or nxt.isalpha() or nxt in "_$"):
            return False
        return not RE_GENERIC_ARROW.match(s, self.i)

    def jsx_ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "\n":
                self.line += 1
                self.i += 1
            elif c.isspace():
                self.i += 1
            elif s.startswith("/*", self.i):
                j = s.find("*/", self.i + 2)
                j = n if j < 0 else j + 2
                self.line += s.count("\n", self.i, j)
                self.i = j
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j
            else:
                return

    def jsx_expr(self) -> None:
        """At '{': emits '{', the JS tokens, and '}'."""
        self.emit("p", "{")
        self.i += 1
        saved = self.in_jsx
        self.in_jsx = 0
        self.lex_js(stop_brace=True)
        self.in_jsx = saved
        self.emit("p", "}")

    def lex_jsx_element(self) -> None:
        s, n = self.s, self.n
        nl_before = self.nl
        self.in_jsx += 1
        line = self.line
        self.i += 1
        self.jsx_ws()
        if s[self.i:self.i + 1] == ">":
            self.i += 1
            self.toks.append(Tok("jsx_open", "", line, line, nl_before))
            self.nl = False
            self.emit("jsx_body", "")
            self.jsx_children("")
            self.in_jsx -= 1
            return
        m = RE_JSX_NAME.match(s, self.i)
        tag = m.group() if m else ""
        self.i = m.end() if m else self.i
        self.toks.append(Tok("jsx_open", tag, line, line, nl_before))
        self.nl = False
        self.jsx_ws()
        if s[self.i:self.i + 1] == "<":  # type arguments: <Select<Option> ... />
            depth = 0
            while self.i < n:
                c = s[self.i]
                if c == "<":
                    depth += 1
                elif c == ">":
                    depth -= 1
                    if depth == 0:
                        self.i += 1
                        break
                elif c == "\n":
                    self.line += 1
                self.i += 1
        while self.i < n:
            self.jsx_ws()
            if self.i >= n:
                break
            if s.startswith("/>", self.i):
                self.i += 2
                self.emit("jsx_close", tag)
                self.in_jsx -= 1
                return
            c = s[self.i]
            if c == ">":
                self.i += 1
                self.emit("jsx_body", "")
                self.jsx_children(tag)
                self.in_jsx -= 1
                return
            if c == "{":
                self.emit("jsx_attr", "...")
                self.jsx_expr()
                continue
            m = RE_JSX_ATTR.match(s, self.i)
            if not m:
                self.i += 1
                continue
            self.emit("jsx_attr", m.group())
            self.i = m.end()
            self.jsx_ws()
            if s[self.i:self.i + 1] != "=":
                continue
            self.i += 1
            self.jsx_ws()
            c = s[self.i:self.i + 1]
            if c in ("'", '"'):
                j = s.find(c, self.i + 1)
                j = n if j < 0 else j
                val = s[self.i + 1:j]
                vline = self.line
                self.line += val.count("\n")
                self.emit("str", val, vline)
                self.i = j + 1
            elif c == "{":
                self.jsx_expr()
            elif c == "<":
                self.lex_jsx_element()
        self.in_jsx -= 1

    def jsx_children(self, tag: str) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "<":
                if s.startswith("</", self.i):
                    j = s.find(">", self.i)
                    j = n if j < 0 else j
                    self.line += s.count("\n", self.i, j)
                    self.i = j + 1
                    self.emit("jsx_close", tag)
                    return
                self.lex_jsx_element()
            elif c == "{":
                self.jsx_expr()
            elif c == "\n":
                self.line += 1
                self.i += 1
            else:
                m = RE_JSX_TEXT.match(s, self.i)
                self.i = m.end()


def tokenize(src: str, jsx: bool) -> tuple[list[Tok], dict[int, list[int]]]:
    lx = Lexer(src, jsx)
    return lx.lex(), lx.tpl_parts
