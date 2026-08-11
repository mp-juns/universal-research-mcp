"""Host-owned, single-use reservations for runtime provider dispatches."""

from __future__ import annotations

import re
import threading


_ARTIFACT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeDispatchReservationError(RuntimeError):
    """Raised when a runtime attempts to reserve an invalid or replayed dispatch."""


class RuntimeDispatchReservationConsumer:
    """Consume reservations without granting authority to create them."""

    __slots__ = ("__authority", "__binding")

    def __init__(
        self,
        authority: RuntimeDispatchReservationAuthority,
        binding: object,
    ) -> None:
        self.__authority = authority
        self.__binding = binding

    def consume(self, dispatch_artifact_hash: str) -> bool:
        return self.__authority._consume(self.__binding, dispatch_artifact_hash)


class RuntimeDispatchReservationAuthority:
    """Reserve exact dispatch artifacts and reject replay within one runtime."""

    def __init__(self) -> None:
        self.__binding = object()
        self.__lock = threading.Lock()
        self.__pending: set[str] = set()
        self.__consumed: set[str] = set()
        self.__consumer = RuntimeDispatchReservationConsumer(
            self, self.__binding,
        )

    def consumer(self) -> RuntimeDispatchReservationConsumer:
        return self.__consumer

    def reserve(self, dispatch_artifact_hash: str) -> None:
        if not isinstance(dispatch_artifact_hash, str) or not _ARTIFACT_HASH.fullmatch(
            dispatch_artifact_hash
        ):
            raise RuntimeDispatchReservationError(
                "runtime dispatch reservation requires an exact artifact hash"
            )
        with self.__lock:
            if (
                dispatch_artifact_hash in self.__pending
                or dispatch_artifact_hash in self.__consumed
            ):
                raise RuntimeDispatchReservationError(
                    "runtime dispatch hash is already reserved or consumed"
                )
            self.__pending.add(dispatch_artifact_hash)

    def _consume(self, binding: object, dispatch_artifact_hash: str) -> bool:
        if binding is not self.__binding:
            return False
        with self.__lock:
            if dispatch_artifact_hash not in self.__pending:
                return False
            self.__pending.remove(dispatch_artifact_hash)
            self.__consumed.add(dispatch_artifact_hash)
            return True
