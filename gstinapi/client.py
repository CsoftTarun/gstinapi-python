"""Client for the gstinapi.in GSTIN verification API."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable

BASE_URL = "https://www.gstinapi.in"
USER_AGENT = "gstinapi-python/1.0.0 (+https://www.gstinapi.in)"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Only these are worth repeating. A 402 will not become a 200 by asking again.
_RETRYABLE = frozenset({429, 502})


class GstinError(Exception):
    """Base error. ``status`` and ``code`` let callers branch without string matching."""

    code = "HTTP_ERROR"

    def __init__(self, message: str, *, status: int | None = None, response: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.response = response

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class InvalidFormat(GstinError):
    code = "INVALID_FORMAT"


class Unauthorized(GstinError):
    code = "UNAUTHORIZED"


class InsufficientCredits(GstinError):
    code = "INSUFFICIENT_CREDITS"


class AccountDeactivated(GstinError):
    code = "ACCOUNT_DEACTIVATED"


class NotFound(GstinError):
    code = "NOT_FOUND"


class RateLimited(GstinError):
    code = "RATE_LIMITED"


class ProviderUnavailable(GstinError):
    code = "PROVIDER_UNAVAILABLE"


ERROR_CODES: dict[int, type[GstinError]] = {
    400: InvalidFormat,
    401: Unauthorized,
    402: InsufficientCredits,
    403: AccountDeactivated,
    404: NotFound,
    429: RateLimited,
    502: ProviderUnavailable,
}


def gstin_checksum(gstin: str) -> str | None:
    """Return the expected 15th character of *gstin*, or ``None`` if unusable.

    The last character is a checksum over the first 14. Verifying it locally
    catches transcription slips without spending a credit on a request that
    cannot succeed.
    """
    total = 0
    for i, char in enumerate(gstin[:14]):
        value = _ALPHABET.find(char)
        if value < 0:
            return None
        product = value * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: Any, *, checksum: bool = True) -> bool:
    """True when *gstin* is structurally valid.

    Pass ``checksum=False`` to check the pattern only, which is exactly what
    the API itself accepts.
    """
    if not isinstance(gstin, str):
        return False
    value = gstin.strip().upper()
    if not GSTIN_RE.match(value):
        return False
    if not checksum:
        return True
    return gstin_checksum(value) == value[14]


@dataclass
class BatchResult:
    """One settled entry from :meth:`GstinApi.lookup_many`."""

    gstin: str
    ok: bool
    data: dict | None = None
    error: GstinError | None = None


class GstinApi:
    """Client for gstinapi.in.

    >>> client = GstinApi(api_key=os.environ["GSTIN_API_KEY"])
    >>> client.lookup("33AAACC1206D1ZN")["data"]["legal_name"]
    'CENTRAL WAREHOUSING CORPORATION'
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
        retries: int = 2,
        validate_checksum: bool = True,
    ):
        if not api_key:
            raise ValueError("api_key is required — get one at https://www.gstinapi.in/api-keys")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.validate_checksum = validate_checksum

    def _request(self, path: str) -> dict:
        attempt = 0
        while True:
            # A User-Agent is not optional here: urllib's default
            # "Python-urllib/3.x" is refused at the edge with Cloudflare error
            # 1010 before the request ever reaches the API, which surfaces as a
            # baffling 403 with no JSON body.
            request = urllib.request.Request(
                f"{self.base_url}{path}",
                headers={
                    "x-api-key": self.api_key,
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    body = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    body = {}
                if exc.code in _RETRYABLE and attempt < self.retries:
                    time.sleep(2**attempt * 0.5)
                    attempt += 1
                    continue
                error_cls = ERROR_CODES.get(exc.code, GstinError)
                raise error_cls(
                    body.get("error") or f"Request failed with status {exc.code}",
                    status=exc.code,
                    response=body,
                ) from None
            except urllib.error.URLError as exc:
                raise GstinError(f"Network error: {exc.reason}", status=None) from None

    def lookup(self, gstin: str) -> dict:
        """Look up one GSTIN.

        Returns the full payload: ``success``, ``gstin``, ``data``,
        ``credits_remaining`` and ``response_ms``.
        """
        if not isinstance(gstin, str) or not gstin.strip():
            raise TypeError("gstin must be a non-empty string")
        value = gstin.strip().upper()
        if not is_valid_gstin(value, checksum=self.validate_checksum):
            raise InvalidFormat(f'"{value}" is not a valid GSTIN', status=400)
        return self._request(f"/v1/gstin/{value}")

    def usage(self) -> dict:
        """Today's usage for this key. Does not consume a credit."""
        return self._request("/v1/gstin/stats/me")

    def lookup_many(self, gstins: Iterable[str], *, concurrency: int = 5) -> list[BatchResult]:
        """Look up many GSTINs, settling each independently.

        One bad number never sinks the batch — check ``.ok`` on each result.
        """
        items = list(gstins)
        if not items:
            return []

        def run(gstin: str) -> BatchResult:
            try:
                return BatchResult(gstin=gstin, ok=True, data=self.lookup(gstin))
            except GstinError as exc:
                return BatchResult(gstin=gstin, ok=False, error=exc)

        with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as pool:
            return list(pool.map(run, items))
