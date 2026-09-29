"""Tests for Supabase key validation."""

import base64
import json
from unittest.mock import patch

import pytest

from db import supabase_client
from db.supabase_client import SupabaseClient, supabase_key_role


def _jwt(role: str) -> str:
    def encode(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()
    return f"{encode({'alg': 'HS256'})}.{encode({'iss': 'supabase', 'role': role})}.signature"


@pytest.mark.parametrize("key, role", [
    (_jwt('service_role'), 'service_role'),
    (_jwt('anon'), 'anon'),
    ('sb_secret_abc123', 'service_role'),
    ('sb_publishable_abc123', 'anon'),
    ('not-a-key', None),
    ('a.%%%.c', None),
])
def test_supabase_key_role(key, role):
    assert supabase_key_role(key) == role


@pytest.fixture(autouse=True)
def reset_singleton():
    SupabaseClient.reset()
    yield
    SupabaseClient.reset()


def test_client_refuses_anon_key(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', _jwt('anon'))

    with patch.object(supabase_client, 'create_client') as create_client:
        with pytest.raises(ValueError, match="service_role"):
            SupabaseClient()

    create_client.assert_not_called()


def test_client_accepts_service_role_key(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', _jwt('service_role'))

    with patch.object(supabase_client, 'create_client') as create_client:
        SupabaseClient()

    create_client.assert_called_once()
