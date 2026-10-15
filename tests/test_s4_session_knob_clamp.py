"""tests.test_s4_session_knob_clamp — matapp-security S4 session-knob clamp
(MC 10469, F3-deferred from T3).

MATAPP_SESSION_TTL / MATAPP_SESSION_IDLE are read ONCE at SessionStore class
definition. At base any value flowed straight into float(): garbage CRASHED the
whole app at import, and "0" / negative / inf / nan silently shipped an
instant-expiry or never-expiring session policy. The clamp
(auth_service._env_seconds) makes every such value fall back to the shipped
default. Guards, each plant-proven RED (transcript in T4-RESULT.md):
  * helper: garbage/0/negative/inf/nan -> default   (plant: drop val>0 -> "0"
    and "-1" pass through; plant: drop isfinite -> "inf" passes through)
  * helper: a VALID override is still honoured      (plant: always-default)
  * import-time: reload with poisoned knobs boots at the shipped 30d/14d
    values — never crash, never zero.
"""
from __future__ import annotations

import importlib

import pytest

DEFAULT_TTL = 30 * 24 * 3600
DEFAULT_IDLE = 14 * 24 * 3600

GARBAGE = ["", "   ", "abc", "0", "-0.0", "-1", "-86400", "inf", "-inf",
           "nan", "NaN", "1e5000", "86400x", "10 20"]


@pytest.mark.parametrize("raw", GARBAGE)
def test_garbage_ttl_falls_back_to_default(monkeypatch, raw):
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0


@pytest.mark.parametrize("raw", GARBAGE)
def test_garbage_idle_falls_back_to_default(monkeypatch, raw):
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_IDLE", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_IDLE", 456.0) == 456.0


@pytest.mark.parametrize("raw,expected", [("120", 120.0), ("86400", 86400.0),
                                          ("0.5", 0.5)])
def test_valid_override_is_still_honoured(monkeypatch, raw, expected):
    """The clamp must not become an always-default stub — an operator's sane
    shortening (the harness use case the S3 docstring advertises) still works."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == expected


@pytest.mark.parametrize("poison", ["garbage", "0", "-5", "nan", "inf"])
def test_import_with_poisoned_knobs_boots_shipped_defaults(monkeypatch, poison):
    """End-to-end: the knobs ARE consumed at class definition, so poison both
    env vars and RE-IMPORT the module — it must not raise and must ship the
    documented 30d/14d values (never 0, never nan/inf)."""
    import app.auth_service as m

    monkeypatch.setenv("MATAPP_SESSION_TTL", poison)
    monkeypatch.setenv("MATAPP_SESSION_IDLE", poison)
    try:
        importlib.reload(m)  # a crash here IS the failure (base: "garbage")
    finally:
        monkeypatch.delenv("MATAPP_SESSION_TTL", raising=False)
        monkeypatch.delenv("MATAPP_SESSION_IDLE", raising=False)
        importlib.reload(m)  # restore clean class values for later tests
    assert m.SessionStore.TTL == DEFAULT_TTL
    assert m.SessionStore.IDLE_TIMEOUT == DEFAULT_IDLE
    assert m.SessionStore().TTL == DEFAULT_TTL  # instance view too
