# Customizing themes

Choose a family and light/dark appearance in **Settings → Appearance**. The browser remembers them under `playground.appearance.v1`. Appearance never changes inference settings or saved conversations.

The composer uses grouped controls in both empty and active chats. **Options** opens system instructions, temperature, and advanced sampling; the compact **Thinking** control stays visible beside it. Supported models show On/Off, and unsupported/unconfigured models show Unavailable/Unknown with explanatory help. Both controls share one saved preference and apply it to the next supported response. The model control shows a readable name and ready indicator; its menu separates selection from explicit loading and retains the full path, current loaded name, load errors, and a link to Settings. The context percentage opens exact or estimated token usage, with warnings as it fills.

Options temporarily moves the existing Settings sections into a native popover and restores them when it closes. Inputs, values, and handlers are reused. Popovers close with Escape or an outside click; opening Settings closes them and restores all sections before focusing the dialog. Shared controls use each theme's colors and dimensions. Override `options`, `responseOptions`, `thinkingShort`, `thinkingMode`, `thinkingOn`, `thinkingOff`, `thinkingHelp`, and the context wording in a theme's `text` map.

Model and Thinking state wording is also configurable through the default text map in `theme.js`, including `selectedNotLoaded`, `loadSelectedFirst`, `thinkingUnavailable`, `thinkingUnknown`, and the support/help strings. Capability decisions come from the shared [model configuration](model-capabilities.md) and remain independent of the selected theme.

Response actions and settings scope share that text map too: `regenerateBranch`, `responseDetails`, `responseScopeHelp`, `saveDefaults`, `useSavedDefaults`, `modelReloadHelp`, and keyboard-help labels can be customized in each theme's JSON. Regenerate branches, keyboard behavior, collapsed details, and preferences use shared components; see the [behavior guide](usability.md). Appearance changes preserve expanded reasoning and response details.

Drafts are independent of appearance. Each chat's exact unsent text is stored under `playground.draft.v1:<chatId>`; an unsent new chat uses `playground.draft.v1:new`. `playground.activeChat.v1` remembers which chat to restore after refresh. Saves are debounced while typing and flushed on chat navigation, page hide, and document hide. Browser storage failures keep drafts in memory for the open tab and show a persistent warning; override `draftUnavailable` to change that message. Deleting a chat removes its draft, and response actions preserve any separate text being composed.

The implementation uses the existing HTML/chat controller, a small `frontend/theme.js`, and three independent JSON/CSS pairs. No frontend build step is required.

## Where to edit

| Family | Configuration | Structural CSS |
| --- | --- | --- |
| ChatGPT | `frontend/themes/chatgpt.json` | `frontend/themes/chatgpt.css` |
| Claude | `frontend/themes/claude.json` | `frontend/themes/claude.css` |
| GitHub Copilot | `frontend/themes/github-copilot.json` | `frontend/themes/github-copilot.css` |

Edit JSON for names, logos, icons, visible wording, fonts, dimensions, colors, and the supported placement choices. Edit the matching CSS for structural details beyond those choices. Shared components live in `frontend/styles.css`; keep unique layout rules in the family's CSS.

Refresh the browser after saving config changes. JSON is fetched with `cache: "no-store"`; the Python server needs no restart. Valid configs stay in memory for instant switching. If a config is invalid, it is left out of the selector and an error identifies the file and field in Settings. Other themes remain usable; if none load, the default interface still works. A missing logo image uses the local fallback mark.

## Branding and wording

For example, change these fields in `claude.json`:

```json
"branding": {
  "appName": "My Model Lab",
  "documentTitle": "My Model Lab — Chat",
  "logoLight": "/assets/themes/assets/my-logo-light.svg",
  "logoDark": "/assets/themes/assets/my-logo-dark.svg"
},
"text": {
  "welcomeTitle": "What would you like to explore?",
  "promptPlaceholder": "Message {appName}…",
  "assistantName": "{appName}",
  "newChat": "New conversation",
  "regenerate": "Try again"
}
```

The app name fills `{appName}` in placeholders, labels, and assistant headings. `documentTitle` is optional and defaults to the app name. The theme's `displayName` is its name in the picker; it does not rename your app or model. Logos are decorative next to visible name text. Store SVG assets under `frontend/themes/assets/`; config paths must begin `/assets/themes/assets/` and cannot traverse directories or use remote URLs. The bundled marks are locally drawn theme-style graphics and can be replaced independently.

Optional `text` entries override a single default map at the top of `theme.js`. That map is the complete list of available keys, including settings labels, action names, status templates, navigation, and accessible tooltips. Example status overrides:

```json
"text": {
  "working": "Thinking for {duration}",
  "loadingStatus": "Loading local model · {seconds}s",
  "modelReady": "{model} is ready"
}
```

Other named substitutions include `{prompt}`, `{value}`, and `{title}`. Substitutions insert plain text, never executable HTML. Chat content, actual model names, backend error details, preset system-prompt values, and the `Continue.` request remain model/application data, independent of appearance. Renaming a visible action changes its label, not its behavior.

## Fonts, dimensions, and colors

Common variables go in `vars`; light/dark overrides go directly in `modes.light` and `modes.dark`. Only these two flat maps are combined; there is no theme inheritance or nested merge. Previous inline theme values are removed when switching, so optional values fall back to shared CSS rather than leaking from the previous family.

```json
"vars": {
  "--font-sans": "system-ui, sans-serif",
  "--font-conversation": "Georgia, serif",
  "--chat-max-width": "800px",
  "--sidebar-width": "270px",
  "--composer-radius": "20px",
  "--button-radius": "8px",
  "--send-radius": "50%",
  "--control-height": "40px",
  "--text-size": "16px",
  "--line-height": "1.7"
}
```

Supported common variables:

- Typography: `--font-sans`, `--font-conversation`, `--font-mono`, `--text-size`, `--line-height`.
- Sizing: `--sidebar-width`, `--chat-max-width`, `--composer-radius`, `--button-radius`, `--send-radius`, `--control-height`.
- Effects: `--shadow` (a CSS box-shadow value).

Every mode must contain the full palette below. The fields are checked by `theme.js`; add a new supported variable there and its default in shared CSS when extending the vocabulary.

| Variable | Meaning |
| --- | --- |
| `--bg` | Conversation background |
| `--panel` | Sidebar and settings surface |
| `--panel-2` | Input/message/control surface |
| `--panel-3` | Hover, selection, and secondary surface |
| `--text` | Primary text |
| `--muted` | Secondary text and diagnostics |
| `--line` | Borders and separators |
| `--accent` | Primary buttons and selected controls |
| `--on-accent` | Text/icons on primary buttons |
| `--danger` | Errors and destructive actions |
| `--warning` | Loading and context warnings |
| `--success` | Ready state and context indicators |
| `--focus` | Keyboard/input focus |
| `--code-bg` | Code block surface |
| `--overlay` | Modal backdrop (alpha colors supported) |

Use literal CSS colors, such as `#262624` or `rgba(0, 0, 0, 0.5)`, for palette entries. CSS variable references in colors and values containing HTML/CSS declarations or URLs are rejected. Font stacks use locally available fonts; there are no remote font requests. For a bundled font, define `@font-face` in the family's CSS and refer to its name from the JSON.

## Placement and icons

Each configuration specifies three explicit placement choices:

```json
"layout": {
  "modelPicker": "composer",
  "emptyComposer": "center",
  "settings": "dialog"
}
```

- `modelPicker`: `header` or `composer`. All bundled themes use `composer`. The model menu button and conditional Load button move together; their handlers and values remain intact.
- `emptyComposer`: `center` or `bottom`. Active conversations always keep their input at the bottom.
- `settings`: `dialog` or `drawer`. Both use the same settings controls, focus management, and values.

For a new component arrangement, edit scoped CSS under `:root[data-theme="claude"]` (or the relevant ID). Do not duplicate the composer or replace message nodes to change appearance. The three layouts deliberately share behavior but retain their own structural rules.

Action icons use the local `assets/icons.svg` sprite. Override any of `panel`, `newChat`, `settings`, `send`, `stop`, `close`, `copy`, `regenerate`, `continue`, `thinking`, `options`, and `chevronDown` in `icons`. Values may be `panel`, `edit`, `plus`, `settings`, `arrow-up`, `arrow-right`, `stop`, `close`, `copy`, `retry`, `send`, `brain`, `sliders`, or `chevron-down`, or a local SVG asset path:

```json
"icons": { "newChat": "plus", "send": "send" }
```

To add another family, copy an existing JSON/CSS pair, change the `id` and `displayName`, add its ID to `THEME_IDS` in `theme.js`, link its scoped CSS from `index.html`, and update the guide/gallery. This deliberately uses a short explicit list rather than a plugin/registry system.

## Playwright verification and screenshots

From the repository root, install development-only tooling:

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'local_llm_playground\.playwright-browsers'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe local_llm_playground/scripts/check_themes.py --screenshots
.\.venv\Scripts\python.exe local_llm_playground/scripts/check_themes.py --scenario empty
```

The runner starts an isolated local preview serving only the frontend and in-memory API fixtures. It does not import FastAPI/llama.cpp, read your chats, load a model, or write application settings. Theme switching, preference persistence, draft/settings preservation, responsive layouts, and generation/stop behavior are checked against the same fixtures used for the screenshots. Failures return a nonzero exit status. Screenshots go to `docs/images/themes/`.

The browser binaries live in the ignored `.playwright-browsers/` directory. The runner uses that directory by default and respects an explicitly set `PLAYWRIGHT_BROWSERS_PATH`.

Screenshot conversations and token metrics are sample data, not model benchmark results. Inspect the captures after changing layout, typography, or color values; automated checks alone cannot establish visual similarity.

Additional captures: [Claude on mobile](images/themes/claude-dark-mobile.png), [GitHub Copilot on mobile](images/themes/github-copilot-dark-mobile.png), and [appearance settings](images/themes/chatgpt-dark-settings.png).

## Reference scope

The three families target browser chat, including the full-page GitHub Copilot surface. Product interfaces vary by release and account. The local layouts reproduce their recognizable navigation, composer, typography, and control treatments for the playground's supported functions. They do not claim exact parity with every current signed-in variant. Public reference access during implementation was limited: GitHub's chat URL redirected to its product page and Claude's public page exposed a cookie/sign-in shell.

Reference entry points: [ChatGPT](https://chatgpt.com/), [Claude](https://claude.ai/new), [GitHub Copilot browser chat documentation](https://docs.github.com/en/copilot/how-tos/copilot-on-github/chat-with-copilot/chat-in-github).
