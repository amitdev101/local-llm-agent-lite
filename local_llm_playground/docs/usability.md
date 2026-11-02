# Chat behavior and settings scope

The three theme families and their light/dark appearances share one chat controller. Theme changes preserve drafts, response preferences, message actions, and expanded response sections.

## Keyboard and touch

| Action | Shortcut |
| --- | --- |
| Send the composer text | Enter |
| Insert a line break | Shift+Enter |
| Stop a running response | Ctrl+Shift+X on Windows/Linux; Cmd+Shift+X on macOS |
| Close an open popover or Settings | Escape |
| Move between controls | Tab / Shift+Tab |

Enter waits for IME composition to finish. Repeated Enter events and modified shortcuts do not submit extra messages. Ctrl+C and Cmd+C retain normal copying behavior. The Stop tooltip and Settings keyboard help show the stop shortcut.

Mobile controls have larger touch targets. Popovers and Settings scroll within the available viewport, including when an on-screen keyboard reduces its height. Sending opens the latest response; streaming leaves the scroll position alone when the user moves up to read older messages.

## Message actions

| Action | Result |
| --- | --- |
| Copy | Copies the full model response text, including reasoning if returned. Native text selection can copy a smaller portion. |
| Retry | Retries the latest failed/stopped response with its original prompt and response settings. Earlier attempts remain available, and the user prompt is not duplicated. |
| Regenerate in new chat | Creates a branch through the selected completed response's source prompt, using that turn's original response settings with the loaded model. The original answer and later turns stay in the original chat. |
| Continue | Adds a visible `Continue.` request after the latest completed response, using this chat's current response settings. |

Regenerate supports older completed answers and legacy chats. The branch appears in recent chats with an Alternative title and an explanatory notice. Failed branch creation leaves the original chat and draft intact. A failed generation inside the branch can be retried there.

Reasoning appears only when the model returns a separate thinking trace. Its section starts collapsed and keeps the user's expansion choice while streaming. **Response details** also starts collapsed and contains timing, speed, token counts, temperature, effective Thinking state, and the model that answered. Theme changes preserve expanded sections.

## Settings scope

| Controls | Scope and persistence |
| --- | --- |
| Appearance | This browser; independent of inference settings. |
| System instructions, Thinking preference, temperature, sampling, and seed | The next response in the current chat. Unsent edits are retained per chat in this browser; sent settings are stored with the conversation. A running response keeps its submitted snapshot. |
| Save as defaults | Explicitly saves response preferences on the local server for future new chats. Existing chats keep their preferences. |
| Use saved defaults | Copies saved response defaults into the current chat. It does not rewrite other chats or the saved defaults. |
| Model path, context, GPU layers, and threads | Apply after Load/Reload. The header, context meter, and generation use the actual loaded settings while edits are pending. Loading remembers these parameters for the next launch and leaves response defaults unchanged. |

Pending model changes show a Reload affordance and explanatory notice. Invalid values receive native field validation and a visible message before sending, loading, or saving defaults. Unfinished response values remain editable after returning to the chat. The server independently validates saved defaults and writes configuration atomically.

Per-chat unsent response settings use `playground.responseSettings.v1:<chatId>` in browser storage, with `new` for an unsent new chat. They are separate from draft text and appearance keys. If storage fails, edits remain in the open tab with a warning. Deleting a chat removes its local draft and response-preference entry.

## Verification

Run the commands in the README for controller and backend checks. These use simulated UI, network, and storage surfaces plus temporary chats and a fake model. They do not load a real GGUF or run a browser.

For manual review, check ChatGPT, Claude, and GitHub Copilot in both light and dark modes:

1. Load a model, enter a multiline prompt, send, stop, retry, and reopen the chat.
2. Copy text while a response streams; verify copying does not stop it. Try the documented Stop shortcut.
3. Compose with an IME and confirm Enter during composition does not send.
4. Regenerate an older answer; verify the branch uses the original prompt/settings and the source chat and draft remain available.
5. Expand reasoning and response details, switch themes, and confirm the sections remain expanded.
6. Edit settings in two chats, switch between them, and refresh. Save defaults and verify a fresh new chat uses them while existing chats retain their values.
7. Change context/GPU/threads and verify the header and context meter retain loaded values until Reload.
8. Repeat with a narrow viewport, keyboard navigation, and browser zoom; check focus, scrolling, touch targets, and readable labels.
