# Kaggle GPU embedding endpoint

Free T4/P100 GPU behind ngrok, OpenAI-compatible `/v1/embeddings`. Used by
`ingest/embed.py` when `REMOTE_EMBED_URL` is set. **Ingestion only** — the
query path stays local (network hop > local CPU for one short query).

**Ceiling** (`ponytail:`): Kaggle sessions die at 12h idle, ngrok URL
changes every restart. Fine for a weekend re-ingest, not for live traffic.
Upgrade: any dedicated GPU box (RunPod, Modal, own server) speaking the
same OpenAI shape — swap `REMOTE_EMBED_URL`, done.

## Notebook cells

Create a new Kaggle notebook, GPU T4 x2, internet ON. Paste these cells:

```python
# Cell 1 — install
!pip install -q fastapi uvicorn pyngrok sentence-transformers==3.0.1
```

```python
# Cell 2 — model + server
import os, uvicorn, threading, time
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("MODEL_NAME", "BAAI/bge-m3")
DIM = int(os.environ.get("EMBED_DIM", "1024"))
SECRET = os.environ.get("EMBED_SECRET", "change-me")

model = SentenceTransformer(MODEL_NAME, device="cuda")
app = FastAPI()

class EmbedReq(BaseModel):
    input: list[str] | str
    model: str | None = None
    dimensions: int | None = None

@app.post("/v1/embeddings")
def embed(req: EmbedReq, authorization: str | None = Header(None)):
    if authorization != f"Bearer {SECRET}":
        raise HTTPException(401, "bad token")
    texts = req.input if isinstance(req.input, list) else [req.input]
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False).tolist()
    d = req.dimensions or DIM
    vecs = [v[:d] for v in vecs]  # matryoshka truncation
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [{"object": "embedding", "index": i, "embedding": v}
                 for i, v in enumerate(vecs)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }

threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=8000,
                                            log_level="warning"), daemon=True).start()
time.sleep(2)
```

```python
# Cell 3 — public URL
from pyngrok import ngrok
# Paste your ngrok authtoken (kaggle secret 'NGROK_TOKEN' works too):
ngrok.set_auth_token(os.environ.get("NGROK_TOKEN", "PASTE_TOKEN"))
tunnel = ngrok.connect(8000, "http")
print("PUBLIC URL:", tunnel.public_url)
# Keep-alive: prevents Kaggle from marking idle
while True:
    time.sleep(300)
```

## App-side config

Add to `lawfirm-rag/.env`:

```
REMOTE_EMBED_URL=https://<ngrok-subdomain>.ngrok-free.app/v1
REMOTE_EMBED_KEY=<same secret as EMBED_SECRET in cell 2>
```

Restart the app. Verify with a small ingest — `worker.log` should show the
usual chunk counts; the timing per file will drop from seconds to
sub-second on the network round-trip side.

## Test the endpoint directly

```bash
curl -X POST "$REMOTE_EMBED_URL/embeddings" \
  -H "Authorization: Bearer $REMOTE_EMBED_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": ["hello", "world"], "dimensions": 1024}' | jq '.data[0].embedding | length'
# → 1024
```

## Ingestion vs query — why not queries too

- Query embedding is ~1 short string, local CPU ~30-80ms.
- Kaggle → ngrok → back is 200-800ms round trip *before* the model runs.
- Ingestion embeds hundreds of chunks per file in one call: GPU wins big,
  round trip amortises.

## Model must match ingest and query

The `MODEL_NAME` in the notebook **must** be the same model as
`providers.HUGGINGFACE_EMBEDDING_MODEL` (or produce identical vectors).
Different models give different vector spaces — mixing them in one column
silently destroys recall with no error anywhere.
