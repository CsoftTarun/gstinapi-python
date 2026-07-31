# gstinapi

Python client for [gstinapi.in](https://www.gstinapi.in) — search any Indian **GST number (GSTIN)** and get back the legal name, trade name, GST status, taxpayer type and registered address, live from India's official GSP network.

No dependencies beyond the standard library. Python 3.9+.

```bash
pip install gstinapi
```

## Quick start

```python
import os
from gstinapi import GstinApi

client = GstinApi(api_key=os.environ["GSTIN_API_KEY"])

result = client.lookup("33AAACC1206D1ZN")

print(result["data"]["legal_name"])   # CENTRAL WAREHOUSING CORPORATION
print(result["data"]["status"])       # Active
print(result["credits_remaining"])    # 9
```

Get an API key at [gstinapi.in/register](https://www.gstinapi.in/register) — new accounts include 10 free lookups, no card required.

## Why use this instead of calling the API directly

**It refuses to spend a credit on a GSTIN that cannot exist.** The 15th character of a GSTIN is a checksum over the first 14. This client verifies it locally before making a request, so a mistyped number fails instantly and for free:

```python
from gstinapi import is_valid_gstin

is_valid_gstin("33AAACC1206D1ZN")  # True
is_valid_gstin("22AAAAA0000A1Z5")  # False — pattern is fine, checksum is not
```

The API itself only checks the pattern, so that second number would have cost you a request.

**It retries the right failures.** A 429 or 502 is worth asking again with backoff. A 402 or 404 is not — repeating those only wastes time, so it doesn't.

## Errors

Each HTTP failure raises a specific exception, all subclassing `GstinError`:

```python
from gstinapi import GstinApi, NotFound, InsufficientCredits, InvalidFormat, GstinError

try:
    result = client.lookup(gstin)
except NotFound:
    print("This GST number is not registered.")
except InsufficientCredits:
    print("Out of credits — top up to continue.")
except InvalidFormat:
    print("That does not look like a GST number.")
except GstinError as exc:
    print(exc.status, exc.code, exc.message)
```

| Exception | HTTP | Meaning |
|---|---|---|
| `InvalidFormat` | 400 | Not a valid GSTIN. No credit charged. |
| `Unauthorized` | 401 | Missing or wrong API key. |
| `InsufficientCredits` | 402 | Out of credits. |
| `AccountDeactivated` | 403 | Account disabled. |
| `NotFound` | 404 | GSTIN is not in the GST database. |
| `RateLimited` | 429 | Over 60 requests/minute. Retried automatically. |
| `ProviderUnavailable` | 502 | Upstream GST network hiccup. Retried automatically. |

## Bulk lookups

`lookup_many` runs a thread pool and settles each entry separately, so one bad number never sinks the batch:

```python
for r in client.lookup_many(gstins, concurrency=5):
    if r.ok:
        print(r.gstin, r.data["data"]["legal_name"])
    else:
        print(r.gstin, r.error.code)
```

## Usage stats

```python
client.usage()
# {'total_calls': 42, 'success': 40, 'errors': 2, 'credits_used': 40}
```

This does not consume a credit.

## Options

```python
GstinApi(
    api_key=os.environ["GSTIN_API_KEY"],  # required
    timeout=15.0,            # per attempt, seconds
    retries=2,               # 429/502 only
    validate_checksum=True,  # set False to match the API exactly
)
```

## What you get back

```python
{
    "success": True,
    "gstin": "33AAACC1206D1ZN",
    "credits_remaining": 9,
    "response_ms": 182,
    "data": {
        "gstin": "33AAACC1206D1ZN",
        "legal_name": "CENTRAL WAREHOUSING CORPORATION",
        "trade_name": "CENTRAL WAREHOUSING CORPORATION",
        "status": "Active",
        "taxpayer_type": "Regular",
        "business_constitution": None,
        "registration_date": "2017-07-01",
        "cancellation_date": None,
        "state_code": "33",
        "state_jurisdiction": None,
        "address": "No.4, North Avenue, Saidapet, Chennai",
        "pincode": "600015",
        "nature_of_business": None,
        "block_status": "Unblocked",
    },
}
```

`business_constitution`, `state_jurisdiction` and `nature_of_business` are currently always `None` — the keys are stable, but don't build logic on their values.

## Working with pandas

```python
import pandas as pd

df = pd.read_csv("vendors.csv")
results = {r.gstin: r for r in client.lookup_many(df["gstin"].tolist())}

df["legal_name"] = df["gstin"].map(lambda g: results[g].data["data"]["legal_name"] if results[g].ok else None)
df["gst_status"] = df["gstin"].map(lambda g: results[g].data["data"]["status"] if results[g].ok else results[g].error.code)
```

## Keep your key on the server

The API key is a credential: anyone holding it can spend your credits. Read it from an environment variable — never commit it, and never ship it in client-side code.

## Links

- [API documentation](https://www.gstinapi.in/docs)
- [Free GST number search](https://www.gstinapi.in/gst-number-search)
- [Pricing](https://www.gstinapi.in/pricing)

## License

MIT
