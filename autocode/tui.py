"""autocode's printing kernel: styles, streaming markdown, code highlighting and LaTeX to Unicode.

Stdlib only, and optional: runner.py imports it when the autocode package is
installed and falls back to plain text otherwise.

    python -m autocode.tui README.md    # render a markdown file, streamed like a model reply
"""
import json
import os
import re
import shutil
import sys

COLOR = "FORCE_COLOR" in os.environ or (
    (sys.stdout.isatty() or sys.stderr.isatty()) and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb")

SGR = {"bold": (1, 22), "dim": (2, 22), "italic": (3, 23), "under": (4, 24), "strike": (9, 29),
       "red": (31, 39), "green": (32, 39), "yellow": (33, 39), "blue": (34, 39), "magenta": (35, 39),
       "cyan": (36, 39), "gray": (90, 39)}
ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x1b]*\x1b\\")


def style(text, *names):
    """Wrap text in SGR codes. Each style is switched off with its own code, so styles nest."""
    if not COLOR or not text or not names:
        return text
    on = ";".join(str(SGR[n][0]) for n in names)
    off = ";".join(str(SGR[n][1]) for n in names)
    return f"\x1b[{on}m{text}\x1b[{off}m"


def vlen(text):
    """Visible length (escape codes don't take up columns)."""
    return len(ANSI.sub("", text))


def trunc(text, n):
    """Cut text to n visible columns, keeping escape codes so styles still close."""
    if vlen(text) <= n:
        return text
    out, count = [], 0
    for m in re.finditer(r"(\x1b\[[0-9;]*m|\x1b\]8;;[^\x1b]*\x1b\\)|(.)", text, re.S):
        if m.group(1):
            out.append(m.group(1))
        elif count < n - 1:
            out.append(m.group(2))
            count += 1
    return "".join(out) + "…"


def prompt(text):
    """Mark escape codes as zero-width so readline measures a colored prompt correctly."""
    return ANSI.sub(lambda m: f"\001{m.group(0)}\002", text)


def width():
    return shutil.get_terminal_size((100, 40)).columns


def wrap(text, cols, indent="", hang=None):
    """Word-wrap styled text to cols visible columns; continuation rows start with hang."""
    hang = indent if hang is None else hang
    rows, row, n, empty = [], indent, vlen(indent), True
    for word in text.split(" "):
        size = vlen(word)
        if not empty and n + 1 + size > cols:
            rows.append(row)
            row, n, empty = hang, vlen(hang), True
        if not empty:
            row, n = row + " ", n + 1
        row, n, empty = row + word, n + size, False
    return rows + [row]


# ---------------------------------------------------------------- LaTeX -> Unicode

GREEK = dict(zip(
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho "
    "varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi "
    "Psi Omega".split(), "αβγδϵεζηθϑικλμνξπϖρϱσςτυϕφχψωΓΔΘΛΞΠΣΥΦΨΩ"))
SYMBOLS = dict(pair.split(":", 1) for pair in """
    times:× cdot:· div:÷ pm:± mp:∓ leq:≤ le:≤ geq:≥ ge:≥ neq:≠ ne:≠ approx:≈ equiv:≡ sim:∼ simeq:≃ cong:≅
    propto:∝ infty:∞ partial:∂ nabla:∇ sum:∑ prod:∏ int:∫ iint:∬ oint:∮ in:∈ notin:∉ ni:∋ subset:⊂
    subseteq:⊆ supset:⊃ supseteq:⊇ cup:∪ cap:∩ setminus:∖ emptyset:∅ varnothing:∅ forall:∀ exists:∃ neg:¬
    lnot:¬ land:∧ wedge:∧ lor:∨ vee:∨ oplus:⊕ otimes:⊗ to:→ rightarrow:→ leftarrow:← gets:← Rightarrow:⇒
    Leftarrow:⇐ leftrightarrow:↔ Leftrightarrow:⇔ iff:⟺ implies:⟹ mapsto:↦ uparrow:↑ downarrow:↓
    langle:⟨ rangle:⟩ lceil:⌈ rceil:⌉ lfloor:⌊ rfloor:⌋ ldots:… cdots:⋯ vdots:⋮ ddots:⋱ dots:… prime:′
    circ:∘ bullet:• star:⋆ ast:∗ perp:⊥ parallel:∥ angle:∠ hbar:ℏ ell:ℓ Re:ℜ Im:ℑ aleph:ℵ ll:≪ gg:≫ mid:∣
    vert:| lvert:| rvert:| Vert:‖ top:⊤ bot:⊥ vdash:⊢ models:⊨ degree:° checkmark:✓ square:□
    triangle:△ therefore:∴ because:∵""".split())
SYMBOLS.update({",": " ", ";": " ", ":": " ", ">": " ", "!": "", " ": " ", "quad": "  ", "qquad": "    ",
                "\\": "\n", "{": "{", "}": "}", "$": "$", "%": "%", "&": "&", "#": "#", "_": "_", "|": "‖"})
ACCENTS = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0305", "vec": "\u20d7",
           "dot": "\u0307", "ddot": "\u0308", "tilde": "\u0303", "widetilde": "\u0303"}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ", "E": "𝔼", "F": "𝔽", "1": "𝟙"}
SUP = dict(zip("0123456789+-=()niabcdefghjklmoprstuvwxyzABDEGHIJKLMNOPRTUVW′∘*",
               "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ′°*"))
SUB = dict(zip("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))
VULGAR = {("1", "2"): "½", ("1", "3"): "⅓", ("2", "3"): "⅔", ("1", "4"): "¼", ("3", "4"): "¾", ("1", "8"): "⅛"}
TEXT_COMMANDS = {"text", "textrm", "textnormal", "textit", "textbf", "mbox", "emph"}
FONT_COMMANDS = {"mathrm", "mathit", "mathsf", "mathtt", "mathbf", "boldsymbol", "bm", "mathcal", "mathscr",
                 "mathfrak", "operatorname"}
INVISIBLE = {"left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr",
             "displaystyle", "textstyle", "limits", "nolimits", "nonumber", "notag"}


def _arg(src, i):
    """The argument starting at i: a {group}, a \\command or one character. Returns (text, next index)."""
    while i < len(src) and src[i] == " ":
        i += 1
    if i >= len(src):
        return "", i
    if src[i] == "{":
        depth, j = 0, i
        while j < len(src):
            if src[j] == "\\":
                j += 2
                continue
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            if depth == 0:
                return src[i + 1:j], j + 1
            j += 1
        return src[i + 1:], len(src)
    m = re.match(r"\\(?:[A-Za-z]+|.)", src[i:])
    return (m.group(0), i + len(m.group(0))) if m else (src[i], i + 1)


def _group(x):
    return x if len(x) <= 1 or re.fullmatch(r"[√∛∜]?[\w.′]+", x) else f"({x})"


def _script(x, table, mark):
    x = x.strip()
    if x and all(c in table for c in x):
        return "".join(table[c] for c in x)
    return mark + _group(x) if x else ""


def latex(src):
    """Render a LaTeX math expression as Unicode text, e.g. \\frac{1}{2}\\alpha^2 -> ½α²."""
    out, i = [], 0
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            m = re.match(r"\\([A-Za-z]+|.?)", src[i:])
            name, i = m.group(1), i + len(m.group(0))
            if name in ("frac", "dfrac", "tfrac", "cfrac"):
                a, i = _arg(src, i)
                b, i = _arg(src, i)
                a, b = latex(a).strip(), latex(b).strip()
                out.append(VULGAR.get((a, b)) or f"{_group(a)}/{_group(b)}")
            elif name == "sqrt":
                root = ""
                if src[i:i + 1] == "[" and "]" in src[i:]:
                    j = src.index("]", i)
                    root, i = latex(src[i + 1:j]), j + 1
                a, i = _arg(src, i)
                out.append({"": "√", "3": "∛", "4": "∜"}.get(root) or _script(root, SUP, "^") + "√")
                out.append(_group(latex(a).strip()))
            elif name in TEXT_COMMANDS:
                a, i = _arg(src, i)
                out.append(a)
            elif name in FONT_COMMANDS:
                a, i = _arg(src, i)
                out.append(latex(a))
            elif name == "mathbb":
                a, i = _arg(src, i)
                out.append("".join(BLACKBOARD.get(c, c) for c in a))
            elif name in ACCENTS:
                a, i = _arg(src, i)
                out.append(latex(a) + ACCENTS[name])
            elif name in ("begin", "end"):  # environment names: aligned, cases, pmatrix, ...
                _, i = _arg(src, i)
            elif name in INVISIBLE:
                if src[i:i + 1] == ".":  # \left. is an empty delimiter
                    i += 1
            else:  # \sin, \log and unknown commands just lose their backslash
                out.append(GREEK.get(name) or SYMBOLS.get(name) or name)
        elif ch in "^_":
            a, i = _arg(src, i + 1)
            out.append(_script(latex(a), SUP if ch == "^" else SUB, ch))
        elif ch in "{}":
            i += 1
        else:
            out.append(" " if ch in "&~" else ch)
            i += 1
    return "".join(out)


# ---------------------------------------------------------------- code highlighting

TOKENS = {"comment": ("gray", "italic"), "string": ("green",), "number": ("yellow",), "keyword": ("magenta",),
          "const": ("yellow",), "func": ("blue",), "type": ("cyan",), "deco": ("cyan",), "var": ("cyan",),
          "key": ("blue",), "tag": ("blue",), "attr": ("cyan",)}
_JS = ("async await break case catch class const continue debugger default delete do else export extends finally "
       "for from function if import in instanceof let new of return static super switch this throw try typeof "
       "var void while yield")
_C = ("auto break case char const continue default do double else enum extern float for goto if inline int long "
      "register return short signed sizeof static struct switch typedef union unsigned void volatile while bool")
KEYWORDS = {
    "python": "and as assert async await break class continue def del elif else except finally for from global if "
              "import in is lambda nonlocal not or pass raise return try while with yield match case self",
    "js": _JS,
    "ts": _JS + " abstract as declare enum implements interface keyof namespace private protected public readonly "
                "type",
    "go": "break case chan const continue default defer else fallthrough for func go goto if import interface map "
          "package range return select struct switch type var",
    "rust": "as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod "
            "move mut pub ref return self Self static struct super trait type unsafe use where while",
    "c": _C,
    "cpp": _C + " class namespace template typename public private protected virtual override new delete this "
                "using try catch throw nullptr constexpr",
    "java": "abstract boolean break byte case catch char class continue default do double else enum extends final "
            "finally float for if implements import instanceof int interface long new package private protected "
            "public return short static super switch synchronized this throw throws try void volatile while var "
            "record fun val when object override namespace using",
    "bash": "if then else elif fi for while until do done case esac function in select return local export "
            "readonly unset shift source alias",
    "ruby": "def end if elsif else unless while until for in do return class module begin rescue ensure yield "
            "self then case when require",
    "sql": "select from where and or not insert into values update set delete create table index view drop alter "
           "add join left right inner outer on as group by order having limit offset union all distinct case when "
           "then else end is in like between exists primary key foreign references default",
}
ALIASES = {"py": "python", "python3": "python", "javascript": "js", "jsx": "js", "mjs": "js", "node": "js",
           "typescript": "ts", "tsx": "ts", "golang": "go", "rs": "rust", "h": "c", "c++": "cpp", "cc": "cpp",
           "hpp": "cpp", "cxx": "cpp", "sh": "bash", "shell": "bash", "zsh": "bash", "console": "bash",
           "rb": "ruby", "kotlin": "java", "kt": "java", "scala": "java", "csharp": "java", "cs": "java",
           "c#": "java", "swift": "java", "dart": "java", "yml": "yaml", "jsonc": "json", "json5": "json",
           "html": "xml", "htm": "xml", "svg": "xml", "vue": "xml", "patch": "diff", "psql": "sql",
           "mysql": "sql", "sqlite": "sql", "postgresql": "sql", "toml": "ini", "cfg": "ini", "conf": "ini",
           "dockerfile": "ini", "makefile": "ini", "make": "ini", "env": "ini", "dotenv": "ini"}
NUMBER = r"\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b"
DQ, SQ = r'"(?:\\.|[^"\\])*"?', r"'(?:\\.|[^'\\])*'?"
_lexers = {}


def _lexer(lang):
    """A compiled token regex for lang, or None for languages we don't highlight."""
    lang = ALIASES.get(lang, lang)
    if lang in _lexers:
        return _lexers[lang]
    consts = r"\b(?:True|False|None|true|false|null|nil|undefined|NaN)\b"
    if lang == "json":
        parts = [("key", DQ + r"(?=\s*:)"), ("string", DQ), ("number", NUMBER), ("const", consts)]
    elif lang == "yaml":
        parts = [("comment", r"#.*"), ("key", r"^\s*(?:- )?[\w.\-/]+(?=\s*:(?:\s|$))"), ("string", DQ + "|" + SQ),
                 ("number", NUMBER), ("const", consts)]
    elif lang == "xml":
        parts = [("comment", r"<!--.*?(?:-->|$)"), ("tag", r"</?[\w:.-]+|/?>"), ("attr", r"[\w:-]+(?==)"),
                 ("string", DQ + "|" + SQ)]
    elif lang == "ini":
        parts = [("comment", r"^\s*[#;].*"), ("tag", r"^\s*\[.*\]"), ("key", r"^\s*[\w.\-]+(?=\s*=)"),
                 ("string", DQ + "|" + SQ), ("number", NUMBER), ("const", consts)]
    elif lang in KEYWORDS:
        parts = []
        if lang == "bash":
            parts.append(("var", r"\$\{[^}]*\}?|\$\w+|\$[@#?$!*\-]"))
        if lang == "sql":
            parts.append(("comment", r"--.*"))
        elif lang in ("python", "bash", "ruby"):
            parts.append(("comment", r"#.*"))
        else:
            parts += [("comment", r"//.*"), ("comment", r"/\*.*?(?:\*/|$)")]
        if lang == "python":
            parts += [("string", r'[rRbBuUfF]{0,2}"""[^\n]*?(?:"""|$)'),
                      ("string", r"[rRbBuUfF]{0,2}'''[^\n]*?(?:'''|$)"),
                      ("string", r"[rRbBuUfF]{0,2}(?:" + DQ + "|" + SQ + ")"), ("deco", r"@[\w.]+")]
        elif lang == "rust":
            parts += [("string", DQ), ("string", r"'(?:\\.|[^'\\])'")]
        elif lang == "bash":
            parts += [("string", DQ + r"|'[^']*'?|`[^`]*`?")]
        else:
            parts += [("string", DQ + "|" + SQ), ("string", r"`(?:\\.|[^`\\])*`?")]
        if lang in ("c", "cpp"):
            parts.append(("keyword", r"^\s*#\s*\w+"))
        if lang in ("java", "ts", "js"):
            parts.append(("deco", r"@\w+"))
        words = "|".join(KEYWORDS[lang].split())
        parts += [("number", NUMBER), ("const", consts),
                  ("keyword", (r"(?i:\b(?:%s)\b)" if lang == "sql" else r"\b(?:%s)\b") % words)]
        if lang not in ("bash", "sql"):
            parts += [("func", r"\b[A-Za-z_]\w*(?=\s*\()"), ("type", r"\b[A-Z][A-Za-z0-9_]*\b")]
    else:
        _lexers[lang] = None
        return None
    _lexers[lang] = re.compile("|".join(f"(?P<{kind}_{n}>{pattern})" for n, (kind, pattern) in enumerate(parts)))
    return _lexers[lang]


def highlight(line, lang, state=None):
    """Highlight one line of code. Returns (text, state); state carries an open ''' or /* to the next line."""
    if ALIASES.get(lang, lang) == "diff":
        for prefix, names in (("+++", ("bold",)), ("---", ("bold",)), ("+", ("green",)), ("-", ("red",)),
                              ("@@", ("cyan",)), ("diff ", ("bold",))):
            if line.startswith(prefix):
                return style(line, *names), None
        return line, None
    lexer = _lexer(lang)
    if lexer is None:
        return line, None
    out, pos = [], 0
    if state:
        kind = "comment" if state == "*/" else "string"
        end = line.find(state)
        if end < 0:
            return style(line, *TOKENS[kind]), state
        pos, state = end + len(state), None
        out.append(style(line[:pos], *TOKENS[kind]))
    for m in lexer.finditer(line, pos):
        token = m.group()
        out += [line[pos:m.start()], style(token, *TOKENS[m.lastgroup.split("_")[0]])]
        pos = m.end()
        body = token.lstrip("rRbBuUfF")
        for opener, closer in (('"""', '"""'), ("'''", "'''"), ("/*", "*/")):
            if body.startswith(opener) and (len(body) < 2 * len(opener) or not body.endswith(closer)):
                state = closer
    out.append(line[pos:])
    return "".join(out), state


def highlight_block(code, lang):
    rows, state = [], None
    for line in code.split("\n"):
        row, state = highlight(line, lang, state)
        rows.append(row)
    return "\n".join(rows)


# ---------------------------------------------------------------- markdown

MATH = re.compile(r"\$\$(.+?)\$\$|\\\((.+?)\\\)|(?<![\\$\w])\$(?=[^\s$])((?:\\.|[^$\\])+?)(?<=\S)\$(?!\d)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")


def link(text, url):
    shown = style(text, "blue", "under") + ("" if text == url else " " + style(f"({url})", "gray"))
    return f"\x1b]8;;{url}\x1b\\{shown}\x1b]8;;\x1b\\" if COLOR else shown


def inline(text):
    """Render inline markdown: `code`, $math$, links, **bold**, *italic*, ~~strike~~."""
    keep = []

    def stash(s):  # finished pieces are parked so later patterns can't touch them
        keep.append(s)
        return f"\x00{len(keep) - 1}\x00"

    def emphasis(s):
        s = BOLD.sub(lambda m: stash(style(emphasis(m.group(1) or m.group(2)), "bold")), s)
        s = ITALIC.sub(lambda m: stash(style(emphasis(m.group(1) or m.group(2)), "italic")), s)
        return STRIKE.sub(lambda m: stash(style(m.group(1), "strike")), s)

    text = re.sub(r"(`+)(.+?)\1", lambda m: stash(style(m.group(2), "yellow")), text)
    text = MATH.sub(lambda m: stash(style(latex(next(g for g in m.groups() if g is not None)), "cyan")), text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!$~|<>])", lambda m: stash(m.group(1)), text)
    text = re.sub(r"!?\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)", lambda m: stash(link(inline(m.group(1)), m.group(2))),
                  text)
    text = re.sub(r"<?(https?://[^\s<>()]*[^\s<>().,;:!?'\"])>?", lambda m: stash(link(m.group(1), m.group(1))), text)
    text = emphasis(text)
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)
    return text


def table(lines):
    """Render markdown table rows as a box, shrinking columns to fit the terminal."""
    rows = [[c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))] for line in lines]
    aligns = []
    if len(rows) > 1 and all(re.fullmatch(r":?-+:?", c) for c in rows[1]):
        aligns = ["c" if c[0] == c[-1] == ":" else "r" if c[-1] == ":" else "l" for c in rows.pop(1)]
    cells = [[inline(c.replace("\\|", "|")) for c in row] for row in rows]
    n = max(len(row) for row in cells)
    cells = [row + [""] * (n - len(row)) for row in cells]
    aligns += ["l"] * (n - len(aligns))
    widths = [max(vlen(row[i]) for row in cells) for i in range(n)]
    room = width() - 1 - (3 * n + 1)
    while sum(widths) > room and max(widths) > 3:
        widths[widths.index(max(widths))] -= 1

    def fit(text, w, align):
        text = trunc(text, w)
        gap = w - vlen(text)
        left = {"l": 0, "r": gap, "c": gap // 2}[align]
        return " " * left + text + " " * (gap - left)

    def border(left, mid, right):
        return style(left + mid.join("─" * (w + 2) for w in widths) + right, "gray")

    bar, out = style("│", "gray"), [border("┌", "┬", "┐")]
    for r, row in enumerate(cells):
        header = r == 0 and len(lines) > 1 and len(rows) < len(lines)
        out.append(bar + bar.join(" " + fit(style(c, "bold") if header else c, widths[i], aligns[i]) + " "
                                  for i, c in enumerate(row)) + bar)
        if header and len(cells) > 1:
            out.append(border("├", "┼", "┤"))
    return out + [border("└", "┴", "┘")]


class Markdown:
    """Streaming markdown renderer: feed() text as it arrives, end() when the message is complete.

    Complete lines are rendered and committed to the terminal. The unfinished tail
    (the current line, or a table or display-math block still being written) is
    drawn in a live region that is erased and redrawn on every feed. When output
    isn't a terminal, text passes through untouched.
    """

    def __init__(self, out=None, muted=False):
        self.out, self.muted = out or sys.stdout, muted  # muted: plain gray italic text, for thinking
        tty = self.out.isatty()
        self.rich = COLOR and (tty or "FORCE_COLOR" in os.environ)
        self.live = self.rich and tty
        self.partial, self.mode, self.block = "", None, []
        self.indent = self.fence = self.lang = ""
        self.state, self.drawn, self.last = None, 0, "\n"
        self.blank = self.shown = False

    def feed(self, text):
        if not self.rich:
            return self.write(text)
        self.partial += text
        *lines, self.partial = self.partial.split("\n")
        self.redraw([row for line in lines for row in self.line(line)])

    def end(self):
        if not self.rich:
            return self.write("\n") if self.last != "\n" else None
        rows = self.line(self.partial) if self.partial else []
        self.partial = ""
        self.redraw(rows + self.close(), final=True)

    def write(self, text):
        if text:
            self.out.write(text)
            self.out.flush()
            self.last = text[-1]

    def redraw(self, rows, final=False):
        buf = ""
        if self.drawn:  # erase the live region
            buf += "\r" + (f"\x1b[{self.drawn - 1}A" if self.drawn > 1 else "") + "\x1b[J"
        buf += "".join(row + "\n" for row in self.tidy(rows))
        self.drawn = 0
        if self.live and not final:
            preview, cols = self.preview(), width()
            height = sum(max(1, -(-vlen(row) // cols)) for row in preview)
            if preview and height < shutil.get_terminal_size((100, 40)).lines - 1:
                buf += "\n".join(preview)
                self.drawn = height
        self.write(buf)

    def tidy(self, rows):
        """Drop leading and trailing blank rows and collapse runs of them into one."""
        out = []
        for row in rows:
            if not ANSI.sub("", row).strip():
                self.blank = self.shown
                continue
            if self.blank:
                out.append("")
            out.append(row)
            self.blank, self.shown = False, True
        return out

    def close(self):
        """Finish whatever block is open and return its rows."""
        rows = {"table": lambda: table(self.block), "math": lambda: self.math(self.block),
                "code": lambda: [style(self.indent + "╰─", "gray")]}.get(self.mode, list)()
        self.mode, self.block = None, []
        return rows

    def line(self, line):
        """Consume one complete source line and return the rows it finishes."""
        if self.muted:
            return self.render(line)
        stripped = line.strip()
        if self.mode == "code":
            if re.fullmatch(r"`{3,}|~{3,}", stripped) and stripped[0] == self.fence[0] \
                    and len(stripped) >= len(self.fence):
                return self.close()
            return [self.code(line)]
        if self.mode == "math":
            if stripped.endswith(("$$", "\\]")):
                self.block.append(stripped[:-2])
                return self.close()
            self.block.append(line)
            return []
        if self.mode == "table":
            if stripped.startswith("|"):
                self.block.append(line)
                return []
            return self.close() + self.line(line)
        m = re.match(r"(\s*)(`{3,}|~{3,})\s*([^\s`]*)", line)
        if m:
            self.mode, self.indent, self.fence, self.lang, self.state = "code", m.group(1), m.group(2), \
                m.group(3).lower(), None
            return [style(self.indent + "╭─" + (" " + m.group(3) if m.group(3) else ""), "gray")]
        m = re.fullmatch(r"(?:\$\$|\\\[)(.*?)(?:\$\$|\\\])", stripped)
        if m:
            return self.math([m.group(1)])
        if stripped.startswith(("$$", "\\[")):
            self.mode, self.block = "math", [stripped[2:]]
            return []
        if stripped.startswith("|") and stripped.count("|") > 1:
            self.mode, self.block = "table", [line]
            return []
        return self.render(line)

    def code(self, line):
        line = line.expandtabs(4)
        if not line[:len(self.indent)].strip():
            line = line[len(self.indent):]
        text, self.state = highlight(line, self.lang, self.state)
        return style(self.indent + "│ ", "gray") + text

    def math(self, lines):
        text = latex(" ".join(line.strip() for line in lines if line.strip()))
        return [style("    " + row.strip(), "cyan") for row in text.split("\n") if row.strip()] or [""]

    def preview(self):
        rows = table(self.block) if self.mode == "table" else self.math(self.block) if self.mode == "math" else []
        if self.partial:
            if self.mode == "code":
                state = self.state
                rows.append(self.code(self.partial))
                self.state = state
            elif self.mode:
                rows.append(style(self.partial, "gray"))
            else:
                rows += self.render(self.partial)
        return rows

    def render(self, line):
        """Render one ordinary line: heading, rule, quote, list item or paragraph text."""
        cols = min(width() - 1, 120)
        if not line.strip():
            return [""]
        if self.muted:
            indent = "  " + line[:len(line) - len(line.lstrip())]
            return [style(row, "gray", "italic") for row in wrap(line.strip(), cols, indent)]
        m = re.match(r"(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            names = {1: ("bold", "under", "blue"), 2: ("bold", "blue")}.get(len(m.group(1)), ("bold",))
            return wrap(style(inline(m.group(2)), *names), cols)
        if re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            return [style("─" * min(cols, 80), "gray")]
        m = re.match(r"(\s*)>\s?(.*)", line)
        if m:
            bar = m.group(1) + style("▎ ", "gray")
            return wrap(style(inline(m.group(2)), "italic"), cols, bar)
        m = re.match(r"(\s*)([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?(.*)", line)
        if m:
            indent, marker, task, body = m.groups()
            if task:
                bullet = style("☑", "green") if task[1] in "xX" else style("☐", "gray")
            elif marker in "-*+":
                bullet = style("•◦▪"[len(indent) // 2 % 3], "cyan")
            else:
                bullet = style(marker, "cyan")
            return wrap(inline(body), cols, indent + bullet + " ", " " * (len(indent) + vlen(bullet) + 1))
        indent = line[:len(line) - len(line.lstrip())]
        return wrap(inline(line.strip()), cols, indent)


# ---------------------------------------------------------------- layout

LIGHT = os.environ.get("COLORFGBG", "").split(";")[-1] in ("7", "15")  # light terminal background?
BAR = "48;5;254" if LIGHT else "48;5;236"  # background of your messages


def _hold(text, tag):
    """Split off a trailing partial tag, which may complete in the next chunk."""
    for k in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-k:]):
            return text[:-k], text[-k:]
    return text, ""


def err(*rows):
    print(*rows, sep="\n", file=sys.stderr, flush=True)


class View:
    """Everything the agent shows, laid out as blocks separated by one blank line.

    The answer streams to stdout as rendered markdown; your messages, thinking,
    tool calls and notes go to stderr.
    """

    def __init__(self):
        self.kind, self.markdown, self.pending, self.thinking = None, None, "", False

    def banner(self, model, session, cwd):
        rows = [style("✻ ", "magenta") + style("autocode", "bold"), "",
                style("model    ", "gray") + model, style("cwd      ", "gray") + str(cwd),
                style("session  ", "gray") + session]
        inner = min(max(vlen(row) for row in rows) + 2, width() - 4)
        rows = [trunc(row, inner) for row in rows]
        err(style("╭" + "─" * (inner + 2) + "╮", "gray"),
            *(style("│ ", "gray") + row + " " * (inner - vlen(row)) + style(" │", "gray") for row in rows),
            style("╰" + "─" * (inner + 2) + "╯", "gray"),
            style("  /help for commands · end a line with \\ to continue it", "gray"))

    def ask(self, meter):
        """Read your message; on a terminal it is shown as a full-width gray bar."""
        if not (COLOR and sys.stdin.isatty() and sys.stdout.isatty()):
            line = input(f"\n{meter} > ")
            while line.endswith("\\"):
                line = line[:-1] + "\n" + input("… ")
            return line
        err("")
        parts, used, cols = [], 0, width()
        while True:
            lead = style(" … ", "gray") if parts else style(f" {meter} ", "gray") + style("❯", "bold", "magenta") + " "
            try:
                text = input(prompt(f"\x1b[{BAR}m\x1b[K{lead}"))
            finally:
                sys.stdout.write("\x1b[0m")
                sys.stdout.flush()
            used += max(1, -(-(vlen(lead) + len(text)) // cols))
            parts.append(text[:-1] if text.endswith("\\") else text)
            if not text.endswith("\\"):
                break
        line = "\n".join(parts)
        sys.stderr.write(f"\x1b[{used}A\r\x1b[J")  # replace the typed lines with a clean bar
        rows = [row for n, part in enumerate(line.split("\n"))
                for row in wrap(part, cols - 3, (style("❯", "bold", "magenta") if n == 0 else " ") + " ", "  ")]
        err(*(f"\x1b[{BAR}m {row}{' ' * max(0, cols - 2 - vlen(row))}\x1b[K\x1b[0m" for row in rows))
        return line

    def stream(self, kind, text):
        """Show model output as it arrives; kind is "reasoning" or "text"."""
        if kind != "text":
            return self._show(kind, text)
        self.pending += text  # some servers leave <think>…</think> inside the answer
        while self.pending:
            tag = "</think>" if self.thinking else "<think>"
            i = self.pending.find(tag)
            if i < 0:
                shown, self.pending = _hold(self.pending, tag)
                self._show("reasoning" if self.thinking else "text", shown)
                break
            self._show("reasoning" if self.thinking else "text", self.pending[:i])
            self.pending, self.thinking = self.pending[i + len(tag):], not self.thinking

    def _show(self, kind, text):
        if kind != self.kind:
            if not text.strip():  # don't open a block for bare whitespace
                return
            text = text.lstrip("\n")
            self._close()
            self.kind = kind
            err("")
            if kind == "reasoning":
                err(style("✻ Thinking…", "gray"))
                self.markdown = Markdown(sys.stderr, muted=True)
            else:
                self.markdown = Markdown(sys.stdout)
        self.markdown.feed(text)

    def _close(self):
        if self.markdown:
            self.markdown.end()
        self.kind, self.markdown = None, None

    def end(self):
        """The model's message is complete."""
        if self.pending:
            self._show("reasoning" if self.thinking else "text", self.pending)
        self._close()
        self.pending, self.thinking = "", False

    def tool(self, name, args):
        self.end()
        if name == "bash" and isinstance(args, dict):
            lines = str(args.get("command", "")).splitlines() or [""]
            shown = highlight_block("\n".join(lines[:8]), "bash").replace("\n", "\n  ")
            more = style(f"\n  … +{len(lines) - 8} lines", "gray") if len(lines) > 8 else ""
            err("", style("● ", "green") + shown + more)
        else:
            shown = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items()) if isinstance(args, dict) \
                else json.dumps(args)
            err("", style("● ", "green") + style(name, "bold") + style(trunc(f"({shown})", width() - 4 - len(name)), "gray"))

    def result(self, output, lines=6):
        rows = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]", "", row)
                for row in output.rstrip("\n").splitlines()] or [""]
        failed = output.startswith("Traceback") or re.search(r"\[(exit \d+|killed after [^\]]*)\]\s*$", output)
        shown = rows[:lines] + ([f"… +{len(rows) - lines} lines"] if len(rows) > lines else [])
        cols = width() - 6
        err(*(style("  ⎿  ", "red" if failed else "gray") * (i == 0) + style("     " * (i > 0) + trunc(row, cols), "gray")
              for i, row in enumerate(shown)))

    def note(self, text, color="gray"):
        self.end()
        err("", style(text, color))


if __name__ == "__main__":
    import time
    source = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    renderer = Markdown()
    for start in range(0, len(source), 7):
        renderer.feed(source[start:start + 7])
        time.sleep(0.003 if renderer.live else 0)
    renderer.end()
