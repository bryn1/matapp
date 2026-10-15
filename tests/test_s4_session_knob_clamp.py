"""tests.test_s4_session_knob_clamp — matapp-security S4 session-knob clamp
(MC 10469, F3-deferred from T3; c2 per gate 202610150832 DA-P1-1 / DA-P1-2).

MATAPP_SESSION_TTL / MATAPP_SESSION_IDLE are read ONCE at SessionStore class
definition. The clamp (auth_service._env_seconds) honours ONLY plain numbers
inside 60 s .. 10 y and falls back to the shipped default otherwise, logging
one warning on the app logger when an operator's value is discarded. Guards,
each plant-proven RED (transcript sec/.tmp/plants.log, c2 rows):
  * grammar: garbage/underscore/hex/spaces/inf/nan -> default
    (plant: regex gate removed -> the "1_000"-family params RED)
  * lower bound: 0/0.4/59/59.9 -> default
    (plant: MIN check removed -> RED; the c1 "0.5" bless-row is REMOVED —
    gate DA-F2 measured Max-Age=int(0.4)=0, the dead-on-arrival cookie)
  * upper bound: >10 y and huge-exponent inf -> default
    (plant: MAX check removed -> RED)
  * boundaries: exactly 60 s and exactly 10 y are the LAST honoured values
  * warning: fallback logs on kvallsmat.app (grammar AND bounds); a clean
    unset boot logs nothing (plants: warning call removed / unconditional)
  * import-time: reload with poisoned knobs boots at the shipped 30d/14d
    values — asserts INSIDE the try, on the POISONED boot's class values
    (DA-P1-1: the c1 assert-after-finally read the CLEAN reload and was
    structurally vacuous for 4/5 params; proof plant: class attrs back to raw
    float(os.environ.get(...)) -> ALL 5 params RED).
"""
from __future__ import annotations

import importlib
import logging

import pytest

DEFAULT_TTL = 30 * 24 * 3600
DEFAULT_IDLE = 14 * 24 * 3600

# Grammar cases (bound cases live in their own named tests below).
GARBAGE = ["", "   ", "abc", "0x10", "10 20", "86400x", "12 ",
           "-1", "-86400", "-0.0", "nan", "NaN", "inf", "-inf", "1e5000"]


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


@pytest.mark.parametrize("raw", ["1_000", "1_0", "86_400"])
def test_underscore_literal_typo_is_rejected(monkeypatch, raw):
    """float("1_000") == 1000.0 in Python — without the strict grammar gate a
    typo silently becomes a real (16-minute) session policy (DA-F2)."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0


@pytest.mark.parametrize("raw", ["0", "0.4", "59", "59.9"])
def test_below_min_bound_falls_back(monkeypatch, raw):
    """Sub-minute lifetimes ship a dead-on-arrival (int(0.4)=0 Max-Age) or
    operationally meaningless cookie; MIN is 60 s."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0


@pytest.mark.parametrize("raw", ["315360001", "86400000000", "1e308"])
def test_above_max_bound_falls_back(monkeypatch, raw):
    """Beyond 10 years a session never effectively expires — the posture the
    clamp exists to forbid (DA-F2: 1e308 reached the wire as a 309-digit
    Max-Age before the bound)."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0


@pytest.mark.parametrize("raw,expected", [
    ("60", 60.0),                      # exactly MIN: last honoured value
    ("120", 120.0),
    ("86400", 86400.0),
    ("315360000", 315360000.0),        # exactly MAX: last honoured value
])
def test_valid_override_is_still_honoured(monkeypatch, raw, expected):
    """The clamp must not become an always-default stub — an operator's sane
    shortening (the harness use case the S3 doc advertises) still works; the
    bounds themselves are inclusive."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", raw)
    assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == expected


def test_fallback_logs_warning_on_garbage(caplog, monkeypatch):
    """DA-F3: the fallback is no longer invisible — one WARNING naming the
    knob and the discarded value lands on the app's own logger."""
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_TTL", "abc")
    with caplog.at_level(logging.WARNING, logger="kvallsmat.app"):
        assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0
    assert any(r.levelname == "WARNING"
               and "MATAPP_SESSION_TTL" in r.getMessage()
               and "abc" in r.getMessage() for r in caplog.records)


def test_fallback_logs_warning_on_out_of_bounds(caplog, monkeypatch):
    from app import auth_service
    monkeypatch.setenv("MATAPP_SESSION_IDLE", "59")
    with caplog.at_level(logging.WARNING, logger="kvallsmat.app"):
        assert auth_service._env_seconds("MATAPP_SESSION_IDLE", 456.0) == 456.0
    assert any(r.levelname == "WARNING"
               and "MATAPP_SESSION_IDLE" in r.getMessage()
               for r in caplog.records)


def test_unset_knob_logs_no_warning(caplog, monkeypatch):
    """Clean boot (knob unset) is normal operation — the logger stays silent;
    a plant making the warning unconditional turns this RED."""
    from app import auth_service
    monkeypatch.delenv("MATAPP_SESSION_TTL", raising=False)
    with caplog.at_level(logging.WARNING, logger="kvallsmat.app"):
        assert auth_service._env_seconds("MATAPP_SESSION_TTL", 123.0) == 123.0
    assert not [r for r in caplog.records if "MATAPP_SESSION" in r.getMessage()]


@pytest.mark.parametrize("poison", ["garbage", "0", "-5", "nan", "inf"])
def test_import_with_poisoned_knobs_boots_shipped_defaults(monkeypatch, poison):
    """End-to-end on the POISONED boot: class values are captured INSIDE the
    try, before the finally-restore reloads clean (DA-P1-1 fix — the c1
    asserts ran after the finally and read the clean reload, vacuous for 4/5
    params). Proof plant: class attrs back to raw float(os.environ.get(...))
    -> ALL 5 params RED: crash-boot on "garbage", silently 0/negative/inf/nan
    expiry policies on the rest)."""
    import app.auth_service as m

    monkeypatch.setenv("MATAPP_SESSION_TTL", poison)
    monkeypatch.setenv("MATAPP_SESSION_IDLE", poison)
    try:
        importlib.reload(m)  # a crash here IS the failure (base: "garbage")
        shipped_ttl = m.SessionStore.TTL
        shipped_idle = m.SessionStore.IDLE_TIMEOUT
        inst = m.SessionStore()
    finally:
        monkeypatch.delenv("MATAPP_SESSION_TTL", raising=False)
        monkeypatch.delenv("MATAPP_SESSION_IDLE", raising=False)
        importlib.reload(m)  # restore clean class values for later tests
    assert shipped_ttl == DEFAULT_TTL
    assert shipped_idle == DEFAULT_IDLE
    assert inst.TTL == DEFAULT_TTL  # instance view of the poisoned boot too
