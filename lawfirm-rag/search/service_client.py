from __future__ import annotations

from supabase import Client, create_client

from config import settings


def make_service_client() -> Client:
    """
    Supabase client authenticated with the SERVICE ROLE key.
    Bypasses RLS — use ONLY in the ingest worker and cleanup scripts.
    NEVER import this into app.py or any chat-path module.
    """
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
