# Single-Model Agent Implementation Plan

## Status

Research and plan-level dry runs are complete. Implementation may begin only after the
curious-kid review passes.

## Scope

Build a production-oriented, provider-independent agent core with one initial local
`llama-cpp-python` provider, one GGUF model, one workspace, six model-facing tools,
deterministic policy and verification, streaming, cancellation, approvals, snapshots,
undo, durable JSONL events, readable logs, session resume, context archiving, and
compaction.

Before execution, the planning response is normalized into a `TaskRequirements` record:

```text
user_goal
task_kind: conversational | read_only | mutation
expected_paths
required_operations
required_checks
accepted_limitations
requires_workspace_evidence
```

The controller records a conservative preliminary contract from the request. It is not presented
as a full user-approved semantic plan. In `ask` mode, each mutation/check is accepted through its
exact preview; that accepted action adds concrete paths and evidence to the contract. In `auto`
mode, the user’s capability-scoped workspace trust supplies that authority. The evolving stored
record—not later model claims—is the completion contract.

Deferred: RAG, network tools, arbitrary shell, delete, automatic Git commits, parallel
tool calls, cloud execution, multiple models/providers in one run, fuzzy matching, and a
second planning model.

## Files

- `main.py`: the only user-facing entry point and interactive console.
- `agent.py`: state machine and deterministic orchestration loop.
- `provider.py`: provider interface, GGUF metadata/profile selection, and llama.cpp adapter.
- `protocol.py`: canonical results and strict protocol adapters.
- `tools.py`: six tool schemas, workspace policy, execution, checks, snapshots, and undo.
- `storage.py`: events, sessions, logs, archives, snapshots, and replay.
- `prompts.py`: compact prompt, tool documentation, observations, and repair prompts.
- `README.md`: complete product, usage, architecture, safety, and operations guide.
- `DECISIONS.md`: decisions, evidence, trade-offs, rejected alternatives, and review record.

## Public boundaries

```python
class ModelProvider(Protocol):
    def stream(self, messages, request, cancel_event): ...
    def metadata(self): ...

class SingleModelAgent:
    def run(self, user_message: str): ...
    def resume(self, run_id: str): ...
    def stop(self): ...
    def approve(self, approval_id: str): ...
    def reject(self, approval_id: str): ...
    def undo_last_checkpoint(self, run_id: str): ...
```

The UI must not know model syntax, and the provider must not execute tools.

## Canonical response types

Every complete response becomes exactly one of:

```text
ToolCall(name, arguments, source_format, raw_summary)
FinalMessage(content, source_format)
InvalidResponse(reason, source_format, raw_summary)
```

Only a validated `ToolCall` can reach policy. Only a policy-approved call can reach an
executor. Raw response text is never executable.

## Six model-facing tools

### `list_files(path=".", depth=2)`

- Both arguments optional; maximum depth is 5.
- Workspace-relative paths only.
- Bounded, sorted results; read-only and automatically allowed.

### `search(query, path=".")`

- `query` required; `path` optional.
- Literal search initially, with bounded file/line/snippet results.
- Hidden storage, binary, ignored, and oversized files are skipped.
- Read-only and automatically allowed.

### `read_file(path, start_line=1, end_line=200)`

- `path` required; line numbers optional.
- Text files only, bounded line range and output size.
- Read-only and automatically allowed.

### `edit_file(operation, path, content/old_text/new_text)`

- `create`: missing file plus `content`; never overwrites.
- `replace`: existing file plus complete `content`.
- `patch`: existing file plus `old_text` and `new_text`; exactly one match required.
- Validate every field and path before mutation.
- Snapshot before writing and prepare a same-directory temporary file. For replacement, claim the
  verified old path under a unique guard and install via a no-overwrite hard link; concurrent
  recreation is a conflict, never an overwrite.
- One file per call initially so approval, rollback, and evidence stay simple.

### `run_check(kind)`

- `kind` is exactly `build`, `test`, `lint`, or `typecheck`.
- The controller selects an argv list from a detected project profile.
- The model cannot supply commands, executables, shell fragments, or a working directory.
- Use `shell=False`, the detected canonical project root, a scrubbed environment, timeout, output cap, and process-tree
  cancellation.
- Never install dependencies or use a network fallback.
- Initial Build & Check Profiles are built-in allow-listed argv templates. Repository files may
  help detect a project type but never supply an executable command.
- Automatic selection requires exactly one ready candidate. Return `AMBIGUOUS`, `INVALID_CONFIG`,
  or `CHECK_UNAVAILABLE` rather than guessing or silently falling back.
- Initial built-in checks redirect generated output to agent temporary storage when supported.
  Compile-only profiles proven not to execute project code may run automatically in `auto` mode;
  tests and project-controlled build systems always require approval.
- Record a source-file manifest before and after every check. Report unexpected source changes
  and mark verification conflicted; never describe a check as read-only merely because its
  requested purpose was verification.

### `finish(message)`

- This is a completion request, not proof of completion.
- The controller checks requested artifacts, mutation evidence, relevant checks, pending
  approvals, unresolved blockers, edit verification, and workspace containment.

## Parser precedence

1. Provider-native structured `tool_calls`.
2. One complete native `<tool_call>...</tool_call>` block.
3. One exact `ACTION:` block outside Markdown fences.
4. One complete JSON object only at an explicitly configured response boundary.
5. `FINAL:` or ordinary plain text.

Rules:

- Buffer streamed content; never parse or execute a partial response.
- Do not scan arbitrary prose for JSON.
- Do not recognize action markers inside source fences.
- Do not fuzzy-match tools or fields.
- Treat malformed tool-shaped output as invalid, not as final text.
- Reject trailing executable-looking content and multiple actions.
- Preserve the selected format and a bounded raw summary in the event log.
- Known aliases may normalize to the six tools; unknown names remain errors.
- A JSON boundary means provider-native structured arguments, the entire visible response being
  exactly one JSON object, or one JSON object inside a complete native tool-call block. No other
  JSON extraction is allowed.

Supported compatibility aliases:

```text
create_file/write_file -> edit_file(create)
replace_file           -> edit_file(replace)
patch_file             -> edit_file(patch)
run_tests              -> run_check(test)
run_build              -> run_check(build)
run_lint               -> run_check(lint)
```

Alias normalization is typed. It adds only its declared fixed operation/kind, requires the
canonical fields, rejects conflicting fixed fields, and rejects unknown or extra fields. It
never drops arguments silently.

## Model profile

The provider records name, architecture, context size, sampling defaults, optional output
limit, parser, thinking capability/control, native-tool support, quantization, runtime
version, and chat-template source. Inspect GGUF metadata and `tokenizer.chat_template`;
filename matching is only a warned fallback.

Defaults:

- No output-token cap. Omit `max_tokens` when the value is `None` or `0`.
- User overrides are explicit and logged.
- Qwen3.5 starts with a 32768-token profile and 0.6 temperature unless metadata or user
  configuration says otherwise.
- Disable thinking for tool turns only when the provider can confirm the control was applied.
- Never claim a thinking mode was applied when the runtime cannot support it.
- Unknown profiles use the explicit natural protocol, not guessed native syntax.

## Prompt

Keep it short: goal, workspace, current state, six compact tool definitions, required versus
optional fields, one natural action example, one final example, one-action rule, path rules,
verification rule, and untrusted-observation rule. Do not request hidden chain of thought, dump
the repository, repeat examples, or add a `list_tools` round trip.

Tool output is delimited as untrusted evidence. It can inform the next action but cannot alter
system rules, permission, workspace, or completion criteria.

## State machine and step

```text
IDLE -> PLANNING -> RUNNING -> WAITING_FOR_APPROVAL
                    |   |              |
                    |   +-> COMPLETED  +-> RUNNING/FAILED
                    +-> INTERRUPTING -> INTERRUPTED
                    +-> FAILED
```

Each step is atomic: assemble context, call model, finish streaming, parse, validate schema,
apply policy, optionally wait for approval, execute, observe, verify, persist, and decide the
next state. Bounded retry counters exist for parse, schema, tool, and identical failures.

Inference runs in a dedicated provider worker process. The main process owns agent state and
cancellation. Stop first requests cooperative cancellation; after a short grace period it
terminates the provider worker and marks it for a clean model reload. No partial streamed response
is parsed. This makes interruption real on Windows without loading a second model copy.

## Events and logging

Every durable JSONL event contains schema version, event id, parent id, run/session ids,
sequence, UTC timestamp, workspace, model, step, type, and payload. Minimum events:

```text
RunStarted UserMessage ContextPrepared ModelStarted ModelResponse
ActionParsed ParseFailed SchemaRejected PolicyAllowed PolicyDenied
ApprovalRequired ApprovalGranted ApprovalRejected ToolStarted ToolOutput ToolFailed
CheckpointCreated EditApplied EditRolledBack VerificationStarted VerificationResult
ContextArchived ContextCompacted RunInterrupted RunCompleted RunFailed
```

Raw model output and controller interpretation are separate events. Logs include parser format,
canonical result, schema/policy outcome, argument names and redacted summary, approval, duration,
verification, and terminal reason. A readable text log mirrors important events. Flush every
event immediately and tolerate only an incomplete final JSONL line after a crash. Critical
permission and mutation events additionally call `flush()` and `os.fsync()` before their state
transition is treated as durable. Token text streams to the UI, but per-token JSONL events are
debug-only; normal logs keep first-token timing, periodic counters, and the complete response.
`ModelToken` therefore exists only as an optional debug event, not a minimum durable event.

Bound independently from model generation: buffered response bytes, tool output, individual
event size, readable log entry, and archive entry. Exceeding a transport/storage bound produces
an explicit truncated or invalid status; it never executes a partial action. Raw output is
redacted for known credential patterns, child environments exclude provider secrets, and full
content is replaced by hashes/bounded summaries when it is unnecessary for replay.

## Policy and approval

Layers are parser -> schema -> path/workspace -> task constraint -> risk -> approval -> executor.

- Low risk: list/search/read; automatic.
- Medium risk: workspace edit; ask in `ask` mode, automatic only for its granted capability in
  an exact trusted `auto` workspace, and simulated in `dry-run`.
- `run_check` requires approval by default. In `auto` mode it may be automatic only when its built-in profile is
  explicitly classified as unable to execute arbitrary/project code, mutate source, or use the
  network, and it still has timeout/output limits. Compile-only build/lint/typecheck profiles can
  meet that classification; tests and project-controlled build systems cannot.
- High risk: delete, network, Git history changes, arbitrary shell; unavailable initially.
- Forbidden: path escape, system destruction, credential access, binary overwrite; always block.

An approval shows the normalized tool, path, diff/content summary or exact check argv and cwd,
risk reason, and approve/reject choices. Approval cannot change the normalized action.

Before waiting, persist `ApprovalRequired`, the normalized action, its hash/fingerprint, and the
displayed diff/command summary. Include the edit target's pre-hash or the check's source-manifest
hash in that fingerprint and revalidate it immediately before execution. Approval executes that
exact persisted fingerprint without reparsing model text. Any argument, path, content, command,
workspace, target hash, or check input change invalidates it.

Workspace trust is an explicit controller record stored in agent-owned storage and excluded from
model tools. It binds canonical root path and root identity hash to user-granted capabilities and
time. Repositories cannot grant or modify their own trust. `auto` applies only to the exact trusted
root and listed capabilities; executable repository code remains approval-only.

## Paths and filesystem

Resolve against the canonical workspace root, reject absolute paths and traversal outside it,
and re-check resolved parents to stop symlink/junction escapes. Agent storage is excluded from
workspace tools. Create parent directories only as part of an approved create. Reject devices,
binary input/output, invalid Windows names, and unsupported encodings. No validation failure
may change disk state.

Read tools also exclude agent state, VCS internals, model files, common secret files, credential
stores, binaries, generated directories, and paths resolving through an external link. A denied
sensitive read is a policy result, not an empty file. Limits apply to traversal count, file size,
line slice, match count, and returned bytes.

## Checkpoints and undo

Before an edit, record path, existence, original bytes/hash, intended hash, and backup. After
an atomic write, record final hash and unified diff. A failed post-write verification keeps the
checkpoint and status. An ambiguous/post-write mismatch becomes `CONFLICT`; it never restores over
possible concurrent user work. Undo restores only the last agent checkpoint and refuses when the
current hash no longer matches the checkpoint's post-edit hash,
preventing loss of later user changes. No `git reset --hard` or automatic commit is used.

On Windows, replacement retries only a small fixed number of times for transient sharing errors.
It never deletes the destination first. The original backup remains until the final hash is
verified. A permission, link, concurrent-change, or exhausted-sharing failure emits the exact
stage, preserves the checkpoint, and stops without attempting a different file operation.

Every mutation journal records original hash, intended hash, checkpoint id, write-started,
write-completed, and verification status. On resume, compare the current hash with both known
hashes: intended means recover `EditApplied` and continue verification; original means the write
did not complete and may be reconsidered from the safe boundary; anything else is `CONFLICT` and
requires the user. Never repeat or overwrite an unknown state automatically.

## Cancellation

Stop sets a cancellation event, prevents new work, stops consuming the stream, discards partial
action text, terminates the dedicated provider worker after a cooperative grace period, terminates
a running check process tree, persists `RunInterrupted`, and leaves a resumable safe boundary.
Atomic writes prevent partially written files. The next model request starts a clean worker and
reloads the model if termination was required.

## Persistence and resume

Runtime data defaults to `./single_model_agent_data`, relative to the launch directory, with `logs/`,
`runs/`, `sessions/`, `snapshots/`, and `archives/`. A user may override it, but a location inside
the workspace is ignored by scans, denied to model-facing tools, and must be Git-ignored.
Reconstruct state by replaying complete events. An unterminated run
becomes interrupted. Never resume inside a model stream, tool execution, or file write. Restore a
pending approval exactly, including its normalized fingerprint. Verify checkpoint and touched-file
hashes before resuming mutation work.

Persist and fsync controller-derived `TaskRequirements` before any executable step. Do not label
the heuristic record as user-accepted; only explicit action approvals or stored trust grant
authority.

## Context and compaction

Count with the model tokenizer when possible and a clearly logged estimate otherwise. Archive and
compact at about 72% of active context, before hard overflow. First persist a readable full-history
snapshot. Preserve goal, unresolved requirements, plan/state, changed files and hashes, failed
strategies, latest verification, recent conversation, pending approval, and whole tool-call/result
groups. Remove old large tool output first. Never split a tool call from its result.

Initial compaction is deterministic and extractive: retain state/evidence records and recent
message groups, replace old verbose observations with typed summaries (tool, target, outcome,
hash/exit code, failure cause), and archive the originals. The model does not summarize its own
history in the first version.

## Verification and completion

After edits, verify existence and expected hash, detect the project, and run only an available,
configured relevant check. Record when no deterministic check exists. Existence, content, syntax,
build, behavior, and tests are distinct evidence levels. `finish` is accepted only when the
controller has enough evidence for the user's actual request.

Check evidence uses explicit statuses: `PASSED`, `FAILED`, `TIMED_OUT`, `CANCELLED`,
`UNAVAILABLE`, and `NOT_RUN`. `UNAVAILABLE` never satisfies a required check.

Ordinary final text is accepted only for a conversational/read-only contract whose requirements
are satisfied. For a mutation contract it is treated as a premature completion request and the
agent remains `RUNNING` unless the stored requirements and deterministic evidence already prove
completion.

A project/file read-only contract requires at least one successful workspace observation
(`list_files`, `search`, or `read_file`); prose without evidence cannot satisfy it.

## Dry-run matrix

| Scenario | Canonical/controller outcome | Policy and disk effect | Verification and next state |
|---|---|---|---|
| Normal final | `FinalMessage` | No tool, no disk effect | Complete only a satisfied conversational/read-only contract; otherwise repair/continue |
| List/search/read | Valid `ToolCall` | Auto allow, read-only | Bounded observation, continue |
| Create Java file | `edit_file(create)` | Ask/auto; checkpoint then atomic new file | Hash/existence and configured build; continue |
| Patch existing file | `edit_file(patch)` | Ask/auto; exact one-match mutation | Diff/hash and relevant check; continue/repair |
| Malformed action | `InvalidResponse` | Block; no effect | One bounded repair, then `FAILED` |
| Incomplete JSON/XML/fence | `InvalidResponse` after stream ends | Block; no effect | Log incomplete boundary; retry/fail |
| Unknown tool | Schema rejection | Block; no effect | Name valid tools; bounded retry |
| Known alias | Canonical six-tool call | Normal policy | Log alias and continue |
| Missing/extra field | Schema rejection | Block; no effect | Exact field error; bounded retry |
| Traversal/absolute escape | Valid parse, policy denied | Forbidden; no effect | Log resolved boundary; fail |
| Symlink/junction escape | Valid parse, path denied | Forbidden; no effect | Log canonical target; fail |
| Create over existing | Valid parse, executor precondition rejected | No effect | Ask model for explicit replace if task permits |
| Ambiguous patch | Valid parse, precondition rejected | No effect | Report match count; read/revise |
| Failed check | `run_check` | Allow-listed argv with approval when code executes; compare source manifests | Return exit/output and unexpected changes; repair/conflict loop |
| Check timeout | `run_check` | Kill process tree, then compare source manifest; never assume no mutation | `TIMED_OUT` if unchanged, otherwise `CONFLICT` and user review |
| Premature finish | `finish` request | Completion rejected; no effect | State missing evidence; remain `RUNNING` |
| Repeated identical failure | Same normalized fingerprint | Stop at budget; no effect | `FAILED` or wait for user |
| Cancel model stream | No completed action | Cancellation wins; no effect | Mark partial raw text; `INTERRUPTED` |
| Cancel check | Active process is killed | No intended source mutation | Record termination; `INTERRUPTED` |
| Compact | Controller event | Write archive only | Record counts/preserved groups; continue |
| Resume | Replay complete events and reconcile hashes | Never repeat an uncertain mutation | Restore safe state/approval or enter `CONFLICT` |
| Nested JSON values | Preserve only if schema allows | No effect until approved | Reject rather than guess |
| Quotes/braces in fenced source | Content stays opaque | Normal edit policy | Diff/hash evidence |
| `ACTION:` in source fence | Not an action | No effect | Fence-aware parse result |
| `<tool_call>` inside source | Not a call when inside action content | No accidental execution | Adapter boundary logged |
| Split stream before close | No result yet | No effect | Keep buffering or cancel |
| Complete call plus trailing action | Invalid/multiple action | Block; no effect | Repair/fail |
| Tool output gives instructions | Untrusted observation | Cannot affect policy | Continue under original rules |
| Delete/shell request | Capability unavailable | Block; no effect | Explain limitation |
| Qwen tagged call | Native adapter `ToolCall` | Normal policy | Log adapter; continue |
| Dangling Qwen thinking | Invalid if action boundary ambiguous | Block; no effect | Warn and repair |
| User rejects | Approval rejection | No effect | Fail or await new instruction |
| User approves | Exact pending fingerprint allowed | Checkpoint then execute | Observe/verify; continue |
| Crash after checkpoint | Replay detects unfinished run | Do not repeat edit | Verify hashes; interrupt/resume decision |
| Incomplete JSONL tail | Ignore only final partial line | No speculative action | Warn; resume prior complete event |

## Implementation order

1. Freeze canonical data contracts and event schema.
2. Implement strict parser adapters and schemas.
3. Implement canonical workspace/path guard.
4. Implement read-only tools and bounded observations.
5. Implement checkpoints, atomic edit, guarded undo, and dry-run diffs.
6. Implement hard-coded `run_check` argv profiles, source manifests, and process cancellation.
7. Implement event store, text logs, replay, sessions, and archives.
8. Implement the provider worker boundary, GGUF profile inspection, streaming, and cancellation.
9. Implement compact prompts and context accounting/compaction.
10. Implement the state machine, approvals, retries, progress fingerprinting, and finish gate.
11. Implement the console entry point and status commands.
12. Run the dry-run matrix without loading a model; then do small real-model smoke scenarios.
13. Record results and remaining risks in `DECISIONS.md` and `README.md`.

## Success measures

Track tool/argument correctness, parse and repair rates, false execution, unsafe/path rejection,
edit application, ambiguous-patch rejection, checks, premature-finish rejection, repeated-failure
termination, steps, first-token/total latency, throughput, context growth/compactions,
cancellation latency, resume, undo, and event completeness. Valid syntax alone is not success.

## Known risks before implementation

- Cooperative llama.cpp cancellation inside the worker may be slow; bounded process termination
  is the fallback and must also terminate descendants without leaving an orphan.
- Per-request Qwen thinking control may be unavailable in the installed Python binding.
- GGUF metadata/template exposure varies by binding and model.
- Windows junction and subprocess-tree behavior require deliberate handling.
- A single model may plan and verify its own mistake; controller checks must remain independent.
- No unrestricted shell or delete means some coding tasks will intentionally stop and explain the
  missing capability instead of silently broadening authority.
- Resolve allow-listed executables from trusted installed locations rather than blindly trusting
  `PATH`, terminate Windows descendants, and clean provider/check temporary files after stopping.
- An unavailable required check blocks completion unless the user explicitly records that exact
  gap in `accepted_limitations`; the log and final response must still call it unverified.
