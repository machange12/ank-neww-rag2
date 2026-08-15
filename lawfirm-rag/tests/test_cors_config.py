"""CORS allow-origins configuration tests."""
from __future__ import annotations

import pytest

from config import Settings


def test_production_rejects_wildcard():
    with pytest.raises(ValueError, match="allow_origins=\\['\\*'\\]"):
        Settings(environment="production", cors_origins="*").cors_allow_origins

    with pytest.raises(ValueError, match="allow_origins"):
        Settings(
            environment="production", cors_origins="*,http://localhost:5173"
        ).cors_allow_origins


def test_production_allows_explicit_list():
    s = Settings(environment="production", cors_origins="https://app.firm.law,https://admin.firm.law")
    assert s.cors_allow_origins == ["https://app.firm.law", "https://admin.firm.law"]


def test_development_allows_wildcard():
    s = Settings(environment="development", cors_origins="*")
    assert s.cors_allow_origins == ["*"]


def test_development_explicit_list_kept():
    s = Settings(environment="development", cors_origins="http://localhost:5173")
    assert s.cors_allow_origins == ["http://localhost:5173"]