# your_app/api.py

import frappe
from frappe.utils import now_datetime, add_to_date

WINDOW_MINUTES = 30


def _latest_stats(center_time):
    """Aggregate KPI stats from the latest reading per inverter within
    +/- WINDOW_MINUTES of center_time."""
    start = add_to_date(center_time, minutes=-WINDOW_MINUTES)
    end = add_to_date(center_time, minutes=WINDOW_MINUTES)

    readings = frappe.get_all(
        "GoodWe Reading",
        filters={"reading_time": ["between", [start, end]]},
        fields=["inverter", "reading_time", "current_power_kw",
                "daily_energy_kwh", "total_energy_kwh"],
        order_by="reading_time desc",
    )
    if not readings:
        return None

    latest_per_inverter = {}
    for r in readings:
        latest_per_inverter.setdefault(r.inverter, r)

    devices = list(latest_per_inverter.values())
    n = len(devices)

    return {
        "avg_power": sum(d.current_power_kw or 0 for d in devices) / n,
        "peak_power": max(d.current_power_kw or 0 for d in devices),
        "avg_daily_energy": sum(d.daily_energy_kwh or 0 for d in devices) / n,
        "avg_total_energy": sum(d.total_energy_kwh or 0 for d in devices) / n,
    }


def _pct_change(current, previous):
    if not previous:
        return None
    return round(((current - previous) / previous) * 100, 1)


@frappe.whitelist()
def get_kpi_summary():
    """KPI values for the dashboard header, each with % change vs. the
    same time yesterday. Replaces the client-side aggregation currently
    done over frappe.client.get_list."""
    now = now_datetime()
    yesterday = add_to_date(now, days=-1)

    today_stats = _latest_stats(now)
    yesterday_stats = _latest_stats(yesterday)

    if not today_stats:
        return {}

    result = {}
    for key in ("avg_power", "peak_power", "avg_daily_energy", "avg_total_energy"):
        current_val = today_stats[key]
        previous_val = yesterday_stats[key] if yesterday_stats else None
        result[key] = {
            "value": round(current_val, 1),
            "change_pct": _pct_change(current_val, previous_val),
        }

    return result