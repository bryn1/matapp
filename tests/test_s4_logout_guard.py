"""tests.test_s4_logout_guard — matapp-security S4 logout (MC 10469, F3 gap).

Suite-wide there was NO logout test at base: the audit's F3 gap. The shipped
contract (auth_service.logout): logout kills EXACTLY the token that presented
itself — the same user's other sessions survive (multi-device is legitimate),
the presented token dies (its next /api/auth/me is the SAME byte-identical 401
as a never-issued token, no oracle), and the operation is idempotent. Plant-
proven RED twice: no-op logout (killed token still 200) and clear-all logout
(sibling token wrongly 401) — transcripts in T4-RESULT.md.
"""
from __future__ import annotations

LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
ME = "/api/auth/me"
PW = "Sup3r-Secret-1"


def _register(client, username: str) -> str:
    """Register (auto-login) and return the minted session token."""
    r = client.post("/api/auth/register", json={"username": username,
                                                "password": PW})
    assert r.status_code == 200, r.text
    return r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


def _me(client, token: str):
    from app import security
    return client.get(ME, cookies={security.SESSION_COOKIE: token})


def test_logout_kills_exactly_the_presenting_token(client, fresh_auth):
    token1 = _register(client, "s4-logout")
    # A SECOND, independent session for the SAME user (e.g. another device).
    token2 = fresh_auth.login("s4-logout", PW)
    assert token2 is not None and token2 != token1
    assert _me(client, token1).status_code == 200
    assert _me(client, token2).status_code == 200

    from app import security
    client.cookies.set(security.SESSION_COOKIE, token1)
    r = client.post(LOGOUT)
    assert r.status_code == 200, r.text

    # The presenting token is dead: gone from the store, and its 401 is
    # byte-identical to a never-issued token's (no expired/invalid oracle).
    assert token1 not in fresh_auth.sessions._sessions
    dead = _me(client, token1)
    unknown = _me(client, "never-issued-token")
    assert dead.status_code == 401
    assert dead.content == unknown.content

    # The sibling token of the same user SURVIVES (logout never nukes the user).
    assert _me(client, token2).status_code == 200
    assert token2 in fresh_auth.sessions._sessions

    # Idempotent: logging out the already-dead token is a 200 no-op, and it
    # must not touch the survivor either.
    client.cookies.set(security.SESSION_COOKIE, token1)
    assert client.post(LOGOUT).status_code == 200
    assert _me(client, token2).status_code == 200


def test_logout_clears_the_browser_cookie(client, fresh_auth):
    """DA-F6 (gate 202610150832): logout must also tell the BROWSER to drop
    the cookie. Measured at the c1 gate: replacing _clear_session_cookie
    (response) with pass left the FULL suite green (228) — nothing read the
    logout response at all. This guard pins the clearing Set-Cookie: an empty
    matapp_session value with Max-Age=0 (starlette delete_cookie emits
    set_cookie(value="", max_age=0, expires=0))."""
    from app import security

    token = _register(client, "s4-logout-clear")
    client.cookies.set(security.SESSION_COOKIE, token)
    r = client.post(LOGOUT)
    assert r.status_code == 200, r.text
    clears = [c.lower() for c in r.headers.get_list("set-cookie")
              if c.startswith("matapp_session=")]
    assert len(clears) == 1, r.headers.get_list("set-cookie")
    name, _, value = clears[0].split(";", 1)[0].partition("=")
    assert name == "matapp_session"
    assert value.strip('"') == ""  # SimpleCookie quotes the empty value on the wire
    assert "max-age=0" in clears[0]
