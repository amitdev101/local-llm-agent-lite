# Single-Model Agent Decisions

This is the durable decision record for `single_model_agent`. Research evidence is in
[`../research/single_model_agent_research.md`](../research/single_model_agent_research.md),
the reviewed design and dry runs are in [`PLAN.md`](PLAN.md), and usage/operations are in
[`README.md`](README.md).

Real-model evidence and the tradeoffs it changed are recorded in
[`experiments/2026-09-18-java8-snake.md`](experiments/2026-09-18-java8-snake.md).

## Decision status

| State | Meaning |
|---|---|
| Accepted | Implemented and the current default |
| Experimental | Implemented but needs real-model evidence |
| Deferred | Intentionally outside the first production slice |
| Rejected | Considered and intentionally not used |

## D-001 — One model, one provider instance, one workspace

**Status:** Accepted

The first agent loads one GGUF through `llama-cpp-python` and operates on one canonical
workspace. Provider and agent interfaces remain separate so another backend can be added without
changing tool, policy, storage, or UI code.

Why:

- It matches the local, low-setup product goal.
- It avoids router errors and multi-model memory cost.
- It keeps failures attributable to one model/profile.

Deferred: remote APIs, multiple models in a run, model routing, parallel inference.

## D-002 — Six tools, no discovery round trip

**Status:** Accepted

The model sees only `list_files`, `search`, `read_file`, `edit_file`, `run_check`, and `finish`.
All documentation is present in the compact prompt. There is no `list_tools` tool.

Why:

- Small models spread probability across similar names.
- One polymorphic edit tool removes create/write/replace confusion while keeping safety-distinct
  operations explicit through `operation`.
- A discovery call wastes a slow local inference step and adds no controller knowledge.

Rejected: dozens of specialized tools and an unrestricted shell tool.

## D-003 — Natural actions first, native formats supported narrowly

**Status:** Accepted

The prompted format is an `ACTION:` block with fenced multiline values. The parser also supports
provider-native calls, complete Hermes JSON tags, complete Qwen XML tags, and an entire-response
JSON object. The Qwen3.5 adapter additionally accepts exactly one orphan `</think>` boundary—a
shape observed when llama.cpp consumes the opening template token—and parses only its suffix.
Other unbalanced/repeated thinking tags remain invalid. It never scans arbitrary prose for JSON.

Why:

- Project logs showed Qwen understood tasks but failed deeply escaped JSON envelopes.
- Source code naturally fits fences.
- Known model-native formats are useful compatibility paths.
- Narrow boundaries prevent examples or source strings from becoming actions.

Rejected: permissive “find any JSON anywhere” extraction, fuzzy tool names, partial-stream
execution, and model-generated Python/shell expressions.

## D-004 — Parsing is not authorization

**Status:** Accepted

Every response passes through separate parse, schema, path, task, risk, approval, execution, and
verification stages. A syntactically valid action has no authority by itself.

Executed actions are not echoed back into model history using synthetic assistant-side tool-call
markup. The following controller observation already identifies the tool and result; omitting the
extra pseudo-protocol prevents small models from copying internal history markers as their next
action and reduces context size.

Why: JSON validity cannot prove that a tool, path, operation, command, or completion claim is safe
or correct.

## D-005 — Controller-owned completion contract

**Status:** Accepted

Each user request becomes a durable, controller-derived `TaskRequirements` record containing the goal, task kind,
expected paths, operations, checks, and accepted limitations. Mutation requests cannot complete
from ordinary prose alone. Project/file read-only work also needs a successful workspace
observation. `finish` is a request checked against controller evidence.

This preliminary record is not misrepresented as a user-approved semantic plan. Current
classification is deliberately conservative and deterministic. Approved edits add their
paths to the contract; Java changes require `build`, and Python changes require `lint` when the
agent asks to finish. Users can explicitly accept a named unavailable check limitation.

Known limitation: understanding all semantic requirements in arbitrary natural language remains
an agent problem. The controller proves observable artifacts and checks, not subjective quality.

## D-006 — Dedicated, killable provider worker

**Status:** Accepted

The llama.cpp model lives in one spawned worker process. The main process owns agent state,
permissions, storage, and UI. Stop requests cooperative cancellation first and then terminate the
worker after a grace period. A force-stopped worker reloads the model on the next request.

Why:

- In-process native inference does not guarantee prompt cancellation.
- Partial generated actions must never execute.
- A worker contains provider crashes and avoids a second simultaneous model copy.

Trade-off: force cancellation makes the next response slow because the model must reload.

## D-007 — Unlimited model output by default, bounded transport

**Status:** Accepted

`max_tokens` is omitted unless the user supplies `--max-output-tokens`. This avoids blindly
cutting complete responses. Independent parser, event, tool-output, file, list, search, and log
limits protect memory and storage. Oversized actions are invalid rather than partially executed.

## D-008 — Metadata-led model profile

**Status:** Accepted with experimental edges

The worker reads exposed GGUF metadata, including architecture, name, quantization information,
and `tokenizer.chat_template`. The template and metadata select a Qwen/Hermes/natural adapter;
filename is only a supporting hint. Profile choice and warnings are logged.

The direct installed Python binding does not reliably expose per-request Qwen3.5
`enable_thinking`. The agent reports this instead of pretending thinking was disabled.

## D-009 — Visible reasoning is status, not evidence

**Status:** Accepted

When the backend exposes a separate reasoning stream, the console labels it `[thinking]` and
keeps the final stream separate. Reasoning is never parsed as permission, verification, or a tool
result. Models may omit, merge, or malformedly tag it.

## D-010 — Workspace containment and sensitive-file denial

**Status:** Accepted

All model paths are workspace-relative, canonically resolved, and rechecked. Absolute paths,
traversal, external symlink/junction targets, agent storage, VCS internals, model folders, log
folders, credential filenames, key stores, binaries, and oversized files are denied.

Why: “read-only” access can still leak secrets, and lexical prefix checks do not stop links.

## D-011 — One atomic file edit per action

**Status:** Accepted

`edit_file` supports:

- `create`: a new UTF-8 text file; never overwrite.
- `replace`: complete content for an existing UTF-8 file.
- `patch`: exact old/new text; the old text must occur exactly once.

All arguments and preconditions are checked first. Every real write has a checkpoint, original
and intended hashes, an approval preview, same-directory temporary content, bounded replacement
retries, post-write hashing, and a unified diff. Replacements first rename the verified old path
to a unique guard, verify that claimed content, and install the prepared file with a no-overwrite
hard link; a concurrent recreation becomes a conflict instead of being overwritten. One file per
action keeps rollback and evidence unambiguous.

Rejected: fuzzy patches, filename guessing, silent overwrite, and automatic batch edits.

## D-012 — Undo refuses to erase later user work

**Status:** Accepted

Undo restores the latest active checkpoint only when the current hash equals the recorded
post-edit hash. If a user or another process changed the file afterward, undo reports `CONFLICT`
and does nothing.

Rejected: `git reset --hard` and blind backup restoration.

## D-013 — Checks are allow-listed and approval-aware

**Status:** Accepted

The model selects only `build`, `test`, `lint`, or `typecheck`. Python syntax validation uses
`ast.parse` and is safe-auto. Python `unittest` and Java compilation use exact built-in argv
profiles; Java disables annotation processing and writes class output to agent temporary storage.
Executable checks ask for approval. Unsupported checks return `UNAVAILABLE`; commands are never
guessed from repository configuration.

Before and after subprocess checks, source manifests are compared. A source change becomes
`CONFLICT`, including after cancellation or timeout.

Why: build and test files are executable code, even when the command name sounds safe.

## D-014 — No arbitrary shell in the first slice

**Status:** Accepted

There is no shell tool. This intentionally limits tasks needing package installation, Git
history changes, custom project commands, or file deletion.

Why: one shell tool reduces model choice but creates a much larger quoting, environment, network,
destruction, and workspace-escape surface. It can be reconsidered only with a separate policy and
sandbox design.

## D-015 — Ask, auto, and dry-run modes

**Status:** Accepted

- `ask`: edit and executable checks require exact approval.
- `auto`: only explicitly trusted capabilities for the exact canonical workspace may skip
  approval; executable build/test checks still ask.
- `dry-run`: parse, validate, preview, log, and return observations without file or subprocess
  mutation.

Trust is stored in agent-owned user data. A repository cannot trust itself. Approval is bound to
the persisted normalized-action fingerprint; changed arguments invalidate it.

## D-016 — Append-only JSONL plus readable logs

**Status:** Accepted

Each run and session receives ordered JSONL events; important information is mirrored to a dated
text log. Raw responses and controller interpretations are separate. Critical requirements,
approval, checkpoint, mutation, interruption, and terminal events are flushed and fsynced.

Per-token durable events are disabled by default to avoid slowing local generation. Complete
responses, first-token latency, duration, parser, policy, tool, output, and verification are kept.

## D-017 — External state directory by default

**Status:** Accepted

Windows defaults to `%LOCALAPPDATA%/MyLLM/single_model_agent`; other systems use
`$XDG_STATE_HOME/myllm/single_model_agent` or `~/.local/state/...`. This prevents the model from
editing its own policy, trust, events, and snapshots. `--data-dir` can override it; a directory
inside the workspace is still denied to model tools and should be ignored by Git.

## D-018 — Archive before deterministic compaction

**Status:** Accepted

At approximately 72% of the active context, full messages are archived to text before the prompt
is shortened. The active prompt retains controller state plus at most three recent messages, each
bounded to 4,096 characters. Before/after estimates are logged and compaction must reduce the
active prompt. It does not ask the same model to summarize its own history.

Why: compaction must not lose tool/result pairs or fabricate state.

## D-019 — Resume only at durable boundaries

**Status:** Accepted with a conservative recovery model

Resume replays complete JSONL events, ignores only an incomplete final line, restores task
requirements and evidence, reconciles current hashes with original/intended checkpoint hashes,
and re-presents a pending exact approval. Unknown file state becomes `CONFLICT`; no uncertain edit
is repeated.

The resumed work receives a new run id linked through `resumed_from`, while retaining the same
conversation session.

## D-020 — Bounded no-progress handling

**Status:** Accepted

Failures are fingerprinted from canonical action and reason. A repeated identical invalid response
or failed strategy stops after two occurrences. Merely using the same tool twice is not failure;
new output, changed arguments, a successful read, a new compiler error, or a changed file counts as
progress.

Rejected: random/adaptive temperature as a recovery mechanism. It may be evaluated later with
evidence.

## D-021 — CLI is the first adapter, not the architecture

**Status:** Accepted

`main.py` is a small interactive adapter. Agent, provider, parser, storage, prompt, and tools have
no dependency on a web framework. A future browser UI can consume the same events and methods.

The broader repository prefers a browser experience, but adding a frontend before the safety and
event contracts stabilize would duplicate unstable behavior.

## D-022 — Production-grade means observable and fail-closed, not “finished forever”

**Status:** Accepted

The implementation aims for production-grade boundaries: no raw execution, durable evidence,
explicit authority, cancellation, recovery, and honest limitations. It is not yet certified for
hostile multi-tenant code, arbitrary commands, or unattended operation. Real-model and real-project
benchmarks remain required before a stable release label.

## D-023 — One action per response remains the safety boundary

**Status:** Accepted after real-model stress testing

The Snake experiment showed that Qwen3.5 naturally tried to return an edit, a build, and a final
answer together. The controller rejected the entire response instead of executing its first
apparently valid fragment.

Why:

- Approval and replay must bind to one normalized action.
- Partial execution would make the unexecuted remainder ambiguous model state.
- A later action may rely on an earlier action that failed.

Trade-off: a three-stage edit/build/finish task requires at least three slow inference calls.
Latency optimization must come from smaller prompts/provider improvements, not silent batching.

Rejected: execute the first recognizable action and ignore trailing model output.

## D-024 — Unlimited output is a user default, not an experiment requirement

**Status:** Accepted with operational guidance

Normal operation still omits an explicit output ceiling. Bounded experiments should set
`--max-output-tokens` explicitly so a weak model cannot consume unbounded validation time.

Trade-off:

- Unlimited output avoids truncating complete code.
- A ceiling makes benchmarks repeatable and contains repetitive thinking.
- Any reported benchmark must state the ceiling; it must not be mistaken for the product default.

The Snake experiment used `8192`. This did not prevent extremely high first-token latency because
prompt evaluation, not completion length, was the dominant cost.

## D-025 — Checks belong to a source version

**Status:** Accepted

A verified edit invalidates every recorded check result. `ChecksInvalidated` is critical/fsynced,
is replayed during resume, and changes the state to `STALE`. Completion requires a new passing
result for the latest source.

Why: `build passed → edit → finish` must never reuse evidence for older content.

Trade-off: correct evidence requires extra compiler/test turns, which is expensive for local
models but cannot be optimized away safely.

## D-026 — Compile evidence and semantic evidence are different

**Status:** Accepted

Java `javac` success proves language/type compatibility for the compiled source. It does not prove
that a game renders correctly, receives keys, grows correctly, or restarts safely. Final reporting
must label compiler evidence, static review, and interactive testing separately.

For the Snake experiment:

- The local-model run failed.
- The independently repaired artifact compiled without warnings.
- Independent static review passed.
- Interactive GUI play was not performed.

Trade-off: the first slice stays deterministic and does not add unrestricted GUI/process tools.
Semantic validators or supervised GUI smoke tests remain future experiments.

## D-027 — Experiment failures are first-class project knowledge

**Status:** Accepted

Every material real-model experiment should record configuration, prompt shape, run outcome,
timing, parser/tool/check behavior, discovered defects, code changes, tradeoffs, limitations, and
next hypotheses. A useful final artifact must not overwrite the historical fact that the original
agent run failed.

Raw logs are external state and may contain private source or secrets. They are not committed by
default. A sanitized report and evidence summary are durable repository artifacts.

Trade-off: summarized evidence is reviewable and safe to commit, but cannot answer every future
forensic question. Future formal benchmarks should preserve a redacted event/timing bundle.

## Deferred decisions

- Sandboxed arbitrary shell execution.
- File deletion and multi-file transactions.
- Git checkpoints/commits.
- Repository symbol map and dependency graph.
- Browser UI over streaming events.
- llama-server/OpenAI-compatible provider.
- Per-model prompt/profile registry beyond current metadata rules.
- Native tool schema submission to the provider.
- Parallel calls and multiple workspaces.
- Network access, dependency installation, and RAG.
- Automated benchmark suite and release thresholds.
