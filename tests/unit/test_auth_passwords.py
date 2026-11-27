"""argon2id with pepper + breach list (T020 / FR-003 / REQ-SEC-01)."""

from __future__ import annotations

import secrets

import pytest

from app.auth import passwords

# Random per run: no literal password in the repository (secret scan of 2026-11-27).
PASSWORD = secrets.token_urlsafe(16)
OTHER_PASSWORD = secrets.token_urlsafe(16)


def test_hash_is_nondeterministic_and_verify_matches() -> None:
    a = passwords.hash_password(PASSWORD)
    b = passwords.hash_password(PASSWORD)
    assert a != b, "argon2 salt must randomise each call"
    assert passwords.verify_password(a, PASSWORD)
    assert passwords.verify_password(b, PASSWORD)


def test_verify_rejects_the_wrong_password() -> None:
    stored = passwords.hash_password(PASSWORD)
    assert passwords.verify_password(stored, OTHER_PASSWORD) is False


def test_pepper_isolates_hashes_across_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = passwords.hash_password(PASSWORD)
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "password_pepper", SecretStr("other-pepper"))
    passwords._hasher.cache_clear()  # type: ignore[attr-defined]
    assert passwords.verify_password(stored, PASSWORD) is False


def test_short_password_is_rejected() -> None:
    with pytest.raises(passwords.WeakPasswordError, match="too_short"):
        passwords.hash_password("short")


def test_breached_password_is_rejected_before_hash() -> None:
    # "password" has SHA-1 5BAA61E4C9... — present in the shipped breach prefix list.
    with pytest.raises(passwords.WeakPasswordError, match="breached"):
        passwords.hash_password("password1234")


def test_verify_handles_malformed_hash() -> None:
    assert passwords.verify_password("not-an-argon2-string", "anything") is False
