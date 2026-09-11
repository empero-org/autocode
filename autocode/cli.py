"""`autocode`: put runner.py in the current directory (first run only) and run it.

The project's runner.py *is* the agent, and the agent may rewrite it over time,
so it is never overwritten unless you ask for that with --reset. When no model
is configured yet, an interactive setup writes ~/.config/autocode/config.json
first; run it again any time with --setup.
"""
import difflib
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .tui import prompt, style

PRISTINE = Path(__file__).with_name("runner.py")
MARKER = "autocode runner"
GLOBAL = Path.home() / ".config" / "autocode" / "config.json"

PROVIDERS = [  # name, base url, env var that usually holds the key
    ("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("Anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    ("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    ("DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    ("Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    ("Ollama", "http://localhost:11434/v1", None),
    ("LM Studio", "http://localhost:1234/v1", None),
    ("llama.cpp server", "http://localhost:8080/v1", None),
    ("vLLM", "http://localhost:8000/v1", None),
    ("Other OpenAI-compatible URL", None, None),
]
NOT_CHAT = re.compile(r"embed|tts|whisper|dall-e|moderation|transcribe|rerank|image|audio|realtime|search")


def request(url, key="", body=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=timeout) as response:
        return json.loads(response.read())


def failure(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
    return str(getattr(e, "reason", e))


def ask(label, default=""):
    hint = style(f" [{default}]", "dim") if default else ""
    return input(prompt(f"{style('?', 'bold', 'magenta')} {label}{hint}: ")).strip() or default


def running(base):
    try:
        request(base + "/models", timeout=0.5)
        return True
    except Exception:
        return False


def choose_backend():
    marks = []
    for _, base, env in PROVIDERS:
        if env and os.environ.get(env):
            marks.append(style(f"${env} is set", "green"))
        elif base and not env and running(base):
            marks.append(style("running", "green"))
        else:
            marks.append("")
    default = next((i for i, mark in enumerate(marks) if mark), 0) + 1
    for i, ((name, base, _), mark) in enumerate(zip(PROVIDERS, marks), 1):
        host = urlparse(base).netloc if base else ""
        print(f"  {style(str(i).rjust(2), 'cyan')}  {name.ljust(29)}{style(host.ljust(38), 'dim')}{mark}")
    print()
    while True:
        choice = ask("Backend", str(default))
        if choice.isdigit() and 1 <= int(choice) <= len(PROVIDERS):
            return PROVIDERS[int(choice) - 1]


def choose_key(base, env):
    """Returns (value to store in the config, the actual key)."""
    if env and os.environ.get(env):
        print(style(f"  using ${env} from your environment; only the reference is saved", "dim"))
        return "$" + env, os.environ[env]
    local = urlparse(base).hostname in ("localhost", "127.0.0.1", "::1")
    label = "API key " + style("(hidden; a $VAR name works too; Enter for none)" if local or not env
                               else f"(hidden; or export {env} and rerun)", "dim")
    key = getpass.getpass(prompt(f"{style('?', 'bold', 'magenta')} {label}: ")).strip()
    return (key, os.environ.get(key[1:], "")) if key.startswith("$") else (key, key)


def choose_model(base, key):
    """Returns (model id, context window or None)."""
    try:
        listed = request(base + "/models", key).get("data", [])
        models = [(m["id"].replace("models/", "", 1), m.get("context_length") or m.get("context_window")
                   or (m.get("top_provider") or {}).get("context_length")) for m in listed]
        models = [m for m in models if not NOT_CHAT.search(m[0])]
        print(style(f"  {len(models)} models available", "dim"))
    except Exception as e:
        models = []
        print(style(f"  couldn't list models ({failure(e)})", "yellow"))
    if not models:
        while True:
            name = ask("Model name")
            if name:
                return name, None
    shown = models
    while True:
        for i, (name, window) in enumerate(shown[:20], 1):
            print(f"  {style(str(i).rjust(2), 'cyan')}  {name}" + (style(f"  {window // 1000}k", "dim") if window else ""))
        if len(shown) > 20:
            print(style(f"  … {len(shown) - 20} more; type part of a name to filter", "dim"))
        answer = ask("Model (number, name or filter)", shown[0][0])
        if answer.isdigit() and 1 <= int(answer) <= min(20, len(shown)):
            return shown[int(answer) - 1]
        exact = [m for m in models if m[0] == answer]
        if exact:
            return exact[0]
        matches = [m for m in models if answer.lower() in m[0].lower()]
        if not matches:
            return answer, None  # not listed, but the server may still know it; the test call will tell
        shown = matches


def test_call(base, key, model):
    print(style(f"  testing {model} …", "dim"), end="", flush=True)
    try:
        data = request(base + "/chat/completions", key, {
            "model": model, "messages": [{"role": "user", "content": "Reply with just the word: ok"}]}, timeout=120)
        reply = (data["choices"][0]["message"].get("content") or "").strip().replace("\n", " ")
        print(style(" ✓ ", "green") + style(f"replied {reply[:60]!r}", "dim"))
        return True
    except Exception as e:
        print(style(f" ✗ {failure(e)}", "red"))
        return False


def setup():
    print(style("autocode setup", "bold", "magenta"))
    print(style(f"Pick a backend; anything OpenAI-compatible works. Saved to {GLOBAL}\n", "dim"))
    while True:
        name, base, env = choose_backend()
        if base is None:
            base = ask("Base URL (e.g. http://host:port/v1)")
        base = base.rstrip("/")
        stored_key, key = choose_key(base, env)
        model, window = choose_model(base, key)
        if name == "Ollama":
            print(style("  Ollama's default context is small; start it with OLLAMA_CONTEXT_LENGTH=32768 "
                        "(or more) and enter the same number below.", "yellow"))
        window = ask("Context window in tokens", str(window or (32768 if name == "Ollama" else 128000)))
        if test_call(base, key, model) or ask("Save anyway? (y/N)", "n").lower().startswith("y"):
            break
        print()
    try:
        config = json.loads(GLOBAL.read_text()) if GLOBAL.exists() else {}
    except ValueError:
        config = {}
    config.update(base_url=base, api_key=stored_key, model=model,
                  context_window=int(window) if str(window).isdigit() else 128000)
    GLOBAL.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL.write_text(json.dumps(config, indent=2) + "\n")
    GLOBAL.chmod(0o600)
    print(style("✓ saved ", "green") + style(f"{GLOBAL} · change it any time with `autocode --setup`\n", "dim"))


def configured(args):
    if os.environ.get("AUTOCODE_MODEL") or any(a in ("-m", "--model") or a.startswith("--model=") for a in args):
        return True
    for path in (GLOBAL, Path.cwd() / ".autocode" / "config.json"):
        try:
            if json.loads(path.read_text()).get("model"):
                return True
        except OSError:
            pass
        except ValueError:  # broken JSON: let the runner report it rather than overwrite it here
            return True
    return False


def main():
    args = sys.argv[1:]
    runner = Path.cwd() / "runner.py"
    backup = Path.cwd() / ".autocode" / "runner.prev.py"

    if args[:1] == ["--diff"]:
        if runner.exists():
            sys.stdout.writelines(difflib.unified_diff(
                PRISTINE.read_text().splitlines(True), runner.read_text().splitlines(True),
                "pristine/runner.py", "runner.py"))
        return 0
    if args[:1] == ["--reset"]:
        if runner.exists():
            backup.parent.mkdir(exist_ok=True)
            shutil.copy(runner, backup)
        shutil.copy(PRISTINE, runner)
        print(f"autocode: restored pristine {runner}" + (f" (old one saved to {backup})" if backup.exists() else ""),
              file=sys.stderr)
        return 0
    if runner.exists() and MARKER not in runner.read_text()[:500]:
        print(f"autocode: {runner} exists but is not an autocode runner; "
              "run `autocode --reset` to replace it.", file=sys.stderr)
        return 2
    if args[:1] == ["--setup"] or (not configured(args) and sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            setup()
        except (KeyboardInterrupt, EOFError):
            print("\nautocode: setup cancelled", file=sys.stderr)
            return 130
        if args[:1] == ["--setup"]:
            return 0

    if not runner.exists():
        shutil.copy(PRISTINE, runner)
        runner.chmod(0o755)
        print(f"autocode: created {runner}", file=sys.stderr)

    proc = subprocess.Popen([sys.executable, str(runner), *args])
    while True:
        try:
            code = proc.wait()
            break
        except KeyboardInterrupt:  # Ctrl-C belongs to the runner, which handles it itself
            pass
    if code == 1:  # an uncaught exception, i.e. the runner itself is probably broken
        print(f"\nautocode: runner.py crashed. If the agent broke it, `autocode --reset` "
              f"or restore {backup}.", file=sys.stderr)
    return code
