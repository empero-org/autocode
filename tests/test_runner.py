"""End-to-end tests against a scripted fake OpenAI-compatible server.

Run with: python -m unittest discover tests
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "autocode" / "runner.py"


def tool_call(name, **args):
    return {"tool_calls": [{"id": f"call_{name}", "name": name, "arguments": json.dumps(args)}]}


class FakeServer:
    """Answers each /chat/completions request with the next scripted reply, streamed as SSE."""

    def __init__(self, replies, stream=True):
        self.replies, self.requests = list(replies), []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append(body)
                reply = server.replies.pop(0)
                if callable(reply):
                    reply = reply(body)
                if isinstance(reply, dict) and "status" in reply:
                    return self.send(reply["status"], "application/json",
                                     json.dumps({"error": {"message": reply["error"]}}).encode())
                if not stream:
                    return self.send(200, "application/json", json.dumps(server.full(reply)).encode())
                self.send(200, "text/event-stream", b"".join(
                    b"data: " + json.dumps(c).encode() + b"\n\n" for c in server.chunks(reply)) + b"data: [DONE]\n\n")

            def send(self, code, ctype, data):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"

    @staticmethod
    def chunks(reply):
        if isinstance(reply, str):
            reply = {"content": reply}
        if reply.get("reasoning"):
            yield {"choices": [{"delta": {"reasoning_content": reply["reasoning"]}}]}
        for parts in reply.get("reasoning_details", []):
            yield {"choices": [{"delta": {"reasoning_details": parts}}]}
        text = reply.get("content") or ""
        for i in range(0, len(text), 5):  # split text across chunks like a real stream
            yield {"choices": [{"delta": {"content": text[i:i + 5]}}]}
        for n, call in enumerate(reply.get("tool_calls", [])):
            args = call["arguments"]
            yield {"choices": [{"delta": {"tool_calls": [{"index": n, "id": call["id"], "type": "function",
                                                          "function": {"name": call["name"], "arguments": args[:7]}}]}}]}
            yield {"choices": [{"delta": {"tool_calls": [{"index": n, "function": {"arguments": args[7:]}}]}}]}
        yield {"choices": [], "usage": {"prompt_tokens": reply.get("tokens", 100), "completion_tokens": 10}}

    @staticmethod
    def full(reply):
        if isinstance(reply, str):
            reply = {"content": reply}
        calls = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                 for c in reply.get("tool_calls", [])]
        return {"choices": [{"message": {"role": "assistant", "content": reply.get("content"),
                                         **({"tool_calls": calls} if calls else {})}}]}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(RUNNER, self.dir / "runner.py")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_agent(self, server, *args, config=None):
        env = dict(os.environ, AUTOCODE_BASE_URL=server.url, AUTOCODE_MODEL="fake", AUTOCODE_API_KEY="k",
                   **{f"AUTOCODE_{k.upper()}": json.dumps(v) for k, v in (config or {}).items()})
        proc = subprocess.run([sys.executable, "runner.py", "-p", *args], cwd=self.dir, env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        server.close()
        return proc

    def session(self):
        (path,) = (self.dir / ".autocode" / "sessions").glob("*.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_bash_tool_loop(self):
        server = FakeServer([
            {"content": "Creating it.", "tool_calls": [
                {"id": "a", "name": "bash", "arguments": json.dumps({"command": "mkdir sub && cd sub && echo hi > f.txt"})},
                {"id": "b", "name": "bash", "arguments": json.dumps({"command": "pwd; cat f.txt; exit 3"})}]},
            {"reasoning": "all good", "content": "Done."},
        ])
        proc = self.run_agent(server, "make a file")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Done.", proc.stdout)
        tool_results = [m["content"] for m in self.session() if m["role"] == "tool"]
        self.assertEqual(tool_results[0], "(no output)")
        self.assertIn(str(self.dir / "sub"), tool_results[1])  # cwd persisted across calls
        self.assertIn("hi\n[exit 3]", tool_results[1])
        first, second = server.requests
        self.assertEqual(first["messages"][0]["role"], "system")
        self.assertEqual(first["tools"][0]["function"]["name"], "bash")
        self.assertEqual(second["messages"][2]["tool_calls"][1]["function"]["arguments"],
                         json.dumps({"command": "pwd; cat f.txt; exit 3"}))

    def test_reasoning_details(self):
        server = FakeServer([
            {**tool_call("bash", command="true"), "reasoning_details": [
                [{"type": "reasoning.text", "text": "Plan ", "index": 0}],
                [{"type": "reasoning.text", "text": "it.", "index": 0, "signature": "sig"}],
                [{"type": "reasoning.summary", "summary": "Short plan.", "index": 1}],
                [{"type": "reasoning.encrypted", "data": "xyz", "index": 2}]]},
            "Done.",
        ])
        proc = self.run_agent(server, "think")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Plan it.Short plan.", proc.stderr)  # shown as thinking
        expected = [{"type": "reasoning.text", "text": "Plan it.", "index": 0, "signature": "sig"},
                    {"type": "reasoning.summary", "summary": "Short plan.", "index": 1},
                    {"type": "reasoning.encrypted", "data": "xyz", "index": 2}]
        sent = server.requests[1]["messages"][2]
        self.assertEqual(sent["reasoning_details"], expected)  # merged, and sent back for the tool loop
        self.assertNotIn("reasoning", sent)  # the plain text copy stays local unless keep_reasoning

    def test_non_streaming_server(self):
        server = FakeServer([tool_call("bash", command="echo ok"), "Fine."], stream=False)
        proc = self.run_agent(server, "go")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Fine.", proc.stdout)

    def test_agent_written_tool(self):
        tool = ("SCHEMA = {'name': 'add', 'description': 'Add numbers.', 'parameters': {'type': 'object',"
                " 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}}}}\n"
                "def run(a, b):\n    return str(a + b)\n")
        server = FakeServer([
            tool_call("bash", command=f"cat > .autocode/tools/add.py <<'EOF'\n{tool}EOF"),
            lambda body: tool_call("add", a=2, b=3) if any(t["function"]["name"] == "add" for t in body["tools"])
            else "tool missing",
            "5",
        ])
        proc = self.run_agent(server, "add")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([m["content"] for m in self.session() if m["role"] == "tool"][-1], "5")

    def test_broken_tool_is_visible(self):
        (self.dir / ".autocode" / "tools").mkdir(parents=True)
        (self.dir / ".autocode" / "tools" / "oops.py").write_text("SCHEMA = {\n")
        server = FakeServer(["ok"])
        self.run_agent(server, "hi")
        (oops,) = [t for t in server.requests[0]["tools"] if t["function"]["name"] == "oops"]
        self.assertIn("BROKEN", oops["function"]["description"])

    def test_self_modification_reloads_and_resumes(self):
        server = FakeServer([
            tool_call("bash", command="echo '# improved' >> runner.py"),
            "Reloaded and done.",
        ])
        proc = self.run_agent(server, "improve yourself")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("reloading", proc.stderr)
        self.assertIn("Reloaded and done.", proc.stdout)
        self.assertTrue((self.dir / ".autocode" / "runner.prev.py").exists())
        self.assertTrue((self.dir / "runner.py").read_text().endswith("# improved\n"))

    def test_syntax_error_is_reported_not_reloaded(self):
        server = FakeServer([tool_call("bash", command="echo 'def (' >> runner.py"), "Oops."])
        proc = self.run_agent(server, "break yourself")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("reloading", proc.stderr)
        self.assertIn("not reloaded: SyntaxError", server.requests[1]["messages"][-1]["content"])

    def test_compaction(self):
        def summarize(body):
            assert body["tool_choice"] == "none" and body["messages"][-1]["content"].startswith("Your context is full")
            return "SUMMARY: user wants X; step 1 done."

        server = FakeServer([
            {**tool_call("bash", command="echo step1"), "tokens": 900},  # 900 > 0.8 * 1000: compact next
            summarize,
            "Finished.",
        ])
        proc = self.run_agent(server, "do X", config={"context_window": 1000})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        last = server.requests[-1]["messages"]
        self.assertEqual([m["role"] for m in last], ["system", "user", "assistant", "tool"])  # summary + latest step
        self.assertIn("SUMMARY: user wants X", last[1]["content"])
        self.assertEqual(last[3]["content"], "step1\n")
        self.assertTrue(list((self.dir / ".autocode" / "sessions" / "old").glob("*.jsonl")))

    def test_compaction_survives_server_ignoring_tool_choice(self):
        server = FakeServer([
            {**tool_call("bash", command="echo 'FAILED test_tz - AssertionError: 13 != 12'"), "tokens": 900},
            {"content": "Let me look at the test file first.", **tool_call("bash", command="cat test_dates.py")},
            "SUMMARY: fixing the tz test in test_dates.py.",
            "Finished.",
        ])
        proc = self.run_agent(server, "fix the failing date test", config={"context_window": 1000})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fallback = server.requests[2]
        self.assertNotIn("tools", fallback)  # retried as a plain transcript, with nothing to call
        self.assertIn("<transcript>", fallback["messages"][-1]["content"])
        seen = "\n".join(m.get("content") or "" for m in server.requests[-1]["messages"])
        self.assertIn("SUMMARY: fixing the tz test", seen)
        self.assertIn("AssertionError: 13 != 12", seen)  # the latest tool output survives verbatim
        self.assertNotIn("Let me look at the test file first.", seen)

    def test_failed_compaction_does_not_block(self):
        server = FakeServer([
            {**tool_call("bash", command="echo step1"), "tokens": 900},
            "",  # compaction answered with nothing, natively...
            "",  # ...and as a transcript
            "Finished anyway.",
        ])
        proc = self.run_agent(server, "go", config={"context_window": 1000})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("compaction failed, continuing without it", proc.stderr)
        self.assertIn("Finished anyway.", proc.stdout)
        self.assertEqual(len(server.requests), 4)

    def test_context_overflow_error_compacts_and_retries(self):
        server = FakeServer([
            tool_call("bash", command="echo step1"),
            {"status": 400, "error": "This model's maximum context length is 1000 tokens. You requested 1200."},
            "SUMMARY: step1 done.",
            "Finished.",
        ])
        proc = self.run_agent(server, "go")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(server.requests[2]["tool_choice"], "none")
        self.assertIn("SUMMARY: step1 done.", server.requests[3]["messages"][1]["content"])

    def test_unrelated_error_mentioning_context_is_not_overflow(self):
        server = FakeServer([{"status": 400, "error": "Invalid schema for function 'add': In context=(), "
                                                      "'required' is required to be supplied"}])
        proc = self.run_agent(server, "go")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Invalid schema", proc.stderr)
        self.assertEqual(len(server.requests), 1)

    def test_output_is_clipped(self):
        server = FakeServer([tool_call("bash", command="seq 1 20000"), "ok"])
        self.run_agent(server, "count", config={"output_limit": 1000})
        result = [m["content"] for m in self.session() if m["role"] == "tool"][0]
        self.assertIn("chars omitted; full output:", result)
        self.assertLess(len(result), 1300)
        self.assertTrue(result.rstrip().endswith("20000"))

    def test_timeout_kills_command(self):
        server = FakeServer([tool_call("bash", command="echo start; sleep 30", timeout=1), "ok"])
        self.run_agent(server, "wait")
        result = [m["content"] for m in self.session() if m["role"] == "tool"][0]
        self.assertIn("start", result)
        self.assertIn("killed after 1s timeout", result)

    def test_background_process_does_not_hang(self):
        server = FakeServer([tool_call("bash", command="sleep 30 & echo launched"), "ok"])
        proc = self.run_agent(server, "bg")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("launched", [m["content"] for m in self.session() if m["role"] == "tool"][0])


if __name__ == "__main__":
    unittest.main()
