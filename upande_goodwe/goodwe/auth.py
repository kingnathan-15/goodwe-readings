import json
import requests
import frappe
from frappe.utils import now_datetime


LOGIN_URL = "https://www.semsportal.com/api/v1/Common/CrossLogin"

@frappe.whitelist()
def authenticate():
    settings = frappe.get_single("GoodWe Credentials")

    headers = {
        "Content-Type": "application/json",
        "Token": json.dumps({
            "version": settings.version,
            "client": settings.client,
            "language": settings.language
        })
    }

    payload = {
        "account": settings.email,
        "pwd": settings.get_password("password")
    }

    response = requests.post(
        LOGIN_URL,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if result.get("hasError"):
        settings.authentication_status = "Authentication Failed"
        settings.save(ignore_permissions=True)

        frappe.throw(result.get("msg"))

    if result.get("code") != 0:
        settings.authentication_status = "Authentication Failed"
        settings.save(ignore_permissions=True)

        frappe.throw(result.get("msg"))

    data = result["data"]

    settings.uid = data["uid"]
    settings.session_token = data["token"]
    settings.timestamp = data["timestamp"]
    settings.uid = result["data"]["uid"]
    settings.session_token = result["data"]["token"]
    settings.timestamp = result["data"]["timestamp"]

    settings.base_api_url = result["api"]
    settings.socket_url = result["components"]["msgSocketAdr"]

    settings.authentication_status = "Authenticated"
    settings.last_authenticated = now_datetime()

    settings.save(ignore_permissions=True)

    frappe.db.commit()

    return result