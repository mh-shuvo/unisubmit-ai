"""Passwords, login throttling and CSRF tokens (P4.1, P4.2, P4.4).

Passwords are hashed with Argon2id. Hashes written before v10.3 are the
hand-written PBKDF2 format `salt_hex:digest_hex` (DEF-040); they still verify,
and are replaced with Argon2id at the next successful login.
"""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Protocol

from markupsafe import Markup
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from starlette.requests import Request

from .core.errors import PermissionDenied

MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class LegacyPbkdf2Hasher:
    """Verifies the pre-v10.3 hashes only; nothing new is hashed with it."""

    ITERATIONS = 180_000
    FORMAT = re.compile(r"^[0-9a-f]{32}:[0-9a-f]{64}$")

    @classmethod
    def identify(cls, hash: str | bytes) -> bool:
        text = hash.decode() if isinstance(hash, bytes) else hash
        return bool(cls.FORMAT.match(text))

    def hash(self, password: str | bytes, *, salt: bytes | None = None) -> str:
        raise NotImplementedError("PBKDF2 hashes are only verified, never created")

    def verify(self, password: str | bytes, hash: str | bytes) -> bool:
        text = hash.decode() if isinstance(hash, bytes) else hash
        salt_hex, digest_hex = text.split(":", 1)
        secret = password.encode() if isinstance(password, str) else password
        digest = hashlib.pbkdf2_hmac("sha256", secret, bytes.fromhex(salt_hex), self.ITERATIONS)
        return hmac.compare_digest(digest.hex(), digest_hex)

    def check_needs_rehash(self, hash: str | bytes) -> bool:
        return True


_passwords = PasswordHash((Argon2Hasher(), LegacyPbkdf2Hasher()))
# Verified when no account matches, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _passwords.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _passwords.hash(password)


class Account(Protocol):
    password_hash: str
    failed_logins: int
    locked_until: datetime | None


class Login(Enum):
    OK = "ok"
    WRONG = "wrong"
    LOCKED = "locked"


def check_login(account: Account | None, password: str) -> Login:
    """Verify a login attempt and update the account's counters; the caller commits.

    Ten wrong passwords in a row lock the account for 15 minutes. While it is
    locked, even the right password is refused and nothing is counted.
    """
    if account is None:
        _passwords.verify(password, _DUMMY_HASH)
        return Login.WRONG
    now = datetime.now(UTC)
    if account.locked_until is not None and account.locked_until > now:
        return Login.LOCKED
    valid, updated = _passwords.verify_and_update(password, account.password_hash)
    if not valid:
        account.failed_logins += 1
        if account.failed_logins >= MAX_FAILED_LOGINS:
            account.failed_logins = 0
            account.locked_until = now + LOCKOUT
            return Login.LOCKED
        return Login.WRONG
    account.failed_logins = 0
    account.locked_until = None
    if updated is not None:
        account.password_hash = updated
    return Login.OK


def verify_password(password: str, stored: str) -> bool:
    """For re-entering the current password; no counters are touched."""
    return _passwords.verify(password, stored)


def csrf_token(request: Request) -> str:
    """The session's CSRF token, created on first use."""
    token = request.session.get(CSRF_FIELD)
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session[CSRF_FIELD] = token
    return token


def csrf_field(request: Request) -> Markup:
    """Jinja global: `{{ csrf_field(request) }}` inside every POST form."""
    return Markup('<input type="hidden" name="{}" value="{}">').format(
        CSRF_FIELD, csrf_token(request)
    )


async def verify_csrf(request: Request) -> None:
    """Router dependency: every unsafe request carries the session's token, in
    the form or in the X-CSRF-Token header (DEF-041)."""
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get(CSRF_FIELD)
    submitted: object = request.headers.get(CSRF_HEADER)
    if submitted is None:
        submitted = (await request.form()).get(CSRF_FIELD)
    if not (
        isinstance(expected, str)
        and isinstance(submitted, str)
        and hmac.compare_digest(submitted, expected)
    ):
        raise PermissionDenied("Your form has expired. Reload the page and try again.")
