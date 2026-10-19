# Deploying on Oracle Cloud Infrastructure (OCI)

Move the app off Render onto an OCI VM. **Hosting only** — this keeps Supabase
(Postgres + RLS + Auth) unchanged. Moving the *database* to Oracle is a separate,
much larger project (see `progress.md` / the notes at the end).

The system has **two** processes:

- **app** (`app:app`) — the API + built frontend, and an **in-process**
  APScheduler job that polls Google Drive every 6 hours (`app.py` startup →
  `ingest/scheduler.start_scheduler`).
- **worker** (`ingest.worker:app`) — the Drive webhook, manual re-ingest, and
  the **nightly orphan cleanup** @ 02:00 (`ingest/worker.py`).

They schedule *different* jobs, so running both causes **no duplicate work** —
run both. Consequences:

- Run **one** uvicorn worker per process. More app workers ⇒ more Drive polls
  (the app's poll is in-process); extra app *containers* have the same effect.

## What you deploy

| File | Purpose |
|---|---|
| `lawfirm-rag/Dockerfile` | Multi-stage image (Vite build → Python 3.11 runtime) |
| `docker-compose.yml` | Run the app container on the VM |
| `deploy/oci/lawfirm-rag.service` | systemd unit for the app (bare-VM, non-Docker) |
| `deploy/oci/lawfirm-rag-worker.service` | systemd unit for the worker (cleanup / webhook) |
| `deploy/oci/nginx-lawfirm-rag.conf` | nginx reverse proxy + TLS (SSE-aware) |

Pick **one** of: Docker Compose, or bare-VM systemd. Both put the app on
`127.0.0.1:8000` behind nginx. The worker is a separate process either way.

## 0. Prerequisites / decisions

- **Region**: co-locate with the Supabase project (the app makes 5–8 DB calls
  per chat). If Supabase is `eu-west-1`, pick an EU OCI region (e.g. Frankfurt).
- **Shape**: the image has **no torch** (embeddings run via a hosted API), so a
  small shape is fine. The Ampere A1 free tier (up to 4 OCPU / 24 GB) is ample;
  a 1 OCPU / 6 GB AMD shape also works.
- **Hosted embedder required.** Set `OPENAI_API_KEY`, or
  `REMOTE_EMBED_URL` / `REMOTE_EMBED_KEY` / `REMOTE_EMBED_MODEL`. Without one the
  app tries to load the local HuggingFace model, which is not installed.
- **DNS**: point `rag.example.com` at the VM's public IP before TLS.

## 1. OCI networking

1. VCN → Subnet → Internet Gateway; route `0.0.0.0/0` to the gateway.
2. **Security List / NSG** ingress rules:
   - TCP 22 from your admin IP only
   - TCP 80 from `0.0.0.0/0` (ACMECertbot + redirect)
   - TCP 443 from `0.0.0.0/0`
3. Note the VM public IP; create the DNS A record.

## 2. Base packages

Oracle Linux 9:

```bash
sudo dnf install -y git nginx python3.11 python3.11-pip firewalld
sudo systemctl enable --now firewalld nginx
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --reload
# SELinux: let nginx make outbound proxy connections
sudo setsebool -P httpd_can_network_connect 1
```

Ubuntu 22.04/24.04:

```bash
sudo apt update && sudo apt install -y git nginx python3.11 python3.11-venv python3-pip
```

Install certbot (`dnf install -y certbot python3-certbot-nginx` or
`apt install -y certbot python3-certbot-nginx`).

## 3. Get the code, create the env file

```bash
sudo useradd --create-home --shell /usr/sbin/nologin lawfirm   # systemd path
sudo mkdir -p /opt/lawfirm-rag && sudo chown "$USER" /opt/lawfirm-rag
git clone <your-repo-url> /opt/lawfirm-rag
sudo chown -R lawfirm:lawfirm /opt/lawfirm-rag

cp /opt/lawfirm-rag/.env.example /opt/lawfirm-rag/.env
chmod 600 /opt/lawfirm-rag/.env
# edit .env — see the production checklist below
```

> `config.py` resolves `ROOT_DIR` to the **parent** of `lawfirm-rag/`, so the
> `.env` must sit at the repo root (`/opt/lawfirm-rag/.env`), not inside
> `lawfirm-rag/`.

## 4. Build the frontend

The backend serves the built assets from `lawfirm-rag/static/`.

```bash
cd /opt/lawfirm-rag/frontend
npm ci && npm run build      # writes ../lawfirm-rag/static
```

(With the **Docker** path, stage 1 of the image does this for you — skip this
step.)

## 5A. Run with Docker Compose (recommended)

```bash
cd /opt/lawfirm-rag
docker compose up -d --build
docker compose logs -f app
```

The container binds `127.0.0.1:8000` (nginx is the public entrypoint). The
image's `HEALTHCHECK` curls `/healthz`.

## 5B. Run with systemd (bare VM, no Docker)

```bash
cd /opt/lawfirm-rag
python3.11 -m venv .venv
.venv/bin/pip install --no-cache-dir -r lawfirm-rag/requirements.txt

sudo cp deploy/oci/lawfirm-rag.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lawfirm-rag
sudo systemctl status lawfirm-rag
```

Logs: `journalctl -u lawfirm-rag -f`.

## 6. nginx + TLS

```bash
sudo cp /opt/lawfirm-rag/deploy/oci/nginx-lawfirm-rag.conf \
        /etc/nginx/conf.d/lawfirm-rag.conf
sudo sed -i 's/rag.example.com/your.domain.example/g' /etc/nginx/conf.d/lawfirm-rag.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your.domain.example     # fills the TLS cert paths
```

The proxy config sets `proxy_buffering off` and a 300s read timeout so chat
streaming (`/lawfirm-chat-stream`) and section generation stream token-by-token.

## 7. Production `.env` checklist

- `ENVIRONMENT=production` — rejects `CORS_ORIGINS=*` and refuses unverified JWTs.
- `CORS_ORIGINS=https://your.domain.example` — explicit allow-list.
- `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_JWT_SECRET`.
- `POSTGRES_DSN` — for `scripts/verify_schema.py` / `apply_migrations.py`.
- A hosted embedder: `OPENAI_API_KEY` **or** `REMOTE_EMBED_URL` + `REMOTE_EMBED_KEY` + `REMOTE_EMBED_MODEL`.
- `GROQ_API_KEY` (chat), optional `COHERE_API_KEY` (rerank).
- `INGEST_WORKER_TOKEN` — required if you expose `/ingest/manual`.
- Google Drive: `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `GOOGLE_REFRESH_TOKEN` / `DRIVE_FOLDER_ID` / `DRIVE_WEBHOOK_SECRET`.

Never commit `.env`; it is gitignored and excluded from the image.

## 8. Verify

```bash
curl -fsS https://your.domain.example/healthz        # {"status":"ok"}
open  https://your.domain.example/                    # login screen
cd /opt/lawfirm-rag/lawfirm-rag
../.venv/bin/python -m pytest tests/ -q               # 157 offline tests
../.venv/bin/python scripts/verify_schema.py          # live DB -> SCHEMA OK
```

## Ingest worker (cleanup / Drive webhook / manual re-ingest)

Run this alongside the app. It owns the nightly orphan cleanup and the three
routes that exist **only** in `ingest/worker.py` (`POST /drive/webhook`,
`POST /ingest/manual`); the main app does not expose them. Without the worker,
nightly cleanup never runs.

```bash
sudo cp deploy/oci/lawfirm-rag-worker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lawfirm-rag-worker
# to reach the webhook routes, uncomment /drive/webhook and /ingest/manual in
# the nginx conf (and set INGEST_WORKER_TOKEN for /ingest/manual)
```

There is no double-polling between the app and the worker: the app polls Drive,
the worker runs cleanup. Do **not** scale the app to multiple instances/workers
without first moving its Drive poll to a single dedicated process.

## Updates & rollback

```bash
# Docker
cd /opt/lawfirm-rag && git pull && docker compose up -d --build

# systemd
cd /opt/lawfirm-rag && git pull
cd frontend && npm ci && npm run build
cd .. && .venv/bin/pip install -r lawfirm-rag/requirements.txt
sudo systemctl restart lawfirm-rag
```

Rollback = `git checkout <previous-sha>` and re-run the build/restart. The
database is untouched by an app deploy (migrations are applied separately — see
[`migrations-and-rollbacks.md`](migrations-and-rollbacks.md)).

## OCI gotchas

- **Firewall layers**: OCI Security List *and* host `firewalld`/`ufw` must both
  allow 80/443. The classic "opened the port in OCI but still unreachable" is
  the host firewall.
- **SELinux** (Oracle Linux): `setsebool -P httpd_can_network_connect 1` or
  nginx returns 502.
- **Ephemeral public IP** changes on VM stop/start — use a *reserved* public IP
  so DNS/TLS survive a reboot.
- **Secrets**: prefer OCI Vault; at minimum `chmod 600` the `.env` and keep the
  service user unprivileged.

## Not covered here: moving the *database* to Oracle

That is a separate, multi-week rewrite, not a deploy change: Oracle AI Vector
Search replaces pgvector, Oracle Text replaces tsvector/GIN, VPD replaces RLS,
and PostgREST (the per-request RLS boundary the whole authz model rests on) has
no drop-in equivalent — see the discussion in `progress.md`. Do this only if a
data-residency or contractual requirement forces it.
