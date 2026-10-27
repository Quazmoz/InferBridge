# Native Windows UI design brief

Scope: the WinUI 3 client in `windows-native/InferBridge.Native` (`MainWindow.xaml`
plus its partial classes). The web UI is out of scope. This brief records the
audit that motivated the redesign, the target information architecture, the
layout grid, the component set and the state rules every page follows.

Guiding sources: Fluent 2 / Windows app design guidance (navigation, layout,
typography ramp, spacing, color and accessibility), WinUI 3 control guidance,
and desktop local-LLM tools such as LM Studio **as inspiration only** (see
[Distinct InferBridge identity](#distinct-inferbridge-identity)).

## 1. Audit (before)

Every page was a single `StackPanel` capped at 700–840 px, left-aligned, made of
18 px headings, long gray (`Opacity="0.8"`) paragraphs and multi-line text
blocks that carried data as prose ("Requested GPU → actual GPU\nFirst token: …").
Commands were plain buttons scattered between paragraphs, so the primary action
of a page was not obvious.

| Page | What it showed | Main problems |
| --- | --- | --- |
| Chat | Toolbar (model picker, status flyout, saved-chats flyout, settings link, new chat), device picker row, transcript, composer | Inference settings lived on a different page; no live performance feedback; bubble colors were hard-coded ARGB values that ignore light/high-contrast themes. |
| Model library | One `ListView` of strings ("Name · Downloaded (id)") plus a 2×3 button grid and two text blocks below | Status, device, size, vision and recommendation were buried in text; every action required select-then-scroll-to-buttons. |
| Hugging Face | Account text, token buttons, search form, result rows | Reasonable, but single column and no card grouping. |
| Engine (overview) | Connection InfoBar, refresh/start, hardware prose, API key, web GUI link | Hardware inventory was a sentence; no glanceable state (connected, port, loaded model, device). |
| Local API (Connection Hub) | Base URL, listener/auth/LAN prose, model picker, Python and curl examples, self-test, network settings | 30+ stacked controls in one column; read-only status and editable network settings were interleaved. |
| Operations | Engine/installation prose, quick benchmark, diagnostics ZIP, restart | Three unrelated tasks in one text column. |
| Benchmark Lab | Configuration checkboxes, preset, run, progress, history picker, run details as prose, comparison prose | Throughput/TTFT numbers only as text; no way to see which device or model is faster at a glance; no trend. |
| System Doctor | Summary sentence, devices, model readiness, `[LEVEL] title` check list, model-health rows | Counts and RAM/disk readings existed but were only text; level was a bracketed word, not a visual state. |
| Storage & cache | Summary sentence and cleanup rows | Acceptable; single column. |
| Settings | Generation settings, per-model context window, about & updates | Generation settings belong next to the conversation they affect. |

## 2. Information architecture

Left `NavigationView` rail (Fluent "left navigation"), grouped so the rail reads
as *what I do* → *what I run* → *what runs it*:

| Group | Item (tag) | Icon (Segoe Fluent) | Access key | Purpose |
| --- | --- | --- | --- | --- |
| — | **Chat** (`chat`) | Message `E8BD` | C | Converse with a loaded model |
| Models | **Model library** (`models`) | Library `E8F1` | M | Installed/catalog models and lifecycle |
| Models | **Hugging Face** (`huggingface`) | Globe `E774` | H | Remote discovery and account |
| Engine | **Engine & devices** (`overview`) | Component `E950` | E | Connection, hardware inventory, memory |
| Engine | **Benchmark Lab** (`benchmark`) | Area chart `E9D2` | B | Measure and compare |
| Engine | **System Doctor** (`doctor`) | Diagnostic `E9D9` | D | Health checks and remediation |
| Engine | **Local API** (`connection`) | Code `E943` | A | Server address, examples, self-test, network |
| Engine | **Operations** (`operations`) | Repair `E90F` | O | Quick benchmark, diagnostics, restart |
| Engine | **Storage & cache** (`storage`) | Hard drive `EDA2` | S | Managed files and cleanup |
| Footer | **Settings** (`settings`) | Settings `E713` | T | Context window, updates |

The pane footer shows **live status pills** — engine connected / key required /
offline, server `host:port`, and each loaded model with its *engine-reported*
device. Pills are hidden when the pane collapses to the 48 px rail. Tags are the
navigation contract: code-behind navigates with `NavigateTo(tag)`, which also
searches footer items.

The requested "Hardware / Benchmarks / Health / Server-API" destinations map to
InferBridge's existing names (*Engine & devices*, *Benchmark Lab*, *System
Doctor*, *Local API*) so documentation, the web UI and support threads keep one
vocabulary.

## 3. Layout grid

* **Page frame:** content padding 24 px (12 px top), page content stretches to a
  1280 px max width (`InferBridgeContentMaxWidth`) instead of 700 px left
  columns. Detail pages keep a vertical `ScrollViewer` with horizontal scrolling
  disabled.
* **Page header:** a 3 px brand rule, title (`TitleTextBlockStyle`), one-line
  subtitle (`InferBridgeHelpTextStyle`), then a wrapping command row
  (`WrapRow`): the primary action uses `AccentButtonStyle`, secondary actions
  are standard buttons, all with a Segoe Fluent icon + label.
* **Spacing ramp:** 4 / 8 / 12 / 16 / 24. 16 between cards and inside cards,
  24 between page sections, 8 between related controls.
* **Breakpoints:** `CardGrid` lays cards in 1 column below 640 px of available
  content width, 2 below 1008 px and 3 above (Fluent small / medium / large).
  It measures the width the page *actually* has. Window-level
  `AdaptiveTrigger`s were rejected: in a WinUI 3 `Window` (no `Page`) they need
  a control template root, and they react to window width, which differs from
  content width by the 48–320 px navigation pane. `ChartMath.Columns` holds the
  breakpoint math so contract tests prove it.
* **Chat:** two columns — conversation (`*`) and a 320 px inference pane. Below
  900 px the pane overlays the conversation from the right instead of squeezing
  it, and it is toggled from the toolbar.
* **Model library:** inventory list stays the star-sized row; each row is a card
  with its own commands. The selection details/actions region keeps its bounded,
  scrollable height so short windows always show the inventory.

## 4. Components

| Component | Implementation | Notes |
| --- | --- | --- |
| Card | `Border` with `InferBridgeCardStyle` (`CardBackgroundFillColorDefaultBrush`, `CardStrokeColorDefaultBrush`, 8 px radius, 16 px padding) | Code-built cards use `Ui.Card` with the same resources. |
| Section header | `InferBridgeSectionHeaderStyle` (Subtitle ramp, 18 px) | Inside cards or between card groups. |
| Status pill | `Ui.Pill(text, kind)` — dot + caption text on `SystemFillColor*BackgroundBrush` | Kind: success, warning, error, accent (brand), neutral. Text is always primary color for contrast; the dot carries the hue. |
| InfoBar | Fluent `InfoBar` for page-level status | Existing names and messages preserved. |
| Progress | `ProgressRing` (indeterminate) for short operations, `ProgressBar` for engine-reported percentages | |
| Bar chart | `BarChart` (`ChartControls.cs`) | Two rows per item (label + value, then bar). Bars scale to the largest *real* reading. |
| Sparkline | `Sparkline` — `Canvas` + `Polyline` | Trend across saved runs, oldest left. |
| Gauge | `RingMeter` — determinate Fluent `ProgressRing` + centered % | RAM, disk and context usage. |
| Flow row | `WrapRow` panel | Commands and pills wrap instead of clipping at 125–150 % scale. |
| Card grid | `CardGrid` panel | Breakpoints above. |

### Why in-house charts

No charting package was added. The app needs three primitive charts over at most
~100 points. LiveCharts2 / ScottPlot / Syncfusion would add SkiaSharp or
Win2D-sized native payloads (several MB per architecture) to a self-contained
MSIX, complicate trimming, and bring their own theming that would need work to
honour high contrast. The in-house controls are ~300 lines, use only XAML
shapes and Fluent controls, follow theme resources, and keep the layout math in
`ChartMath.cs`, which has no WinUI dependency and is exercised by
`ChartMathContractTests`.

### Chart data rules

* Only data the engine or the client actually measured is plotted. Missing,
  non-finite or negative readings keep a zero-length bar and the label
  **Not reported** — they never become zero.
* Benchmark charts: decode tokens/s (or the labelled legacy overall rate when
  only that exists) and first-token latency per *model · actual device* of the
  selected run; a sparkline of the best real decode rate per saved run.
* Chat stats strip: first-token time and total time are measured by the client
  around the stream; completion/prompt tokens come from the engine's
  `stream_options.include_usage` event; decode tokens/s uses the same formula as
  the engine benchmark, `(tokens − 1) / (total − TTFT)`. Context used is
  `prompt + completion` tokens against the model's reported context window.
  Anything unavailable is shown as "—" with a "not reported" tooltip.
* Engine & devices / System Doctor gauges: RAM used = total − available and
  model-volume free space, from the same `/v1/system/status` read the doctor
  already performs. Doctor summary bars count check levels.

## 5. States

Every page and component distinguishes:

| State | Treatment |
| --- | --- |
| Not loaded | InfoBar (Informational) with the existing "Open this page to…" text; charts show their empty text. |
| Loading | Commands that start the same work are disabled; `ProgressRing` active. Background polls never disable pickers or rebuild unchanged lists (`PickerRefresh`). |
| Ready | InfoBar Success or closed; pills green. |
| Partial / not reported | Values read "Not reported" / "—"; never zero. |
| Warning / error | InfoBar Warning/Error with the engine's sanitized message; pills caution/critical. |
| Destructive | Unchanged confirmation dialogs (delete, cleanup, network, restart, health actions). Per-row commands call the same handlers and guards as the page buttons. |

## 6. Accessibility and quality bar

* Keyboard: every command is a real `Button`; charts are tab stops with system
  focus visuals; nav items have unique access keys; Ctrl+Enter sends.
* Narrator: charts set `AutomationProperties.Name` to the full data text
  (`ChartMath.Describe`) and `HelpText` to how to read them; pills are named;
  live regions keep `AutomationProperties.LiveSetting="Polite"`.
* Themes: no literal colors in `MainWindow.xaml` or C#; the only literal colors
  are the brand values in `App.xaml` theme dictionaries; high contrast maps the
  brand to `SystemColorHighlightColor`. Secondary text uses
  `TextFillColorSecondaryBrush`, not opacity (opacity fails high contrast).
* Text scaling / DPI: no fixed heights on text containers, `TextWrapping="Wrap"`
  instead of trimming for data, wrapping command rows, 32 px minimum targets
  (Fluent default button height).

## Distinct InferBridge identity

LM Studio informed *patterns* (a model + device bar above the conversation,
inference controls beside it, live generation stats, models as actionable
cards). InferBridge deliberately does not copy its look:

* **Navigation:** grouped rail with InferBridge vocabulary — *Chat*, *Model
  library*, *Hugging Face*, *Engine & devices*, *Benchmark Lab*, *System
  Doctor*, *Local API*, *Operations*, *Storage & cache* — instead of LM Studio's
  Chat / Developer / My Models / Discover set, and a status-pill pane footer
  rather than a bottom status bar.
* **Color:** a "bridge teal" data accent (`InferBridgeBrandBrush`: `#0B6E78`
  light, `#4FC3C7` dark, system highlight in high contrast) for charts, pills
  and the header rule, while primary commands follow the user's Windows accent.
  LM Studio's purple/indigo palette and dark-only chrome are not used; the app
  follows the system light/dark theme.
* **Iconography:** Segoe Fluent Icons only (no custom glyph set), chosen for
  OpenVINO concepts — Component for devices, Area chart for benchmarks,
  Diagnostic for the doctor, Code for the local API.
* **Layout:** Fluent card grid with a brand rule beside page titles; inference
  settings in a right pane that *overlays* on narrow windows; the stats strip
  sits under the composer as pills, and context usage is a ring gauge.
* **Naming:** "Inference settings", "Engine status", "Load / switch",
  "Benchmark Lab", "System Doctor" — existing InferBridge terms.
