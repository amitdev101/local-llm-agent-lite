# 🌐 Local LLM Playground

A lightweight browser chat for testing local GGUF models directly.

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
- 🔄 Switches models beside the prompt without clearing the chat
- ♻️ Replaces an older playground instance on port `8000`
- 🛡️ Never stops an unrelated application using that port

## ✨ Features

- 🤖 Model selector beside the prompt
- 🌊 Streaming responses with **Stop**
- 📋 Copy, Regenerate, and Continue actions
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
- 📝 Logs: `logs/`

- 💭 Compatible models receive `/think` or `/no_think`
- 🙈 The thinking directive is not shown in saved user messages

## 🧰 Models not appearing?

- 🔄 Refresh with `Ctrl+F5`
- 📦 Confirm files end in `.gguf`
- 📁 Keep models in the repository's root `models/` folder
