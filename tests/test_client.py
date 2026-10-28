import json
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

from gstinapi import (
    GstinApi,
    InsufficientCredits,
    InvalidFormat,
    NotFound,
    RateLimited,
    gstin_checksum,
    is_valid_gstin,
)

VALID = "33AAACC1206D1ZN"
BAD_CHECKSUM = "22AAAAA0000A1Z5"


class _Response(BytesIO):
    """Minimal stand-in for the object urlopen yields."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def ok(payload):
    return lambda *a, **k: _Response(json.dumps(payload).encode())


def fail(status, payload):
    def raise_it(*a, **k):
        raise urllib.error.HTTPError(
            "https://x", status, "err", {}, BytesIO(json.dumps(payload).encode())
        )

    return raise_it


class TestValidation(unittest.TestCase):
    def test_accepts_real_gstin(self):
        self.assertTrue(is_valid_gstin(VALID))
        self.assertTrue(is_valid_gstin(VALID.lower()), "should be case insensitive")
        self.assertTrue(is_valid_gstin(f"  {VALID}  "), "should tolerate space")

    def test_rejects_bad_checksum_unless_disabled(self):
        self.assertFalse(is_valid_gstin(BAD_CHECKSUM))
        self.assertTrue(
            is_valid_gstin(BAD_CHECKSUM, checksum=False),
            "pattern-only mode should match what the API accepts",
        )

    def test_rejects_malformed(self):
        for bad in ["", "ABC", "33AAACC1206D1Z", "33AAACC1206D1ZNX", None, 42]:
            self.assertFalse(is_valid_gstin(bad), f"expected {bad!r} invalid")

    def test_checksum_value(self):
        self.assertEqual(gstin_checksum(VALID), "N")
        self.assertNotEqual(gstin_checksum(BAD_CHECKSUM), "5")


class TestClient(unittest.TestCase):
    def test_api_key_required(self):
        with self.assertRaises(ValueError):
            GstinApi(api_key="")

    def test_bad_gstin_never_hits_network(self):
        client = GstinApi(api_key="gak_x")
        with patch("urllib.request.urlopen") as mock:
            with self.assertRaises(InvalidFormat):
                client.lookup(BAD_CHECKSUM)
            mock.assert_not_called()

    def test_lookup_returns_payload_and_uppercases(self):
        payload = {"success": True, "gstin": VALID, "data": {"legal_name": "ACME"}}
        client = GstinApi(api_key="gak_secret")
        with patch("urllib.request.urlopen", side_effect=ok(payload)) as mock:
            result = client.lookup(VALID.lower())
        self.assertEqual(result, payload)
        request = mock.call_args[0][0]
        self.assertTrue(request.full_url.endswith(f"/v1/gstin/{VALID}"))
        self.assertEqual(request.headers["X-api-key"], "gak_secret")

    def test_retries_502_then_succeeds(self):
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return fail(502, {"error": "provider down"})()
            return _Response(json.dumps({"success": True}).encode())

        client = GstinApi(api_key="gak_x", retries=2)
        with patch("urllib.request.urlopen", side_effect=flaky):
            self.assertTrue(client.lookup(VALID)["success"])
        self.assertEqual(len(calls), 2)

    def test_does_not_retry_402(self):
        client = GstinApi(api_key="gak_x", retries=3)
        with patch(
            "urllib.request.urlopen",
            side_effect=fail(402, {"error": "Insufficient credits."}),
        ) as mock:
            with self.assertRaises(InsufficientCredits) as ctx:
                client.lookup(VALID)
        self.assertEqual(ctx.exception.status, 402)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_CREDITS")
        self.assertEqual(mock.call_count, 1, "retrying a credit failure only wastes time")

    def test_gives_up_after_retry_budget(self):
        client = GstinApi(api_key="gak_x", retries=2)
        with patch(
            "urllib.request.urlopen", side_effect=fail(429, {"error": "slow down"})
        ) as mock:
            with self.assertRaises(RateLimited):
                client.lookup(VALID)
        self.assertEqual(mock.call_count, 3, "one attempt plus two retries")

    def test_lookup_many_settles_each_entry(self):
        def mixed(request, *a, **k):
            if BAD_CHECKSUM in request.full_url:
                return fail(404, {"error": "not found"})()
            return _Response(json.dumps({"success": True}).encode())

        client = GstinApi(api_key="gak_x", retries=0)
        with patch("urllib.request.urlopen", side_effect=mixed):
            results = client.lookup_many([VALID, "24AAKPV8888P1ZB", BAD_CHECKSUM], concurrency=1)

        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].ok)
        self.assertFalse(results[2].ok, "a locally-invalid GSTIN must not sink the batch")
        self.assertIsInstance(results[2].error, InvalidFormat)

    def test_404_maps_to_not_found(self):
        client = GstinApi(api_key="gak_x", retries=0)
        with patch("urllib.request.urlopen", side_effect=fail(404, {"error": "GSTIN not found"})):
            with self.assertRaises(NotFound):
                client.lookup(VALID)


if __name__ == "__main__":
    unittest.main()


class TestInclude(unittest.TestCase):
    def test_include_profile_is_passed_through(self):
        payload = {"success": True, "profile_complete": True, "data": {}}
        client = GstinApi(api_key="gak_x")
        with patch("urllib.request.urlopen", side_effect=ok(payload)) as mock:
            result = client.lookup(VALID, include="profile")
        self.assertTrue(mock.call_args[0][0].full_url.endswith(f"/v1/gstin/{VALID}?include=profile"))
        self.assertTrue(result["profile_complete"])

    def test_unknown_include_never_hits_network(self):
        client = GstinApi(api_key="gak_x")
        with patch("urllib.request.urlopen") as mock:
            with self.assertRaises(ValueError):
                client.lookup(VALID, include="profiel")
            mock.assert_not_called()

    def test_lookup_many_forwards_include(self):
        client = GstinApi(api_key="gak_x")
        with patch("urllib.request.urlopen", side_effect=ok({"success": True})) as mock:
            results = client.lookup_many([VALID, VALID], include="profile")
        self.assertTrue(all(r.ok for r in results))
        urls = [c[0][0].full_url for c in mock.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(u.endswith("?include=profile") for u in urls))

    def test_lookup_many_rejects_unknown_include_up_front(self):
        client = GstinApi(api_key="gak_x")
        with patch("urllib.request.urlopen") as mock:
            with self.assertRaises(ValueError):
                client.lookup_many([VALID], include="all")
            mock.assert_not_called()
