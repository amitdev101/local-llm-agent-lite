# 🤖 MyLLM

**Version 0.1.4**

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
faster.

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
- 🎯 **Temperature:** `0.1` for predictable tool calls
- 🔍 **Debug:** `0` for quiet output; `1` for the raw model stream
- 📦 **Model:** a 3B–4B `Q4_K_M` GGUF is a practical starting point

Model size and context length have the greatest effect on RAM usage and response
speed.

## ✨ Features

- 🧭 Preserves requested languages and frameworks
- 🔎 Detects Node, React, Next.js, Vite, Python, Maven, Gradle, Rust, and Go projects
- ✅ Discovers available test, build, lint, and type-check commands
- 🗂️ Tracks created, modified, and inspected files
- 🚧 Tracks blockers and unavailable project capabilities
- 🔄 Stops repeated actions with a no-progress circuit breaker
- ✂️ Trims old observations while preserving working state
- ↩️ Can undo file creation, modification, and deletion during the current run
- 🧾 Saves project facts only when backed by successful tool evidence
- 🧱 Blocks file access outside the selected workspace

## 💾 Local data

- ⚙️ Settings: `.myllm/config.json`
- 🧠 Verified project memory: `.myllm/memory`
- 🤖 Models: `models/*.gguf`

## 🛡️ Safety

- 📁 Tools are restricted to the selected workspace
- 🚪 Paths outside the workspace are blocked
- 🗑️ File deletion inside the workspace does not yet ask for approval
- ↩️ File changes can be undone only during the current run
- 💾 Use version control for important projects
- 👀 Clearly say **do not change files** when requesting a read-only review

## ⚠️ Current limitations

- ✅ Command exit codes need stricter verification handling
- 🕒 Verification is not yet tied to the latest edit revision
- 🔁 Equivalent relative and absolute paths may evade duplicate detection
- 🧩 Mixed-language task constraints may need manual clarification
- ⏹️ There is no dedicated Stop button

## 🕵️ Journey — clues newest first

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
- Compatible updates increment the patch number: `0.1.4` → `0.1.5`
- Breaking configuration or storage changes may increment the minor version
