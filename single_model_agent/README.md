# 🧠 MyLLM Single-Model Coding Agent

A local, production-oriented coding agent for one GGUF model, one workspace, and ordinary
Windows/Linux computers. It was built for developers who want inspectable local automation without
cloud APIs, RAG, a model router, or a large agent framework.

> Current maturity: a working safety-first foundation, not an unattended or hostile-code sandbox.
> Real-model benchmarks are still required before calling it a stable production release.

The first detailed stress-test report is
[`experiments/2026-09-18-java8-snake.md`](experiments/2026-09-18-java8-snake.md). It records the
failed local-model runs, controller fixes, final independent repair, measurements, tradeoffs, and
next optimization targets without misreporting the final example as a model success.

## 🎯 Who this is for

- Developers running a local Qwen, Hermes-compatible, or generic GGUF model.
- People who want source code, prompts, conversations, snapshots, and logs to stay local.
- Users who value small, readable Python modules and deterministic safety boundaries.
- Teams evaluating whether a small local model can perform reliable coding-agent work.
- Windows users who need interruption, workspace controls, and useful logs without Docker.
- Machines where loading two models is too slow or memory-heavy.

It is not currently intended for:

- Unattended execution against hostile repositories.
- Arbitrary shell automation, dependency installation, or network tasks.
- Multi-tenant/cloud isolation.
- Multi-model routing, RAG, or parallel agents.
- Replacing human review for generated code.

## ✅ What is implemented

- One local GGUF through `llama-cpp-python`.
- One persistent provider worker process and one active workspace.
- Streaming response and separately labelled reasoning when the backend exposes it.
- Real Ctrl+C/stop behavior with worker termination fallback.
- Six model-facing tools—no near-synonym tool clutter.
- Natural fenced actions plus bounded Qwen/Hermes/JSON compatibility.
- Strict schema, path, risk, approval, execution, and verification layers.
- Ask, trusted-auto, and no-write dry-run modes.
- Atomic UTF-8 file creation/replacement/patching.
- Hash-based checkpoints and guarded undo.
- Allow-listed project checks; no arbitrary shell command from the model.
- Controller-owned task requirements and completion evidence.
- JSONL run/session events and readable dated logs.
- Context accounting, archive-before-compaction, and deterministic compaction.
- Crash-aware checkpoint reconciliation and conservative session resume.
- No explicit output-token cap unless the user chooses one.

## 🚀 Start

From the repository root:

```powershell
python single_model_agent/main.py
```

The launcher searches `models/` recursively for `.gguf` files. It selects the only model
automatically or shows a numbered menu when several exist. The model loads on the first request,
not before the interactive prompt appears.

Choose an explicit model and project:

```powershell
python single_model_agent/main.py `
  --model models/Qwen3.5-9B-Q4_K_M.gguf `
  --workspace D:\path\to\project
```

Use the repository virtual environment if applicable:

```powershell
.\.venv\Scripts\python.exe single_model_agent\main.py
```

Required runtime dependency is already listed in the repository’s `requirements.txt`:

```text
llama-cpp-python==0.3.35
```

`psutil` is used for process-tree termination and is also present in the root requirements.

## ⚙️ Command-line options

| Option | Default | Meaning |
|---|---:|---|
| `--model <path>` | model menu | Exact GGUF file |
| `--models-dir <path>` | repository `models/` | Folder searched recursively |
| `--workspace <path>` | current directory | Only project the agent can inspect/change |
| `--data-dir <path>` | OS user-data folder | Events, sessions, logs, archives, trust, snapshots |
| `--context <tokens>` | `32768` | llama.cpp context allocation |
| `--temperature <value>` | model profile | Explicit sampling override |
| `--max-output-tokens <n>` | omitted | Optional output cap; absent means no explicit cap |
| `--gpu-layers <n>` | `0` | Layers offloaded by llama.cpp |
| `--threads <n>` | CPU count minus one | llama.cpp worker threads |
| `--max-steps <n>` | `20` | Bounded agent loop |
| `--mode <mode>` | `ask` | `ask`, `auto`, or `dry-run` |

Examples:

```powershell
# Preview actions without file writes or subprocess checks
python single_model_agent/main.py --mode dry-run

# Use GPU offload and a larger context
python single_model_agent/main.py --gpu-layers 40 --context 65536

# Explicitly cap output only when you want that trade-off
python single_model_agent/main.py --max-output-tokens 8192
```

An output cap is a hard generation boundary. A model may finish within it, but it may also be cut
off. Therefore this agent leaves it unset by default and rejects incomplete actions instead of
executing partial content.

## ⌨️ Interactive commands

| Command | Behavior |
|---|---|
| `/status` | Loads/reads the model profile and shows model, workspace, context, temperature, mode, session, and data folder |
| `/undo` | Restores the latest unchanged agent checkpoint |
| `/resume <run-id>` | Replays and reconciles an interrupted run, then continues at a safe boundary |
| `/accept <check>` | Explicitly accepts one current required check as an unverified limitation |
| `/trust-edit` | Grants only `edit_file` auto permission to this exact workspace |
| `/new` | Starts a new conversation/session using the same configuration |
| `/stop` | Requests interruption; Ctrl+C does the same during active work |
| `/help` | Shows command help |
| `/exit` | Closes the model worker and exits |

`/trust-edit` does not auto-approve executable build/test commands. Trust is capability-scoped and
bound to the canonical workspace path.

## 🔄 Exact run lifecycle

```text
user request
  → controller-derived TaskRequirements persisted and fsynced
  → model/profile metadata selected
  → compact context prepared
  → tokens streamed
  → complete response buffered
  → protocol adapter
  → canonical ToolCall / FinalMessage / InvalidResponse
  → exact schema validation
  → workspace and sensitive-path policy
  → risk and approval policy
  → dry run or execution
  → observation and deterministic verification
  → next model step or controller-gated completion
```

The model proposes intent. Python decides whether it is complete, valid, allowed, applicable,
executed, verified, resumable, and finished.

## 🧰 The six tools

### `list_files`

```text
list_files(path=<workspace-relative directory>, depth=<1-5>)
```

- Required: none.
- Optional: `path` defaults to `.`, `depth` defaults to `2`.
- Maximum 1,000 returned entries.
- Sorted, bounded, read-only.
- Excluded/sensitive areas are omitted.

### `search`

```text
search(query=<literal text>, path=<workspace-relative path>)
```

- Required: `query`.
- Optional: `path` defaults to `.`.
- Query length is capped at 1,000 characters.
- Literal text matching, currently case-sensitive.
- Maximum 200 matches and 10,000 scanned visible text files.
- Results include relative file, line number, and a bounded snippet.
- No regex supplied by the model and no shell process.

### `read_file`

```text
read_file(
  path=<workspace-relative UTF-8 file>,
  start_line=<positive integer>,
  end_line=<positive integer>
)
```

- Required: `path`.
- Optional: `start_line=1`, `end_line=200`.
- Maximum 500 lines and 2 MiB per file.
- Output carries line numbers.
- Binary, secret, external-link, and excluded paths are rejected.

### `edit_file`

Create:

````text
ACTION: edit_file
operation: create
path: <new relative file>
content:
```<language>
<complete content>
```
````

Replace:

````text
ACTION: edit_file
operation: replace
path: <existing relative file>
content:
```<language>
<complete replacement>
```
````

Patch:

````text
ACTION: edit_file
operation: patch
path: <existing relative file>
old_text:
```
<exact text occurring once>
```
new_text:
```
<replacement text>
```
````

Rules:

- Exactly one operation and one file.
- `create` refuses an existing target.
- `replace` requires an existing UTF-8 file.
- `patch` requires one and only one exact `old_text` match.
- Empty/no-op output is rejected.
- Maximum current/new file size is 2 MiB.
- Preview and approval happen before mutation.
- A stale approval fails if the file hash changed after preview.
- Existing-file writes claim the approved target by renaming it to a unique guard, verify that
  claimed hash, and install prepared content with a no-overwrite hard link. A concurrent
  recreation becomes `CONFLICT` instead of being overwritten.
- Windows sharing failures retry only three short times.
- The original checkpoint remains available for guarded undo.

### `run_check`

```text
run_check(kind=<build|test|lint|typecheck>)
```

The model cannot provide a command. The controller chooses only a built-in profile:

| Project/check | Implementation | Auto-safe |
|---|---|---:|
| Python `build`/`lint` | Read visible `.py` files and call `ast.parse` | Yes |
| Python `test` | Exact current-Python `-m unittest discover` argv | No; approval |
| Java `build` | Canonical `$JAVA_HOME/bin/javac -proc:none -d <agent-temp> @<agent-argfile>` using all visible `.java` files | No; approval |
| Unsupported combination | `UNAVAILABLE` | No execution |

- No `shell=True`.
- No repository-provided executable string.
- Java compilation requires a valid controller environment `JAVA_HOME`; repository `PATH`
  shadowing is not trusted.
- No dependency installation or network fallback.
- Provider/API secrets are removed from the child environment.
- Timeout is 120 seconds.
- Cancellation terminates the process tree.
- Output is bounded while preserving useful beginning/end sections.
- Visible source hashes are compared before and after a subprocess.
- Any unexpected source change becomes `CONFLICT`.

Repository tests/builds may execute repository code. Approval is therefore meaningful even when
the executable name is trusted.

### `finish`

```text
finish(message=<short factual result>)
```

`finish` does not end a run by itself. The controller checks:

- A mutation task actually produced a verified edit.
- A project/file read-only task has a successful list/search/read observation.
- Expected files exist and remain workspace-contained.
- Required checks are `PASSED`, or the user accepted that exact limitation.
- No approval, conflict, or unresolved controller failure remains.

Ordinary prose cannot bypass this gate. It completes only conversational/read-only work whose
stored requirements are already satisfied.

## 🗣️ Accepted model response formats

Parser precedence is deterministic:

1. Provider-native structured call when a future provider returns one.
2. One complete `<tool_call>...</tool_call>` block.
3. One exact `ACTION:` or `TOOL:` block outside code fences.
4. One JSON object occupying the entire visible response.
5. `FINAL:` or ordinary final text.

Hermes JSON compatibility:

```xml
<tool_call>
{"name":"read_file","arguments":{"path":"README.md"}}
</tool_call>
```

Qwen3.5 tagged compatibility:

```xml
<tool_call>
<function=read_file>
<parameter=path>README.md</parameter>
</function>
</tool_call>
```

Whole-response JSON compatibility:

```json
{"tool":"read_file","args":{"path":"README.md"}}
```

Explicit aliases are typed:

| Model name | Canonical call |
|---|---|
| `create_file`, `write_file` | `edit_file(operation=create)` |
| `replace_file` | `edit_file(operation=replace)` |
| `patch_file` | `edit_file(operation=patch)` |
| `run_tests` | `run_check(kind=test)` |
| `run_build` | `run_check(kind=build)` |
| `run_lint` | `run_check(kind=lint)` |

An alias cannot contradict its fixed operation/kind. Unknown/extra arguments fail. Unknown tool
names are not corrected by edit distance.

The parser rejects:

- Partial JSON, XML, fences, or thinking blocks.
- Multiple actions in one response.
- A second executable-looking action after a valid call.
- `ACTION:` written inside a code fence.
- Arbitrary JSON embedded in explanatory prose.
- Duplicate parameters/fields.
- Tool-shaped malformed output disguised as final text.
- Responses larger than the parser safety limit.

## 🔐 Workspace and path policy

Every path is resolved from one canonical workspace root. The agent rejects:

- Absolute model-supplied paths.
- `..` traversal escaping the workspace.
- Symbolic links or Windows junctions resolving outside it.
- Agent-owned data.
- `.git`, `.hg`, `.svn`, IDE state, model folders, dependency folders, generated caches, and
  existing MyLLM log folders.
- Common secret files such as `.env`, `.npmrc`, credentials files, private keys, certificates,
  keystores, and key containers.
- Non-UTF-8/binary content for text tools.
- Oversized files and results.

A denied read returns an explicit policy error. It is never presented as an empty/missing file.

## 🛂 Modes, trust, and approval

### Ask mode (default)

- List/search/read and safe internal syntax checks run automatically.
- Edits and executable project checks show an exact preview and require `y`.

### Auto mode

- Still has the same schemas and path policy.
- Only capabilities explicitly granted to the exact workspace can skip approval.
- `/trust-edit` grants only edits.
- Build/test and project-configurable lint/typecheck remain approval-required.

### Dry-run mode

- The complete parse/schema/policy/preview loop runs.
- `edit_file` returns its diff without writing.
- `run_check` returns its selected profile without spawning a process.
- Events/logs are still written so behavior can be audited.

Approval is persisted before waiting and includes tool, normalized arguments, workspace, risk,
preview, edit pre-hash or check source-manifest hash, and fingerprint. Execution rechecks that state.
On resume, only that exact fingerprint can be approved; model text is not reparsed into a different
action.

## 💾 Checkpoints and undo

Before each real edit, the controller saves:

- Run/checkpoint ids and timestamp.
- Relative path and whether it existed.
- Original bytes when applicable.
- Original and intended SHA-256 hashes.
- Write-started/write-completed flags.
- Verification status and final hash.

`/undo` verifies that current content still equals the agent’s post-edit hash. If it differs, undo
returns `CONFLICT` to protect user changes. A created file is removed only by an authorized undo
when it is still byte-for-byte the agent-created version.

This implementation never uses `git reset --hard`, never auto-commits, and never rewrites unrelated
dirty files.

## 🛑 Cancellation

The main process owns a spawned provider worker:

1. Stop sets a shared cancellation signal.
2. No new action/tool is scheduled.
3. Partial response content is never parsed.
4. The worker gets a cooperative grace period.
5. A stuck worker is terminated and must reload later.
6. A running check and descendants are terminated through `psutil` when possible.
7. `RunInterrupted` is fsynced with completed events/checkpoints preserved.

Force-stopping model inference trades a future model reload for deterministic interruption.

## 🧾 Runs, sessions, and local files

Default Windows root:

```text
%LOCALAPPDATA%\MyLLM\single_model_agent\
```

Default Linux/macOS-style root:

```text
$XDG_STATE_HOME/myllm/single_model_agent/
# fallback: ~/.local/state/myllm/single_model_agent/
```

Layout:

```text
single_model_agent state/
├── archives/<run-id>/context-*.txt
├── logs/<YYYY-MM-DD>/<run-id>.log.txt
├── runs/<run-id>.jsonl
├── sessions/<workspace-hash>/<session-id>.jsonl
├── snapshots/<run-id>/<checkpoint-id>/
├── tmp/
└── workspace_trust.json
```

- A session spans multiple user requests in one interactive chat.
- Each request receives a new run id.
- Session JSONL receives the same ordered events as its runs.
- Runtime JSONL uses `schema_version: 1`, event/parent ids, sequence, UTC timestamp, run/session,
  workspace, model, step, type, and payload.
- Critical boundaries call `flush()` and `fsync()`.
- An incomplete final JSONL line can be ignored after a crash; corruption earlier in a log fails
  recovery.
- Human logs bound large payloads; structured logs retain the controller interpretation.
- Known credential patterns are redacted, but logs may still contain local source/model text needed
  for diagnosis. Protect the data directory accordingly.

Important event families:

```text
RunStarted, UserMessage, TaskRequirementsRecorded, ModelProfileSelected,
ContextPrepared, ModelStarted, ModelResponse, ActionParsed, ParseFailed,
SchemaRejected, PolicyAllowed, PolicyDenied, ApprovalRequired/Granted/Rejected,
ToolStarted, ToolOutput, ToolFailed, CheckpointCreated, EditApplied,
EditRolledBack, VerificationResult, ContextArchived, ContextCompacted,
RunInterrupted, RunCompleted, RunFailed
```

Per-token persistence is intentionally off. The console still streams; the log stores complete
responses, first-token latency, total duration, reasoning character count, and controller outcome.

## ♻️ Resume and crash reconciliation

`/resume <run-id>`:

- Replays only complete events.
- Restores the persisted `TaskRequirements`.
- Uses the latest durable requirements record, including verification checks added after edits.
- Restores known changed paths and check evidence.
- Reconciles every checkpoint’s current file hash.
- Treats `current == intended` as an applied edit needing/retaining verification.
- Treats `current == original` as a write that did not complete.
- Treats any third value as `CONFLICT` and refuses overwrite.
- Re-presents an unresolved approval using its persisted normalized action and fingerprint.
- Creates a new linked run instead of writing speculative events into the old run.

Resume never continues in the middle of a token stream, subprocess, or write.

## 📏 Context and compaction

- Context usage uses a conservative character estimate in the controller; llama.cpp still enforces
  its real tokenizer/context window.
- Around 72%, the complete prompt/history is written to a readable archive first.
- The active repair window then keeps at most three recent messages, each bounded to 4,096
  characters, plus controller state. Compaction records before/after token estimates and must
  reduce rather than grow the prompt.
- The active prompt preserves the user goal, requirements, changed paths, check states, recent
  message groups, and concise typed observations.
- Old verbose tool output is removed first.
- Tool call/result groups are kept together.
- The first version does not ask the model to summarize its own history.
- No RAG or vector database is used.

The configured context can be higher than a model’s useful attention. A larger number is not proof
that the model follows a very long coding prompt well.

## 🤖 Model profiles and thinking

The worker records exposed GGUF fields such as model name, architecture, chat template,
quantization indicator, and selected context. It chooses:

- Qwen3.5-compatible tagged/natural parsing when metadata or template identifies Qwen3.5.
- Hermes-compatible parsing for Qwen-family templates.
- Explicit natural actions for unknown models.

Filename hints are not the only source of truth. If `tokenizer.chat_template` is unavailable, the
log contains a warning.

Qwen3.5 commonly enables thinking by default. The direct `llama-cpp-python` API used here does not
guarantee per-request `chat_template_kwargs={enable_thinking: false}`. The agent therefore:

- Does not claim thinking was disabled when it was not.
- Shows a separate reasoning stream only when the runtime provides one.
- Removes complete `<think>...</think>` blocks before plain-text parsing. For the Qwen3.5 adapter
  only, one orphan `</think>` is accepted because some llama.cpp templates consume the opening
  token; only the suffix is parsed, while repeated or otherwise unbalanced tags remain invalid.
- Rejects dangling/ambiguous thinking around tool-shaped output.
- Keeps reasoning separate from permission and verification.

A future llama-server provider can implement confirmed per-request thinking control behind the same
provider interface.

### Live GGUF validation — 2026-09-18

- `Qwen3-1.7B-Q8_0.gguf` understood the task but returned multiple actions in one response. The
  controller rejected the response without creating a file. Its repair turn then repeated its
  reasoning until the isolated run was stopped. This confirms the safety boundary works, but the
  1.7B model is not yet reliable for this agent protocol with unlimited output.
- The first `Qwen3.5-9B-Q4_K_M.gguf` run exposed the llama.cpp template shape where the opening
  `<think>` is consumed but `</think>` remains in generated text. The bounded Qwen3.5 adapter was
  amended to accept exactly one such boundary and parse only the suffix.
- The repeated Qwen3.5 run completed `edit_file` → user approval → `run_check(kind=lint)` → final
  response. The generated program printed the exact requested text, the event log recorded
  `CONTENT_VERIFIED` and `PASSED`, and `/undo` removed the unchanged created file with an
  `EditRolledBack` event.
- That validation explicitly used `--max-output-tokens 2048` to bound the experiment. This did not
  change the product default: output remains unlimited when the option is omitted.

## 🧩 Source files

| File | Single responsibility |
|---|---|
| `main.py` | Model menu, CLI, commands, streaming display, approval input |
| `agent.py` | Task contract, state machine, loop, policy orchestration, completion, compaction, resume |
| `provider.py` | Provider protocol, GGUF profile, persistent llama.cpp worker, cancellation |
| `protocol.py` | Canonical response types and bounded response adapters |
| `tools.py` | Six schemas, workspace guard, execution, checks, checkpoints, undo |
| `storage.py` | Paths, JSONL/text events, archives, trust, redaction, durability |
| `prompts.py` | Compact system/tool/observation/repair prompts |
| `PLAN.md` | Reviewed implementation plan and complete dry-run matrix |
| `DECISIONS.md` | Accepted/rejected choices and research consequences |

## 🔎 Failure behavior

| Condition | Result |
|---|---|
| Empty or malformed response | `InvalidResponse`; one repair opportunity |
| Same invalid/failing fingerprint twice | Run fails as no progress |
| Unknown tool or argument | Schema rejection; no execution |
| Unsafe/missing/sensitive path | Policy/precondition rejection; no write |
| Stale edit approval | Hash mismatch; no write |
| Existing-file create | Rejected; model must explicitly replace if appropriate |
| Patch matches zero/multiple times | Rejected with match count |
| Unsupported check | `UNAVAILABLE`, never passed |
| Check nonzero exit | `FAILED` plus bounded output |
| Check timeout | Process tree killed; source manifest compared |
| Check changes source | `CONFLICT`; user review required |
| User rejects approval | No action; run ends failed |
| Ctrl+C/model worker stop | Partial action discarded; run interrupted |
| Post-write hash mismatch | `CONFLICT`; keep checkpoint and refuse automatic rollback over possible concurrent work |
| Later user change before undo | `CONFLICT`; undo refuses |
| Crash between checkpoint/write events | Resume compares original/intended/current hashes |
| Premature final/finish | Controller explains missing evidence and continues |
| Max agent steps | Run fails rather than looping forever |

Executed actions are represented in active context by their controller observations, not by an
extra synthetic assistant tool-call tag. This avoids teaching small models to repeat an internal
history marker instead of the documented `ACTION:` protocol.

Controller-derived artifact paths require a recognized source/config filename suffix. Slash-like
phrases such as `game-over/restart` are not treated as files, preventing false completion blockers.
Checkpoint and edit events inherit the active agent step so JSONL traces remain correctly ordered.
Any passing or failed check becomes `STALE` after a later verified source edit. The invalidation is
fsynced and replayed on resume, so completion always requires evidence for the latest source hash.

## 🧪 Planning dry runs and curious-kid review

Before implementation, independent research covered Aider, Hermes/Qwen, OpenHands, SWE-agent,
smolagents, Continue, PydanticAI, llama.cpp, and this repository’s actual Qwen logs. A planning
agent then dry-ran:

- Chat/final, list, search, and read.
- Java creation and existing-file patch.
- Malformed/partial natural, JSON, XML, fence, and thinking output.
- Unknown aliases, missing/extra arguments, nested values, braces/quotes in code.
- Traversal, absolute paths, links, overwrite, ambiguous patch, secrets.
- Failed/timeout/cancelled/conflicting checks.
- Premature finish and repeated no-progress failure.
- Cancellation during model generation and subprocess work.
- Approval rejection/acceptance and exact fingerprint replay.
- Archive/compaction, incomplete JSONL, crash-after-write, and resume.

The first curious-kid review failed the plan because it found:

- Plain final text could bypass mutation completion.
- Cancellation was promised without a killable boundary.
- Repository-derived check commands and check mutations were underspecified.
- Workspace trust had no durable definition.
- Crash-after-write and Windows replacement were ambiguous.
- Completion requirements were not controller state.

After those were corrected, a second review found a check-approval contradiction; a third found
that a timed-out check might already have changed source. The final plan requires post-timeout
manifest comparison and passed the independent gate.

See [`PLAN.md`](PLAN.md) for the full scenario-by-scenario expected parse, policy, filesystem,
verification, and terminal state.

## 📚 Research-derived lessons

### From Aider

- Model-specific profiles and edit protocols beat one universal prompt.
- Targeted repository context is safer and more accurate than dumping the repository.
- Whole files fit creation; exact search/replace fits focused edits.
- Checkpoints, diffs, lint/build evidence, and history management are part of the agent—not extras.
- Evaluate the complete edit/check pipeline, not whether output looked intelligent.

### From Hermes and Qwen

- Tool call/result ordering must remain intact.
- Qwen3 Hermes JSON and Qwen3.5 tagged XML are different native shapes.
- Thinking and native parsing have real llama.cpp edge cases.
- Long prompts and many tools hurt small-model selection.
- The embedded chat template matters more than the filename.

### From OpenHands, SWE-agent, smolagents, Continue, and PydanticAI

- Make each step an explicit action/observation event.
- The agent-computer interface is as important as the model.
- Separate parser, memory, permissions, executor, and bounded retries.
- Default permissions should ask at meaningful mutation/execution boundaries.
- Typed validation and usage/step budgets prevent silent loops.

Primary sources and issue links are preserved in
[`../research/single_model_agent_research.md`](../research/single_model_agent_research.md).

## ⚠️ Honest limitations

- No browser UI yet; this folder provides the provider-independent core and console adapter.
- No arbitrary shell, delete, package installation, network, Git mutation, or RAG.
- No multi-file atomic transaction; the model edits one file per action.
- No repository symbol/dependency map yet; navigation uses bounded list/search/read.
- Direct llama.cpp thinking control may be unavailable.
- The controller’s task-kind/path extraction is conservative, not a full natural-language spec
  compiler.
- Python syntax and Java compilation do not prove behavior.
- A user-approved test/build can execute untrusted project code on the host; this is not a sandbox.
- Secret redaction is best-effort, not a data-loss-prevention product.
- Worker force-stop requires a later model reload.
- Resume is conservative and may require user review rather than guessing.
- Context token use is estimated by characters in the controller.
- Real Qwen/other-GGUF end-to-end benchmark results are not yet recorded for this new folder.

## 📊 Production evaluation still required

Measure with real models and temporary workspaces:

- Correct tool and argument selection.
- Parser success and bounded repair rate.
- False-positive execution rate (target: zero).
- Unsafe/path/secret rejection rate.
- Edit application and exact-patch rejection rate.
- Syntax/build/test result and semantic task success.
- Premature-finish rejection.
- Repeated-failure termination.
- First-token latency, total latency, and tokens/second.
- Context growth and compaction correctness.
- Cancellation latency and orphan-process count.
- Crash recovery, resume, and undo success.
- JSONL completeness and ability to explain every decision.

Generated code must be evaluated in disposable workspaces. Valid JSON, a successful parser, or a
zero compiler exit code alone is not agent success.

## 🛣️ Deliberately deferred next work

Only add these after evidence justifies them:

- Browser UI consuming the same event stream.
- Model compatibility/conformance report for each GGUF.
- Repository symbol map cached by content hashes.
- Sandboxed shell with separate destructive/network policy.
- File delete and multi-file transaction support.
- Git-aware optional checkpoints.
- llama-server provider with confirmed Qwen thinking controls.
- Native provider tool-schema submission.
- Reproducible benchmark corpus and release thresholds.

## 📖 Related documents

- [`PLAN.md`](PLAN.md) — reviewed architecture and dry-run matrix.
- [`DECISIONS.md`](DECISIONS.md) — durable choices and rejected alternatives.
- [`../research/single_model_agent_research.md`](../research/single_model_agent_research.md) —
  consolidated external research.
- [`../research/README.md`](../research/README.md) — this repository’s tool-parser experiment
  history and log evidence.
- [`../myllm_coding_standards.md`](../myllm_coding_standards.md) — governing simplicity and safety
  rules.
