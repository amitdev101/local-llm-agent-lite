# MyLLM Research Notes

This folder records evidence and decisions that should survive individual experiments.
It is intentionally separate from the product README and the implementation progress log.

## Tool calling with small local models

### Goal

Make local models perform coding work reliably without assuming frontier-model instruction
following. The controller must understand common model output formats while remaining
deterministic, safe, and easy to inspect.

The current reference model is `Qwen3.5-9B-Q4_K_M.gguf`, running locally through
`llama-cpp-python` on Windows. The same principles should apply to other GGUF models.

## What our experiments established

### Original JSON-only contract

The coding agent originally required exactly:

```json
{"tool":"<tool_name>","args":{}}
```

This was too brittle for Qwen3.5. The model understood the task and selected a sensible
operation, but naturally emitted reasoning before the action and sometimes left an unmatched
`</think>` tag.

Observed response shape:

```text
I need to create a Java file...
</think>

{"tool":"create_file","args":{"path":"SnakeGame.java","content":"..."}}
```

The original parser checked whether the cleaned response started with `{`. It therefore
classified the entire response as a final answer, accepted completion because no mutation had
been recorded, and ended at step 1 without creating a file.

Evidence:

- `myllm_logs/2026-09-10/myllm-2026-09-10-225105-426.log.txt`
- `myllm_logs/2026-09-16/myllm-2026-09-16-232755-716.log.txt`
- `myllm_logs/2026-09-16/myllm-2026-09-16-233706-688.log.txt`

### Logging gap

The raw model response was logged, but the controller did not log its parsing decision or loop
exit. Diagnostics were added for:

```text
🧩 Parsed response: TOOL (<name>)
🧩 Parsed response: FINAL
🧩 Parsed response: INVALID
✅ Completion accepted: <reason>
🏁 Agent loop ended at step <n>/<maximum>
```

This made the controller failure visible instead of making it appear that the model had simply
answered incorrectly.

### Tolerant JSON extraction

A deterministic JSON decoder was added to find a complete tool object after reasoning text.
This fixed premature completion but exposed subsequent model behavior:

```text
Step 1: create_file with incomplete JSON -> INVALID
Step 2: write_file with complete JSON -> extracted, then rejected as unknown tool
Step 3: {"tool":"list_tools"} -> args missing under the old strict envelope
```

Evidence:

- `myllm_logs/2026-09-17/myllm-2026-09-17-185104-209.log.txt`

Important result: parsing and tool validation are different responsibilities. Successfully
extracting a call does not mean its tool name, arguments, permissions, or intent are valid.

### Natural action protocol

Small models are more comfortable producing normal text and code fences than deeply escaped
JSON containing entire source files. The preferred protocol was changed to:

```text
ACTION: <tool_name>
<argument>: <value>
```

Long values use ordinary fenced blocks:

````text
ACTION: create_file
path: SnakeGame.java
content:
```java
// complete source
```
````

The first real Qwen3.5 run with this protocol produced:

```text
ACTION: list_tools
```

It was parsed as `format=natural` and executed successfully. This was the first clean tool call
in the snake-game experiments.

On the next step Qwen produced:

```text
ACTION: create_files
[{"path":"SnakeGame.java","content":"..."}]
```

It omitted the `files:` label but otherwise used an unambiguous representation based on the
tool documentation. The parser now accepts a complete JSON array directly after
`ACTION: create_files` as the `files` argument. Malformed arrays or trailing text remain invalid.

Evidence:

- `myllm_logs/2026-09-17/myllm-2026-09-17-193939-022.log.txt`

This final array form has not yet been rerun through a complete real-model generation.

## Current parser behavior

Implementation: `myllm_tool_parser.py`

Accepted inputs:

1. Natural `ACTION:` or `TOOL:` blocks with `key: value` arguments.
2. Fenced multiline argument values.
3. A complete array after `ACTION: create_files`.
4. Qwen/Hermes `<tool_call>...</tool_call>` blocks.
5. Legacy JSON using `tool`/`args` or `name`/`arguments`.
6. Plain text final responses when no explicit tool shape is detected.

Explicit compatibility aliases:

| Model output | Canonical tool |
|---|---|
| `create_file` | `create_files` |
| `write_file` | `create_files` |
| `edit_file` | `replace_file` |
| `patch_file` | `apply_patch` |
| `run_tests` | `run_project_tests` |
| `run_build` | `run_project_build` |
| `run_lint` | `run_project_lint` |
| `run_typecheck` | `run_project_typecheck` |

`file` and `filename` are normalized to `path`.

Aliases are intentionally explicit. There must be no fuzzy or edit-distance matching for tool
names. An unknown name must remain unknown.

## Safety boundary

The parser only translates syntax into a canonical action. It does not authorize execution.

The existing controller must still enforce:

- Exact canonical tool lookup.
- Required and allowed arguments.
- Workspace path restrictions.
- Source and technology constraints.
- Destructive-action policy.
- Progress and repetition checks.
- Verification requirements after mutations.

A structurally valid call can still select the wrong tool, wrong file, or incorrect arguments.
Parsing success is not semantic correctness and is never permission.

## How established implementations handle tool calls

The common architecture is not one highly permissive parser. It is a group of narrow adapters
that all produce one strict internal representation:

```text
Model response
      ↓
model/format adapter
      ↓
canonical ToolCall(name, arguments, source_format)
      ↓
schema validation
      ↓
permissions and workspace policy
      ↓
execution
```

### Approaches and trade-offs

| Approach | Advantages | Limitations |
|---|---|---|
| Native model chat template | Best alignment with training; tool schemas and markers use the model's expected format | Template and model-version sensitive; wrappers may expose calls as raw text |
| Qwen/Hermes `<tool_call>` | Strong boundaries; supports reasoning separately from the call | Tags may be malformed, missing, or emitted inside thinking |
| JSON Schema or GBNF | Guarantees structural validity and can restrict legal tool names | Does not guarantee the correct intent or values; can reduce small-model accuracy |
| Natural action block | Human-readable; easy for small models; avoids escaping source code | Must use explicit markers and be aware of code fences |
| Bounded tolerant parser | Handles known, observed model variations cheaply | Becomes dangerous if it starts guessing or scanning arbitrary prose |
| Validation and repair | Invalid calls never execute and simple mistakes can be corrected | Costs another generation and can become a loop |
| Two-pass reason then package | Keeps natural reasoning while constraining only the action | Doubles inference work; unsuitable for a 9B CPU-first experience |
| Streaming boundary parser | Can stop after one complete action and never expose partial arguments | Requires a quote-, escape-, fence-, and nesting-aware state machine |

## External findings

### Qwen guidance

Qwen recommends Hermes-style function calling for Qwen3. In thinking mode it generates a
thought first and then one or more function calls. Qwen-Agent converts these into structured
`function_call` messages containing a name and JSON arguments.

- [Qwen3 function-calling guide](https://github.com/QwenLM/Qwen3/blob/main/docs/source/framework/function_call.md)
- [Qwen3 tool-calling concepts](https://github.com/QwenLM/Qwen3/blob/main/docs/source/getting_started/concepts.md#tool-calling)

Qwen also warns against ReAct-style stopword detection for reasoning models because apparent
tool markers can occur inside thinking.

### Qwen3.5 and thinking

Other users have reproduced Qwen3.5 tool calls appearing inside or after problematic thinking
content. One Qwen3.5 9B report found that disabling thinking allowed consecutive tool calls to
work reliably.

- [Qwen3.5 9B thinking/tool-call issue](https://github.com/ggml-org/llama.cpp/issues/20837)
- [Qwen3.5 prefix-text parser issue](https://github.com/ggml-org/llama.cpp/issues/21158)

This matches our unmatched `</think>` and prefixed-action logs. Disabling thinking only for tool
turns remains a strong experiment, while ordinary chat may retain thinking.

### llama.cpp parsing

llama.cpp uses template-specific PEG parsers and normalizes content, reasoning, and tool calls
into common internal shapes. It supports direct JSON arguments and tagged formats rather than
assuming all models emit the same syntax.

- [llama.cpp parser architecture](https://github.com/ggml-org/llama.cpp/blob/master/docs/development/parsing.md)
- [llama.cpp function calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

Tool support depends on the model's chat template. A GGUF containing a template is not proof that
the current wrapper parses every model-specific output correctly.

### llama-cpp-python

`llama-cpp-python` supports `tools`, `tool_choice`, JSON mode, and JSON Schema mode, but its
documented function-calling paths do not guarantee automatic parsing for every Qwen GGUF.

- [llama-cpp-python function calling](https://github.com/abetlen/llama-cpp-python#function-calling)
- [llama-cpp-python structured output](https://github.com/abetlen/llama-cpp-python#json-and-json-schema-mode)
- [Raw Qwen tool-call parsing report](https://github.com/abetlen/llama-cpp-python/issues/1784)

The project's installed `llama-cpp-python` version is `0.3.35`. Its direct
`create_chat_completion` signature does not expose per-request chat-template keyword arguments;
supporting `enable_thinking=false` may require a wrapped chat handler, server mode, or dependency
upgrade.

### Constrained decoding

JSON Schema and grammar-constrained decoding solve syntax, not judgment. Benchmarks show that
schema validity can improve while a small model's executable or semantic accuracy decreases.

- [llama.cpp grammar guide](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
- [JSONSchemaBench](https://arxiv.org/abs/2501.10868)
- [Grammar-constrained decoding](https://arxiv.org/abs/2305.13971)
- [Constraint Tax for small models](https://arxiv.org/abs/2605.26128)

Use constraints selectively with small, shallow schemas. Do not treat a valid schema as proof of
a safe or correct action.

### Streaming

Partial tool arguments must remain buffered until a complete boundary and successful parse.
Incomplete fragments must never reach the executor or be saved as completed tool history.

- [Partial Qwen call poisoning conversation history](https://github.com/ggml-org/llama.cpp/issues/21771)
- [llama.cpp streaming tool-call issue](https://github.com/ggml-org/llama.cpp/issues/22722)

## Recommended design for MyLLM

### Parsing order

1. Structured API `tool_calls`, when the backend genuinely provides it.
2. Complete native `<tool_call>...</tool_call>` content.
3. Exact natural `ACTION:` block outside code fences.
4. Complete JSON only when it is the visible response or enclosed by an explicit tool marker.
5. Otherwise treat the output as a final response.

Every accepted representation should become the same canonical action dictionary before tool
validation.

### Near-term improvements

1. Make natural marker detection code-fence aware. An `ACTION:` line inside generated source must
   not become a tool call.
2. Stop scanning unrestricted prose for arbitrary tool-shaped JSON. Accept it only at a known
   boundary.
3. Keep the alias table explicit, small, and biased toward safe failure. For example,
   `write_file → create_files` cannot overwrite an existing file.
4. On rejection, retain only the tool name, concise error, argument keys, and payload reference.
   Do not add thousands of invalid source characters back into context.
5. Return one exact repair instruction and allow only a small retry budget.
6. Buffer streamed actions until their closing boundary. Later, stop inference once one complete
   action is available.
7. Experiment with thinking disabled only for tool turns before adopting a two-pass design.
8. Evaluate native Qwen/Hermes tool calling against the natural protocol using the same tasks.

### Not recommended now

- A second model call for every action: too slow on CPU.
- A broad grammar covering reasoning, final answers, every tool, and source payloads: complex and
  likely to hurt generation speed or semantics.
- Fuzzy tool-name matching: ambiguous and unsafe.
- One universal chat template for all GGUF models.
- Executing the first JSON object found in a response.
- Regex-only streaming JSON extraction.
- Unlimited automatic repair attempts.
- Treating parser flexibility as authorization flexibility.

## Evaluation plan

Test each parser/model adapter with:

- No-tool final response.
- One no-argument tool call.
- One call with scalar arguments.
- File creation with fenced source.
- File creation with a direct `files` array.
- Nested arrays and objects.
- Escaped quotes and braces inside source strings.
- `ACTION:` and `<tool_call>` text inside generated code.
- Malformed JSON and unclosed tags/fences.
- Missing arguments and unknown fields.
- Unknown and aliased tool names.
- A stream split at different positions inside a tool call.
- Tool output containing instruction-like text.
- Write, delete, and shell operations requiring policy checks.
- Qwen model variants and quantization levels.

Measure:

- Correct tool-selection rate.
- Correct argument rate.
- False-positive execution rate.
- Parse and repair rate.
- Steps to completion.
- First-token and total latency.
- Tokens per second.
- Context growth caused by failed actions.

JSON parse success alone is not a sufficient agent metric.

## Current decision

Keep the natural action protocol because it produced a clean real Qwen3.5 `list_tools` call and
is easy to inspect. Keep native tagged and JSON formats as bounded compatibility adapters. Tighten
format boundaries before adding more accepted variations. The controller remains the source of
truth for tool names, schemas, permissions, progress, and verification.
