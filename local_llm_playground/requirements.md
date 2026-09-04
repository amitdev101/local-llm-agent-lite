# Local LLM Playground Requirements

## Purpose

A single-user, localhost-only browser interface for directly testing GGUF models without agent tools, RAG, project context, or hidden prompts.

## Required behavior

- Start with `python local_llm_playground/main.py` and serve the complete app at `127.0.0.1:8000`.
- Detect models from the repository `models/` directory and allow a custom GGUF path.
- Keep one selected model loaded across chats and show loading, ready, generating, stopped, and error states.
- Stream responses, support Stop, render Markdown/code, and remain keyboard friendly and responsive.
- Keep the composer visible using the dynamic browser viewport while only the conversation area scrolls.
- Provide a compact detected-model selector beside the composer and allow model switching without clearing the active chat.
- Provide first-run guidance, live loading/generation timers, model-ready confirmation, and clear disabled states.
- Make an active system prompt visible and directly editable from the composer.
- Provide Copy, Regenerate/Retry, and Continue response actions.
- Provide searchable, date-grouped recent chats with explicit Rename and Delete menus.
- Show approximate/exact context usage, warn near the limit, and visibly report trimmed older turns.
- Provide recent chats, New Chat, reopen, rename, delete, retry, and regenerate behavior.
- Provide an optional per-chat system prompt with no hidden default prompt.
- Provide model-dependent `/think` and `/no_think` control without changing the visible user message.
- Provide temperature from 0.0 to 2.0 plus advanced top-p, top-k, min-p, repeat penalty, and seed controls.
- Do not set a maximum output-token limit; generation ends only at the model stop condition, context boundary, interruption, or error.
- Persist one append-only JSONL file per conversation and recover valid events before a malformed final line.
- Display first-token latency, total time, approximate prompt/completion tokens, tokens per second, context, model, temperature, and thinking mode.
- Store operational timestamped logs separately from conversations.
- Use local frontend assets only, sanitize rendered output, expose no shell endpoint, and bind to localhost.
- Allow only one active generation; settings other than model path, context, GPU layers, and threads must not reload the model.
- Optimize drawers, controls, diagnostics, and safe-area spacing for narrow and mobile screens.

## Excluded

- Coding-agent tools, file editing, shell execution, project detection, RAG, LanceDB, cloud providers, accounts, authentication, and concurrent generations.

## Completion

The playground is complete when a user can select and load a model, create or reopen a chat, configure its system prompt/thinking/sampling values, stream or stop a response, inspect metrics, and find the conversation stored as JSONL.
