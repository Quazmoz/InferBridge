# Context budget visibility

InferBridge can inspect a chat request before generation and show how the selected loaded model will use its context window.

The WebGUI displays a compact context indicator beside the existing token counter. Open it to review:

- prompt tokens used
- the model's prompt budget and full context window
- requested and currently available output tokens
- whether the requested output will be capped
- retained and omitted message counts
- older whole turns that will be omitted
- a bounded preview of omitted messages
- the conservative token allowance used for pending image attachments

## API

```http
POST /v1/chat/context-budget
```

The request accepts the same core fields as Chat Completions, plus `image_count` for browser attachments that have not yet been inserted into the request message.

```json
{
  "model": "tinyllama-1.1b-chat-fp16",
  "messages": [
    {"role": "system", "content": "Answer concisely."},
    {"role": "user", "content": "Summarize the discussion."}
  ],
  "max_tokens": 512,
  "image_count": 0
}
```

The selected model must already be loaded because the preflight uses that engine's actual tokenizer and chat template.

## Accuracy and retention behavior

For text messages, the preflight uses the same components as generation:

1. OpenAI-style message normalization
2. tool-instruction injection when tools are supplied
3. the loaded model's chat template
4. the loaded model's tokenizer
5. InferBridge's existing whole-turn sliding-window algorithm
6. the same eight-token generation safety allowance

Leading system instructions remain pinned. When the request exceeds the prompt budget, InferBridge retains the newest user-led turns and removes complete older turns. It does not retain an assistant answer after omitting the user message that prompted it.

Pending browser images use the same conservative per-image token reserve as vision generation. That portion is an estimate because the images have not yet entered the server-side vision prompt. The UI labels attachment token use with an approximation marker.

The preflight does not change generation behavior. It reports what the existing prompt builder will do.

## WebGUI actions

The expanded context panel offers:

- **Start new chat from here**: creates a fresh chat while preserving the current draft.
- **Reduce output to fit**: lowers the output setting to the currently effective limit.
- **Refresh**: reruns the exact preflight immediately.

The indicator updates after draft, system-instruction, output-limit, model, chat, generation, or attachment changes. Requests are debounced and older in-flight preflights are cancelled.

## Privacy and security

- The endpoint follows the configured API-key policy.
- Cross-site browser requests are rejected.
- Existing request-body limits apply.
- Prompt text is processed in memory and is not persisted by the context-budget feature.
- The preflight does not increment generation metrics.
- Omission previews are bounded to 12 messages and 180 characters per message.
- The server does not return rendered prompts, tokenizer IDs, image data, local paths, or model files.
