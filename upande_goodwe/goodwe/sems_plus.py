"""Client for GoodWe SEMS+ (the platform replacing the classic SEMS Portal).

SEMS+ has no public API documentation. Login, request signing and endpoints follow
pygoodwe 0.2.x (https://github.com/yaleman/pygoodwe, MIT licence, James Hodgkinson).
The daily production endpoint and the browser headers it needs follow gw2pvo's SEMS+
notes (https://github.com/aroundmyroom/gw2pvo, SEMSPLUS.md, MIT licence).
GoodWe can change any of this without notice; the health alerts in health.py report
when that happens.

Session: the SEMS+ token is kept in the Redis cache (re-login on expiry or cache flush),
so no long token has to fit into a doctype field.
"""

import base64
import hashlib
import json
import time

import frappe
import requests

LOGIN_URL = "https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login"
DEFAULT_GATEWAY = "https://eu-gateway.semsportal.com/web/sems"
SUCCESS_CODES = (0, "0", "00000")
RATE_LIMIT_CODE = "GY0429"
SESSION_EXPIRED_CODE = "C0602"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CACHE_KEY = "goodwe_sems_plus_session"
SESSION_TTL = 12 * 3600
TIMEOUT = 30

DEVICE_TYPES = ("INVERTER", "ENERGY_STORAGE_INTEGRATED_CABINET")

# The SEMS+ web app sends these on every call. Some endpoints (stations/production)
# refuse requests without brand/os even with a valid token (per gw2pvo's notes).
BROWSER_HEADERS = {
    "neutral": "0",
    "currentLang": "en",
    "brand": "Chrome 126.0.0.0 Google Inc. en-GB Africa/Nairobi",
    "os": "Windows 10.0 desktop",
}


class SemsPlusError(Exception):
    """kind: auth | rate_limit | permission | transport | api | unavailable"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _signature(token):
    timestamp = round(time.time() * 1000)
    digest = hashlib.sha256(
        f"{timestamp}@{token.get('uid', '')}@{token.get('token', '')}".encode()
    ).hexdigest()
    return base64.b64encode(f"{digest}@{timestamp}".encode()).decode()


class SemsPlusClient:
    def __init__(self):
        self.settings = frappe.get_single("GoodWe Credentials")
        cached = frappe.cache.get_value(CACHE_KEY) or {}
        self.token = cached.get("token")
        self.api_base = cached.get("api_base") or DEFAULT_GATEWAY

    # ------------------------------------------------------------------ auth

    def login(self):
        """Logs in to SEMS+ and caches the session. Returns the login response data."""
        password = self.settings.get_password("password")
        pwd = base64.b64encode(hashlib.md5(password.encode(), usedforsecurity=False).hexdigest().encode()).decode()
        empty_token = {"uid": "", "timestamp": 0, "token": "", "client": "semsPlusWeb",
                       "version": "", "language": self.settings.language or "en"}
        try:
            response = requests.post(
                LOGIN_URL,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://semsplus.goodwe.com",
                    "Referer": "https://semsplus.goodwe.com/",
                    "Token": json.dumps(empty_token),
                    "X-Signature": _signature({}),
                    "User-Agent": WEB_USER_AGENT,
                },
                json={"account": self.settings.email, "pwd": pwd, "agreement": 1,
                      "isChinese": False, "isLocal": False},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise SemsPlusError("transport", f"SEMS+ login request failed: {exc}") from exc

        if response.status_code == 429:
            raise SemsPlusError("rate_limit", "SEMS+ rate limit reached during login")
        if response.status_code in (401, 403):
            raise SemsPlusError("auth", "SEMS+ login was rejected (check email and password)")
        try:
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SemsPlusError("transport", f"SEMS+ login returned HTTP {response.status_code}") from exc

        code = body.get("code") if isinstance(body, dict) else None
        if code == RATE_LIMIT_CODE:
            raise SemsPlusError("rate_limit", "SEMS+ rate limit reached during login")
        if code not in SUCCESS_CODES:
            msg = body.get("msg") or body.get("message") if isinstance(body, dict) else None
            raise SemsPlusError("auth", f"SEMS+ login was rejected: {msg or code}")
        token = body.get("data")
        if not isinstance(token, dict) or not token.get("token"):
            raise SemsPlusError("auth", "SEMS+ login returned no token")

        api = body.get("api") or token.get("api")
        self.token = token
        self.api_base = api.rstrip("/") if isinstance(api, str) and api.startswith("https://") else DEFAULT_GATEWAY
        frappe.cache.set_value(CACHE_KEY, {"token": token, "api_base": self.api_base},
                               expires_in_sec=SESSION_TTL)
        return token

    # --------------------------------------------------------------- request

    def request(self, path, method="GET", payload=None):
        if not self.token:
            self.login()
        for attempt in range(2):
            try:
                response = requests.request(
                    method,
                    self.api_base + path,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": WEB_USER_AGENT,
                        "Token": json.dumps(self.token),
                        "X-Signature": _signature(self.token),
                        **BROWSER_HEADERS,
                    },
                    json=payload,
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                raise SemsPlusError("transport", f"SEMS+ request failed: {path}: {exc}") from exc

            if response.status_code == 429:
                raise SemsPlusError("rate_limit", "SEMS+ rate limit reached")
            if response.status_code == 403:
                raise SemsPlusError("permission", f"SEMS+ access denied: {path}")
            if response.status_code == 401 and attempt == 0:
                self.login()
                continue
            try:
                response.raise_for_status()
                body = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SemsPlusError("transport", f"SEMS+ HTTP {response.status_code}: {path}") from exc

            code = body.get("code") if isinstance(body, dict) else None
            if code == RATE_LIMIT_CODE:
                raise SemsPlusError("rate_limit", "SEMS+ rate limit reached")
            if code == SESSION_EXPIRED_CODE and attempt == 0:
                self.login()
                continue
            if code not in SUCCESS_CODES:
                kind = "auth" if code == SESSION_EXPIRED_CODE else "api"
                raise SemsPlusError(kind, f"SEMS+ rejected {path}: {code} {body.get('msg', '') if isinstance(body, dict) else ''}")
            return body.get("data")
        raise SemsPlusError("auth", "SEMS+ session renewal failed")

    # ------------------------------------------------------------------ data

    @staticmethod
    def factors(groups):
        """Flattens SEMS+ factor groups into {code: value}."""
        result = {}
        for group in groups or []:
            if isinstance(group, dict):
                for factor in group.get("factors") or []:
                    if isinstance(factor, dict) and isinstance(factor.get("code"), str) and factor.get("data") is not None:
                        result[factor["code"]] = factor["data"]
        return result

    def devices(self, station_id):
        data = self.request(f"/sems-plant/api/stations/device/all-status?stationId={station_id}")
        if not isinstance(data, dict):
            raise SemsPlusError("unavailable", "SEMS+ device status is unavailable")
        out = []
        for group in data.get("deviceDetailList") or []:
            if not isinstance(group, dict) or group.get("deviceType") not in DEVICE_TYPES:
                continue
            for status in group.get("statusDetailList") or []:
                if not isinstance(status, dict):
                    continue
                details = status.get("detailMap") or {}
                for serial in status.get("snList") or []:
                    if isinstance(serial, str):
                        detail = details.get(serial) if isinstance(details, dict) else None
                        out.append({"sn": serial, "status": status.get("status"),
                                    "deviceType": group["deviceType"], "detail": detail or {}})
        return out

    def station_snapshot(self, station_id):
        """Everything needed for one sync: station flow plus telemetry and counters per inverter.
        A device whose telemetry or counters are refused is kept with what is available."""
        devices = self.devices(station_id)
        if not devices:
            raise SemsPlusError("unavailable", "SEMS+ returned no inverters for this station")
        flow = self.request(f"/sems-plant/api/stations/flow?stationId={station_id}")
        if not isinstance(flow, dict):
            raise SemsPlusError("unavailable", "SEMS+ station flow is unavailable")

        for device in devices:
            suffix = f"?deviceType={device['deviceType']}&pwId={station_id}"
            device["telemetry"], device["counters"], device["errors"] = {}, {}, []
            for key, endpoint in (("telemetry", "telemetry"), ("counters", "telecounting")):
                try:
                    device[key] = self.factors(
                        self.request(f"/sems-plant/api/equipments/{device['sn']}/{endpoint}{suffix}")
                    )
                except SemsPlusError as exc:
                    if exc.kind == "rate_limit":
                        raise
                    device["errors"].append(f"{endpoint}: {exc}")
        return {"station_id": station_id, "flow": flow, "devices": devices}

    def daily_production(self, station_id, day):
        """PV energy (kWh) the station generated on `day` (date or YYYY-MM-DD), as SEMS+ shows it.
        Returns None if SEMS+ has no figure for that day."""
        day = str(day)[:10]
        data = self.request(
            "/sems-plant/api/stations/production",
            method="POST",
            payload={
                "stationId": station_id,
                "items": ["proSystemTotalStats"],
                "dimension": "day",
                "isReport": False,
                "startTime": f"{day} 00:00:00",
                "endTime": f"{day} 23:59:59",
            },
        )
        if not isinstance(data, dict):
            return None
        value = data.get("proSystemTotalStats")
        try:
            return float(value) if value not in (None, "", "--") else None
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------- manual tools

@frappe.whitelist()
def test_login():
    """Checks the SEMS+ login with the credentials in GoodWe Credentials."""
    frappe.only_for("System Manager")
    client = SemsPlusClient()
    client.login()
    return {"logged_in": True, "gateway": client.api_base}


@frappe.whitelist()
def inspect_station(station_id=None):
    """Returns the raw SEMS+ snapshot, including every telemetry and counter code GoodWe sends,
    so more fields can be mapped in sync.SEMS_PLUS_TELEMETRY_MAP. Nothing is saved."""
    frappe.only_for("System Manager")
    station_id = station_id or frappe.db.get_value("Solar Site", {"station_id": ["is", "set"]}, "station_id")
    if not station_id:
        frappe.throw("No station_id given and no Solar Site has one.")
    return SemsPlusClient().station_snapshot(station_id)
