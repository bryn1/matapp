"""tests.conftest — shared fixtures for the matapp framtidsversion test suite.

Boots the app stack against a TEMP sqlite DB (never the repo's .data/), the same
staging the audit (MC 1188.9) used: rebind app.db._engine/_Session to a temp file
BEFORE boot(), then create the schema. Also hands out fresh per-test auth
singletons (SessionStore / LoginRateLimiter) so tests never share limiter state.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# matapp S3 c4 (MC 10468): SessionStore reads MATAPP_SESSION_TTL /
# MATAPP_SESSION_IDLE ONCE at import, and test modules import app.auth_service
# at COLLECTION time — before any fixture can run. Conftest import precedes
# every test module, so scrub exported knobs HERE; a stray export must never
# bake a wrong expiry policy into the class (false-RED whole suite).
os.environ.pop("MATAPP_SESSION_TTL", None)
os.environ.pop("MATAPP_SESSION_IDLE", None)

# Make the app package importable: tests/ sits under apps/matapp/.
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@pytest.fixture()
def fresh_auth(monkeypatch):
    """Fresh SessionStore + LoginRateLimiter singletons for one test."""
    from app import auth_service

    monkeypatch.setattr(auth_service, "sessions", auth_service.SessionStore())
    monkeypatch.setattr(auth_service, "login_limiter", auth_service.LoginRateLimiter())
    return auth_service


@pytest.fixture()
def client(fresh_auth):
    """FastAPI TestClient against a temp-DB-booted app stack.

    The temp engine is bound BEFORE app.main is imported: app.main's lifespan
    calls db.boot(), which reuses an already-bound _engine (boot only ADDS
    schema, never replaces a bind) — so no request ever touches the repo's
    .data/ sqlite (which is read-only for this user).
    """
    from fastapi.testclient import TestClient

    from app import db as dbm
    from database import init_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(f"sqlite:///{tmp}/test.db")
        init_db(engine)
        dbm._engine = engine
        dbm._Session = sessionmaker(bind=engine, autoflush=False)
        from app.main import app  # import AFTER the temp bind (lifespan reuses it)
        yield TestClient(app)
        dbm._engine = None
        dbm._Session = None
