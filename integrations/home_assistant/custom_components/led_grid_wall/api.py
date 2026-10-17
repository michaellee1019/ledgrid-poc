"""Bounded, asynchronous automation API client; commands are never retried."""
import asyncio
import math
import time
from uuid import UUID
from urllib.parse import urlsplit

import aiohttp

API_PATH = "/api/v1/automation"


class WallError(Exception):
    """The wall did not provide a valid current observation or acknowledgement."""


def normalize_url(value):
    """Accept an HTTP origin only; the API is hosted at its root."""
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment):
        raise ValueError("Use an http(s) origin, such as http://ledgridwall.local:5000")
    # Reading the port also rejects malformed ports.
    _ = parsed.port
    return value


def validate_status(data, installation_id=None):
    """A successful HTTP response alone is not a current controller observation."""
    if not isinstance(data, dict):
        raise WallError("Invalid wall status")
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise WallError("Invalid wall settings")
    stamp = data.get("observed_at")
    pace = settings.get("animation_speed_scale")
    brightness = settings.get("brightness")
    if (data.get("schema") != "ledgrid.automation-status" or data.get("schema_version") != 1
            or data.get("fresh") is not True
            or not isinstance(data.get("installation_id"), str) or not data["installation_id"]
            or installation_id is not None and data["installation_id"] != installation_id
            or not isinstance(data.get("server_session_id"), str) or not data["server_session_id"]
            or not isinstance(data.get("controller_session_id"), str) or not data["controller_session_id"]
            or type(data.get("controller_state_revision")) is not int or data["controller_state_revision"] < 0
            or type(stamp) not in {int, float} or not -2 <= time.time() - stamp <= 5
            or type(settings.get("power")) is not bool
            or type(brightness) is not int or not 0 <= brightness <= 255
            or type(pace) not in {int, float} or not math.isfinite(pace) or pace <= 0):
        raise WallError("No fresh, valid controller observation")
    return data


class WallApi:
    def __init__(self, session, url):
        self.session = session
        self.url = normalize_url(url)

    async def request(self, method, path, **kwargs):
        try:
            async with self.session.request(
                method, self.url + API_PATH + path,
                timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False, **kwargs,
            ) as response:
                if not 200 <= response.status < 300:
                    try:
                        details = await response.json()
                        error = details.get('error') if isinstance(details, dict) else None
                    except (ValueError, aiohttp.ClientError):
                        error = None
                    raise WallError(f"Wall returned HTTP {response.status}: {error or 'request rejected'}; no retry sent")
                data = await response.json()
                if not isinstance(data, dict):
                    raise WallError("Invalid wall response")
                return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WallError(f"Wall request failed: {err}") from err

    async def capabilities(self):
        data = await self.request("GET", "/capabilities")
        if (data.get("schema") != "ledgrid.automation-capabilities" or data.get("schema_version") != 1
                or not isinstance(data.get("installation_id"), str) or not data["installation_id"]
                or not isinstance(data.get("commands"), list)
                or "set_device_state" not in data["commands"]):
            raise WallError("Unsupported wall automation API")
        return data

    async def status(self, installation_id=None):
        return validate_status(await self.request("GET", "/status"), installation_id)

    async def catalog(self, installation_id):
        data = await self.request("GET", "/catalog")
        if (data.get("schema") != "ledgrid.automation-catalog" or data.get("schema_version") != 1
                or data.get("installation_id") != installation_id or not isinstance(data.get("content"), list)):
            raise WallError("Invalid wall content catalog")
        identities = set()
        for item in data['content']:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                    or item['id'] in identities or not isinstance(item.get('label'), str)
                    or type(item.get('supported')) is not bool):
                raise WallError("Invalid or duplicate wall content identity")
            identities.add(item['id'])
        return data

    async def apply(self, before, fields):
        return await self.command(before, {("pace" if key == "multiplier" else key): value for key, value in fields.items()})

    async def command(self, before, fields, *, action="set_device_state"):
        """Submit once; poll only its receipt. Catalog changes never enqueue work."""
        payload = {
            "action": action,
            "expected_server_session_id": before["server_session_id"],
            "expected_controller_session_id": before["controller_session_id"],
            "expected_controller_state_revision": before["controller_state_revision"],
            "expires_at": time.time() + 10,
            **fields,
        }
        if action != 'set_device_state':
            payload['expected_composer_revision'] = before['composer_revision']
        try:
            async with asyncio.timeout(12):
                receipt = await self.request("POST", "/commands", json=payload)
                request_id = receipt.get("request_id")
                try:
                    UUID(request_id)
                except (ValueError, TypeError, AttributeError) as err:
                    raise WallError("Wall returned an invalid command receipt") from err
                command_id = receipt.get("command_id")
                self.validate_receipt(receipt, request_id, command_id)
                while receipt.get("state") == "accepted":
                    receipt = await self.request("GET", f"/commands/{request_id}", params={"wait_seconds": 2})
                    self.validate_receipt(receipt, request_id, command_id)
                    if receipt.get("state") == "accepted":
                        await asyncio.sleep(0.1)
                if receipt.get("state") != "completed":
                    raise WallError(str(receipt.get("error") or "Wall command was not acknowledged"))
                if (receipt.get("controller_session_id") != before["controller_session_id"]
                        or type(receipt.get("controller_state_revision")) is not int
                        or receipt["controller_state_revision"] != before["controller_state_revision"] + 1):
                    raise WallError("Wall acknowledgement did not match the guarded controller revision")
                reconciliation = receipt.get('reconciliation')
                if isinstance(reconciliation, dict) and reconciliation.get('state') not in {'completed', 'following'}:
                    raise WallError(str(reconciliation.get('error') or 'Composer reconciliation failed'))
                return receipt
        except TimeoutError as err:
            raise WallError("Wall command acknowledgement timed out; no retry sent") from err

    @staticmethod
    def validate_receipt(receipt, request_id, command_id):
        if (receipt.get("schema") != "ledgrid.automation-command" or receipt.get("schema_version") != 1
                or receipt.get("request_id") != request_id
                or type(command_id) is not int or receipt.get("command_id") != command_id
                or receipt.get("state") not in {"accepted", "completed", "failed"}):
            raise WallError("Wall command receipt did not match the submitted command")
