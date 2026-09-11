#!/usr/bin/env python3
"""autocode runner: a minimal coding agent in a single file.

`autocode` copies this file into your project and runs it. It is the whole
agent, and it is meant to be edited, by you or by the agent itself: before each
model call the runner checks whether this file changed and, if it still
compiles, re-execs itself and resumes the session where it left off.

Stdlib only. Works with any OpenAI-compatible /chat/completions endpoint.
"""
import argparse
import http.client
import importlib.util
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

SELF = Path(__file__).resolve()
ROOT = SELF.parent
HOME = ROOT / ".autocode"  # config.json, sessions/, tools/, out/
START_SRC = SELF.read_text()

SYSTEM = """\
You are autocode, a coding agent working in {root} ({os}, {date}). Work autonomously until the task is done, verifying as you go, then reply briefly.
Your implementation is {self}, a single Python file you may improve; edits take effect after each step. Your state (config, sessions, tools) lives in {home}.
To add a tool, write {home}/tools/<name>.py defining SCHEMA (an OpenAI function schema) and run(**args) -> str; it is available from the next step."""

COMPACT = """\
Your context is full, so this conversation is about to be replaced by your summary of it. Write the summary you need to continue seamlessly: the user's requests and constraints (quote the precise ones), what has been done and learned, files changed, the current state, and the exact next steps. Be dense and complete."""

DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",             # "$VAR" references are expanded from the environment
    "model": "",
    "context_window": 128000,  # tokens
    "compact_at": 0.8,         # compact when this fraction of the window is used
    "output_limit": 30000,     # max chars of one tool result kept in context
    "timeout": 600,            # HTTP timeout, seconds
    "temperature": None,
    "max_tokens": None,
    "keep_reasoning": False,   # send reasoning text back (reasoning_details always goes back)
    "headers": {},             # extra HTTP headers
    "extra_body": {},          # merged into every request; a null value removes a key
}


def load_config(model=None):
    cfg = dict(DEFAULTS, base_url=os.environ.get("OPENAI_BASE_URL", DEFAULTS["base_url"]),
               api_key=os.environ.get("OPENAI_API_KEY", ""))
    for path in (Path.home() / ".config" / "autocode" / "config.json", HOME / "config.json"):
        if path.exists():
            cfg.update(json.loads(path.read_text()))
    for key, default in DEFAULTS.items():  # every option can be set as AUTOCODE_<KEY>
        env = os.environ.get("AUTOCODE_" + key.upper())
        if env is not None:
            cfg[key] = env if isinstance(default, str) else json.loads(env)
    if model:
        cfg["model"] = model
    cfg["api_key"] = os.path.expandvars(cfg["api_key"])
    cfg["headers"] = {k: os.path.expandvars(v) for k, v in cfg["headers"].items()}
    return cfg


# ---------------------------------------------------------------- terminal

try:  # the printing kernel (layout, markdown, code highlighting, LaTeX) ships with the autocode package
    from autocode.tui import View
except ImportError:
    class View:
        """Plain-text fallback with the same interface as autocode.tui.View."""

        def __init__(self):
            self.kind = None

        def banner(self, model, session, cwd):
            print(f"autocode · {model} · session {session} · /compact /new /exit", file=sys.stderr)

        def ask(self, meter):
            line = input(f"\n{meter} > ")
            while line.endswith("\\"):
                line = line[:-1] + "\n" + input("… ")
            return line

        def stream(self, kind, text):
            if kind != self.kind:
                self.end()
                self.kind = kind
            print(text, end="", flush=True, file=sys.stdout if kind == "text" else sys.stderr)

        def end(self):
            if self.kind:
                print(flush=True, file=sys.stdout if self.kind == "text" else sys.stderr)
            self.kind = None

        def tool(self, name, args):
            print(f"\n> {name} {json.dumps(args)[:300]}", file=sys.stderr, flush=True)

        def result(self, output):
            print("\n".join(output.splitlines()[:6]), file=sys.stderr, flush=True)

        def note(self, text, color=None):
            self.end()
            print(text, file=sys.stderr, flush=True)

view = View()


# ---------------------------------------------------------------- tools

BASH = {"type": "function", "function": {
    "name": "bash",
    "description": "Run a bash command; returns combined stdout and stderr. "
                   "The working directory persists between calls. stdin is closed.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120)."},
    }, "required": ["command"]},
}}

# Nothing should wait for a human: no pagers, editors or credential prompts.
ENV = dict(os.environ, PAGER="cat", GIT_PAGER="cat", GIT_EDITOR="true", GIT_TERMINAL_PROMPT="0",
           DEBIAN_FRONTEND="noninteractive", PYTHONUNBUFFERED="1")


def bash(command, timeout=None):
    timeout = int(timeout or 120)
    with tempfile.TemporaryDirectory() as tmp:
        cwd_file, out_file = os.path.join(tmp, "cwd"), os.path.join(tmp, "out")
        script = f"trap 'pwd > {shlex.quote(cwd_file)}' EXIT\n{command}"
        # Output goes to a file rather than a pipe, so a backgrounded process can't hang us.
        with open(out_file, "wb") as out:
            proc = subprocess.Popen(["bash", "-c", script], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=out, env=ENV, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except BaseException as e:  # timeout or Ctrl-C: kill the command's whole process group
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                if not isinstance(e, subprocess.TimeoutExpired):
                    raise
                code = f"killed after {timeout}s timeout"
        with open(out_file, "rb") as f:
            text = f.read().decode(errors="replace")
        try:
            with open(cwd_file) as f:
                os.chdir(f.read().strip())
        except OSError:
            pass
    if code:
        text = (text if not text or text.endswith("\n") else text + "\n") + f"[exit {code}]"
    return text or "(no output)"


_loaded = {}  # (path, mtime) -> (schema, fn)


def load_tools():
    """bash plus every .autocode/tools/*.py, reloaded whenever a file changes."""
    tools = {"bash": (BASH, bash)}
    for path in sorted((HOME / "tools").glob("*.py")):
        key = (path, path.stat().st_mtime_ns)
        if key not in _loaded:
            try:
                spec = importlib.util.spec_from_file_location(f"autocode_tool_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                fn = dict(module.SCHEMA.get("function", module.SCHEMA))
                fn.setdefault("name", path.stem)
                fn.setdefault("parameters", {"type": "object", "properties": {}})
                _loaded[key] = ({"type": "function", "function": fn}, module.run)
            except Exception as e:  # a broken tool shows up as such, so the agent can fix it
                error = f"{path} failed to load: {type(e).__name__}: {e}"
                _loaded[key] = ({"type": "function", "function": {
                    "name": path.stem, "description": "BROKEN. " + error,
                    "parameters": {"type": "object", "properties": {}}}}, lambda **_: error)
        schema, fn = _loaded[key]
        tools[schema["function"]["name"]] = (schema, fn)
    return tools


def clip(text, limit):
    if len(text) <= limit:
        return text
    (HOME / "out").mkdir(parents=True, exist_ok=True)
    path = HOME / "out" / f"{time.time_ns()}.txt"
    path.write_text(text)
    half = limit // 2
    return f"{text[:half]}\n\n[... {len(text) - limit} chars omitted; full output: {path} ...]\n\n{text[-half:]}"


# ---------------------------------------------------------------- model API

class APIError(Exception):
    pass


class ContextFull(APIError):
    pass


OVERFLOW = re.compile(r"context.{0,20}(length|window|size|limit)|maximum context|context_length_exceeded|too long|"
                      r"too many tokens|reduce the length|exceeds? the max|token limit", re.I)


def chat(cfg, messages, tools, emit=lambda kind, text: None, **extra):
    """One streaming chat completion. Returns (assistant message, usage or None)."""
    drop = set() if cfg["keep_reasoning"] else {"reasoning", "reasoning_content"}
    body = {"model": cfg["model"], "messages": [{k: v for k, v in m.items() if k not in drop} for m in messages],
            "tools": tools, "stream": True, "stream_options": {"include_usage": True},
            "temperature": cfg["temperature"], "max_tokens": cfg["max_tokens"], **extra, **cfg["extra_body"]}
    body = {k: v for k, v in body.items() if v is not None}
    headers = {"Content-Type": "application/json", **cfg["headers"]}
    if cfg["api_key"]:
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    request = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions",
                                     json.dumps(body).encode(), headers)
    for attempt in range(6):
        delay = min(2 ** attempt, 30)
        try:
            with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
                if "event-stream" in response.headers.get("Content-Type", ""):
                    return collect(sse(response), emit)
                return collect([json.loads(response.read())], emit)  # server ignored "stream"
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:2000]
            if e.code == 413 or (e.code in (400, 422) and OVERFLOW.search(detail)):
                raise ContextFull(detail)
            if e.code not in (408, 409, 429) and e.code < 500:
                raise APIError(f"HTTP {e.code}: {detail}")
            retry_after = e.headers.get("Retry-After") or ""
            delay = int(retry_after) if retry_after.isdigit() else delay
            problem = f"HTTP {e.code}"
        except (OSError, http.client.HTTPException) as e:
            problem = f"{type(e).__name__}: {e}"
        view.note(f"[{problem}; retrying in {delay}s]", "yellow")
        time.sleep(delay)
    raise APIError(f"giving up after repeated failures ({problem})")


def sse(response):
    for raw in response:
        line = raw.strip()
        if line.startswith(b"data:"):
            data = line[5:].strip()
            if data == b"[DONE]":
                return
            yield json.loads(data)


def collect(chunks, emit):
    """Assemble streamed chunks (or one non-streamed response) into an assistant message."""
    content, reasoning, reasoning_key, details, calls, usage = "", "", None, [], [], None
    for chunk in chunks:
        if chunk.get("error"):
            raise APIError(json.dumps(chunk["error"]))
        usage = chunk.get("usage") or usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            thought = None
            for key in ("reasoning_content", "reasoning"):  # servers differ; some send both
                if isinstance(delta.get(key), str) and delta[key]:
                    thought, reasoning_key = delta[key], key
                    break
            parts = [p for p in delta.get("reasoning_details") or [] if isinstance(p, dict)]
            for part in parts:  # OpenRouter: text, summaries, signatures and encrypted blocks, merged by index
                i = part.get("index", len(details))
                while len(details) <= i:
                    details.append({})
                for k, v in part.items():
                    if k in ("text", "summary") and isinstance(v, str):
                        details[i][k] = details[i].get(k, "") + v
                    elif v is not None:
                        details[i][k] = v
            if thought is None:  # only reasoning_details carried the thinking this time
                thought = "".join(v for p in parts for v in (p.get("text"), p.get("summary")) if isinstance(v, str))
                reasoning_key = reasoning_key or "reasoning"
            if thought:
                reasoning += thought
                emit("reasoning", thought)
            if delta.get("content"):
                content += delta["content"]
                emit("text", delta["content"])
            for tc in delta.get("tool_calls") or []:
                i = tc.get("index")
                if i is None:  # some servers omit the index; a new id means a new call
                    i = len(calls) if not calls or (tc.get("id") and tc["id"] != calls[-1]["id"]) else len(calls) - 1
                while len(calls) <= i:
                    calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                call, fn = calls[i], tc.get("function") or {}
                call["id"] = tc.get("id") or call["id"]
                call["function"]["name"] = fn.get("name") or call["function"]["name"]
                args = fn.get("arguments")
                if args:
                    call["function"]["arguments"] += args if isinstance(args, str) else json.dumps(args)
    message = {"role": "assistant", "content": content or (None if calls else "")}
    if calls:
        for n, call in enumerate(calls):
            call["id"] = call["id"] or f"call_{time.time_ns()}_{n}"
        message["tool_calls"] = calls
    if reasoning:
        message[reasoning_key] = reasoning
    if details:  # always sent back: providers need the signatures to continue a tool loop
        message["reasoning_details"] = details
    return message, usage


def transcript(messages, limit):
    """A conversation as plain text, with tool calls and results cut to `limit` characters."""
    def cut(text):
        return text if len(text) <= limit else text[:limit] + " […]"

    parts = []
    for m in messages:
        if m["role"] == "tool":
            parts.append("[tool result]\n" + cut(m["content"] or ""))
            continue
        if m.get("content"):
            parts.append(f"[{m['role']}]\n{m['content']}")
        for call in m.get("tool_calls") or []:
            parts.append(f"[tool call] {call['function']['name']} {cut(call['function']['arguments'] or '')}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- agent

class Agent:
    def __init__(self, cfg, sid=None, flags=()):
        self.cfg, self.flags = cfg, list(flags)
        self.sid = sid or time.strftime("%Y%m%d-%H%M%S")
        self.file = HOME / "sessions" / f"{self.sid}.jsonl"
        self.messages = [json.loads(line) for line in self.file.read_text().splitlines() if line.strip()] \
            if self.file.exists() else []
        self.tokens, self.counted = 0, 0  # last API token count, and how many messages it covers
        self.floor = 0  # after a compaction that failed or didn't shrink enough, don't retry below this size
        self.seen = START_SRC  # last version of this file already reported as broken

    # -- persistence

    def add(self, message):
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == message["role"] == "user":  # keep roles alternating
            last["content"] += "\n\n" + message["content"]
            return self.save()
        self.messages.append(message)
        with self.file.open("a") as f:
            f.write(json.dumps(message) + "\n")

    def save(self):
        self.file.write_text("".join(json.dumps(m) + "\n" for m in self.messages))

    def context(self):
        """Estimated tokens in context: the last API count plus ~4 chars/token for anything newer."""
        return self.tokens + sum(len(json.dumps(m)) for m in self.messages[self.counted:]) // 4

    def system(self):
        text = SYSTEM.format(root=ROOT, os=platform.system(), date=time.strftime("%Y-%m-%d"), self=SELF, home=HOME)
        for name in ("AGENTS.md", "CLAUDE.md"):
            if (ROOT / name).exists():
                text += f"\n\n# {name}\n\n{(ROOT / name).read_text()}"
                break
        return {"role": "system", "content": text}

    # -- the loop

    def turn(self, prompt=None):
        """Run until the model stops calling tools. Returns False on interrupt or error."""
        try:
            if prompt is not None:
                self.add({"role": "user", "content": prompt})
            while self.step():
                pass
            return True
        except KeyboardInterrupt:
            view.note("[interrupted]", "yellow")
        except APIError as e:
            view.note(f"[error] {e}", "red")
        self.settle()
        return False

    def step(self):
        """One model call plus the tools it asks for. Returns True while the turn goes on."""
        self.reload_if_changed()
        if self.context() > max(self.limit(), self.floor):
            try:
                self.compact()
            except ContextFull:
                raise
            except APIError as e:  # the estimate may be pessimistic, so try the real request anyway
                view.note(f"[compaction failed, continuing without it: {e}]", "yellow")
            self.floor = self.context() + self.cfg["context_window"] // 10
        tools = load_tools()
        try:
            message, usage = self.call(tools)
        except ContextFull:
            view.end()
            self.compact()
            message, usage = self.call(tools)
        finally:
            view.end()
        self.add(message)
        if usage:
            self.tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0) or usage.get("total_tokens", 0)
            if not (self.cfg["keep_reasoning"] or message.get("reasoning_details")):  # reasoning isn't sent back
                self.tokens -= (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        else:
            self.tokens = self.context()
        self.counted = len(self.messages)
        for call in message.get("tool_calls") or []:
            self.add({"role": "tool", "tool_call_id": call["id"], "content": self.run_tool(tools, call)})
        return bool(message.get("tool_calls"))

    def call(self, tools):
        return chat(self.cfg, [self.system(), *self.messages], [schema for schema, _ in tools.values()], view.stream)

    def run_tool(self, tools, call):
        name, raw = call["function"]["name"], call["function"]["arguments"] or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:
            view.note(f"● {name}: invalid arguments", "red")
            return f"Invalid JSON arguments ({e}): {raw[:500]}"
        view.tool(name, args)
        if name not in tools:
            return f"Unknown tool {name!r}. Available: {', '.join(tools)}"
        try:
            output = str(tools[name][1](**args))
        except Exception:
            output = traceback.format_exc()
        output = clip(output, self.cfg["output_limit"])
        view.result(output)
        return output

    def settle(self):
        """After an interrupt, answer tool calls left without a result so the history stays valid."""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "assistant":
                break
        else:
            return
        answered = {m.get("tool_call_id") for m in self.messages[i + 1:]}
        for call in self.messages[i].get("tool_calls") or []:
            if call["id"] not in answered:
                self.add({"role": "tool", "tool_call_id": call["id"], "content": "[interrupted by user]"})

    def limit(self):
        """Compact above this many tokens: compact_at of the window, always leaving room for a max_tokens reply."""
        window = self.cfg["context_window"]
        return max(window // 2, min(self.cfg["compact_at"] * window, window - (self.cfg["max_tokens"] or 0)))

    def compact(self):
        """Replace the conversation with the model's summary of it, keeping the latest step and request verbatim."""
        history, pending = self.messages, []
        if history and history[-1]["role"] == "user":  # a fresh request is kept verbatim, not summarized
            history, pending = history[:-1], history[-1:]
        if not history:
            return
        cut = max((i for i, m in enumerate(history) if m["role"] == "assistant"), default=len(history))
        recent = self.fit(history[cut:])  # the latest step: the last assistant message and its tool results
        view.note("[compacting context…]")
        note = "\n\nYour latest step (your last message and its tool results) stays verbatim after the summary." \
            if recent else ""
        summary = self.summarize(history, COMPACT + note)
        (HOME / "sessions" / "old").mkdir(exist_ok=True)
        if self.file.exists():  # keep the full transcript around
            (HOME / "sessions" / "old" / f"{self.sid}-{int(time.time())}.jsonl").write_text(self.file.read_text())
        resume = "\n\nContinue from where you left off." if history[-1]["role"] == "tool" and not recent else ""
        self.messages = [{"role": "user", "content": "[Context compacted. Your summary of the conversation so far:]"
                                                     f"\n\n{summary}{resume}"}, *recent]
        self.save()
        for message in pending:
            self.add(message)
        self.tokens, self.counted = 0, 0
        view.note(f"[compacted to ~{self.context()} tokens]")

    def fit(self, tail):
        """The tail to keep verbatim within a quarter of the window, clipping long tool output; [] if it can't fit."""
        budget = self.cfg["context_window"]  # a quarter of the window, in characters at ~4 per token

        def size(messages):
            return sum(len(json.dumps(m)) for m in messages)

        results = [m for m in tail if m["role"] == "tool"]
        rest = size(tail) - size(results)
        if size(tail) > budget and results and rest < budget // 2:
            share = (budget - rest) * 3 // 4 // len(results)
            tail = [dict(m, content=clip(m["content"], share)) if m["role"] == "tool" else m for m in tail]
        return tail if size(tail) <= budget else []

    def summarize(self, history, instructions):
        tools = [schema for schema, _ in load_tools().values()]
        try:  # the same prefix as a normal request, so the provider's prompt cache is reused
            message, _ = chat(self.cfg, [self.system(), *history, {"role": "user", "content": instructions}], tools,
                              tool_choice="none")
            if not message.get("tool_calls") and (message.get("content") or "").strip():
                return message["content"].strip()
        except APIError:
            pass
        # Works on any server, even one that ignores tool_choice: a plain transcript and nothing to call.
        for limit in (2000, 200):
            request = f"<transcript>\n{transcript(history, limit)}\n</transcript>\n\n{instructions}"
            try:
                message, _ = chat(self.cfg, [self.system(), {"role": "user", "content": request}], None)
            except ContextFull:
                continue
            summary = (message.get("content") or "").strip()
            if not summary:
                raise APIError("compaction returned an empty summary")
            return summary
        raise ContextFull("too large to summarize, even with tool output cut short; start a /new session")

    def reload_if_changed(self):
        """Self-modification: if this file was edited and still compiles, re-exec it and resume."""
        source = SELF.read_text()
        if source in (START_SRC, self.seen):
            return
        try:
            compile(source, str(SELF), "exec")
        except SyntaxError as e:
            self.seen = source
            if self.messages:
                self.messages[-1]["content"] = (self.messages[-1]["content"] or "") + \
                    f"\n[{SELF.name} was edited but not reloaded: SyntaxError: {e}]"
                self.save()
            return
        (HOME / "runner.prev.py").write_text(START_SRC)
        view.note(f"[{SELF.name} changed; reloading]", "magenta")
        save_history()
        os.execv(sys.executable, [sys.executable, str(SELF), "--resume", self.sid, *self.flags])


# ---------------------------------------------------------------- CLI

def save_history():
    try:
        import readline
        readline.write_history_file(HOME / "history")
    except (ImportError, OSError):
        pass


def repl(agent):
    try:
        import readline
        readline.set_history_length(1000)
        if (HOME / "history").exists():
            readline.read_history_file(HOME / "history")
    except (ImportError, OSError):
        pass
    view.banner(agent.cfg["model"], agent.sid, os.getcwd())
    while True:
        try:
            line = view.ask(f"{100 * agent.context() // agent.cfg['context_window']}%")
        except (EOFError, KeyboardInterrupt):
            break
        command = line.strip()
        if not command:
            continue
        if command in ("/exit", "/quit"):
            break
        if command == "/new":
            agent = Agent(agent.cfg, flags=agent.flags)
            view.note(f"new session {agent.sid}")
        elif command == "/compact":
            try:
                agent.compact()
            except (APIError, KeyboardInterrupt) as e:
                view.note(f"[compaction failed] {e}", "red")
        else:
            agent.turn(line)
    save_history()
    if agent.messages:
        view.note(f"session saved · resume with: autocode -r {agent.sid}")


def main():
    parser = argparse.ArgumentParser(prog="autocode", description="A minimal self-modifying coding agent.",
                                     epilog="`autocode --setup` picks the backend and model; "
                                            "`autocode --reset` restores the pristine runner.py; "
                                            "`autocode --diff` shows how it has evolved.")
    parser.add_argument("prompt", nargs="*", help="first message (piped stdin is appended)")
    parser.add_argument("-p", "--print", action="store_true", help="run the prompt, then exit")
    parser.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the latest session")
    parser.add_argument("-r", "--resume", metavar="ID", help="resume a session by id")
    parser.add_argument("-m", "--model", help="override the configured model")
    args = parser.parse_args()

    cfg = load_config(args.model)
    if not cfg["model"]:
        print('No model configured. Run `autocode --setup`, or set AUTOCODE_MODEL, or "model" in '
              ".autocode/config.json or ~/.config/autocode/config.json.", file=sys.stderr)
        sys.exit(2)
    (HOME / "sessions").mkdir(parents=True, exist_ok=True)
    (HOME / "tools").mkdir(exist_ok=True)
    if not (HOME / ".gitignore").exists():  # tools are worth committing; transcripts and keys are not
        (HOME / ".gitignore").write_text("config.json\nsessions/\nout/\nhistory\nrunner.prev.py\n")

    sid = args.resume
    if args.cont:
        latest = max((HOME / "sessions").glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)
        sid = latest.stem if latest else None
    if sid and not (HOME / "sessions" / f"{sid}.jsonl").exists():
        print(f"No session {sid!r} in {HOME / 'sessions'}", file=sys.stderr)
        sys.exit(2)
    prompt = " ".join(args.prompt)
    if not sys.stdin.isatty():
        prompt = f"{prompt}\n\n{sys.stdin.read()}".strip()

    flags = (["--print"] if args.print else []) + (["--model", args.model] if args.model else [])
    agent = Agent(cfg, sid, flags)
    ok = True
    if agent.messages and agent.messages[-1]["role"] in ("user", "tool"):  # resumed mid-turn, e.g. after a reload
        ok = agent.turn()
    if prompt:
        ok = agent.turn(prompt)
    if args.print:
        sys.exit(0 if ok else 2)
    repl(agent)


if __name__ == "__main__":
    main()
