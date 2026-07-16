"""UOMP Gateway StorageProvider.

Uploads deliverables to the buyer's local payload relay, exposed publicly via
a Cloudflare Tunnel (reverse gateway). The buyer passes delivery_gateway_url
and delivery_gateway_token in the notify_funded A2A data; the seller uses them
to choose this provider over the default LocalStorageProvider.

The upload endpoint (POST /v1/payload/upload) requires Bearer-token auth.
The download endpoint (GET /v1/payload/:id) is public — the payload_id is
unguessable and the URL is published on-chain anyway.

The returned URL is: {gateway_url}/v1/payload/{payload_id}
It goes on-chain as-is (uses_file_url=False — no ERC8183_AGENT_URL needed).
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.request

from bnbagent.storage import StorageProvider

_VERIFY_SSL = False                             # trycloudflare certs are CA-signed but HTTPS via tunnel
_TIMEOUT_UPLOAD = 30
_TIMEOUT_DOWNLOAD = 30

# Disable SSL verification for cloudflare tunnel endpoints
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class UOMPGatewayStorageProvider(StorageProvider):
    """StorageProvider backed by the buyer's UOMP payload relay."""

    uses_file_url = False     # the returned URL is already public (https://…trycloudflare.com)

    def __init__(self, gateway_url: str, token: str) -> None:
        self._base = gateway_url.rstrip("/")
        self._token = token

    async def upload(self, data: dict, filename: str | None = None) -> str:
        body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/v1/payload/upload",
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=_TIMEOUT_UPLOAD) as resp:
            result: dict = json.loads(resp.read())
        payload_id = result["payload_id"]
        return f"{self._base}/v1/payload/{payload_id}"

    async def download(self, url: str) -> dict:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=_TIMEOUT_DOWNLOAD) as resp:
            return json.loads(resp.read())

    async def exists(self, url: str) -> bool:
        try:
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, context=_SSL_CTX, timeout=10)
            return True
        except Exception:
            return False


# Thread-level lock so concurrent submit_result calls (rare but possible when
# two jobs run at the same time) never overlap their storage_provider_from_config
# monkey-patch.
submit_lock = threading.Lock()
