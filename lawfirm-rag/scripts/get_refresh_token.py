"""
get_refresh_token.py
====================
Runs a local OAuth 2.0 flow to obtain a Google Drive refresh token.

If you already have a valid access_token / refresh_token from a previous run,
you can skip the interactive browser flow by calling get_refresh_token(
    GOOGLE_REFRESH_TOKEN=..., GOOGLE_OAUTH_CLIENT_ID=..., GOOGLE_OAUTH_CLIENT_SECRET=...
) yourself; this script always opens the browser for a fresh token.

Usage:
    python scripts/get_refresh_token.py

Environment:
    GOOGLE_OAUTH_CLIENT_ID      — OAuth 2.0 client ID (Desktop app)
    GOOGLE_OAUTH_CLIENT_SECRET  — OAuth 2.0 client secret
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Unlike the rest of the app (config.py, via pydantic-settings), this script
# previously read raw os.getenv() only — meaning GOOGLE_OAUTH_CLIENT_ID/
# SECRET in .env were never actually picked up unless separately exported
# into the shell. Load .env explicitly, same file config.py uses.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def main() -> int:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print(
            "ERROR: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
            "must be set in the environment.",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # Runs a loopback redirect server so the auth code never needs manual pasting.
    creds = flow.run_local_server(port=0, prompt="consent")

    if not creds.refresh_token:
        print(
            "ERROR: no refresh token returned (offline access not enabled).",
            file=sys.stderr,
        )
        return 1

    print(creds.refresh_token)
    print("\nPaste this into your .env as GOOGLE_REFRESH_TOKEN=" + creds.refresh_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())