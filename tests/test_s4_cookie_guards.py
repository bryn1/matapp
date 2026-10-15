"""tests.test_s4_cookie_guards — matapp-security S4 cookie-attribute guards (MC 10469).

The session cookie IS the credential, so every attribute of the Set-Cookie the
app mints is pinned by its OWN named test, on BOTH routes that mint one
(login and register share app.routers.auth._set_session_cookie). One named test
per attribute means a stripped attribute names itself in the failing test —
each guard was plant-proven RED by stripping exactly that attribute (transcript
in T4-RESULT.md, MC 10469). HSTS and CSP are Cloudflare-layer headers and are
NOT app-testable (stated honestly in the result doc).
"""
from __future__ import annotations

LOGIN = "/api/auth/login"
REGISTER = "/api/auth/register"
PW = "Sup3r-Secret-1"


def _raw_cookie(response) -> str:
    """DA-P2 (gate 202610150832): httpx JOINS repeated Set-Cookie headers, so
    substring-matching the union lets ANY other cookie satisfy an attribute
    (measured masking plant: session cookie without Secure + a second cookie
    carrying Secure -> 7/7 green). Select the session cookie's OWN segment and
    assert per-cookie on that one only."""
    entries = [c for c in response.headers.get_list("set-cookie")
               if c.startswith("matapp_session=")]
    assert len(entries) == 1, (
        f"expected exactly one matapp_session cookie, got {len(entries)}")
    return entries[0].lower()


def _register(client, username: str):
    r = client.post(REGISTER, json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r


def _login(client, username: str):
    r = client.post(LOGIN, json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------ login side

def test_login_cookie_is_httponly(client):
    """No client script may read the session cookie."""
    _register(client, "s4c-htto")
    assert "httponly" in _raw_cookie(_login(client, "s4c-htto"))


def test_login_cookie_is_secure(client):
    """The cookie only ever travels over HTTPS."""
    _register(client, "s4c-sec")
    assert "secure" in _raw_cookie(_login(client, "s4c-sec"))


def test_login_cookie_is_samesite_lax(client):
    """CSRF posture: SameSite=lax exactly (not strict/none/absent)."""
    _register(client, "s4c-ss")
    raw = _raw_cookie(_login(client, "s4c-ss"))
    assert "samesite=lax" in raw
    assert "samesite=strict" not in raw and "samesite=none" not in raw


# ---------------------------------------------------------------- register side
# register auto-logs in, so it mints a cookie through the SAME setter; if the
# register route ever stopped setting it, every register test below goes RED.

def test_register_cookie_is_httponly(client):
    assert "httponly" in _raw_cookie(_register(client, "s4c-r-htto"))


def test_register_cookie_is_secure(client):
    assert "secure" in _raw_cookie(_register(client, "s4c-r-sec"))


def test_register_cookie_is_samesite_lax(client):
    raw = _raw_cookie(_register(client, "s4c-r-ss"))
    assert "samesite=lax" in raw
    assert "samesite=strict" not in raw and "samesite=none" not in raw


# ------------------------------------------------------------------- Max-Age

def test_session_cookie_max_age_is_ttl(client, fresh_auth):
    """The named Max-Age guard for BOTH minting routes: Set-Cookie Max-Age
    equals the server-side absolute session TTL, so the cookie dies no later
    than the server-side entry it authenticates to.

    F4-pending (owner ruling 30d-persistent vs browser-session cookie): pins
    CURRENT shipped behaviour. On owner option 1 exactly TWO tests flip — this
    one AND tests/test_session_ttl_t3.py::test_login_set_cookie_carries_max_age
    (gate F5: the Max-Age contract is asserted in both files; this one adds the
    register side)."""
    from app.auth_service import SessionStore

    max_age = f"max-age={int(SessionStore.TTL)}"
    _register(client, "s4c-ma")
    assert max_age in _raw_cookie(_login(client, "s4c-ma"))
    assert max_age in _raw_cookie(_register(client, "s4c-ma-reg"))
