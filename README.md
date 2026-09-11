<p align="center">
  <img src="https://raw.githubusercontent.com/empero-org/autocode/main/assets/tui.png" alt="autocode starting up in a terminal">
</p>

# autocode

A minimal coding agent that lives in your project as one Python file and is
allowed to rewrite itself.

```bash
pip install empero-autocode
cd my-project
autocode                 # the first time, a short setup picks the backend and model
```

Setup lists common providers and marks the ones it can already use (an API
key in your environment, or a local server that's running). It then lists the
server's models, runs a one-line test call, and saves the result to
`~/.config/autocode/config.json`. Keys found in the environment are saved as a
`$VAR` reference, never the key itself.

The first run copies `runner.py` into the current directory and runs it. Every
later run uses that local copy, which is never overwritten. The agent can edit
its own copy, so each project's agent can evolve on its own.

## The idea

- **One file, stdlib only.** `runner.py` is the whole agent: an agent loop,
  streaming client, sessions, compaction and REPL, in about 640 lines. You can
  also run it directly with `python runner.py`.
- **One built-in tool: `bash`.** Commands run as written. The working directory
  persists between calls, stdin is closed, and there's a timeout. Long output
  is clipped, with the full text saved to a file the agent can page through.
- **The agent writes its other tools.** Any `.autocode/tools/<name>.py` that
  defines `SCHEMA` and `run(**args) -> str` is picked up on the next step:

  ```python
  SCHEMA = {"name": "add", "description": "Add two numbers.",
            "parameters": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}

  def run(a, b):
      return str(a + b)
  ```

  A tool that fails to import still shows up, marked `BROKEN` with its error,
  so the agent can see what went wrong and fix it.
- **Self-modification.** Before each model call the runner checks whether
  `runner.py` changed. If it still compiles, the runner saves the session,
  backs up the running version to `.autocode/runner.prev.py`, re-execs itself
  and continues the same turn. If it doesn't compile, the agent is told about
  the syntax error and the current version keeps running.
- **Compaction.** It runs when the context passes `compact_at` ×
  `context_window` (always leaving room for a `max_tokens` reply), or when
  the server rejects a request for being too long.
  - The model summarizes the conversation, and the summary replaces
    everything except your newest message and the latest step (the model's
    last message and its tool results), which are kept word for word.
  - The summary request starts with the same messages as a normal request,
    so the provider's prompt cache still applies.
  - If the server answers with a tool call instead of a summary, which
    happens on servers that ignore `tool_choice`, compaction retries with
    the conversation as a plain transcript and no tools.
  - If compaction fails, the agent says so and keeps working.
  - Full transcripts are kept in `.autocode/sessions/old/`.
- **A minimal system prompt.** It is three sentences, plus `AGENTS.md` (or
  `CLAUDE.md`) if the project has one.
- **Readable output.** Replies render as they stream: markdown headings,
  lists, quotes and tables, syntax-highlighted code blocks, and LaTeX math as
  Unicode (`\frac{1}{2}\alpha^2` becomes `½α²`). Your messages appear as a
  gray bar and thinking is gray italic (including `<think>` tags some servers
  leave in the answer). Tool calls show as `●` with an output preview, and a
  blank line separates each block. The renderer is the
  package's printing module, [autocode/tui.py](autocode/tui.py) (stdlib
  only). `runner.py` imports it when it's available and falls back to plain
  text, and output that isn't going to a terminal is left untouched. Try it
  on any file with `python -m autocode.tui README.md`.

## Usage

```bash
autocode                         # interactive
autocode "fix the failing test"  # start with a prompt
autocode -p "summarize this repo" > summary.md   # one-shot: answer on stdout, trace on stderr
git diff | autocode -p "review this"             # piped stdin is appended to the prompt
autocode -c                      # continue the latest session
autocode -r 20260911-014900      # resume a specific session
autocode -m some-other-model     # override the model
autocode --setup                 # pick the backend and model again
autocode --diff                  # how has this project's runner drifted from the original?
autocode --reset                 # restore the original runner.py (old one → .autocode/runner.prev.py)
```

In the REPL: `/compact`, `/new`, `/exit`. End a line with `\` to continue it
on the next line. Ctrl-C interrupts the current turn, and Ctrl-D exits.

## Configuration

Works with any OpenAI-compatible `/chat/completions` endpoint, such as OpenAI,
OpenRouter, vLLM, llama.cpp, Ollama, LM Studio, Groq or DeepSeek.

Settings are applied in this order, later ones winning: built-in defaults,
then `~/.config/autocode/config.json`, then `.autocode/config.json`, then
`AUTOCODE_<KEY>` environment variables, then `-m`. `OPENAI_BASE_URL` and
`OPENAI_API_KEY` are used as fallbacks.

```json
{
  "base_url": "https://openrouter.ai/api/v1",
  "api_key": "$OPENROUTER_API_KEY",
  "model": "qwen/qwen3-coder",
  "context_window": 262144,
  "compact_at": 0.8,
  "output_limit": 30000,
  "timeout": 600,
  "temperature": null,
  "max_tokens": null,
  "keep_reasoning": false,
  "headers": {},
  "extra_body": {"reasoning_effort": "high"}
}
```

- `api_key` and `headers` values expand `$VARS`, so keys don't have to be stored in files.
- `extra_body` is merged into every request. Set a key to `null` to remove it,
  for example `"stream_options": null` for servers that reject it.
- Thinking is read from `reasoning_content`, `reasoning`, OpenRouter's
  `reasoning_details` (text and summaries), or `<think>` tags in the answer.
  `reasoning_details`, which includes signatures and encrypted blocks, is
  always sent back, because providers need it to continue a tool loop.
  `keep_reasoning` also sends the plain reasoning text back, which some
  thinking models require.
- For a local Ollama server:
  `AUTOCODE_BASE_URL=http://localhost:11434/v1 AUTOCODE_MODEL=qwen3-coder autocode`.

## Files

```
runner.py                 the agent (yours to change)
.autocode/config.json     project config (gitignored)
.autocode/tools/          tools the agent wrote (worth committing)
.autocode/sessions/       transcripts, one JSON message per line (gitignored)
.autocode/out/            full text of clipped tool output (gitignored)
.autocode/runner.prev.py  the runner version before the last self-edit
```

## Development

```bash
python -m unittest discover tests   # end-to-end against a scripted fake server
```

## License

Apache-2.0: see [LICENSE](https://github.com/empero-org/autocode/blob/main/LICENSE). If you
redistribute autocode or build on it, keep the attribution in
[NOTICE](https://github.com/empero-org/autocode/blob/main/NOTICE), as Section 4(d) of the license
requires.
