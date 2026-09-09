"""Sign in with Google (DESIGN.md 12.2).

Entirely optional: local accounts work without any of this. It exists so that
relatives can use an account they already have rather than remember another
password.

**Authentication is not authorization.** All Google tells us is an email
address. That address still has to appear in `users.toml`, so having a Google
account grants nothing by itself — this only replaces the password check.

The authorization-code flow, with the code exchanged server-to-server:

1. We send the browser to Google with a random ``state`` kept in the session.
2. Google sends the browser back with a one-time ``code``.
3. *We* — not the browser — POST that code to Google over TLS, authenticating
   with the client secret, and get an ID token back.

Two consequences of doing it that way, both deliberate:

* The ID token arrives over an authenticated TLS connection directly from
  Google's token endpoint, so its signature does not have to be verified
  locally. OpenID Connect says so explicitly (Core §3.1.3.7), and it is what
  lets this module exist without a JWT/crypto dependency. The claims are still
  checked — issuer, audience, expiry, verified email — because the cost is
  nothing and a mistake here is total.
* The browser never handles a token, only a single-use code that is worthless
  without the client secret.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
import time
from dataclasses import dataclass

import requests

from .config import Config

log = logging.getLogger("harelphotos.google")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Only the email address. Anything more would be data we do not want, and
# asking for a sensitive scope is what triggers Google's verification review.
SCOPE = "openid email"

VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# Tolerance for clock skew between this machine and Google's.
LEEWAY_SECONDS = 120

SESSION_STATE = "g_state"
SESSION_NONCE = "g_nonce"
SESSION_NEXT = "g_next"

TIMEOUT = 15


class GoogleAuthError(Exception):
    """Anything that means we must not sign this person in."""


@dataclass(frozen=True)
class Identity:
    email: str
    subject: str


def enabled(cfg: Config) -> bool:
    return bool(cfg.google.enabled and cfg.google.client_id and cfg.google.client_secret)


def redirect_uri(cfg: Config) -> str:
    """Must match what is registered in the Google console, character for
    character — a trailing slash or http-vs-https is a hard error there."""
    return f"{cfg.base_url}/auth/google/callback"


def begin(cfg: Config, session: dict, next_url: str | None) -> str:
    """Pick the one-time values, stash them, and return where to send the browser."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    session[SESSION_STATE] = state
    session[SESSION_NONCE] = nonce
    session[SESSION_NEXT] = next_url or ""

    from urllib.parse import urlencode

    params = {
        "client_id": cfg.google.client_id,
        "redirect_uri": redirect_uri(cfg),
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        # We only ever want an identity, never offline access to anything.
        "access_type": "online",
        # Otherwise a browser already signed in to one Google account is sent
        # straight back, and someone with two accounts can never pick.
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _b64url_json(segment: str) -> dict:
    pad = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as e:
        raise GoogleAuthError(f"malformed ID token: {e}") from e
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GoogleAuthError(f"ID token is not JSON: {e}") from e
    if not isinstance(value, dict):
        raise GoogleAuthError("ID token payload is not an object")
    return value


def claims_from_id_token(token: str) -> dict:
    """Decode the payload. See the module docstring for why not verified here."""
    parts = token.split(".")
    if len(parts) != 3:
        raise GoogleAuthError("ID token is not a three-part JWT")
    return _b64url_json(parts[1])


def check_claims(cfg: Config, claims: dict, nonce: str | None) -> Identity:
    """Reject anything that is not a currently-valid token issued to us.

    Cheap, and the failure mode is signing in the wrong person, so every one of
    these is checked even though the transport already established provenance.
    """
    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleAuthError(f"unexpected issuer {claims.get('iss')!r}")
    # An ID token minted for a *different* client is still a genuine Google
    # token; without this check one could be replayed at us.
    if claims.get("aud") != cfg.google.client_id:
        raise GoogleAuthError("ID token was issued for a different client")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or time.time() > exp + LEEWAY_SECONDS:
        raise GoogleAuthError("ID token has expired")
    if nonce is not None and claims.get("nonce") != nonce:
        raise GoogleAuthError("ID token nonce does not match")

    email = (claims.get("email") or "").strip()
    if not email:
        raise GoogleAuthError("Google did not return an email address")
    # An unverified address is one the account holder merely typed in, so it
    # could be anyone's, including someone who has an account here.
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError(f"{email} is not verified with Google")
    return Identity(email=email, subject=str(claims.get("sub") or ""))


def exchange(cfg: Config, code: str) -> dict:
    """Trade the one-time code for an ID token, server to server."""
    try:
        resp = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": cfg.google.client_id,
                "client_secret": cfg.google.client_secret,
                "redirect_uri": redirect_uri(cfg),
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise GoogleAuthError(f"cannot reach Google: {e}") from e
    if resp.status_code != 200:
        # Google's body says which of redirect_uri/client_secret is wrong, and
        # that is by far the most common setup mistake, so keep it for the log.
        raise GoogleAuthError(
            f"Google refused the code (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise GoogleAuthError(f"Google's reply was not JSON: {e}") from e
    id_token = payload.get("id_token")
    if not id_token:
        raise GoogleAuthError("Google's reply contained no id_token")
    return claims_from_id_token(id_token)


def complete(cfg: Config, session: dict, *, code: str, state: str) -> tuple[Identity, str]:
    """Finish the round trip. Returns the identity and where to go next.

    The state check is what stops someone getting a victim's browser to
    complete *their* sign-in and land in their account.
    """
    expected = session.pop(SESSION_STATE, None)
    nonce = session.pop(SESSION_NONCE, None)
    next_url = session.pop(SESSION_NEXT, "") or ""
    if not expected:
        raise GoogleAuthError("no sign-in was in progress in this browser")
    if not secrets.compare_digest(str(state), str(expected)):
        raise GoogleAuthError("state does not match — sign-in was not started here")
    claims = exchange(cfg, code)
    return check_claims(cfg, claims, nonce), next_url
