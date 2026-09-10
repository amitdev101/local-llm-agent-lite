# 🤖 Models

- 📁 Place `.gguf` files in this folder; MyLLM detects them on the next start.
- 🧪 `Qwen3.5-9B-Q4_K_M.gguf` remains the primary coding-agent test model.
- ⚠️ Larger models need more memory and usually respond more slowly on CPU.


## ⭐ Which model should I choose?

Ratings are relative to the models listed here and assume CPU-only use. More
stars are better, except in the RAM column.

| Model | System RAM | Coding | General chat | Tools | Speed | Recommended coding config | Best use |
|---|---:|:---:|:---:|:---:|:---:|---|---|
| FunctionGemma 270M Q8 | 4 GB+ | ⭐ | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | Use embedded defaults; benchmark first | Function-call experiments |
| Qwen3 1.7B Q8 | 6 GB+ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | `Think · T 0.6 · P 0.95 · K 20 · Min 0` | Fast chat, Router, Kid |
| Qwen3 4B Q4 | 8 GB+ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | `Think · T 0.6 · P 0.95 · K 20 · Min 0` | **Best default for most users** |
| Qwen3.5 9B Q4 | 16 GB+ | ⭐⭐⭐⭐⭐* | ⭐⭐⭐⭐⭐* | ⭐⭐⭐⭐⭐* | ⭐⭐ | `Think · T 0.6 · P 0.95 · K 20 · Min 0 · Pres 0 · Repeat 1` | Higher-quality experiments |

`T` = temperature, `P` = top-p, `K` = top-k, `Min` = min-p,
`Pres` = presence penalty, and `Repeat` = repetition penalty.

- 👍 **Unsure?** Choose `Qwen3-4B-Q4_K_M.gguf`.
- ⚡ **Need faster responses?** Choose `Qwen3-1.7B-Q8_0.gguf`.
- 🧠 **Want the strongest candidate?** Try Qwen3.5 9B only after confirming
  runtime compatibility and available memory.
- 🔧 **Testing function calling only?** Use FunctionGemma; it is not intended
  to replace a general coding/chat model.
- 📝 `*` Qwen3.5 ratings and configuration are based on its published precise
  coding guidance, not results from this repository. Benchmark it locally
  before relying on them.

## 🧪 Optional model to evaluate

- 🆕 `Qwen3.5-9B-Q4_K_M.gguf` — stronger 9B candidate for coding and agent
  experiments.
- 💾 The Q4_K_M GGUF is approximately 5.63 GB.
- 📥 The file is listed for evaluation but is not currently present in this
  folder.
- ⚙️ Qwen3.5 uses the newer `qwen35` architecture. Verify that the installed
  `llama-cpp-python` build supports it before making it the default model.

## 🔗 Downloads

- [Qwen3.5 9B Q4_K_M GGUF](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF?show_file_info=Qwen3.5-9B-Q4_K_M.gguf)
- [Qwen3 4B Q4_K_M GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true)
- [Qwen3 1.7B GGUF](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF)
- [FunctionGemma 270M GGUF](https://huggingface.co/unsloth/functiongemma-270m-it-GGUF?show_file_info=functiongemma-270m-it-Q8_0.gguf)

## 💬 Chat templates

- ✅ The installed Qwen3 GGUF files contain the native Qwen3 chat template.
- 🧠 Their template supports system/user/assistant roles, `<think>`,
  `enable_thinking`, native tool calls, and tool responses.
- 🔧 `myllm.py` does not force `chat_format`; `llama-cpp-python` uses the
  template embedded in each GGUF.
- 🆕 Qwen3.5 has a different architecture and template; do not force the Qwen3
  or FunctionGemma format when evaluating it.
- 🧠 Qwen3.5 thinks by default and does not officially support Qwen3's `/think`
  and `/nothink` text switches; use the runtime's `enable_thinking` setting
  when it is supported.
- ⚠️ FunctionGemma also embeds its own template.
- 🔍 Temporarily use `verbose=True` while loading a model to confirm the
  selected chat format.

## 📚 References

- [Qwen3.5 9B model card](https://huggingface.co/Qwen/Qwen3.5-9B)
- [llama-cpp-python chat-format selection](https://github.com/abetlen/llama-cpp-python#chat-completion)
- [Qwen3 model documentation](https://huggingface.co/Qwen/Qwen3-1.7B)
- [Qwen llama.cpp usage](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html)
