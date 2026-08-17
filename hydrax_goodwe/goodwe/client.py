import json
import requests

import frappe


class GoodWeClient:
    def __init__(self):
        self.settings = frappe.get_single("GoodWe Credentials")

        self.base_url = self.settings.base_api_url.rstrip("/")

    @property
    def headers(self):
        return {
            "Content-Type": "application/json",
            "Token": json.dumps({
                "version": self.settings.version,
                "client": self.settings.client,
                "language": self.settings.language,
                "timestamp": str(self.settings.timestamp),
                "uid": self.settings.uid,
                "token": self.settings.get_password("session_token"),
            })
        }

    def post(self, endpoint, payload=None):

        if payload is None:
            payload = {}
        print("========== GOODWE REQUEST ==========")
        print("URL:", f"{self.base_url}/{endpoint.lstrip('/')}")
        print("HEADERS:", self.headers)
        print("PAYLOAD:", payload)
        print("====================================")
        response = requests.post(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            headers=self.headers,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        result = response.json()

        if str(result.get("code")) != "0":
            frappe.throw(result.get("msg", "GoodWe API Error"))

        return result["data"]

    def get_monitor_detail(self, power_station_id):
        return self.post(
            "/v1/PowerStation/GetMonitorDetailByPowerstationId",
            {
                "powerStationId": power_station_id
            }
        )