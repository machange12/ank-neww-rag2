"""
check_production_env.py
=======================
Validate a production ``.env`` **before** you deploy, so the two silent killers
- no hosted embedder, and a CORS allow-list that isn't one - fail on the box's
own terms instead of as a mystery 500/502 after cutover.

This is a static check: it reads the file (or the process environment) and
reasons about presence/format. It never connects to Supabase, Postgres or a
model provider, so it is safe and fast to run anywhere, including CI.

Usage:
    python scripts/check_production_env.py                     # repo-root .env
    python scripts/check_production_env.py --env-file .env.prod
    python scripts/check_production_env.py --require-worker-token
    python scripts/check_production_env.py --json

Exit codes:
    0  no errors (warnings may still be printed)
    1  one or more blocking errors
    2  the env file could not be read

See docs/operations/render-to-oci-cutover.md section 4 and oci-deployment.md section 7.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The repo root is two levels up from lawfirm-rag/scripts/.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Column dimension is baked into the live schema (documents.embedding and
# draft_clauses.embedding are vector(768)); a mismatch silently breaks retrieval.
EXPECTED_EMBEDDING_DIM = "768"

# Substrings that mean "still the example value", not a real secret. Checked
# case-insensitively. Kept conservative so real keys never trip it.
PLACEHOLDER_MARKERS = (
    "your-",
    "your_",
    "choose-a-",
    "changeme",
    "change-me",
    "replace-me",
    "placeholder",
    "example.com",
    "xxxx",
    "todo",
)

# (key, human label) pairs that must be present and non-placeholder in production.
REQUIRED_KEYS = (
    ("SUPABASE_URL", "Supabase project URL"),
    ("SUPABASE_ANON_KEY", "Supabase anon key"),
    ("SUPABASE_SERVICE_ROLE_KEY", "Supabase service-role key"),
    ("SUPABASE_JWT_SECRET", "Supabase JWT secret (unverified JWTs are refused)"),
    ("POSTGRES_DSN", "direct Postgres DSN (scripts: verify_schema, migrations)"),
    ("GROQ_API_KEY", "Groq chat key"),
)

# Optional services: absent is fine, half-configured is a warning.
GOOGLE_DRIVE_KEYS = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "DRIVE_FOLDER_ID",
)


def parse_env_file(text: str) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, ignores comments/blanks, tolerates a
    leading ``export `` and strips one layer of surrounding quotes."""
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def is_placeholder(value: str | None) -> bool:
    """True when a value is empty or still looks like an example/placeholder."""
    v = (value or "").strip().lower()
    if not v:
        return True
    return any(marker in v for marker in PLACEHOLDER_MARKERS)


def check_environment(
    env: dict[str, str],
    *,
    require_worker_token: bool = False,
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)``. Errors block a deploy; warnings don't."""
    errors: list[str] = []
    warnings: list[str] = []

    def get(key: str) -> str:
        return (env.get(key) or "").strip()

    # --- environment mode -------------------------------------------------
    environment = get("ENVIRONMENT").lower()
    if environment != "production":
        errors.append(
            f"ENVIRONMENT is {get('ENVIRONMENT') or '(unset)'!r}; must be 'production' "
            "so CORS '*' is rejected and unverified JWTs are refused."
        )

    # --- CORS: explicit allow-list, never '*' -----------------------------
    cors = get("CORS_ORIGINS")
    if not cors:
        errors.append("CORS_ORIGINS is unset; set an explicit allow-list (e.g. https://rag.example.org).")
    elif "*" in cors:
        errors.append("CORS_ORIGINS contains '*'; production requires an explicit allow-list.")
    elif any(o.startswith("http://localhost") or o.startswith("http://127.0.0.1") for o in cors.split(",")):
        warnings.append("CORS_ORIGINS still lists a localhost origin - remove it in production.")

    # --- required secrets -------------------------------------------------
    for key, label in REQUIRED_KEYS:
        value = get(key)
        if is_placeholder(value):
            errors.append(f"{key} is missing or still a placeholder ({label}).")

    # --- a hosted embedder must exist (the image has no torch) ------------
    openai = get("OPENAI_API_KEY")
    remote = (get("REMOTE_EMBED_URL"), get("REMOTE_EMBED_KEY"), get("REMOTE_EMBED_MODEL"))
    remote_complete = all(not is_placeholder(part) for part in remote)
    remote_any = any(part for part in remote)
    if is_placeholder(openai) and not remote_complete:
        errors.append(
            "No hosted embedder: set OPENAI_API_KEY, or all three of "
            "REMOTE_EMBED_URL + REMOTE_EMBED_KEY + REMOTE_EMBED_MODEL. "
            "The container installs no torch, so the first embed call will fail."
        )
    elif is_placeholder(openai) and remote_any and not remote_complete:
        errors.append(
            "REMOTE_EMBED_* is only partially set "
            f"(url={bool(remote[0])}, key={bool(remote[1])}, model={bool(remote[2])})."
        )

    # --- embedding dimension must match the live vector(768) columns ------
    dim = get("EMBEDDING_DIM")
    if dim and dim != EXPECTED_EMBEDDING_DIM:
        errors.append(
            f"EMBEDDING_DIM={dim!r} but the live schema is vector({EXPECTED_EMBEDDING_DIM}). "
            "Changing it requires migration + re-embedding documents AND draft_clauses."
        )
    elif not dim:
        warnings.append(f"EMBEDDING_DIM unset; the code default is {EXPECTED_EMBEDDING_DIM}.")

    # --- worker token -----------------------------------------------------
    if require_worker_token and is_placeholder(get("INGEST_WORKER_TOKEN")):
        errors.append(
            "INGEST_WORKER_TOKEN is unset; POST /ingest/manual fails closed "
            "(required when the worker routes are exposed)."
        )

    # --- format sanity ----------------------------------------------------
    supa = get("SUPABASE_URL")
    if supa and not supa.startswith("https://"):
        errors.append(f"SUPABASE_URL should be https://... (got {supa[:24]!r}).")
    dsn = get("POSTGRES_DSN")
    if dsn and not dsn.startswith(("postgresql://", "postgres://")):
        errors.append("POSTGRES_DSN must start with postgresql:// .")

    # --- optional services: warn when half-configured ---------------------
    if is_placeholder(get("COHERE_API_KEY")):
        warnings.append("COHERE_API_KEY unset - reranking is disabled (retrieval still works).")

    google = {k: get(k) for k in GOOGLE_DRIVE_KEYS}
    missing_google = [k for k, v in google.items() if is_placeholder(v)]
    if missing_google and len(missing_google) != len(GOOGLE_DRIVE_KEYS):
        warnings.append(
            "Drive ingest partially configured; missing: " + ", ".join(missing_google)
        )
    elif len(missing_google) == len(GOOGLE_DRIVE_KEYS):
        warnings.append("Drive ingest not configured (all GOOGLE_*/DRIVE_* unset) - scheduled ingest is off.")

    if get("USE_EMBED_PREFIXES").lower() == "true":
        warnings.append(
            "USE_EMBED_PREFIXES=true only helps AFTER a full re-ingest; with a mixed "
            "corpus it hurts recall on legacy (prefix-less) documents."
        )

    return errors, warnings


def _resolve_env_path(raw: str) -> Path:
    """Relative paths resolve from the caller's cwd (normal CLI behaviour),
    falling back to the repo root so `--env-file .env` works from anywhere."""
    path = Path(raw)
    if path.is_absolute():
        return path
    from_cwd = Path.cwd() / path
    if from_cwd.exists():
        return from_cwd
    return REPO_ROOT / path


def _load_env(args: argparse.Namespace) -> dict[str, str]:
    if args.env_file:
        return parse_env_file(_resolve_env_path(args.env_file).read_text(encoding="utf-8"))
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        return parse_env_file(env_path.read_text(encoding="utf-8"))
    # Fall back to the process environment (containers often inject env vars).
    import os

    return {k: v for k, v in os.environ.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a production .env before deploying (static check)."
    )
    parser.add_argument("--env-file", default=None, help="Path to the env file (default: repo-root .env, else process env).")
    parser.add_argument("--require-worker-token", action="store_true", help="Also require INGEST_WORKER_TOKEN (worker routes exposed).")
    parser.add_argument("--json", action="store_true", help="Emit results as JSON.")
    args = parser.parse_args()

    try:
        env = _load_env(args)
    except OSError as exc:
        print(f"Could not read env file: {exc}", file=sys.stderr)
        return 2

    errors, warnings = check_environment(env, require_worker_token=args.require_worker_token)

    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, indent=2))
        return 1 if errors else 0

    if errors:
        print(f"PRODUCTION ENV FAILED ({len(errors)} error(s)):")
        for err in errors:
            print(f"  x {err}")
    else:
        print("PRODUCTION ENV OK")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for warn in warnings:
            print(f"  ! {warn}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
