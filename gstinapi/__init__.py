"""Python client for gstinapi.in — Indian GST number (GSTIN) verification."""

from .client import (
    ERROR_CODES,
    AccountDeactivated,
    GstinApi,
    GstinError,
    InsufficientCredits,
    InvalidFormat,
    NotFound,
    ProviderUnavailable,
    RateLimited,
    Unauthorized,
    gstin_checksum,
    is_valid_gstin,
)

__version__ = "1.0.0"

__all__ = [
    "GstinApi",
    "GstinError",
    "InvalidFormat",
    "Unauthorized",
    "InsufficientCredits",
    "AccountDeactivated",
    "NotFound",
    "RateLimited",
    "ProviderUnavailable",
    "is_valid_gstin",
    "gstin_checksum",
    "ERROR_CODES",
    "__version__",
]
