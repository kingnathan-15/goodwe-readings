import frappe


def has_app_permission():
    """Shows the Solar & Water app icon to users who can read GoodWe or RO data."""
    if frappe.session.user == "Administrator":
        return True
    return bool(
        frappe.has_permission("GoodWe Reading", "read")
        or frappe.has_permission("RO Daily Log", "read")
    )
