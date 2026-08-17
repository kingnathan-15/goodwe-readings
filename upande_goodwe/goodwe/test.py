import frappe
import pprint
from upande_goodwe.goodwe.client import GoodWeClient


@frappe.whitelist()
def test_client():
    client = GoodWeClient()

    data = client.get_monitor_detail(
        "6554968c-206d-4035-a8a5-737cf36ddda1"
    )

    pprint.pp(data)
    return data