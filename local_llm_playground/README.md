# 🌐 Local LLM Playground

A lightweight browser chat for testing local GGUF models directly.

## Appearance

Open **Settings → Appearance** to choose **ChatGPT**, **Claude**, or **GitHub Copilot**, then **Light** or **Dark**. Each family has its own layout, typography, navigation, composer, and buttons, with Local LLM Playground branding. Choices stay in this browser; switching preserves your draft, conversation, model, and generation settings, including an ongoing response.

Customize each theme's JSON/CSS pair in [`frontend/themes/`](frontend/themes/). See the [customization guide](docs/themes.md) for changing names, logos, wording, colors, fonts, buttons, and supported component positions. These are local recreations of the browser chat styles; they do not add the products' cloud services or agent features.

The composer groups system instructions, temperature, and advanced sampling under **Options**. **Thinking** stays visible beside it and synchronized with Settings: supported models show **On/Off**, while unsupported or unconfigured models show **Unavailable** or **Unknown**. The model control shows a shorter name, with the full path in its menu, and the compact context percentage opens detailed usage.

Choose a model, then press **Load**. Selection and loading are separate; Send becomes available when the selected model is loaded. Model menus identify both the selected and loaded models, and load failures retain useful error details. Thinking compatibility is configured by filename in [`model_capabilities.json`](model_capabilities.json); see the [model state and capability guide](docs/model-capabilities.md). Unconfigured models receive no Thinking directives, and your preference is kept for supported models.

Failed and stopped responses keep their partial text, with a clear status and a Copy action. **Retry** is available for the latest response and reuses its original prompt and response settings with the loaded model, without adding the user message again. Earlier attempts remain visible. If the server already finished an answer while the connection was lost, Retry recovers the saved answer instead of generating it again. Retry remains available after reopening a chat.

Unsent drafts save locally in this browser for each chat, including an unfinished **New chat**. Switching chats or refreshing restores the text and last-opened chat. New chats get a server record when first sent. A draft clears after the server accepts its send request; edits made while Send is pending, plus drafts present during Retry, Regenerate, or Continue, stay intact. If browser storage is unavailable or full, drafts remain available in the open tab and a warning asks you to copy the text before refreshing.

**Regenerate in new chat** creates an alternative from the selected answer's original prompt and settings, preserving the original conversation. **Continue** applies to the latest completed answer. Reasoning is shown when returned, and reasoning/performance details start collapsed. **Enter** sends, **Shift+Enter** inserts a line break, and **Ctrl/Cmd+Shift+X** stops; copying retains normal behavior and IME composition never submits early.

Response preferences are kept per chat. **Save as defaults** applies them to future new chats; **Use saved defaults** copies defaults into the current chat. Model changes show a pending Reload state, and responses keep using loaded settings until reload. See the [chat behavior and settings guide](docs/usability.md) for details and a manual review checklist.

### Screenshots

These Playwright screenshots use deterministic sample conversations and metrics, not a live inference benchmark. They predate the grouped composer, model states, and current response controls.

| Theme | Light | Dark |
| --- | --- | --- |
| ChatGPT | ![ChatGPT light](docs/images/themes/chatgpt-light.png) | ![ChatGPT dark](docs/images/themes/chatgpt-dark.png) |
| Claude | ![Claude light](docs/images/themes/claude-light.png) | ![Claude dark](docs/images/themes/claude-dark.png) |
| GitHub Copilot | ![GitHub Copilot light](docs/images/themes/github-copilot-light.png) | ![GitHub Copilot dark](docs/images/themes/github-copilot-dark.png) |

## 🎯 Built for

- 🧪 People evaluating model responses and speed
- 🧠 Prompt testers comparing system prompts and thinking modes
- 🌡️ Users tuning temperature and sampling settings
- 🔐 Anyone who wants private, local browser-based conversations
- 🪶 Developers and learners running models on modest hardware

## 🧭 Design

- 🧼 No agent tools, RAG, project context, or hidden prompts
- 🌊 Direct streaming from the selected model
- 💬 Simple ChatGPT-style browser experience

## 🚀 Run

From the repository root:

```powershell
python local_llm_playground/main.py
```

- 🌐 Opens `http://127.0.0.1:8000`
- 🔎 Finds `.gguf` files in the root `models/` folder
- ⚡ Loads the last valid selected model automatically
- 🔄 Select and load models beside the prompt without clearing the chat or draft
- ♻️ Replaces an older playground instance on port `8000`
- 🛡️ Never stops an unrelated application using that port

## ✨ Features

- 🤖 Model selector beside the prompt
- 🌊 Streaming responses with **Stop**
- 💭 Collapsed reasoning when returned, plus expandable response details
- 📋 Copy, Retry, Regenerate in a new chat, and Continue actions
- 💬 Searchable, date-grouped recent chats
- 🧠 Editable system prompt and thinking mode
- 🌡️ Temperature and advanced sampling controls
- 📊 Context use, latency, speed, and token metrics
- ✂️ Safe trimming of older complete turns
- 📱 Responsive layout with an always-visible prompt
- ♾️ No fixed output-token ceiling

- 🛑 Generation ends at EOS, context boundary, error, or **Stop**

## 💾 Storage

- 🤖 Models: `../models/*.gguf`
- 💬 JSONL chats: `data/chats/`
- ⚙️ Settings: `data/config.json`
- ✍️ Unsent drafts, response preferences, and last-opened chat: this browser's local storage
- 📝 Logs: `logs/`

- 💭 Models explicitly configured as supported receive `/think` or `/no_think`
- 🙈 The thinking directive is not shown in saved user messages

## 🧰 Models not appearing?

- 🔄 Refresh with `Ctrl+F5`
- 📦 Confirm files end in `.gguf`
- 📁 Keep models in the repository's root `models/` folder

## Behavior checks

From the repository root, run the backend and frontend recovery checks without a browser or a real model:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s local_llm_playground/tests -p test_recovery.py
node --test local_llm_playground/tests/recovery.test.cjs
```

The checks use isolated temporary chats and a fake model, plus simulated DOM, storage, and network surfaces for the chat controller. They cover recovery, drafts, model states, keyboard behavior, Regenerate branches, response presentation, settings scope, validation, and pending reloads. Review the resulting UI manually in each theme and color mode; the [manual scenarios](docs/usability.md#verification) include the complete chat journey.
