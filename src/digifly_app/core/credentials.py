"""Credential handling that keeps provider secrets outside Digifly artifacts."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit


NEUPRINT_CREDENTIAL_ENV = "NEUPRINT_APPLICATION_CREDENTIALS"
NEUPRINT_KEYRING_SERVICE = "org.digifly.workstation.neuprint"


class CredentialStoreError(RuntimeError):
    """A deliberately redacted operating-system credential-store failure."""


def normalize_neuprint_token(value: str) -> str:
    """Accept legacy neuPrint tokens and current DatasetGateway API keys.

    neuPrint historically copied a JSON document containing ``token``.  Newer
    authenticated deployments use a DatasetGateway key named ``dsg_token``.
    Both are still sent to neuPrintHTTP as an ordinary Bearer credential.
    """

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("A neuPrint application token is required")
    token: object = raw
    if raw[:1] in {'{', '"'}:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("The neuPrint token text is not valid JSON or a bare token") from exc
        if isinstance(decoded, dict):
            token = decoded.get("dsg_token") or decoded.get("token")
        else:
            token = decoded
    elif raw.startswith("dsg_token="):
        token = raw.removeprefix("dsg_token=").split(";", 1)[0]
    normalized = str(token or "").strip().strip('"')
    if not normalized or any(character in normalized for character in "\r\n"):
        raise ValueError(
            "The neuPrint credential is empty or malformed. Paste the complete "
            "DatasetGateway API key (dsg_token) or legacy neuPrint token."
        )
    return normalized


def token_from_environment() -> str | None:
    raw = os.environ.get(NEUPRINT_CREDENTIAL_ENV, "")
    if not raw.strip():
        return None
    return normalize_neuprint_token(raw)


def credential_account(server: str) -> str:
    parsed = urlsplit(server.strip())
    host = (parsed.hostname or "").casefold()
    if not host:
        raise ValueError("The neuPrint server has no host name")
    return host


def credential_reference(server: str) -> str:
    return f"keyring:{NEUPRINT_KEYRING_SERVICE}:{credential_account(server)}"


class NeuPrintCredentialStore:
    """Small adapter around ``keyring`` with secret-free errors and fallbacks."""

    def _backend(self):
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise CredentialStoreError(
                "Operating-system credential storage is unavailable in this installation"
            ) from exc
        try:
            backend = keyring.get_keyring()
            priority = float(getattr(backend, "priority", 0))
        except (KeyringError, RuntimeError, TypeError, ValueError) as exc:
            raise CredentialStoreError(
                "The operating-system credential store could not be opened"
            ) from exc
        if priority <= 0:
            raise CredentialStoreError(
                "No secure operating-system credential-store backend is available"
            )
        return keyring

    @property
    def available(self) -> bool:
        try:
            self._backend()
        except CredentialStoreError:
            return False
        return True

    def get(self, server: str) -> str | None:
        keyring = self._backend()
        try:
            value = keyring.get_password(
                NEUPRINT_KEYRING_SERVICE,
                credential_account(server),
            )
        except Exception as exc:  # backend-specific exceptions must stay redacted
            raise CredentialStoreError(
                "The neuPrint credential could not be read from the operating-system store"
            ) from exc
        return normalize_neuprint_token(value) if value else None

    def set(self, server: str, token: str) -> str:
        keyring = self._backend()
        normalized = normalize_neuprint_token(token)
        try:
            keyring.set_password(
                NEUPRINT_KEYRING_SERVICE,
                credential_account(server),
                normalized,
            )
        except Exception as exc:  # backend-specific exceptions must stay redacted
            raise CredentialStoreError(
                "The neuPrint credential could not be saved to the operating-system store"
            ) from exc
        return credential_reference(server)

    def delete(self, server: str) -> None:
        keyring = self._backend()
        try:
            keyring.delete_password(
                NEUPRINT_KEYRING_SERVICE,
                credential_account(server),
            )
        except Exception as exc:  # backend-specific exceptions must stay redacted
            raise CredentialStoreError(
                "The neuPrint credential could not be removed from the operating-system store"
            ) from exc
