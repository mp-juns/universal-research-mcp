"""Opaque credential references and short-lived secret resolution."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Callable, Mapping

from .contracts import ProviderConfigurationError


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DISALLOWED_SCHEMES = frozenset({"argv", "chat", "literal", "plaintext", "raw"})


class SecretValue:
    """A value whose normal string and repr forms are always redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ProviderConfigurationError("resolved credential is empty")
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True)
class CredentialRef:
    kind: str
    locator: str

    @classmethod
    def parse(cls, value: str) -> "CredentialRef":
        if not isinstance(value, str) or ":" not in value:
            raise ProviderConfigurationError(
                "credential reference must use env:NAME or keyring:SERVICE/ACCOUNT"
            )
        kind, locator = value.split(":", 1)
        kind = kind.casefold().strip()
        locator = locator.strip()
        if kind in _DISALLOWED_SCHEMES:
            raise ProviderConfigurationError("plaintext, argv, and chat credentials are forbidden")
        if kind == "env":
            if not _ENV_NAME.fullmatch(locator):
                raise ProviderConfigurationError("invalid environment credential reference")
        elif kind == "keyring":
            if locator.count("/") != 1 or any(not part for part in locator.split("/", 1)):
                raise ProviderConfigurationError(
                    "keyring reference must use keyring:SERVICE/ACCOUNT"
                )
        else:
            raise ProviderConfigurationError("unsupported credential reference kind")
        return cls(kind=kind, locator=locator)

    def __str__(self) -> str:
        return f"{self.kind}:{self.locator}"


KeyringGetter = Callable[[str, str], str | None]


class CredentialResolver:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        keyring_getter: KeyringGetter | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._keyring_getter = keyring_getter

    def resolve(self, reference: str | CredentialRef) -> SecretValue:
        parsed = reference if isinstance(reference, CredentialRef) else CredentialRef.parse(reference)
        if parsed.kind == "env":
            value = self._environ.get(parsed.locator)
        else:
            getter = self._keyring_getter or self._load_keyring_getter()
            service, account = parsed.locator.split("/", 1)
            value = getter(service, account)
        if not value:
            raise ProviderConfigurationError(
                f"credential is unavailable for {parsed}; supply it outside chat and command arguments"
            )
        return SecretValue(value)

    @staticmethod
    def _load_keyring_getter() -> KeyringGetter:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProviderConfigurationError(
                "keyring credential requested but no keyring runtime is installed"
            ) from exc
        return keyring.get_password
