"""Tests for collab.env_secrets placeholder handling."""

from __future__ import annotations

import pytest

from collab.env_secrets import (
    effective_anon_key,
    effective_env_secret,
    effective_service_role_key,
    is_placeholder_env_value,
)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "your_service_role_key_here",
        "your-anon-key",
        "your_project_url",
        "CHANGE_ME",
        "change_me",
        "replace_me",
        "TODO",
        "<team-supabase-url>",
    ],
)
def test_is_placeholder_env_value_true(value):
    assert is_placeholder_env_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "admin_key",
        "https://example.supabase.co",
    ],
)
def test_is_placeholder_env_value_false(value):
    assert is_placeholder_env_value(value) is False


def test_effective_env_secret_strips_real_values():
    assert effective_env_secret("  secret-value  ") == "secret-value"


def test_effective_service_role_key_ignores_placeholder(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "your_service_role_key_here")
    assert effective_service_role_key() is None


def test_effective_anon_key_ignores_placeholder(monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "your_anon_key")
    assert effective_anon_key() is None


def test_effective_service_role_key_raw_override():
    assert effective_service_role_key("real-service-key") == "real-service-key"
    assert effective_service_role_key("your_service_role_key_here") is None
