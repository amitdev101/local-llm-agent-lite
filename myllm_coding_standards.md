# MyLLM Coding Standards

## Core principle

> Build the smallest understandable system that solves the real problem. Remove choices, repetition, indirection, and machinery unless they provide measurable value.

Before adding anything, ask:

> Can this be done with fewer concepts, fewer choices, fewer methods, and less code without making the behavior ambiguous or unsafe?

## Architecture

- 🪶 Prefer simple architecture over fashionable architecture.
- 🧩 Add abstractions only when they remove real duplication.
- 🚫 Avoid unnecessary services, containers, frameworks, concurrency, and background machinery.
- 📁 Keep the number of files and layers low.
- 🔌 Separate components only when the boundary provides practical value.
- 🎯 Give every class, file, method, and dependency one clear purpose.
- 🧹 Remove obsolete implementations instead of keeping competing versions.
- 🔄 Evolve existing code instead of rebuilding it without a concrete reason.

## APIs and tools

- 🧰 Prefer fewer, clearer tools when one safe tool can cover multiple cases.
- 📦 Use one consistent structure for singular and batch operations.
- 🏷️ Mark every parameter as required or optional and show defaults.
- `<>` Use generalized angle-bracket placeholders in model-facing documentation.
- 🚦 Keep operations separate when their safety consequences differ.
- ✅ Validate the complete request before making changes.
- ♻️ Make operations idempotent where practical.
- 🚫 Never silently overwrite existing data.
- 📤 Return concise, structured, actionable results.
- 🧠 Never ask the LLM to infer facts the runtime can determine reliably.

## Agent behavior

- 🤖 Use the LLM for judgment, reasoning, and code generation.
- ⚙️ Use deterministic Python for validation, permissions, progress, verification, and safety.
- 🔁 Block proven no-progress loops, not repeated action names alone.
- 📈 Recognize meaningful improvement even before the task is complete.
- 🚑 Give concrete recovery guidance after failures.
- ✅ Require evidence before allowing completion.
- ✂️ Keep model context small, current, and relevant.
- 🗺️ Prefer explicit paths and targeted inspection over broad searches.
- 🌡️ Avoid adaptive or clever behavior until evidence shows that it helps.
- 🧪 Design prompts and tools for small local models, not frontier-model assumptions.

## Product design

- ▶️ Provide one obvious command to start the product.
- 🪟 Prefer an accessible browser interface over mandatory CLI interaction.
- 🔐 Keep models, conversations, source code, and logs local.
- 💻 Optimize for ordinary computers, especially systems with 16 GB RAM.
- 📦 Detect models automatically from one predictable folder.
- 🌊 Show progress and streaming output immediately.
- 👀 Keep the active model, workspace, context, settings, and status visible.
- 💾 Prefer simple, inspectable storage formats such as JSONL.
- 🛑 Allow users to interrupt long-running work.
- 🧼 Keep direct model chat separate from coding-agent behavior.

## User experience

- 🎯 Place important controls where they are needed.
- 🧭 Avoid hidden or surprising behavior.
- 📱 Support different screen sizes cleanly.
- ⌨️ Keep interaction keyboard-friendly.
- 💬 Prefer familiar interaction patterns.
- ✨ Favor information density and polish over decoration.
- 🔤 Use consistent, readable fonts.
- 🧠 Separate reasoning and status from final responses.
- 🔧 Show useful technical information without overwhelming the user.

## Development process

- 📋 Plan before implementing meaningful changes.
- 🔍 Review plans from both simplicity and small-local-model perspectives.
- 📝 Keep `myllm_context.md` accurate and current.
- 📖 Keep documentation synchronized with behavior.
- 🔬 Diagnose using logs and concrete code evidence.
- 🩹 Prefer minimal, targeted changes.
- 🚫 Do not add or run automated tests unless explicitly requested.
- ✅ Perform lightweight validation appropriate to each change.
- 🗑️ Do not preserve dead code merely because it once worked.
- 🗣️ Challenge recommendations that ignore the existing implementation or project constraints.

