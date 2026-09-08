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

        try:
            response = requests.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            frappe.throw("Unable to reach GoodWe SEMS Portal. Please check network connectivity.")
        except requests.exceptions.Timeout:
            frappe.throw("GoodWe API request timed out after 30 seconds.")
        except requests.exceptions.HTTPError as e:
            frappe.throw(f"GoodWe HTTP Error {response.status_code}: {str(e)}")
        except requests.exceptions.RequestException as e:
            frappe.throw(f"GoodWe Request Error: {str(e)}")

        try:
            result = response.json()
        except json.JSONDecodeError:
            frappe.throw("Failed to parse JSON response from GoodWe API.")

        if not isinstance(result, dict):
            frappe.throw("Invalid response structure received from GoodWe API.")

        if str(result.get("code")) != "0":
            frappe.throw(result.get("msg", "GoodWe API Error"))

        if "data" not in result:
            frappe.throw("Response missing 'data' field from GoodWe API.")

        return result["data"]
    
    def get_monitor_detail(self, power_station_id):
        return self.post(
            "/v1/PowerStation/GetMonitorDetailByPowerstationId",
            {
                "powerStationId": power_station_id
            }
        )