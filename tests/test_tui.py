"""Unit tests for the printing kernel (autocode/tui.py)."""
import io
import unittest

from autocode import tui


def plain(text):
    return tui.ANSI.sub("", text)


class KernelTest(unittest.TestCase):
    def setUp(self):  # render as if on a 100-column color terminal, whatever runs the tests
        self.saved = tui.COLOR, tui.width
        tui.COLOR, tui.width = True, lambda: 100

    def tearDown(self):
        tui.COLOR, tui.width = self.saved

    def render(self, text, chunk=3):
        out = io.StringIO()
        md = tui.Markdown(out)
        md.rich = True
        for i in range(0, len(text), chunk):
            md.feed(text[i:i + chunk])
        md.end()
        return plain(out.getvalue())

    def test_latex(self):
        cases = {r"\frac{1}{2}\alpha^2": "½α²", r"\sum_{i=1}^{n} x_i^2": "∑ᵢ₌₁ⁿ xᵢ²",
                 r"\mathbb{R}^n \to \mathbb{R}": "ℝⁿ → ℝ", r"\sqrt{x^2+y^2}": "√(x²+y²)", r"90^\circ": "90°",
                 r"\frac{\sqrt{\pi}}{2}": "√π/2", r"\left( \frac{a+b}{c} \right)": "( (a+b)/c )",
                 r"\hat{y} \leq \infty": "ŷ ≤ ∞", r"\text{if } x \in A": "if  x ∈ A"}
        for source, expected in cases.items():
            self.assertEqual(tui.latex(source), expected, source)
        self.assertEqual(len(tui.GREEK), 40)

    def test_inline(self):
        self.assertEqual(plain(tui.inline("**bold**, *it*, `x*y*z` and ~~old~~")), "bold, it, x*y*z and old")
        self.assertIn("\x1b[1m", tui.inline("**bold**"))
        self.assertEqual(plain(tui.inline("costs $5 or $10")), "costs $5 or $10")
        self.assertEqual(plain(tui.inline(r"where $x^2$ and \(\alpha\)")), "where x² and α")
        self.assertEqual(plain(tui.inline("call snake_case_name")), "call snake_case_name")
        self.assertEqual(plain(tui.inline(r"\*literal\*")), "*literal*")
        self.assertEqual(plain(tui.inline("[docs](https://x.y/z)")), "docs (https://x.y/z)")

    def test_highlight(self):
        self.assertIn("\x1b[35mdef\x1b[39m", tui.highlight("def f(): return None", "python")[0])
        text, state = tui.highlight('doc = """start', "py")
        self.assertEqual(state, '"""')
        self.assertIsNone(tui.highlight('end""" + x', "py", state)[1])
        self.assertEqual(tui.highlight("int x; /* open", "c")[1], "*/")
        self.assertEqual(tui.highlight("just words", "text"), ("just words", None))
        self.assertTrue(tui.highlight("+ added", "diff")[0].startswith("\x1b[32m"))

    def test_blocks(self):
        out = self.render("# Title\n\n- one\n  - two\n- [x] done\n\n> quoted\n\n```python\ndef f():\n    pass\n```\n"
                          "| a | b |\n|---|--:|\n| x | 12 |\n\n$$\n\\frac{1}{2}\n$$\n---\n")
        for piece in ["Title", "• one", "  ◦ two", "☑ done", "▎ quoted", "╭─ python", "│ def f():",
                      "│     pass", "╰─", "┌───┬────┐", "│ a │  b │", "│ x │ 12 │", "    ½", "─" * 80]:
            self.assertIn(piece, out)

    def test_chunking_does_not_change_output(self):
        text = "Intro with **bold\nacross** lines and `code`.\n\n```js\nconst a = `t`; // c\n```\n| h |\n|---|\n| v |\n"
        self.assertEqual(self.render(text, 1), self.render(text, 1000))

    def test_wrapping_keeps_list_indent(self):
        rows = self.render("- " + "word " * 40 + "\n").rstrip("\n").split("\n")
        self.assertGreater(len(rows), 1)
        self.assertTrue(all(len(row) <= 99 for row in rows))
        self.assertTrue(rows[1].startswith("  word"))

    def test_blank_rows_collapse(self):
        self.assertEqual(self.render("\n\nA\n\n\n\nB\n\n"), "A\n\nB\n")

    def test_think_tags_in_answer_go_to_thinking(self):
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            view = tui.View()
            for chunk in ["<thi", "nk>plan ", "it</thi", "nk>\n\nThe ", "answer."]:
                view.stream("text", chunk)
            view.end()
        self.assertEqual(out.getvalue(), "The answer.\n")
        self.assertIn("Thinking", err.getvalue())
        self.assertIn("plan it", err.getvalue())
        self.assertNotIn("think>", out.getvalue() + err.getvalue())

    def test_plain_passthrough_off_terminal(self):
        out = io.StringIO()
        md = tui.Markdown(out)
        md.rich = False
        md.feed("# raw *markdown*")
        md.end()
        self.assertEqual(out.getvalue(), "# raw *markdown*\n")


if __name__ == "__main__":
    unittest.main()
