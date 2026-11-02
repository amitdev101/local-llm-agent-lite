# 🌐 Local LLM Playground

A lightweight browser chat for testing local GGUF models. Compare responses, adjust system instructions and sampling, and inspect performance without agent tools, RAG, or project context.

## 🚀 Run

Install the repository's [Python dependencies](../README.md#-install), place `.gguf` files in the root `models/` folder, then run from the repository root:

```powershell
python local_llm_playground/main.py
```

The launcher opens `http://127.0.0.1:8000`, discovers models, and automatically loads the last valid selection. It replaces an older playground instance on that port and leaves unrelated applications running. To switch models, select one beside the prompt and press **Load**; your conversation and draft stay available.

## 🎨 Themes and screenshots

Open **Settings → Appearance** to choose **ChatGPT**, **Claude**, or **GitHub Copilot**, in **Light** or **Dark**. Each family has its own layout, typography, navigation, composer, and button styling, with Local LLM Playground branding. Switching preserves drafts, response preferences, and an ongoing response. Appearance stays in this browser.

These Playwright captures show the current frontend, including grouped **Options**, visible **Thinking**, model readiness, and collapsed response details. Conversations and metrics are deterministic sample data, not live model benchmarks.

| Theme | Light | Dark |
| --- | --- | --- |
| ChatGPT | ![ChatGPT light theme with grouped composer controls](docs/images/themes/chatgpt-light.png) | ![ChatGPT dark theme with grouped composer controls](docs/images/themes/chatgpt-dark.png) |
| Claude | ![Claude light theme with grouped composer controls](docs/images/themes/claude-light.png) | ![Claude dark theme with grouped composer controls](docs/images/themes/claude-dark.png) |
| GitHub Copilot | ![GitHub Copilot light theme with grouped composer controls](docs/images/themes/github-copilot-light.png) | ![GitHub Copilot dark theme with grouped composer controls](docs/images/themes/github-copilot-dark.png) |

Customize names, logos, wording, colors, fonts, buttons, and supported component positions in each theme's JSON/CSS pair in [`frontend/themes/`](frontend/themes/). Follow the [theme customization guide](docs/themes.md). These local recreations provide the playground's chat features; product cloud services are outside their scope.

### Composer controls

**Options** groups system instructions, temperature, and advanced sampling. **Thinking** stays visible beside it: supported models show **On/Off**, while unsupported or unconfigured models show **Unavailable** or **Unknown**. Configure compatibility by filename in [`model_capabilities.json`](model_capabilities.json); see the [model state and capability guide](docs/model-capabilities.md).

The model menu separates selection from loading, shows the full path and loaded state, and retains load errors. **Send** becomes available when the selected model is loaded. The compact **Context** percentage opens detailed usage.

| Response options | Model selection |
| --- | --- |
| ![Grouped response options in the Claude dark theme](docs/images/themes/claude-dark-options.png) | ![Selected model, loaded state, and Thinking support in the Claude dark theme](docs/images/themes/claude-dark-model.png) |

More views: [Claude on mobile](docs/images/themes/claude-dark-mobile.png), [GitHub Copilot on mobile](docs/images/themes/github-copilot-dark-mobile.png), [appearance settings](docs/images/themes/chatgpt-dark-settings.png), and empty chats in [ChatGPT](docs/images/themes/chatgpt-dark-empty.png), [Claude](docs/images/themes/claude-dark-empty.png), and [GitHub Copilot](docs/images/themes/github-copilot-dark-empty.png).

## 💬 Chat behavior

- **Drafts:** unsent text and response preferences stay with each chat, including an unfinished New chat. Switching chats or refreshing restores them. If browser storage is unavailable, a warning asks you to keep the tab open or copy the text.
- **Recovery:** failed or stopped responses retain partial text. **Retry** reuses the latest attempt's original prompt and settings without duplicating the user message. If the server already completed the answer, Retry recovers it. Earlier attempts remain available after reopening the chat.
- **Alternatives:** **Regenerate in new chat** branches from the selected answer's original prompt and settings, preserving the original conversation. **Continue** adds a visible `Continue.` request after the latest completed answer. These actions preserve a separate composer draft.
- **Response details:** reasoning appears when the model returns it. Reasoning and performance sections start collapsed; expand them to inspect timing, speed, token counts, temperature, effective Thinking state, and the model that answered.
- **Context:** older complete turns may be trimmed when needed. There is no fixed output-token ceiling; generation ends at EOS, the context boundary, an error, or **Stop**.

### Settings scope

| Controls | Applies to |
| --- | --- |
| System instructions, Thinking, temperature, sampling, seed | The next response in this chat; unsent preferences are retained per chat in this browser. |
| **Save as defaults** | Future new chats. Existing chats keep their preferences. |
| **Use saved defaults** | Copies saved response defaults into this chat. |
| Model path, context, GPU layers, threads | The runtime after **Load/Reload**. Pending edits show a Reload state; responses use the loaded settings until it succeeds. |

### Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| Send | Enter |
| Insert a line break | Shift+Enter |
| Stop a response | Ctrl+Shift+X on Windows/Linux; Cmd+Shift+X on macOS |
| Close a popover or Settings | Escape |

IME composition waits until text is committed before sending. Ctrl+C / Cmd+C keep normal copying behavior. For exact action behavior and manual review scenarios, see the [chat behavior and settings guide](docs/usability.md).

## 💾 Storage

| Data | Location |
| --- | --- |
| Models | Repository root `models/*.gguf` |
| JSONL conversations | `local_llm_playground/data/chats/` |
| Saved defaults and model parameters | `local_llm_playground/data/config.json` |
| Drafts, unsent response preferences, appearance, last-opened chat | This browser's local storage |
| Logs | `local_llm_playground/logs/` |

Models explicitly configured as supported receive `/think` or `/no_think`; these directives are hidden from saved user messages. Unconfigured models receive no Thinking directive, and the On/Off preference is retained for supported models.

## 🧰 Troubleshooting

If a model is missing, confirm its filename ends in `.gguf`, place it in the repository's root `models/` folder, and refresh with **Ctrl+F5**. You can also enter a custom GGUF path in Settings. A newly selected model needs **Load** before sending; context, GPU, or thread changes need **Reload**.

## Verification and screenshot refresh

From the repository root, run the controller and backend checks without a browser or a real model:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s local_llm_playground/tests -p test_recovery.py
node --test local_llm_playground/tests/recovery.test.cjs
```

These use temporary chats, a fake model, and simulated DOM, storage, and network surfaces. They cover recovery, drafts, model states, keyboard behavior, branches, response presentation, settings scope, validation, and pending reloads.

To check the rendered themes and regenerate the gallery with Playwright, follow the [screenshot instructions](docs/themes.md#playwright-verification-and-screenshots). The preview serves the real frontend with in-memory fixtures and leaves your chats, models, and saved defaults untouched. Review the captures after layout changes and use the [manual scenarios](docs/usability.md#verification) for the complete chat journey.
