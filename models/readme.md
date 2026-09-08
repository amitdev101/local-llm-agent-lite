# Models

- 📁 Place `.gguf` model files here; MyLLM detects them on the next start.
- 🧪 The primary test model is `Qwen3-4B-Q4_K_M.gguf`.
- 🔗 [Download Qwen3 4B GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true)
- 🔗 [Qwen3 1.7B GGUF](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF)
- 🔗 [FunctionGemma 270M GGUF](https://huggingface.co/unsloth/functiongemma-270m-it-GGUF?show_file_info=functiongemma-270m-it-Q8_0.gguf)

## Chat templates

- ✅ Both bundled Qwen3 GGUF files contain the native Qwen3 chat template.
- 🧠 Their template supports system/user/assistant roles, `<think>`, `enable_thinking`, native tool calls, and tool responses.
- 🔧 `myllm.py` does not force a `chat_format`; `llama-cpp-python` therefore uses the template embedded in each GGUF.
- ⚠️ FunctionGemma embeds a different template. Do not force the Qwen format when using it.
- 🔍 Set `verbose=True` temporarily while loading a model to print the selected chat format.

References:

- [llama-cpp-python chat-format selection](https://github.com/abetlen/llama-cpp-python#chat-completion)
- [Qwen3 model documentation](https://huggingface.co/Qwen/Qwen3-1.7B)
- [Qwen3 llama.cpp usage](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html)
