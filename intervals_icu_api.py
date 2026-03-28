import base64
import json
import os
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "https://intervals.icu/api/v1"


def get_api_key(cli_value: str | None) -> str:
    api_key = cli_value or os.getenv("INTERVALS_ICU_API_KEY")
    if not api_key:
        raise SystemExit("Missing API key. Use --api-key or set INTERVALS_ICU_API_KEY.")
    return api_key


def api_request(
    api_base: str,
    athlete_id: str,
    api_key: str,
    method: str,
    path: str,
    payload=None,
) -> str:
    data = None
    headers = {
        "Authorization": f"Basic {build_basic_auth(api_key)}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{api_base}/athlete/{athlete_id}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def build_basic_auth(api_key: str) -> str:
    return base64.b64encode(f"API_KEY:{api_key}".encode("utf-8")).decode("ascii")
