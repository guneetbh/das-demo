# Reference — vector database for recommendations

## Why the vector DB is better than the previous implementation

| | Before | After |
|---|---|---|
| Query matching | 3 hardcoded phrases (`date+night`, `work`, `gym`) via `LIKE` | Any free-text query, embedded and matched by meaning |
| Unmatched query | Returns entire 1,300-SKU catalogue, no relevance filter | Returns semantically ranked candidates |
| Relevance in ranking | None — scored only by tier/trending/engagement | `semantic_score` feeds the ranking formula directly |
| New products | Needs hand-tagged `occasion_tags` to ever match | Matches on name/category text, no manual tagging |
| Transparency | N/A | Logged to `audit_log` as `vector_store:search`, same as model calls |

**Verified:** `"gift ideas"` → 100% accessories in top 15 (old matcher had no path to handle this query at all). `"jeans"` → 100% jeans. Date-night queries still match `top`/`dress` as before — no regression.

**Tradeoffs:** adds a dependency (`chromadb`) + one-time ~80MB local model download (cached, no ongoing cost/API key); token-overlap fallback if `chromadb` isn't installed.

## The embedding model

- **Model:** `all-MiniLM-L6-v2`, ONNX build maintained by Chroma (avoids a torch dependency). 384-dim output, cosine similarity, 256-token max input.
- **Source:** `chroma-onnx-models.s3.amazonaws.com`, SHA256-verified on download.
- **Runs:** in-process, via `onnxruntime` — no separate model server. ~34ms/embed on CPU, measured locally.
- **Cache:** `~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/` (~86MB `model.onnx` + tokenizer files). Our catalogue index is separate: `data/chroma/` (gitignored).
- **Network:** `AzureExecutionProvider` shows up in `get_available_providers()` but isn't configured with an endpoint, so ONNX Runtime falls through to CPU for every op. Confirmed by blocking `socket.socket.connect` and re-running an embed call — it still succeeded.

### Verify it yourself

```bash
ls -la ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/          # cached model files
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
python -m neutail.vector_store 20                                  # peek at indexed catalogue entries

python3 -c "
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
import time
ef = ONNXMiniLM_L6_V2()
t0 = time.time(); v = ef(['a red silk evening dress'])
print('ms:', round((time.time()-t0)*1000, 1), 'dim:', len(v[0]))
"
```

Model is only invoked indirectly — Chroma calls it internally on `upsert()` (seed time) and `query()` (each search) inside `neutail/vector_store.py`.
