from __future__ import annotations

from supabase import Client, create_client

from config import settings


def make_anon_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def make_service_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def make_user_client(access_token: str, refresh_token: str | None = None) -> Client:
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.auth.set_session(access_token, refresh_token or "")
    return client
