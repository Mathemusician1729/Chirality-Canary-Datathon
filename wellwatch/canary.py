"""Minimal client for the Canary Historian web APIs (version 26).

Write API: Store & Forward service, https://<host>:55293/api/v1
    getSessionToken -> storeData (tvqs) -> keepAlive -> revokeSessionToken
Read API: Views service, https://<host>:55236/api/v2
    browseNodes, browseTags, getTagData2 (raw or aggregated, paged with a continuation)

The API token is read from Windows Credential Manager and is never logged or printed.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Iterable

import requests
import urllib3

from . import config

if config.TLS_VERIFY is False:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class CanaryError(RuntimeError):
    pass


def get_token() -> str:
    import keyring
    token = keyring.get_password(config.KEYRING_SERVICE, config.KEYRING_USER)
    if not token:
        raise CanaryError(
            "No Canary API token found. Store it once with:\n"
            "  python -c \"import keyring,getpass; keyring.set_password('wellwatch-canary','api_token',"
            "getpass.getpass('Canary API token: '))\"")
    return token


def _iso(ts) -> str:
    """ISO 8601 with a UTC offset (Canary rejects timestamps without one)."""
    ts = dt.datetime.fromisoformat(str(ts)) if not isinstance(ts, dt.datetime) else ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.isoformat()


def _post(url: str, body: dict, token: str, timeout: float = 120, tolerate_tag_errors: bool = False) -> dict:
    r = requests.post(url, json=body, verify=config.TLS_VERIFY, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        raise CanaryError(f"{url.rsplit('/', 1)[-1]}: HTTP {r.status_code}") from None
    per_tag = r.status_code < 400 and all(str(e).startswith("Tag Error") for e in data.get("errors") or [])
    if tolerate_tag_errors and per_tag:
        return data  # e.g. one requested tag does not exist; the other tags' data is still valid
    if r.status_code >= 400 or data.get("statusCode") not in ("Good", None):
        errors = "; ".join(data.get("errors") or [])[:800].replace(token, "<token>")
        raise CanaryError(f"{url.rsplit('/', 1)[-1]}: HTTP {r.status_code}: {errors}")
    return data


class CanaryWriter:
    """Writes time-value pairs through the Store & Forward Write API.

    Use as a context manager so the session is always closed:
        with CanaryWriter() as w:
            w.store({"Wells.Winter.100...OilRate": [(ts, 12.3), ...]})
    """

    def __init__(self, historian: str = "localhost", insert_replace: bool = True, client_id: str = config.CLIENT_ID):
        self.base = f"https://{config.CANARY_HOST}:{config.WRITE_API_PORT}/api/v1"
        self.token = get_token()
        self.historian, self.insert_replace, self.client_id = historian, insert_replace, client_id
        self.session: str | None = None
        self.values_written = 0

    def __enter__(self):
        settings = {"clientTimeout": 600000, "autoCreateDatasets": True,
                    "insertReplaceData": self.insert_replace, "fileSize": 8}
        data = _post(f"{self.base}/getSessionToken", {
            "apiToken": self.token, "historians": [self.historian], "clientId": self.client_id,
            "settings": settings}, self.token)
        self.session = data["sessionToken"]
        return self

    def __exit__(self, *exc):
        if self.session:
            try:
                _post(f"{self.base}/revokeSessionToken", {"sessionToken": self.session}, self.token)
            finally:
                self.session = None

    def keep_alive(self) -> None:
        _post(f"{self.base}/keepAlive", {"sessionToken": self.session}, self.token)

    def store(self, tvqs: dict[str, Iterable], batch_values: int = 50_000) -> None:
        """tvqs: tag -> iterable of (timestamp, value). NaN/None values are skipped."""
        batch, n = {}, 0
        for tag, points in tvqs.items():
            clean = [[_iso(t), v] for t, v in points if v is not None and v == v]
            for i in range(0, len(clean), batch_values):
                chunk = clean[i:i + batch_values]
                if n + len(chunk) > batch_values and batch:
                    self._send(batch, n)
                    batch, n = {}, 0
                batch.setdefault(tag, []).extend(chunk)
                n += len(chunk)
        if batch:
            self._send(batch, n)

    def _send(self, batch: dict, n: int) -> None:
        for attempt in range(3):
            try:
                _post(f"{self.base}/storeData", {"sessionToken": self.session, "tvqs": batch}, self.token)
                self.values_written += n
                return
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))


class CanaryReader:
    """Reads tag data through the Views Read API."""

    def __init__(self):
        self.base = f"https://{config.CANARY_HOST}:{config.READ_API_PORT}/api/v2"
        self.token = get_token()

    def _call(self, endpoint: str, tolerate_tag_errors: bool = False, **body) -> dict:
        return _post(f"{self.base}/{endpoint}", {"apiToken": self.token, **body}, self.token,
                     tolerate_tag_errors=tolerate_tag_errors)

    def browse_nodes(self, path: str = "") -> dict:
        return self._call("browseNodes", path=path)["nodes"]

    def browse_tags(self, path: str, deep: bool = True) -> list[str]:
        tags, cont = [], None
        while True:
            data = self._call("browseTags", path=path, deep=deep, **({"continuation": cont} if cont else {}))
            tags += data.get("tags") or []
            cont = data.get("continuation")
            if not cont:
                return tags

    def get_tag_data(self, tags: list[str], start, end, aggregate: str | None = None,
                     interval: str | None = None, max_size: int = 10_000) -> dict[str, list[tuple]]:
        """Return tag -> [(timestamp, value)], following continuations. Empty-edge (null) values are dropped.

        Read paths are prefixed with the view, e.g. "localhost.Wells.Winter.<UWI>.OilRate".
        """
        body = {"tags": tags, "startTime": start if isinstance(start, str) else _iso(start),
                "endTime": end if isinstance(end, str) else _iso(end), "maxSize": max_size}
        if aggregate:
            body.update(aggregateName=aggregate, aggregateInterval=interval or "1d")
        out: dict[str, list] = {t: [] for t in tags}
        cont = None
        while True:
            data = self._call("getTagData2", tolerate_tag_errors=True, **body,
                              **({"continuation": cont} if cont else {}))
            self.last_errors = data.get("errors") or []  # missing tags etc.; their lists stay empty
            for tag, points in (data.get("data") or {}).items():
                out.setdefault(tag, []).extend((p["t"], p["v"]) for p in points if p.get("v") is not None)
            cont = data.get("continuation")
            if not cont:
                return out
