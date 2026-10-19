# Render → OCI cutover checklist

Practical, ordered runbook for moving the **app + built frontend** off Render's
free plan onto an OCI VM. **The database stays on Supabase** — this is a hosting
move only, with no schema or authz changes.

> Scope reminder: moving the *database* to Oracle is a separate, multi-week
> rewrite (see the last section of [`oci-deployment.md`](oci-deployment.md)).
> Nothing in this checklist touches it.

**Why do this at all** (the specific wins for this app): Render's free instance is
0.1 CPU / 512 MB and **spins down after 15 idle minutes**, which (a) makes a
draft take ~1 min to open after a cold start, and (b) silently stops the
**in-process 6-hourly Google Drive poll** (`app.py` → `ingest/scheduler.py`) —
so ingestion quietly stalls. A free OCI A1 VM is 2 OCPU / 12 GB and **always on**.

**What you are signing up for:** the ops tax. Render manages TLS, logs, metrics
and rollback; on a VM you own those. The mitigations are listed at the end.

Legend: `[ ]` to do · `[B]` blocking · `[M]` mitigation applied in this doc.

---

## 0. Pre-flight (do this locally, before touching OCI)

- [ ] `[B]` Tests pass: `cd lawfirm-rag && ../.venv/Scripts/python -m pytest tests/ -q` → **174 passed**
- [ ] `[B]` Schema matches migrations: `python scripts/verify_schema.py --manifest-only` → `SCHEMA OK`
- [ ] `[B]` Frontend builds: `cd frontend && npm run build` → writes `lawfirm-rag/static/`
- [ ] `[B]` **The Docker image has never actually been built.** Verify it now —
      either locally (`docker compose build`) or by letting CI do it
      ([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) builds for
      `linux/amd64` **and** `linux/arm64`). The OCI A1 shape is **Arm**, so the
      arm64 build is the one that matters.
- [ ] `[B]` Production env is complete: `python scripts/check_production_env.py --require-worker-token`
      (see §4). This is the single most common silent failure.
- [ ] Note the current Render deploy SHA (rollback target) and the Render URL.

## 1. Provision OCI

- [ ] `[B]` Pick a **home region** co-located with Supabase (currently
      `eu-west-1`) — Frankfurt or Amsterdam. Latency here is a real user-facing
      cost: every chat makes 5–8 DB round-trips.
- [ ] Create the VCN → subnet → Internet Gateway; route `0.0.0.0/0` to it.
- [ ] Security List / NSG ingress:
  - TCP 22 from **your admin IP only**
  - TCP 80 from `0.0.0.0/0`
  - TCP 443 from `0.0.0.0/0`
  - close everything else
- [ ] `[B]` Create the **Ampere A1** instance: `VM.Standard.A1.Flex`,
      **2 OCPU / 12 GB**. If you get *"out of host capacity"*, retry / another AD.
      Do **not** use the AMD micro (`E2.1.Micro`, 1/8 OCPU / 1 GB) — too small.
- [ ] `[B]` Assign a **reserved public IP** (an ephemeral IP changes on
      stop/start and silently breaks DNS + TLS).
- [ ] Create the DNS A record → reserved IP.

## 2. Base OS

```bash
# Oracle Linux 9
sudo dnf install -y git nginx python3.11 python3.11-pip firewalld
sudo systemctl enable --now firewalld nginx
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --reload
sudo setsebool -P httpd_can_network_connect 1     # [M] SELinux, else nginx 502s
```

- [ ] `[B]` `firewalld` **and** the OCI Security List both allow 80/443 (the
      classic "opened the port in OCI but still unreachable").
- [ ] `[M]` Enable automatic security updates: `sudo dnf install -y dnf-automatic`
      then set `apply_updates = yes` in `/etc/dnf/automatic.conf`.
      (Ubuntu: `sudo apt install unattended-upgrades`.)

## 3. Code + secrets

```bash
sudo useradd --create-home --shell /usr/sbin/nologin lawfirm
sudo mkdir -p /opt/lawfirm-rag && sudo chown "$USER" /opt/lawfirm-rag
git clone https://github.com/machange12/ank-neww-rag2.git /opt/lawfirm-rag
cp /opt/lawfirm-rag/.env.example /opt/lawfirm-rag/.env
chmod 600 /opt/lawfirm-rag/.env
```

- [ ] `[B]` `.env` sits at the **repo root** (`/opt/lawfirm-rag/.env`), not in
      `lawfirm-rag/` — `config.py` resolves `ROOT_DIR` to the parent.
- [ ] `[B]` `chmod 600` it; it holds the Supabase service-role key and JWT secret.

## 4. Production `.env`

Fill from the Render dashboard (Environment tab) — same values, Supabase
untouched. Then validate:

```bash
cd /opt/lawfirm-rag/lawfirm-rag
../.venv/bin/python scripts/check_production_env.py --require-worker-token
```

- [ ] `[B]` `ENVIRONMENT=production` (rejects CORS `*` and refuses unverified JWTs)
- [ ] `[B]` `CORS_ORIGINS=https://your.domain` — explicit, **no `*`**
- [ ] `[B]` `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_JWT_SECRET`
- [ ] `[B]` `POSTGRES_DSN` (scripts only)
- [ ] `[B]` **A hosted embedder**: `OPENAI_API_KEY` **or**
      `REMOTE_EMBED_URL` + `REMOTE_EMBED_KEY` + `REMOTE_EMBED_MODEL`
      (the image ships no torch — without one, the first embed call fails)
- [ ] `[B]` `GROQ_API_KEY`
- [ ] `[B]` `INGEST_WORKER_TOKEN` if you expose the worker routes
- [ ] `EMBEDDING_DIM=768` (must match the live `vector(768)` columns)
- [ ] Optional: `COHERE_API_KEY` (rerank), Google Drive `GOOGLE_*` / `DRIVE_*`

## 5. Deploy — pick ONE path

**A. Docker Compose (recommended; stage 1 builds the frontend for you)**

```bash
cd /opt/lawfirm-rag && docker compose up -d --build
docker compose logs -f app
```

**B. Bare-VM systemd**

```bash
cd /opt/lawfirm-rag
python3.11 -m venv .venv
.venv/bin/pip install --no-cache-dir -r lawfirm-rag/requirements.txt
cd frontend && npm ci && npm run build && cd ..
sudo cp deploy/oci/lawfirm-rag.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lawfirm-rag
```

- [ ] `[B]` **Exactly one app worker** (`--workers 1`). The Drive poll is
      in-process; extra workers = extra polls. Same for extra containers.
- [ ] `[B]` Start the **worker** too — it owns the nightly cleanup and the
      Drive-webhook / manual-reingest routes:
      `sudo cp deploy/oci/lawfirm-rag-worker.service /etc/systemd/system/`
      `sudo systemctl enable --now lawfirm-rag-worker`
- [ ] `[M]` `Restart=always` is already set in both units.

## 6. nginx + TLS

```bash
sudo cp deploy/oci/nginx-lawfirm-rag.conf /etc/nginx/conf.d/
sudo sed -i 's/rag.example.com/your.domain/g' /etc/nginx/conf.d/lawfirm-rag.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your.domain
```

- [ ] `[B]` The provided config keeps `proxy_buffering off` + 300s timeouts —
      remove either and chat / section-generation SSE will stop streaming
      token-by-token (it fails *quietly*, so do not "simplify" the config).
- [ ] `[M]` Confirm cert renewal is armed: `systemctl list-timers | grep certbot`
      (`certbot` installs a timer; without it TLS expires in 90 days).

## 7. Verify **before** the DNS flip

Reach the new box directly (temporary Host header) so Render stays live:

```bash
curl -fsS -H "Host: your.domain" http://<RESERVED-IP>/healthz     # {"status":"ok"}
```

- [ ] `[B]` `/healthz` returns ok
- [ ] `[B]` Sign in, run one chat **and** stream one draft section (proves
      Supabase auth + SSE through nginx)
- [ ] `[B]` Upload one document and confirm it ingests (proves the embedder)
- [ ] `[B]` Live schema check: `../.venv/bin/python scripts/verify_schema.py` → `SCHEMA OK`
- [ ] `[B]` Confirm the Drive scheduler started: `docker compose logs app | grep -i schedul`
      (or `journalctl -u lawfirm-rag | grep -i schedul`)

## 8. DNS cutover

- [ ] `[B]` Lower the DNS TTL to 60s **a day earlier** so the flip propagates fast.
- [ ] Repoint the A record (or Cloudflare CNAME) to the reserved IP.
- [ ] `[B]` Re-verify `https://your.domain/healthz` and one streamed chat.
- [ ] `[M]` **Keep Render running** (do not delete) for a few days — it is your
      instant rollback.

## 9. Rollback

Repoint DNS back to Render and confirm `/healthz`. The database was never
touched, so there is no data rollback to worry about.

## 10. After it is stable

- [ ] Delete/scale down the Render service and remove `render.yaml` if you're
      fully committed (or keep the file as documented history).
- [ ] `[M]` Backups: OCI block-volume backup policy, or a nightly `restic`/`pg_dump`
      of anything stateful. (Note: the *database* is Supabase and is backed up
      there; the VM holds only code + `.env`.)
- [ ] `[M]` Monitoring: point a free uptime monitor at `/healthz` (this also
      keeps the instance non-idle, which protects against Oracle's reclamation).
- [ ] `[M]` Deploys: add a deploy workflow (Actions → SSH → `git pull` +
      `docker compose up -d --build`) or a systemd timer; there is no
      auto-deploy on a plain VM.

---

## Residual risks (and why they're acceptable)

| Risk | Why it's fine / mitigation |
|---|---|
| Oracle reclaims an **idle** instance (>7 days under 20% CPU/network/memory) | The 6-hourly Drive poll + uptime monitoring keep it active; upgrade the tenancy to Pay-As-You-Go to remove the rule entirely (Always Free resources stay free). |
| "Out of host capacity" on A1 | Transient; retry or another availability domain. |
| Arm (aarch64) image | Proved by the CI arm64 build **before** you deploy. |
| You patch the OS | `dnf-automatic` / `unattended-upgrades`. |
| Single VM, no managed metrics | Uptime monitor + OCI Monitoring (500M free data points). |
| Latency to Supabase (`eu-west-1`) | Choose Frankfurt/Amsterdam. Fixing it properly means moving the DB — the multi-week rewrite, so accept ~10–20 ms. |
