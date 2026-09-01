# local-llm-agent-lite

A lightweight, private Windows coding agent optimized for Qwen Coder GGUF models
running locally through `llama-cpp-python`, with a ChatGPT-style browser interface.

## Phase 1 setup

Use 64-bit Python 3.12. If the existing `.venv` still references a missing Python
installation, rename it before creating a replacement:

```powershell
Rename-Item .venv .venv-broken
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The CPU wheel index required by `llama-cpp-python` is already declared in
`requirements.txt`.

## Fast CLI quickstart (`myllm.py`)

`myllm.py` is the lightweight terminal version of the coding agent. It uses a
small, constrained JSON action format and focused file tools, which can make tool
calling more reliable and responsive with small Qwen Coder models. Use the web
application when you need multiple chats, background generation, cancellation,
persistent chat history, or approval dialogs.

For the quickest first run:

1. Complete the **Phase 1 setup** above and activate `.venv`.
2. Put a Qwen Coder `.gguf` model in the project-local `models` folder. A
   `Q4_K_M` model in the 3B–4B range is a practical CPU starting point.
3. Start the terminal agent:

   ```powershell
   python myllm.py
   ```

4. Select **3. Model Selection** and choose the detected GGUF model.
5. Select **4. Project Selection** and enter the directory the agent may access.
6. Select **1. Chat / Coding Agent**, wait for the model to load, and enter a task.

For example:

```text
Inspect this project and explain its entry point. Do not change any files.
```

Or, to exercise its coding tools:

```text
Create hello.py that prints "Hello from my local LLM", verify the file, and validate the Python syntax.
```

Use `/back` to return to the main menu. The selected model, project and tuning
values are saved in `.myllm/config.json`; verified project-memory records are
kept below `.myllm/memory`.

### Faster settings for a low-end CPU

Open **2. Settings** before starting the chat. Begin with these values:

- **Context size:** `4096` for faster, lower-memory work; use `8192` when the
  task genuinely needs more history.
- **GPU layers:** `0` for the standard CPU build. Intel Iris Xe acceleration
  requires a separately installed compatible `llama-cpp-python` backend.
- **Maximum steps:** `12` for everyday tasks. Raise it only for a task that
  legitimately needs a longer tool sequence.
- **Temperature:** `0.1` for predictable tool calls.
- **Debug level:** `0` for a quieter terminal or `1` to see the raw model stream.

The script automatically uses memory mapping and chooses CPU threads based on the
machine. Model size and context length have a larger effect on responsiveness than
debug output. The model is loaded once when entering the chat screen, so keep that
screen open for follow-up tasks to avoid another load.

> **Safety:** `myllm.py` restricts paths to the selected project, but its current
> CLI tools do not show the web application's approval dialog before file deletion.
> Select the project directory carefully, use version control, and phrase read-only
> requests explicitly when no changes are wanted. Press `Ctrl+C` to interrupt the
> process; unlike the web application, the CLI does not yet provide a per-run Stop
> button.

## Start the application

Activate the environment, then start both the backend and frontend:

```powershell
python main.py
```

Open the local URL printed at startup if the browser is not already on that page.
The default is [http://127.0.0.1:8000](http://127.0.0.1:8000); if that port is
occupied, the application selects the next available port. The web interface provides
chat, model load/unload controls, status, and a local performance benchmark.

The interface includes streamed chat, recent sessions, the selected workspace tree,
model and thinking-mode selectors, model metadata, and always-visible context/RAM
status. New `.gguf` files placed in `models` are discovered automatically when the
model selector is opened or a new chat starts. The selected model loads immediately
in the background after the web app becomes available. A yellow model dot means
unloaded/loading and a green dot means loaded. Turn off **Load model immediately** in Settings, or set
`LOAD_MODEL_ON_STARTUP = False` in `config.py`, to use lazy first-chat loading.

Multiple chats are stored independently and can be reopened from the sidebar. Because
the local runtime processes one generation at a time, choosing **New chat** during an
active response asks for confirmation before stopping it; cancelling the prompt leaves
the current generation running.

The agent can list and read files, search text, edit files atomically, and execute
PowerShell commands. Reads inside the selected workspace are allowed automatically.
File deletion, mutating shell commands, destructive Git commands, package/network
operations, and access outside the workspace require an exact one-time approval in
the UI. Use **Stop** or `Ctrl+C` to interrupt an active run. If a direct action request
produces only explanatory prose or a tool-shaped code block, the agent normalizes
Qwen's native, tagged, bare-JSON, or fenced-JSON format and retries invalid protocol
responses up to `MAX_TOOL_PROTOCOL_RETRIES`. This safeguard can be changed with
`RETRY_ACTION_WITHOUT_TOOL` in `config.py`.

Recent chats are stored locally in `data/chat_sessions.jsonl`, with exactly one JSON
object—and therefore one line—per chat. Each object contains the chat ID, title,
metadata, and complete message list. Chats can be renamed, pinned, unpinned, and
deleted from the sidebar. Changes are saved with an atomic file replacement so the
line count continues to match the number of stored chats.

On the first launch after upgrading from the earlier event-based format, the app
automatically consolidates the existing records. It keeps the original file at
`data/chat_sessions.legacy-backup.jsonl` before writing the simpler format.
Semantic memory is optional: place the configured Nomic embedding GGUF in `models` to
enable LanceDB retrieval; ordinary chat continues to work when it is absent.

## Configuration

User-overridable defaults and their inline hints live in `config.py`. The first-run
workspace is the project-local `workspace` folder. Each selected workspace keeps its
own UI/runtime profile in `.llmAgentLite/settings.json`, so model, context, generation,
and agent tuning follow that workspace. `data/user_settings.json` only remembers the
last selected workspace. The default context is 8,192 tokens, history uses an automatic
context-aware budget, and output is unlimited by default (`MAX_NEW_TOKENS = -1`). The
agent instructions live separately in `system_prompt.py`.

Operational events are written with Python's standard `logging` module to the rotating
`logs/app.log` file. Logs record lifecycle, model, tool, workspace, and chat-management
events without recording full prompt or response text.

Thinking choices appear only for models whose metadata/family supports them. Qwen3
currently exposes **None** and **Thinking**; unsupported levels are not shown.
The bundled default is `qwen2.5-coder-3b-instruct-q4_k_m.gguf`; its GGUF chat template
is detected automatically and it runs in non-thinking mode.

## API

Interactive API documentation is available while the application runs:

[http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)

The benchmark reports process memory, inference latency, token counts, and generation
speed. The implemented scope is described in `plans/plan.md`; deferred robustness and richer
thinking controls remain in `plans/phase2.md`.
