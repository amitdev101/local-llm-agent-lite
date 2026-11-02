# Model selection and Thinking support

Choose a model in the composer or Settings, then press **Load**. Selecting a filename prepares the choice; loading starts only when you press a Load button. The model menu shows the selected model's full path, its loading state, and the currently loaded model when these differ. **Send** becomes available when the selected model is loaded and the runtime is ready.

A custom GGUF path in Settings takes priority over the detected-model list. The composer menu displays that path and offers **Choose a detected model** to return to the list.

Failed loads retain an error message in both model menus and Settings. If the previous model remains loaded, selecting it again lets you continue. If loading fails after unloading the runtime, the interface clears the old loaded name. Model loading and generation share a backend lock, and generation requests identify the expected model so a change from another browser tab cannot silently send the prompt to a different model.

If the runtime is busy after a connection interruption or an action in another tab, its status is refreshed until it is ready. This refresh leaves drafts and conversation content intact. A failed status refresh shows a connection error and makes model selection available again.

Response metrics include the model that produced the answer. Recovering an already completed answer keeps that answer's original model and metrics.

Context, GPU layers, and thread edits show a pending Reload state. The header, context meter, and requests keep using the runtime's loaded settings until Reload succeeds. Loading remembers model parameters independently of response defaults; see [settings scope](usability.md#settings-scope).

## Thinking states

Thinking stays visible in every theme and appearance mode:

| Configured support | Composer state | Behavior |
| --- | --- | --- |
| `supported` | On or Off | Both Thinking controls are enabled and synchronized. The request uses `/think` or `/no_think`. |
| `unsupported` | Unavailable | The controls are disabled. The app adds no Thinking directive. |
| `unknown` | Unknown | The controls are disabled while support is unverified. The app adds no Thinking directive. |
| No model selected | Unavailable | The help text asks the user to select a model. |

The saved On/Off preference is retained when selecting an unsupported or unverified model and restored when selecting a supported model. A model may still produce reasoning according to its own defaults when the app's Thinking control is unavailable. The support setting describes compatibility with the playground's soft switch.

## Configuration

Edit [`../model_capabilities.json`](../model_capabilities.json). It is a flat map from exact GGUF filenames to one of the three support values. Filename matching ignores case; entries also apply to a custom path with that same filename. There are no family-name guesses or wildcard rules.

```json
{
  "Qwen3-1.7B-Q8_0.gguf": "supported",
  "Qwen3-4B-Q4_K_M.gguf": "supported",
  "my-plain-model.gguf": "unsupported",
  "my-unverified-model.gguf": "unknown"
}
```

Mark a model `supported` after verifying that its chat template and model support the `/think` and `/no_think` soft switch. Other reasoning-control protocols need their own implementation before using this setting. The bundled entries configure the original Qwen3 1.7B and 4B files using their documented soft switch: [Qwen3 1.7B model card](https://huggingface.co/Qwen/Qwen3-1.7B), [Qwen3 4B model card](https://huggingface.co/Qwen/Qwen3-4B).

Refresh the browser after editing the file. Missing entries, invalid values, or an unreadable/malformed file produce `unknown`. The server checks the loaded model's configuration again for generation and records the effective state in response metrics. Saved preferences retain the user's requested mode.

## Manual review

- Select a different model: verify that loading does not start and Send asks you to load it.
- Load it: verify that Loaded appears, the ready dot appears, and Send becomes available.
- Select an unconfigured model: verify that Thinking shows Unknown while remaining visible.
- Return to a configured model: verify that the previous On/Off preference returns.
- Try an invalid custom path: verify that the draft and error details remain available.
- Check light and dark appearances, keyboard navigation, and a narrow viewport.
