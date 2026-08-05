from __future__ import annotations

from supabase import Client, create_client

from config import settings


def make_anon_client() -> Client:
    """Supabase client authenticated with the public anon key. Use for login only."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def make_user_client(access_token: str) -> Client:
    """
    Supabase client authenticated with the user's own JWT.
    All PostgREST calls made through this client run as the authenticated role,
    so RLS policies and GUCs from set_access_context apply.
    """
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client


def make_service_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
