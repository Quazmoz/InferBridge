# Microsoft Store listing — InferBridge 1.1.0

Copy-ready text for Partner Center (*Apps and games > InferBridge > Submission > Store listings > English (United States)*), plus the submission answers that go with it. Everything here describes what the 1.1.0 Store build actually does. Re-check it against the app before each submission and keep it in step with the privacy policy.

- Product name: **InferBridge**
- Store ID: `9PJ0NSD01070` · Package identity: `QuinnFavo.InferBridge` · Publisher display name: `Quinn Favo`
- Category: **Developer tools** (alternative: Productivity)
- Privacy policy URL: <https://consultant.quinnfavo.com/privacy/inferbridge>
- Website: <https://consultant.quinnfavo.com/apps/inferbridge>
- Support contact: <https://consultant.quinnfavo.com/apps/inferbridge#feedback> (GitHub issues: <https://github.com/Quazmoz/InferBridge/issues>)
- Copyright: `© 2026 Quinn Favo. Released under the MIT License.`

## Short description

(Up to 1,000 characters.)

> Run AI models privately on your own Windows PC. InferBridge downloads open models, prepares them for OpenVINO and runs them on your Intel CPU, GPU or NPU, so you can chat and use an OpenAI-compatible API without sending prompts to the cloud.

## Description

(Up to 10,000 characters. The live generative AI disclosure in the third paragraph is required by Store Policies 11.16.)

> InferBridge is a local AI app for Windows. It runs open AI models on your own PC with OpenVINO GenAI on Intel CPUs, integrated and discrete GPUs, and NPUs. You get a native Windows chat app and an OpenAI-compatible API on your PC. No account or cloud inference service is involved.
>
> Pick a model from the Model library. InferBridge recommends one for your hardware, downloads it from Hugging Face and prepares it for your device, and the model is ready to use in Chat. Choose a device for each model, or let the engine decide automatically. Benchmark Lab shows how fast each model runs on each device, and System Doctor checks your devices, memory and model readiness and suggests a fix when something needs attention.
>
> Uses live generative AI: replies are generated on your PC by the open model you choose. They can be inaccurate, biased or inappropriate, and InferBridge does not filter them. Use Report a reply in Chat, or Help & feedback in Settings, to report a problem to the developer.
>
> Your data stays on your PC. Prompts, chats, attached images and model outputs are processed locally and are never sent to the developer. Saved conversations are off by default. InferBridge has no analytics, advertising or crash-upload service. It goes online only when you download a model, search Hugging Face, or open a link. The local API listens only on this PC unless you turn on LAN access, which requires an API key.
>
> For developers, InferBridge serves OpenAI-compatible Chat Completions, Responses and Embeddings endpoints on your PC, with streaming. The Local API page gives you the exact base URL, copyable Python and curl.exe examples and a connection self-test. You can use it with the OpenAI Python SDK, Open WebUI, n8n and other OpenAI-compatible tools.
>
> Requirements: Windows 10 version 2004 or later, or Windows 11, 64-bit (x64). Designed for Intel hardware; GPU and NPU acceleration need a supported Intel GPU or NPU with a current driver. The first model download needs an internet connection and, depending on the model and connection, can take several minutes and several GB of disk space. After that, chat works offline.
>
> InferBridge is open source under the MIT License. It uses OpenVINO GenAI and is not affiliated with or endorsed by Intel or Hugging Face.

## Product features

(Up to 20, each up to 200 characters.)

1. Chat with AI models that run entirely on your PC — no account and no cloud inference
2. Runs on Intel CPU, GPU and NPU with OpenVINO GenAI, with a per-model device choice or automatic selection
3. Hardware-based model recommendation and one-click Download and load
4. Model library with download, load, unload, delete, cancel and recovery of interrupted preparation
5. Discover more models on Hugging Face, with optional token support for gated models (stored encrypted with Windows DPAPI)
6. Per-chat system prompt, temperature, top-p and maximum tokens
7. Vision chat: attach JPEG, PNG or WebP images for supported vision models
8. Markdown replies, live speed and context-usage stats, stop and copy
9. Optional saved conversations with search, pin, archive, duplicate and Markdown/JSON export
10. OpenAI-compatible Chat Completions, Responses and Embeddings APIs on your PC, with streaming
11. Local API page with base URL, copyable Python and curl.exe examples and a connection self-test
12. Optional LAN access protected by an API key; listens only on this PC by default
13. Benchmark Lab: measure first-token latency and tokens per second, and compare runs across models and devices
14. System Doctor: read-only health checks with recommended fixes and a copyable sanitized support report
15. Storage & cache manager to see and safely free space used by models and caches
16. Privacy-safe diagnostics ZIP that excludes prompts, chats, keys, tokens and model weights
17. Native Windows app with light, dark and high-contrast themes, keyboard navigation and screen reader labels
18. Optional Start with Windows, controlled in Windows Settings
19. Open source under the MIT License

## Search terms

(Up to 7 terms, 30 characters each, 21 words in total.)

`local AI` · `OpenVINO` · `LLM` · `AI chat` · `NPU` · `OpenAI API` · `offline AI`

## What's new in this version

(Up to 1,500 characters.)

> InferBridge 1.1.0 is the first Microsoft Store release.
> • New native Windows app: Chat, Model library, Hugging Face, Engine & devices, Benchmark Lab, System Doctor, Local API, Operations, Storage & cache and Settings.
> • Chat with a per-conversation system prompt and sampling settings, image attachments for vision models, live speed and context stats, and optional saved conversations.
> • Choose the CPU, GPU, NPU or an automatic device for each model.
> • Benchmark Lab with charts and run comparison.
> • Dropdowns no longer close while status refreshes, and System Doctor text is fully visible.
> • Settings > About shows the version and links to the privacy policy, help and feedback, and source code. Report a reply in Chat sends feedback about AI output to the developer.

## Screenshots to capture

Desktop screenshots: PNG, 1366×768 or larger (1920×1080 recommended), 1–10 images. Use real hardware with mock mode off, a real downloaded model, light theme for the first set (optionally a dark-theme set), 100% or 150% scaling, and the window at about 1440×900. Put no personal paths, user names, tokens or API keys on screen. Avoid unsuitable prompts and outputs, and use no cross imagery in prompts, replies or backgrounds.

1. **Chat, ready**: a loaded small model, a short helpful exchange (e.g. "Summarize this paragraph"), the inference settings pane open, and the stats strip showing tokens/s.
2. **Model library**: the recommended model card ("Recommended for this PC"), one downloaded/loaded model, and the device picker.
3. **First run**: Chat's empty state "Download your first model" with the **Open Model library** button (a fresh data folder).
4. **Engine & devices**: detected CPU/GPU/NPU pills and the RAM gauge.
5. **Benchmark Lab**: a completed run selected, decode-throughput and first-token-latency charts, and the comparison summary.
6. **System Doctor**: health summary chart and the checks list with at least one recommended action.
7. **Local API**: base URL, a passed connection self-test and the Python example (no key visible).
8. **Hugging Face**: a search for a small model with results (signed out or with the token masked).
9. **Storage & cache**: the storage breakdown chart.
10. **Settings**: About InferBridge with version and links (shows that no update controls appear in the Store edition).

## Age rating (IARC questionnaire)

- Category: **App** (not a game); utility/developer tool.
- **User Content Sharing: Yes** in the IARC questionnaire, as explicitly required by the Microsoft Store certification report dated 2026-10-30 (11.11.1). Users can copy/export generated responses or expose the local API to their own tools, even though InferBridge has no built-in social network, user-to-user messaging, accounts, ads, or purchases. Do not answer No merely because inference runs locally. Other answers must still describe actual functionality accurately; do not claim the app hosts public social features.
- Answer **Yes** to "generates content using AI in response to user input" (or the equivalent live generative AI question). Downloaded open models can produce unfiltered text, and InferBridge applies no content filter. Expect IARC to assign a higher rating (for example 12+ or Teen, possibly higher) because of unrestricted AI-generated content. Answer truthfully rather than aiming for a rating.
- The browser interface is local to the PC and is not unrestricted web browsing. Links open the user's own browser.
- The privacy policy states that the app is not directed at children under 13.

## Restricted capability justification: `runFullTrust`

Paste into *Submission options > Restricted capabilities*:

> InferBridge is a desktop (Win32) app packaged with MSIX. It needs runFullTrust because it is made of two full-trust desktop processes: a native WinUI 3 app (native\InferBridge.Native.exe), and the bundled InferBridge.exe engine that it starts. The engine runs AI models locally with Intel OpenVINO GenAI on the user's CPU, GPU or NPU and serves an OpenAI-compatible API on 127.0.0.1. The engine uses native GPU/NPU drivers and runtime libraries, starts helper processes to convert and compile models, stores models of several GB in a user-visible data folder (%USERPROFILE%\InferBridgeStoreData) that survives updates, and shows a system tray icon. These need the full-trust desktop app model and are not available in an app container. The package declares no other restricted capabilities, does not run as administrator, does not install services or drivers, and does not write to HKLM or system folders. Prompts and model outputs stay on the user's PC.

## Live generative AI (Store Policies 11.16)

- Disclosure in metadata: the third paragraph of the description above.
- In Partner Center, answer **Yes** wherever the submission asks whether the product uses live generative AI, and keep the description paragraph above.
- In-product reporting: **Report a reply** (Chat, beside Copy reply) and **Help & feedback** / **Report an issue** (Settings > About InferBridge) open the developer's feedback form or GitHub issues. Nothing from the chat is sent automatically.
- The developer acts on reports. Triage them through the website form and GitHub issues.

## Notes for certification (Partner Center "Notes for certification")

> InferBridge runs AI models locally. On first launch the app starts its bundled engine (this can take up to a minute), then Chat guides you to Model library. Choose a model marked "Recommended for this PC" and select Download and load. The download comes from Hugging Face, needs an internet connection and can take several minutes. After it finishes, Chat works offline. No account or sign-in is needed. Closing the window offers "Close window only" (InferBridge keeps running in the system tray) or "Quit InferBridge". Updates come only from Microsoft Store; the app has no update checks. The optional browser interface is local to this PC and is not the primary UI.

## Privacy policy alignment — fix before submitting

The live privacy policy (last updated 8 October 2026) must match the 1.1.0 Store build:

- It says installed and Store builds store data in `%LOCALAPPDATA%\InferBridge`. The Store build actually uses `%USERPROFILE%\InferBridgeStoreData`, and that folder stays after uninstall.
- It says conversation history lives only in the browser's localStorage. The native app can also save conversations on the PC when the user turns on **Save completed chats locally** (off by default; plain text in the data folder).
- Network requests: add **Hugging Face search** (the Hugging Face page) next to model downloads.
- Optional: mention the in-app **Report a reply** link as the way to report AI output.
