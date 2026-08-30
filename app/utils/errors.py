"""Error types that carry a user-facing message plus optional technical detail.

The GUI shows ``message`` and ``detail``; ``technical`` is only revealed behind
an expandable "technical details" section (specification section 22).
"""

from __future__ import annotations


class IPChangerError(Exception):
    """Base class for every error the application raises deliberately."""

    def __init__(
        self,
        message: str,
        detail: str = "",
        technical: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.technical = technical

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ElevationError(IPChangerError):
    """Raised when Administrator privileges could not be obtained."""


class AdapterNotFoundError(IPChangerError):
    """Raised when a previously known adapter can no longer be located."""


class NetworkOperationError(IPChangerError):
    """Raised when Windows refused or failed a configuration change."""


class VerificationError(IPChangerError):
    """Raised when the applied configuration could not be verified."""


class StorageError(IPChangerError):
    """Raised on database or settings persistence failures."""


class ValidationError(IPChangerError):
    """Raised when a configuration is objectively invalid."""


def humanize(exc: BaseException) -> IPChangerError:
    """Translate any exception into a user-presentable error.

    Raw tracebacks and subprocess errors must never reach the operator, so
    unknown exceptions are wrapped with a generic message and the original
    text preserved as technical detail.
    """
    if isinstance(exc, IPChangerError):
        return exc
    if isinstance(exc, PermissionError):
        return IPChangerError(
            "Access denied.",
            "Windows refused the operation. Administrator privileges are required.",
            f"{type(exc).__name__}: {exc}",
        )
    if isinstance(exc, TimeoutError):
        return IPChangerError(
            "The operation timed out.",
            "Windows did not respond in time. The network stack may be busy.",
            f"{type(exc).__name__}: {exc}",
        )
    return IPChangerError(
        "An unexpected error occurred.",
        "The operation could not be completed. No changes are assumed to have been made.",
        f"{type(exc).__name__}: {exc}",
    )
