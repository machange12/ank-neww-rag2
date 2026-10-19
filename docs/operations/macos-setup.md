# Running on macOS

The application is plain Python + Node and has **no Windows-only code**. There
is no separate "Mac version": the same checkout runs on macOS, Linux and
Windows. Only the environment setup differs (venv layout, activation script,
shell syntax).

This guide covers **Apple Silicon (M1-M4)** and **Intel** Macs. The steps are
identical; performance notes are called out where they differ.

> **Parity with the Windows dev box.** Answers, retrieval, citations, drafting
> and DOCX export are produced by the same backend code against the same
> Supabase project. When `REMOTE_EMBED_URL` (Gemini) is set, both machines call
> the same hosted model, so embeddings — and therefore retrieval — match the PC.
> The only differences are local paths, the event loop (uvloop on macOS) and,
> if you opt into the local torch embedder, floating-point noise.

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **3.11.9** (matches `.python-version`) | 3.11.x is fine |
| Node.js | **^20.19 or >=22.12** | required by Vite 8 |
| Git | any recent | |

With Homebrew:

```bash
brew install python@3.11 node git
```

Or with `pyenv` (recommended if you keep several Python versions):

```bash
brew install pyenv
pyenv install 3.11.9
pyenv local 3.11.9
```

Verify:

```bash
python3.11 --version   # Python 3.11.9
node --version         # v20.19+ or v22.12+
npm --version
```

---

## 2. Clone and create the venv

The venv lives at the **repo root** (not inside `lawfirm-rag/`), because
`config.py` resolves `.env` relative to the repo root.

```bash
git clone <repo-url> "ank neww rag2"
cd "ank neww rag2"

python3.11 -m venv .venv
source .venv/bin/activate          # macOS / Linux
python -m pip install --upgrade pip
pip install -r lawfirm-rag/requirements.txt
```

**Do not** copy the PC's `.venv/` — it contains Windows binaries
(`Scripts/python.exe`). It is gitignored, so a fresh clone never has it.

### Embeddings: keep the hosted Gemini endpoint

The root `.env` sets `REMOTE_EMBED_URL`, `REMOTE_EMBED_KEY` and
`REMOTE_EMBED_MODEL`. With those set, `providers.get_embeddings()` uses the
hosted OpenAI-compatible endpoint and **torch is never imported**. This is the
recommended Mac setup and gives exact parity with the PC.

Only if you deliberately want the offline local model:

```bash
pip install -r lawfirm-rag/requirements-local.txt   # torch + sentence-transformers
# and unset REMOTE_EMBED_URL / OPENAI_API_KEY in .env
```

Local-model notes:

- **Apple Silicon:** torch uses the MPS backend when available. Add
  `export PYTORCH_ENABLE_MPS_FALLBACK=1` if a model hits an unsupported op.
- **Intel:** CPU-only; expect slower embedding and reranking.
- The local `nomic-embed-text-v1.5` model outputs 768 dimensions, matching
  `EMBEDDING_DIM=768` and the `vector(768)` column. Switching embedders
  requires re-embedding (see the note in `config.py`).

---

## 3. Environment file

```bash
cp .env.example .env
```

Fill in the Supabase, Groq/OpenAI, Cohere and Google Drive values.

- `config.py` reads **only the repo-root `.env`** (`ROOT_DIR / ".env"`).
- `lawfirm-rag/.env` is a stale leftover and is **not** read. Delete it if you
  like; never commit either file (both are gitignored).
- Keep `EMBEDDING_DIM=768` and `REMOTE_EMBED_MODEL` unchanged when moving
  between machines, or retrieval will silently mismatch the stored vectors.

---

## 4. Frontend dependencies

```bash
cd frontend
npm ci
```

`npm ci` installs the correct native bindings for the current architecture.
`package-lock.json` already contains both `darwin-arm64` and `darwin-x64`
entries for `@rolldown` and `lightningcss`, so **no lockfile changes are
needed** on either Mac type. Never copy `node_modules/` from the PC.

---

## 5. Run it

From the repo root, with the venv active:

```bash
cd lawfirm-rag
uvicorn app:app --host 0.0.0.0 --port 8000            # chat + API
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001  # Drive webhook + scheduler
```

Or use the `Makefile` at the repo root:

```bash
make setup     # venv + pip install + npm ci
make api       # API on :8000
make worker    # worker on :8001
make web       # Vite dev server on :5173
make build     # production build -> lawfirm-rag/static
make test      # offline pytest suite
make verify    # schema manifest check
```

Dev UI (separate terminal):

```bash
cd frontend
npm run dev
```

Production-style single process (API serves the built frontend):

```bash
make build
make api
# open http://localhost:8000
```

### Port note

`frontend/.env` and `vite.config.js` proxy `/auth` and
`/lawfirm-chat-trigger-006` to **`127.0.0.1:8001`**, while `README.md` and the
Makefile use **8000** for the API. Either start the API on 8001 for the dev
UI, or change `frontend/.env` (`VITE_API_BASE_URL`) and the two proxy targets
to 8000. This drift exists on every platform, not just macOS.

---

## 6. Verify the install

```bash
cd lawfirm-rag
../.venv/bin/python -m pytest tests/ -q                          # 157 offline tests
../.venv/bin/python scripts/verify_schema.py --manifest-only     # SCHEMA OK
```

Then, with the API running:

1. Log in at `http://localhost:8000`.
2. Ask a question and confirm sources are returned.
3. Upload a document (requires the appropriate access level).
4. Create a draft in Draft Studio and export DOCX.

### Live checks (optional, need credentials + network)

```bash
../.venv/bin/python scripts/verify_schema.py          # live DB
../.venv/bin/python tests/integration_test.py         # expects :8001
```

---

## 7. Google Drive OAuth

The refresh-token helper is cross-platform (loopback redirect, no manual
pasting):

```bash
cd lawfirm-rag
../.venv/bin/python scripts/get_refresh_token.py
```

Paste the printed token into the repo-root `.env` as `GOOGLE_REFRESH_TOKEN`.

---

## 8. Docker (closest to "identical")

If you want the Mac and the PC to run the exact same Linux image:

```bash
docker compose up -d --build
# app on http://127.0.0.1:8000
```

The image installs `requirements.txt` only (no torch), so `.env` must contain a
hosted embedder (`REMOTE_EMBED_URL` or `OPENAI_API_KEY`). See
`docs/operations/oci-deployment.md` for the production variant.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `command not found: uvicorn` | venv not active — `source .venv/bin/activate` |
| `ModuleNotFoundError: config` | run from `lawfirm-rag/`, not the repo root |
| Settings are empty | `.env` is missing from the **repo root**, or you edited `lawfirm-rag/.env` |
| `unsupported operand` / native module errors after copying | copied `node_modules` from another OS — delete and run `npm ci` |
| `ModuleNotFoundError: torch` | expected with `requirements.txt`; set the hosted embedder or install `requirements-local.txt` |
| Frontend calls 404 | dev proxy targets `:8001`; run the API there or update `frontend/.env` |
| Slow first embedding | local model cold-load; hosted endpoint avoids it |
| `invalid_grant` from Drive sync | refresh token expired — re-run `scripts/get_refresh_token.py` |
