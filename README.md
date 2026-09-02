# 🤖 MyLLM

**Version 0.1.7**

A fast, private coding agent optimized for running Qwen Coder GGUF models locally
on Windows.

- 🔒 Local and private
- ⚡ Optimized for small Qwen Coder models
- 🧰 Built-in file, search, edit, and verification tools
- 🧠 Optional verified project memory
- 📁 Restricted to the selected workspace
- ↩️ Reversible file operations during the current run

## 🚀 Install

Use 64-bit Python 3.12:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## ⚡ Quickstart

1. 📦 Place a Qwen Coder `.gguf` model in the `models` folder.
2. ▶️ Start MyLLM:

   ```powershell
   python myllm.py
   ```

3. 🤖 Choose **Model Selection** and select the detected model.
4. 📁 Choose **Project Selection** and select your workspace.
5. 💬 Choose **Chat / Coding Agent** and enter a task.

The model remains loaded while the chat screen is open, making follow-up prompts
faster. Prompt caching can make repeated prefixes and follow-up turns faster too.

## 💡 Example prompts

```text
Inspect this project and explain its entry point. Do not change any files.
```

```text
Create hello.py, verify that it exists, and validate its Python syntax.
```

```text
Find the cause of this error, fix it, and run the available project checks.
```

```text
Search for repeated error handling, explain the duplication, and suggest a simpler design.
```

## ⌨️ Commands

- `/help` — show available commands
- `/back` — return to the main menu
- `Ctrl+C` — interrupt MyLLM

## 🏎️ Recommended low-end settings

- 🧠 **Context:** `4096` for speed; `8192` for longer tasks
- 🎮 **GPU layers:** `0` with the standard CPU build
- 🔁 **Maximum steps:** `12` for everyday tasks
- 🛑 **No-progress steps:** `3`–`6`
- 🎯 **Temperature:** `0.15` for stable tool calls with better failure recovery
- 🔍 **Debug:** `0` for quiet output; `1` for the raw model stream
- ⚡ **Prompt cache:** enabled; try `512 MB` on a memory-limited computer
- 📤 **Output limit:** `0` lets the model stop naturally without a fixed token cap
- 📦 **Model:** a 3B–4B `Q4_K_M` GGUF is a practical starting point

Model size and context length have the greatest effect on RAM usage and response
speed.

## ✨ Features

- 🧠 Remembers the active project, file, goal, language, and technology across follow-ups
- ⚡ Uses an optional RAM prompt cache to accelerate repeated prompt prefixes
- 🎯 Uses explicit file paths first instead of scanning unrelated projects
- 🧭 Preserves requested languages and frameworks
- 🔎 Detects Node, React, Next.js, Vite, Python, Maven, Gradle, Rust, and Go projects
- 🗺️ Finds the relevant nested project root before running project commands
- ✅ Discovers available test, build, lint, and type-check commands
- 🔄 Re-detects project checks after new project files are created
- 📚 Creates several files in one tool call for faster project scaffolding
- 📝 Supports both focused patches and safe full-file replacement
- 📦 Stores large generated content as reusable payload references to keep prompts compact
- 🗂️ Tracks created, modified, inspected, and active files
- 🚧 Tracks blockers and unavailable project capabilities
- 🧪 Detects small unfinished stubs and directs the model to implement them
- 🧰 Validates tool names and arguments before execution
- 📤 Allows unlimited model output by default while retaining an optional limit
- 📝 Writes optional timestamped logs for each MyLLM run
- 🧩 Keeps menus, configuration, logging, tools, and agent logic internally separated
- 🔄 Stops repeated actions with a no-progress circuit breaker
- ✂️ Trims old observations while preserving working state
- 🕒 Requires verification after the latest file mutation
- ↩️ Can undo file creation, modification, and deletion during the current run
- 🧾 Saves project facts only when backed by successful tool evidence
- 🚫 Rejects fake binary files created through text tools
- 🧱 Blocks file access outside the selected workspace

## 💾 Local data

- ⚙️ Settings: `.myllm/config.json`
- 🧠 Verified project memory: `.myllm/memory`
- 📦 Temporary large-content payloads: `.myllm/payloads`
- 📝 Timestamped run logs: `myllm_logs/<date>/`
- 🤖 Models: `models/*.gguf`

## 🛡️ Safety

- 📁 Tools are restricted to the selected workspace
- 🚪 Paths outside the workspace are blocked
- 🗑️ File deletion inside the workspace does not yet ask for approval
- ↩️ File changes can be undone only during the current run
- 💾 Use version control for important projects
- 👀 Clearly say **do not change files** when requesting a read-only review

## ⚠️ Current limitations

- ✅ Failed command exit codes still need to propagate as failed tool results
- 🎯 A successful check can validate the latest revision without proving it covered every changed file
- 📚 An unexpected I/O error during multi-file creation may leave a partial batch
- 🔁 Equivalent relative and absolute paths may evade duplicate detection
- 🧩 Mixed-language task constraints may need manual clarification
- 🧪 Stub detection may be too strict for intentionally small source files
- ⏹️ There is no dedicated Stop button
- 💾 A large RAM prompt cache may reduce available memory on low-end computers

## 🕵️ Journey — clues newest first

- **0.1.7 · The Trail Became Less Predictable** — A small amount of controlled
  variation helped the coder escape rigid recovery loops without making its tool
  protocol reckless.
- **0.1.6 · The Case Was Filed** — The agent separated its inner machinery,
  began keeping timestamped notes, remembered repeated prompt paths, and stopped
  imposing an output ceiling unless the user requested one.
- **0.1.5 · The Agent Remembered the Case** — Follow-ups retained their target,
  large payloads stopped crowding the prompt, multi-file creation accelerated the
  work, and every new edit reopened verification.
- **0.1.4 · The Agent Found Its Map** — Project detection, hard task constraints,
  working-state tracking, verified memory, undo, and a no-progress escape route
  turned a simple loop into a project-aware investigator.
- **`66ac99e` · The Notebook Appeared** — The once-empty guide finally revealed
  how to summon the agent.
- **`d0bfa35` · The Monolith Split in Two** — Simplicity won: reasoning stayed
  with the agent while focused tools moved behind a cleaner boundary.
- **`7a48927` · The Hidden Room Moved Home** — Configuration and memory began
  following the script instead of the terminal's current directory.
- **`61da581` · The First Voice** — A single script loaded a local model and began
  acting inside a chosen project.
- **`4b40790` · The Ingredients Arrived** — The local runtime dependencies entered
  the scene.
- **`7694b20` · The Empty Room** — One tiny README, one initial commit, and no clue
  yet what the project would become.

## 🏷️ Versioning

- Current development series: `0.1.x`
- Compatible updates increment the patch number: `0.1.7` → `0.1.8`
- Breaking configuration or storage changes may increment the minor version
