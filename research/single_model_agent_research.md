# Production Single-Model Coding Agent Research

## Purpose

This document consolidates parallel research into Aider, Hermes/Qwen tool calling,
OpenHands, SWE-agent, smolagents, Continue, PydanticAI, and the local MyLLM experiments.
It is the evidence base for `single_model_agent/`.

## Shared conclusion

A reliable coding agent is not a chat loop with a permissive parser. It is a sequence of
deterministic boundaries around a probabilistic model:

```text
request → context → model → protocol adapter → canonical action
        → schema → policy → dry run → approval → execution
        → observation → verification → checkpoint/final
```

Raw model text must never execute directly.

## Research streams

### Aider

Aider's strongest patterns are:

- Model profiles select edit formats, temperature, reasoning behavior, streaming, context
  limits, and prompt placement.
- A repository map supplies compact paths, symbols, signatures, and dependency relevance
  instead of dumping an entire repository into context.
- New and small files work well with whole-file output.
- Existing files work better with bounded search/replace blocks when the model can reproduce
  exact context.
- Edit application is deterministic and returns targeted repair feedback.
- Source changes are followed by lint/build/test feedback.
- Git and undo provide recovery boundaries.
- Old history is summarized while recent work and unresolved requirements are preserved.
- Benchmarks evaluate the full pipeline: edit-format compliance, file mutation, and tests—not
  text quality alone.

Primary sources:

- [Aider repository map](https://aider.chat/docs/repomap.html)
- [Aider edit formats](https://aider.chat/docs/more/edit-formats.html)
- [Aider edit-block implementation](https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_coder.py)
- [Aider model settings](https://github.com/Aider-AI/aider/blob/main/aider/models.py)
- [Aider lint/test loop](https://aider.chat/docs/usage/lint-test.html)
- [Aider Git integration](https://aider.chat/docs/git.html)
- [Aider history summarization](https://github.com/Aider-AI/aider/blob/main/aider/history.py)
- [Aider benchmark](https://github.com/Aider-AI/aider/tree/main/benchmark)

Do not copy blindly:

- unrestricted or `shell=True` execution
- inherited provider secrets in child processes
- repository configuration that can execute commands without trust approval
- automatic commits that surprise users
- every edit format and every model integration in the first implementation

### Hermes and Qwen

Qwen3 officially recommends Hermes-style tool calling. Qwen3 and Qwen3.5 do not emit exactly
the same native inner format.

Qwen3 commonly emits:

```xml
<tool_call>
{"name":"<tool>","arguments":{"<key>":"<value>"}}
</tool_call>
```

Qwen3.5 may emit tagged functions and parameters:

```xml
<tool_call>
<function=<tool>>
<parameter=<key>><value></parameter>
</function>
</tool_call>
```

Tool results belong in a corresponding tool-response message. Tool call and result groups must
remain together through history persistence and context compaction.

Important Qwen3.5 findings:

- Thinking is enabled by default.
- Thinking/tool parsing has known missing-tag and prefix-text failures in llama.cpp.
- Grammar enforcement may not work while thinking is enabled.
- Disabling thinking improved consecutive tool calls in a Qwen3.5 9B report.
- Long prompts and large tool lists reduce reliable tool selection.
- Filename alone is not a safe way to identify a model protocol; inspect GGUF metadata and its
  embedded `tokenizer.chat_template`.

Primary sources:

- [Qwen3 function calling](https://github.com/QwenLM/Qwen3/blob/main/docs/source/framework/function_call.md)
- [Qwen3.5 model card](https://huggingface.co/Qwen/Qwen3.5-9B)
- [Qwen3.5 chat template](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/chat_template.jinja)
- [Hermes function calling](https://github.com/NousResearch/Hermes-Function-Calling)
- [Hermes agent architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)
- [llama.cpp function calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
- [llama.cpp parser architecture](https://github.com/ggml-org/llama.cpp/blob/master/docs/development/parsing.md)
- [Qwen3.5 9B thinking/tool issue](https://github.com/ggml-org/llama.cpp/issues/20837)
- [Missing thinking close issue](https://github.com/ggml-org/llama.cpp/issues/21118)
- [Grammar with thinking issue](https://github.com/ggml-org/llama.cpp/issues/20345)

### Other production agents

OpenHands contributes an event-driven, atomic `step()` model. Every action and observation is
an event, pending confirmations are explicit, interruption is a state transition, and context
condensation operates on durable history.

SWE-agent demonstrates that the Agent-Computer Interface matters as much as the model:

- show bounded file slices
- return concise search results
- lint before accepting edits
- convert empty output into explicit success
- save complete trajectories for replay and evaluation

smolagents separates parser, memory, and execution, and uses bounded retries. Continue treats
tool permission as policy rather than parsing. PydanticAI provides useful patterns for typed
validation, request/tool limits, and reason-specific retries.

Primary sources:

- [OpenHands agent architecture](https://docs.openhands.dev/sdk/arch/agent)
- [SWE-agent architecture](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/architecture.md)
- [SWE-agent ACI](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md)
- [SWE-agent trajectories](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md)
- [smolagents concepts](https://github.com/huggingface/smolagents/blob/main/docs/source/en/conceptual_guides/intro_agents.md)
- [Continue agent mode](https://docs.continue.dev/features/agent/how-it-works)
- [Continue permissions](https://docs.continue.dev/ide-extensions/agent/how-to-customize)
- [PydanticAI retries](https://pydantic.dev/docs/ai/core-concepts/retries/)
- [PydanticAI usage limits](https://pydantic.dev/docs/ai/core-concepts/agent/)

## Architecture decisions supported by research

### Canonical results

Every protocol adapter must produce one of:

```text
ToolCall(name, arguments, source_format, raw_summary)
FinalMessage(content)
InvalidResponse(reason, raw_summary)
```

### Event model

Minimum durable events:

```text
RunStarted
UserMessage
ContextPrepared
ModelStarted
ModelToken/ModelResponse
ActionParsed/ParseFailed
PolicyAllowed/PolicyDenied/ApprovalRequired
ToolStarted/ToolOutput/ToolFailed
CheckpointCreated
VerificationStarted/VerificationResult
ContextArchived/ContextCompacted
RunInterrupted/RunCompleted/RunFailed
```

JSONL should be the append-only machine record. A readable text log should mirror important
events. Raw responses should be stored, but controller interpretation must always be logged too.

### Initial tools

Expose very few model-facing tools:

```text
list_files(path=".", depth=2)
search(query, path=".")
read_file(path, start_line=1, end_line=200)
edit_file(operation, path, content/search/replace)
run_check(kind)
finish(message)
```

The executor may have internal helper functions, but the model should not choose among near
synonyms. Delete and unrestricted shell execution are not part of the first production slice.

### File editing

- New file: full fenced content.
- Existing file: exact search/replace.
- All requested mutations are parsed and validated before any write.
- Paths must resolve inside the workspace.
- Create must not overwrite.
- Search text must match exactly once.
- Snapshot the previous state before mutation.
- Failed validation must make no change.
- Verification follows every mutation.

### Permission tiers

| Risk | Examples | Default |
|---|---|---|
| Low | list, search, read, status | automatic |
| Medium | create/edit within workspace, configured checks | automatic in trusted workspace; otherwise approval |
| High | delete, network, Git history changes, arbitrary shell | approval; not in first slice |
| Forbidden | path escape, credential access, destructive system command | block |

### Context

Use tiers:

```text
always: rules, goal, current plan/state
small: workspace tree and relevant symbols
targeted: requested/discovered file slices
temporary: latest observations and compiler errors
archive: full older history and large outputs
```

Compact before roughly 70–75% of the active context. Before compaction, persist full history.
Keep the goal, unresolved requirements, files changed, failed strategies, latest verification,
and complete tool-call/result groups. Remove old large tool output first.

### Completion

`finish` is a request, not proof. The controller verifies:

- requested artifacts exist
- intended mutations occurred
- paths stayed inside the workspace
- relevant syntax/build/test status is known
- no pending approval or action remains

### Interruption

Use explicit states:

```text
IDLE
PLANNING
WAITING_FOR_APPROVAL
RUNNING
INTERRUPTING
INTERRUPTED
COMPLETED
FAILED
```

Stop must set a cancellation signal, stop scheduling new tools, terminate a running subprocess
tree, close model streaming when supported, append `RunInterrupted`, and preserve completed
events and checkpoints.

## Planning and dry-run requirements

Before implementation, the plan must dry-run:

- normal chat/final response
- list/search/read
- create a new Java file
- patch an existing file
- malformed natural action
- incomplete JSON/XML/fence
- unknown tool and alias
- missing/extra arguments
- path traversal and absolute path escape
- existing-file overwrite attempt
- ambiguous search/replace
- command timeout and failed check
- premature finish
- repeated identical failure
- cancellation during model streaming and tool execution
- context archive and compaction
- resume from durable events

The dry run must state the expected canonical result, policy result, filesystem impact,
observation, verification, and final run state for each scenario.

## Curious-kid questions

The verifier should ask, for every planned behavior:

- What exactly will happen?
- Which file can change?
- Why is it allowed?
- What happens when the model forgets a field?
- What happens when output stops halfway?
- Can words inside source code accidentally become a command?
- Can the path leave the project?
- Can an existing file be silently overwritten?
- How is the change undone?
- How do we know the task is actually finished?
- What survives a crash or Ctrl+C?
- Can the user understand the log without reading raw model text?

## Initial production slice

Build only:

- one direct local `llama-cpp-python` provider
- one loaded GGUF at a time
- model profiles and protocol adapters
- one workspace
- six model-facing tools
- JSONL events plus text logs
- snapshots and undo for agent mutations
- dry-run and approval modes
- explicit cancellation state
- context accounting, archive, and compaction
- deterministic finish verification

Defer:

- multiple providers/models in one run
- RAG/vector databases
- network tools
- unrestricted shell
- automatic Git commits
- parallel tool calls
- distributed/cloud runtime
- fuzzy tool or path matching
- a second planner/editor model

## Success metrics

Measure the full pipeline:

- correct tool selection
- correct arguments
- parse/repair rate
- false-positive execution rate
- unsafe-action rejection rate
- correct file selection
- edit application rate
- syntax/build result
- retries and steps
- first-token and total latency
- context growth
- interruption success
- recovery/undo success

Valid JSON alone is not success.
