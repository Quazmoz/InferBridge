# Reranking (`POST /v1/rerank`)

A reranker is a small cross-encoder model that reads a query together with each candidate document and scores how relevant the document is. RAG pipelines use it after vector search: retrieve 20–50 chunks with embeddings, rerank them, and send only the best few to the chat model. That usually improves answers more than a larger chat model would.

InferBridge runs rerankers locally with OpenVINO GenAI (`TextRerankPipeline`) and serves them on a Cohere/Jina-compatible endpoint. A reranker loads into its own **reranker slot**, so it stays loaded beside your chat model and your embedding model (see [API contract](API_CONTRACT.md#resident-model-slots-local-extension)).

## Models

| Model ID | Source | Size | Notes |
|---|---|---|---|
| `bge-reranker-base` | `BAAI/bge-reranker-base` | about 278M parameters, FP16 | English/Chinese, fast on CPU. In `models.json` and the Verified Model Library. |
| `bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` | about 568M parameters, FP16 | Multilingual, higher quality, slower. Verified Model Library (`best_quality` profile). |

Neither model is hardware-certified by InferBridge yet. Rerankers are converted with the Optimum Intel `text-classification` task (backend `openvino-rerank`); the pre-converted `OpenVINO/bge-reranker-base-fp16-ov` repository is an equivalent import. You can also import your own cross-encoder: choose **Reranker** as the backend in the Model Library import form (browser or native) or in the custom-model form. Hugging Face search suggests the reranker backend for `text-classification` models whose ID contains `rerank`.

Load a reranker with **Load as reranker** on its Model Library card. It defaults to the CPU; the CPU is usually fast enough for a few dozen short documents and leaves the GPU or NPU to the chat model. Two models on the Intel NPU at once are refused (see [Device support](DEVICE_SUPPORT.md#several-resident-models-and-the-npu)).

## Request and response

```bash
curl http://127.0.0.1:8000/v1/rerank \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_INFERBRIDGE_API_KEY" \
  -d '{
    "model": "bge-reranker-base",
    "query": "What is the capital of France?",
    "documents": [
      "The Eiffel Tower is in Paris.",
      "Paris is the capital of France.",
      "Bananas are a good source of potassium."
    ],
    "top_n": 2,
    "return_documents": true
  }'
```

```json
{
  "id": "rerank-…",
  "object": "rerank",
  "model": "bge-reranker-base",
  "results": [
    {"index": 1, "relevance_score": 0.998, "document": {"text": "Paris is the capital of France."}},
    {"index": 0, "relevance_score": 0.271, "document": {"text": "The Eiffel Tower is in Paris."}}
  ],
  "usage": {"prompt_tokens": 31, "total_tokens": 31}
}
```

(Scores above are illustrative.) Omit the `Authorization` header when API keys are off.

- `query` (required): non-blank text.
- `documents` (required): a list of strings or `{"text": "…"}` objects.
- `model` (optional): an exact reranker model ID. When omitted, the model in the reranker slot is used; with no reranker loaded the request returns `503`.
- `top_n` (optional, ≥ 1): return only the best *n*. Without it every document is returned.
- `return_documents` (default `true`): echo each document's text.
- `results` are sorted by `relevance_score`, highest first, and ties keep the original order. `index` is the position in your `documents` list.

`/rerank` and `/v2/rerank` are aliases of `/v1/rerank` for clients that build the path themselves. All three use the same API key and rate limits as the other routes.

### Scores

For single-logit cross-encoders such as the BGE rerankers, OpenVINO GenAI applies a sigmoid to the model's logit, so `relevance_score` is between 0 and 1. Scores order documents well for one query and one model, but they are not calibrated probabilities: do not compare scores between models, and be careful with fixed thresholds (start around 0.1–0.3 and tune on your own data).

### Limits

- At most 512 documents per request, 100,000 characters per document, 32,000 characters for the query, and the request-wide text limit (2,000,000 characters).
- Empty or whitespace-only documents return `422`.
- Each query/document pair is truncated to the model's context (`max_context_len`: 512 tokens for `bge-reranker-base`, 1024 for `bge-reranker-v2-m3`), so long chunks are scored on their beginning only. Chunk documents before reranking.
- Documents are scored in batches of 32, so memory does not grow with the request size, but time does: expect roughly linear latency in the number of documents.
- Requests to one reranker are serialized; the chat and embedding slots keep serving in parallel.
- `usage` token counts are estimates (about four characters per token), not tokenizer counts.
- Errors: `400` for a model that is not a reranker, `404` for an unknown model, `503` when no reranker is loaded, `500` for a scoring failure (details stay in the server log).

## Use it from RAG tools

Client settings change between versions; check your tool's documentation for the exact names.

**Open WebUI.** In *Admin Settings → Documents*, turn on hybrid search, set the reranking engine to **External**, the reranking URL to `http://127.0.0.1:8000/v1/rerank` (use your InferBridge address if Open WebUI runs in Docker, for example `http://host.docker.internal:8000/v1/rerank`), the API key to your InferBridge key (any text when keys are off), and the reranking model to `bge-reranker-base`. The same settings exist as environment variables in recent versions (`RAG_RERANKING_ENGINE=external`, `RAG_EXTERNAL_RERANKER_URL`, `RAG_EXTERNAL_RERANKER_API_KEY`, `RAG_RERANKING_MODEL`).

**Dify.** Add InferBridge with the **OpenAI-API-compatible** model provider, choose the **Rerank** model type, set the model name to `bge-reranker-base` and the API endpoint to `http://127.0.0.1:8000/v1`. Dify calls `…/v1/rerank`.

**LlamaIndex** (or any Python pipeline): call the endpoint from a small node postprocessor.

```python
import requests
from llama_index.core.postprocessor.types import BaseNodePostprocessor


class InferBridgeRerank(BaseNodePostprocessor):
    url: str = "http://127.0.0.1:8000/v1/rerank"
    model: str = "bge-reranker-base"
    top_n: int = 4

    def _postprocess_nodes(self, nodes, query_bundle=None):
        if not nodes or query_bundle is None:
            return nodes
        reply = requests.post(
            self.url,
            timeout=60,
            json={
                "model": self.model,
                "query": query_bundle.query_str,
                "documents": [node.node.get_content() for node in nodes],
                "top_n": self.top_n,
                "return_documents": False,
            },
        )
        reply.raise_for_status()
        ranked = []
        for row in reply.json()["results"]:
            node = nodes[row["index"]]
            node.score = row["relevance_score"]
            ranked.append(node)
        return ranked
```

Other Cohere- or Jina-compatible clients that accept a custom base URL can point at `http://127.0.0.1:8000/v1` (or `http://127.0.0.1:8000` for clients that add `/v1/rerank` or `/v2/rerank` themselves).

## Try it in InferBridge

The browser **Connection Hub** and the native app's **Local API** page both have a **Rerank for RAG** section: a copyable curl example that names the loaded reranker, and a small form that scores documents (one per line, up to 50) against a query and lists them by relevance, best first. In the native app **Score documents** stays disabled until a reranker is loaded; load one with **Load as reranker** in the Model library, then select **Check reranker**.
