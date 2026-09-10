# 🧠 MyLLM Dual-Model Experiment

This folder contains an experimental local coding agent that uses two GGUF
models with different responsibilities:

- 👷 **Worker — Qwen3 4B:** chooses and performs the next coding action.
- 👶 **Kid — Qwen3 1.7B:** reviews the latest evidence and decides whether to
  continue or finish.
- 🧭 **Router — Qwen3 1.7B:** sends ordinary conversation to chat and project
  work to the agent loop.
- 🛡️ **Python controller:** validates model output, restricts filesystem access,
  executes tools, tracks edits, and enforces completion rules.


The experiment exists to learn what small local models can do reliably without
changing the production agent in `myllm.py`.

## 📁 Files

- `myllm_dual_model_test.py` — models, router, parsers, and agent loop.
- `myllm_dual_model_prompts.py` — Router, Chat, Kid, and Worker prompts.
- `myllm_dual_model_tools.py` — tool documentation, implementations, safety,
  undo, and checks.
- `tests/test_dual_model_roles.py` — deterministic checks and real-model
  integration tests.
- `tests/evaluate_temperatures.py` — opt-in real-model temperature comparison;
  it is intentionally excluded from normal test discovery.
- `tests/evaluate_game_generation_temperatures.py` — opt-in Java 8 Snake and
  Tic-Tac-Toe generation benchmark.
- `tests/evaluate_chat_draft_before_agent.py` — A/B benchmark for the
  no-system-prompt chat-draft handoff idea.
- `tests/temperature_benchmark_common.py` — shared sampling, completion, and
  JSONL helpers for both temperature benchmarks.
- `myllm_dual_model_test_01.py` — an older experimental snapshot; it is not the
  active runner.

Runtime data remains at the repository root:

- `.dualagent.log` — complete model and controller output.
- `.myllm/router_feedback.jsonl` — confirmed router corrections.
- `agent_test_workspace/` — isolated project workspace used by the experiment.

## 🔄 Current flow

```text
User request
     ↓
Router: CHAT or TOOL
     ↓
CHAT ───────────────→ normal streamed response
     
TOOL
     ↓
Worker thinks and returns one Markdown tool action
     ↓
Python parses, validates, and executes the action
     ↓
Kid reviews the result and returns continue or done
     ↓
Python enforces edit and verification revisions
```

## 🧭 Decision journey

Each step below records the starting point, observed problem, experiment,
evidence, and resulting decision.


### 1. One coding prompt handled every request

- **Initially:** every message entered the full coding-agent prompt and tool
  loop.
- **Problem:** normal conversation became slower and worse because the model had
  to process project instructions and structured output rules unnecessarily.
- **Tried:** a tiny first-stage router that returns only `CHAT` or `TOOL`.
- **Confirmed:** greetings and general questions worked better through plain
  chat, while explicit project requests could still reach the agent.
- **Changed:** normal chat now avoids the coding prompt, tools, and project
  context entirely.

### 2. Router output was kept deliberately small

- **Initially:** structured JSON was considered for routing.
- **Problem:** JSON added tokens and formatting failure opportunities to a
  binary decision.
- **Tried:** plain `CHAT` or `TOOL`, temperature `0.1`, and `/no_think`.
- **Confirmed:** the model can usually make this narrow classification without
  a schema.
- **Changed:** the router accepts only those two labels. Ambiguous output falls
  back to `CHAT` because unclear intent should not authorize project changes.

### 3. Short follow-ups exposed router weakness

- **Initially:** every route was based only on the current message.
- **Problem:** messages such as `continue`, `do it`, or `fix that` lack enough
  meaning by themselves.
- **Tried:** a short recent-route history and explicit `/route chat` or
  `/route tool` corrections.
- **Confirmed:** recent context helps without sending the complete conversation.
- **Changed:** confirmed corrections are stored as append-only JSONL and up to
  three relevant examples may be shown to the router.
- **Boundary:** this is retrieval of confirmed examples, not RAG, fine-tuning,
  or automatic weight updates.

### 4. One model both acted and judged itself

- **Initially:** one agent was responsible for choosing tools and deciding it
  had finished.
- **Problem:** the same model could accept its own incomplete work.
- **Tried:** split responsibilities between a Worker and a smaller Kid reviewer.
- **Confirmed:** the Kid often catches failed tools and compiler errors and can
  give the Worker one short next instruction.
- **Changed:** the loop is Worker-first: one action, one controller observation,
  then one Kid decision.

### 5. Forced JSON interfered with natural reasoning

- **Initially:** Kid and Worker generation was constrained directly to JSON.
- **Problem:** Qwen models are trained to reason naturally, and forcing JSON
  from the first token reduced reasoning quality and caused malformed actions.
- **Tried:** allow `<think>...</think>` followed by a final JSON object.
- **Confirmed:** natural reasoning improved tool decisions, but JSON still
  struggled with large source code, escaping, and unfinished objects.
- **Changed at that stage:** JSON was parsed only after thinking, with a second
  constrained formatting call when necessary.
- **Later decision:** this JSON finalizer was removed in Step 10.

### 6. Error output was disappearing

- **Initially:** some exceptions appeared only in the terminal.
- **Problem:** compiler errors and tracebacks could be lost after interruption
  or restart.
- **Tried:** persist output before printing it to the console.
- **Confirmed:** Java compiler diagnostics and full Python tracebacks appeared
  in `.dualagent.log`.
- **Changed:** raw model output, controller observations, compiler output, and
  failures are logged with timestamps.

### 7. Java Snake experiments exposed loop problems

- **Initially:** the Worker could write Java and call `compile_java`.
- **Problems observed:**
  - generated code referenced missing methods;
  - placeholder comments were submitted instead of complete source;
  - the Kid sometimes accepted incomplete game behavior;
  - a successful write could be mistaken for successful verification.
- **Tried:** placeholder rejection, complete compiler output, and mandatory
  compilation after Java changes.
- **Confirmed:** the Worker could recover from a concrete compiler error and
  eventually produce compiling Java.
- **Changed:** Java output is compiled into `.build`, compiler failures return
  as evidence, and Java 8 requests use Java 8 source/target compatibility.

### 8. Too many similar tools confuse small models

- **Initially:** the production tool registry had more than thirty choices,
  including overlapping search, verification, write, and check operations.
- **Problem:** small models spread probability across familiar near-synonyms and
  select the wrong operation.
- **Tried:** a compact, general model-facing tool set.
- **Confirmed:** distinct safety behaviors should remain distinct, but internal
  controller helpers do not need to appear as model choices.
- **Changed in this experiment:** the Worker sees only nine tools:

  - `list_files`
  - `search`
  - `read_file`
  - `write_file`
  - `patch_file`
  - `move_path`
  - `delete_path`
  - `undo_last_edit`
  - `run_check`

- **Decision:** `final` is not a tool. The Kid and controller own completion.

### 9. Fewer tools required stronger controller safety

- **Initially:** separate tool names carried some safety meaning, such as create
  versus replace.
- **Problem:** merging names could create ambiguous or unsafe behavior.
- **Tried:** keep simple model calls while moving complexity into Python.
- **Confirmed:** fewer choices are safe only when the controller validates exact
  semantics.
- **Changed:** the controller now provides:
  - workspace-bound path resolution;
  - symlink and junction blocking;
  - protected-directory blocking;
  - read-before-write enforcement;
  - content hashes that reject stale writes;
  - atomic writes and reversible edits;
  - non-recursive deletion with approval;
  - bounded reads, searches, and listings;
  - repeated-action detection;
  - checks selected from the detected source type.

### 10. JSON tool actions were replaced with Markdown

- **Initially:** the Worker returned `{tool, args, message}` JSON and the Kid
  returned `{status, request}` JSON.
- **Problem:** source code required escaped newlines, quotes, and backslashes.
  Invalid JSON also caused an extra model call for repair.
- **Tried:** natural reasoning followed by a small Markdown footer and ordinary
  fenced code.
- **Confirmed:** both original Qwen models produced parseable Markdown, and code
  content was preserved without JSON escaping.
- **Changed:** Worker actions now look like:

  ````text
  ## TOOL write_file
  path: HelloWorld.java
  ```java
  public class HelloWorld {
  }
  ```
  ````

- **Changed:** Kid decisions now look like:

  ```text
  ## DECISION continue
  request: Run the project check.
  ```

- **Safety:** Python executes nothing unless the exact footer, fields, tool name,
  and fenced blocks pass deterministic validation.

### 11. Explicit output token limits were removed

- **Initially:** Kid and Worker generation used a small `max_tokens` value.
- **Problem:** thinking or source generation could end before the closing tag or
  final action.
- **Tried:** remove explicit generation-token limits.
- **Confirmed:** later responses closed their thinking and Markdown actions
  without truncation.
- **Changed:** Kid, Worker, Router, and Chat calls have no explicit output-token
  cap. Their configured context windows remain because every model requires a
  finite context capacity.
- **Trade-off:** trusting the model to finish can increase response time when it
  reasons excessively.

### 12. Tool and prompt code was separated

- **Initially:** routing, prompts, parsing, tools, and safety lived in one large
  experiment file.
- **Problem:** the file became difficult to read and tool documentation could
  drift away from tool behavior.
- **Tried:** separate files by responsibility.
- **Confirmed:** the runner can import one tool contract and one prompt module
  without changing runtime behavior.
- **Changed:**
  - tools and `TOOL_DOCS` moved to `myllm_dual_model_tools.py`;
  - prompts moved to `myllm_dual_model_prompts.py`;
  - the runner retains orchestration and deterministic Markdown parsing;
  - all dual-model code moved into this folder.

### 13. Real-model tests replaced assumptions

- **Initially:** syntax checks and manual runs were the main validation.
- **Problem:** mocked output cannot reveal how the actual 1.7B and 4B models
  interpret prompts.
- **Tried:** integration tests that load the original GGUF models with no mocks
  and no generation-token limit.
- **Confirmed:** the models reliably handled the basic Markdown write, read,
  completion, and rejection cases.
- **Changed:** the test suite now covers:
  - four Kid model decisions;
  - four Worker tool decisions;
  - four deterministic Markdown parser cases;
  - three filesystem safety and undo cases.
- **Result:** all 15 tests passed in the final full run.

### 14. Harder tests revealed two small-model weaknesses

- **Initially:** the Kid received event history as prose and the Worker received
  the general rule `use run_check after changes`.
- **Problems confirmed by failing tests:**
  - the Kid repeatedly treated a successful check before a newer edit as valid;
  - the Worker selected `read_file` when explicitly asked to validate a completed
    write.
- **Tried first:** stronger natural-language warnings.
- **Confirmed:** wording alone was not consistently enough for the 1.7B Kid; it
  understood the facts but rationalized an exception.
- **Changed for the Kid:** the controller now supplies:

  ```text
  EDIT REVISION: 2
  VERIFIED REVISION: 1
  ```

  The prompt gives one direct rule: when edit revision is greater, continue and
  request `run_check`.

- **Changed for the Worker:** validation words after a successful edit map
  directly to `run_check`; reading is explicitly not validation.
- **Changed for repeatability:** real-model tests use seed `42`. Production
  sampling and temperatures are unchanged.
- **Confirmed:** both previously failing cases passed, followed by the complete
  15-test suite.

### 15. Temperature became an evaluated setting

- **Initially:** Kid used `0.15` and Worker used `0.25` without a comparative
  benchmark.
- **Problem:** one successful seeded run cannot show whether a temperature is
  consistently better for tool selection, completion judgment, or efficiency.
- **Changed:** an opt-in benchmark runs held-out scenarios over role-specific
  temperature ranges and multiple fixed seeds while keeping `top_p`, `top_k`,
  and `min_p` unchanged.
- **Evidence recorded:** parse success, expected decision or tool, critical
  failures, duration, token usage, actual parsed output, and raw response are
  written to append-only JSONL after every run.
- **Decision rule:** critical failures rank worst; otherwise prefer the highest
  pass rate, followed by lower latency and fewer completion tokens.
- **Boundary:** the benchmark reports the best observed value. It does not
  automatically change runtime temperatures.
- **Pilot:** the current Kid setting (`0.15`, seed `42`) passed three of four
  held-out scenarios but incorrectly accepted a stale verification as `done`.
  This is a baseline only; the full temperature and seed sweep is still needed.

### 16. Complete game generation became a separate benchmark

- **Goal:** compare how Worker temperature affects substantial source
  generation rather than only short tool choices.
- **Changed:** a separate benchmark asks the original Worker model to generate
  complete Java 8 console versions of Snake and Tic-Tac-Toe across four
  temperatures and three seeds.
- **Validation:** every candidate must parse as `write_file`, use the expected
  filename, compile with Java 8 settings, and satisfy game-specific static
  feature checks.
- **Detailed evidence:** an event JSONL log and readable text log retain the
  environment, complete prompts, model metadata, sampling settings, raw model
  response, parsed action, generated source, hashes, compiler command and
  output, feature checks, timings, token usage, errors, and tracebacks.
- **Safety:** source is compiled with annotation processing disabled inside an
  isolated results folder. Generated games are never executed.
- **Boundary:** feature checks provide comparable signals; they do not prove
  that a game is enjoyable or semantically perfect.
- **Pilot:** Tic-Tac-Toe at Worker temperature `0.25`, seed `42`, produced a
  correctly parsed `write_file` action and six of seven initially detected
  features, but failed Java compilation by calling `Scanner.nextLine()` as a
  static method. Generation took about 181 seconds and 978 completion tokens.

### 17. Chat-first drafting became an A/B experiment

- **Idea:** before code mode, ask the Worker model for a complete solution in
  natural chat without a system prompt, then give that response to Worker and
  Kid as context.
- **Baseline:** Worker receives the same game task directly through the coding
  prompt; Kid reviews the generated source and controller evidence.
- **Chat-first path:** the Worker model first produces an unconstrained natural
  draft at temperature `0.7`; Worker converts or corrects it at `0.25`; Kid
  reviews the same draft, resulting source, compilation, and feature evidence
  at `0.15`.
- **Comparison:** both paths use the same game, seed, compiler, feature checks,
  and controller completion rule. Results compare compilation, source score,
  feature coverage, Kid accuracy, total time, and total tokens.
- **Detailed evidence:** every prompt, draft, thinking trace, response, parsed
  action/decision, generated file, compiler result, error, and timing is written
  to the same detailed JSONL and readable-log format as the game benchmark.
- **Boundary:** model output is reference material, never trusted evidence or
  automatically executed code.

## ✅ What is confirmed

- A tiny plain-text router is preferable to JSON for `CHAT` versus `TOOL`.
- Normal chat should never receive the coding-agent prompt.
- Markdown fences are a better source-code transport than JSON strings.
- Small models perform better with one concrete next action.
- Explicit state such as revision numbers works better than prose chronology.
- Compiler and tool results must be evidence, not model assumptions.
- Safety and completion invariants must be enforced by Python.
- Tests using the real local models expose failures that parser-only tests miss.
- Fixed seeds are necessary for repeatable model integration tests.

## 🚧 What remains experimental

- Mutation-required tasks can still theoretically reach `done` with zero edits
  if the Kid makes a wrong decision. The next controller improvement should
  classify whether the original goal requires a change and reject `done` when
  `EDIT REVISION` is still zero.
- The router uses examples and recent routes but remains a small-model
  classifier; user correction is still necessary.
- `run_check` currently supports simple Java compilation and Python syntax.
  Project-specific test, lint, and typecheck discovery remains limited.
- `move_path` supports files and empty directories only.
- Model paths are currently configured for this Windows development machine.
- Removing output limits avoids truncation but does not guarantee fast or concise
  reasoning.

## 🧪 Run the tests

From the repository root:

```powershell
python -m unittest discover -s dual_model_test\tests -v
```

The suite loads both original GGUF models and may take several minutes.

## 🌡️ Compare temperatures

Run the complete comparison explicitly:

```powershell
python dual_model_test\tests\evaluate_temperatures.py
```

Run one role or a smaller custom comparison:

```powershell
python dual_model_test\tests\evaluate_temperatures.py --role kid
python dual_model_test\tests\evaluate_temperatures.py --role worker --worker-temperatures 0.25,0.4,0.6 --seeds 11,42,73
```

Completed results are preserved in `temperature_results/` as JSONL, including
when the run is interrupted with `Ctrl+C`. The benchmark has no explicit model
output-token limit and does not run during normal test discovery.

### Generate complete games

Run Snake and Tic-Tac-Toe across all configured temperatures and seeds:

```powershell
python dual_model_test\tests\evaluate_game_generation_temperatures.py
```

Run a smaller comparison first:

```powershell
python dual_model_test\tests\evaluate_game_generation_temperatures.py --game snake --temperatures 0.25,0.6 --seeds 42
```

Each run creates one folder under `temperature_results/` containing:

- `events.jsonl` — append-only machine-readable event details;
- `run.log.txt` — the same events in readable form;
- `summary.json` — comparison totals and best observed temperature;
- `generated/` — every generated Java candidate and compiler output folder.

Scoring gives 10 points for parsing, 10 for the correct action, 40 for Java 8
compilation, and 40 for game-specific feature coverage. Review the source and
logs before treating the highest score as the final temperature choice.

### Compare direct coding with chat-first drafting

Run the A/B experiment for both games with seed `42`:

```powershell
python dual_model_test\tests\evaluate_chat_draft_before_agent.py
```

Run only one game or add more seeds:

```powershell
python dual_model_test\tests\evaluate_chat_draft_before_agent.py --game tic-tac-toe
python dual_model_test\tests\evaluate_chat_draft_before_agent.py --seeds 11,42,73
```

The experiment is opt-in and excluded from normal test discovery. It uses the
original local models, has no explicit output-token limit, and never runs the
generated games.

## ▶️ Run the experiment

From the repository root:

```powershell
python dual_model_test\myllm_dual_model_test.py
```

Useful commands inside the experiment:

```text
/route chat
/route tool
/exit
```

## 🧱 Boundaries

- No RAG or embeddings.
- No online weight updates or fine-tuning.
- No automatic learning from unconfirmed router decisions.
- No unrestricted shell tool.
- No recursive deletion.
- No access outside `agent_test_workspace`.
- No promotion into `myllm.py` until the experiment is consistently reliable.
