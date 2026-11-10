import json

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from upande_goodwe.goodwe.energy import daily_energy_by_inverter, fleet_daily_energy

BASELINE_DAYS = 7


def generate_daily_summary():
    """Scheduled daily: summarises yesterday's fleet performance in GoodWe Daily Summary."""
    build_summary(add_days(nowdate(), -1))


@frappe.whitelist()
def regenerate_summary(date=None):
    """Rebuilds the summary for one day (default yesterday). Safe to run repeatedly.

    frappe.call("upande_goodwe.goodwe.intelligency.regenerate_summary", {date: "2026-11-03"})
    bench --site <site> execute upande_goodwe.goodwe.intelligency.regenerate_summary --kwargs "{'date': '2026-11-03'}"
    """
    frappe.only_for("System Manager")
    return build_summary(getdate(date) if date else add_days(nowdate(), -1))


def build_summary(target_date):
    target_date = getdate(target_date)
    stats = _get_daily_stats(target_date)
    if not stats:
        return None

    baseline = _get_baseline_energy(target_date, days=BASELINE_DAYS)
    summary_text = _build_summary_text(stats, baseline)

    name = frappe.db.exists("GoodWe Daily Summary", {"summary_date": target_date})
    doc = frappe.get_doc("GoodWe Daily Summary", name) if name else frappe.new_doc("GoodWe Daily Summary")
    doc.summary_date = target_date
    doc.summary_text = summary_text
    doc.raw_stats = json.dumps(stats, default=str)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"date": str(target_date), "summary": summary_text, "stats": stats}


def _get_daily_stats(target_date):
    readings = frappe.get_all(
        "GoodWe Reading",
        filters={"reading_time": ["between", [f"{target_date} 00:00:00", f"{target_date} 23:59:59"]]},
        fields=["inverter", "reading_time", "current_power_kw", "status"],
    )
    if not readings:
        return None

    energy = daily_energy_by_inverter(target_date, target_date)

    by_inverter = {}
    for r in readings:
        by_inverter.setdefault(r.inverter, []).append(r)

    per_inverter_stats = {}
    fleet_kwh = 0
    frozen_inverters = []
    offline_events = []

    for inv, rows in by_inverter.items():
        powers = [flt(row.current_power_kw) for row in rows]
        e = (energy.get(inv) or {}).get(str(target_date)) or {}
        kwh = e.get("kwh")
        live = e.get("live", True)

        if live and kwh is not None:
            fleet_kwh += kwh
        if not live:
            frozen_inverters.append(inv)

        per_inverter_stats[inv] = {
            "avg_power_kw": round(sum(powers) / len(powers), 2),
            "peak_power_kw": round(max(powers), 2),
            "daily_energy_kwh": round(kwh, 2) if kwh is not None else None,
            "lifetime_kwh": e.get("lifetime"),
            "live_data": live,
            "covers_days": e.get("gap_days") if (e.get("gap_days") or 1) > 1 else 1,
        }

        offline_rows = [row for row in rows if row.status == "Offline"]
        if offline_rows:
            offline_events.append({
                "inverter": inv,
                "offline_readings": len(offline_rows),
                "first_offline_at": str(min(row.reading_time for row in offline_rows)),
            })

    return {
        "date": str(target_date),
        "fleet_daily_energy_kwh": round(fleet_kwh, 2),
        "energy_method": "lifetime counter difference",
        "no_live_data": len(frozen_inverters) == len(by_inverter),
        "frozen_inverters": frozen_inverters,
        "per_inverter": per_inverter_stats,
        "offline_events": offline_events,
    }


def _get_baseline_energy(target_date, days=BASELINE_DAYS):
    """Average fleet energy over the `days` before target_date, using only days with live data."""
    window = fleet_daily_energy(add_days(target_date, -days), add_days(target_date, -1))
    totals = [d["kwh"] for d in window.values() if d["live"] and d["kwh"]]
    return sum(totals) / len(totals) if totals else None


def _build_summary_text(stats, baseline):
    """Rule-based natural-language summary, no external API required."""
    if stats["no_live_data"]:
        lifetimes = ", ".join(
            f"{inv} at {v['lifetime_kwh']} kWh" for inv, v in stats["per_inverter"].items() if v.get("lifetime_kwh")
        )
        return (
            f"No live data from GoodWe for {stats['date']}: the inverters' lifetime energy counters didn't move "
            f"all day ({lifetimes}), so the readings are frozen rather than a real 0 kWh. "
            "Check the GoodWe/SEMS connection."
        )

    parts = []
    energy = stats["fleet_daily_energy_kwh"]

    if baseline:
        pct_diff = ((energy - baseline) / baseline) * 100
        if pct_diff <= -15:
            parts.append(
                f"Fleet output was {abs(round(pct_diff))}% below the {BASELINE_DAYS}-day average "
                f"({flt(energy, 1)} kWh vs. {flt(baseline, 1)} kWh)."
            )
        elif pct_diff >= 15:
            parts.append(
                f"Fleet output was {round(pct_diff)}% above the {BASELINE_DAYS}-day average, "
                f"a strong day ({flt(energy, 1)} kWh)."
            )
        else:
            parts.append(f"Fleet output was normal at {flt(energy, 1)} kWh.")
    else:
        parts.append(f"Fleet generated {flt(energy, 1)} kWh.")

    if stats["frozen_inverters"]:
        parts.append(
            f"No live data from {', '.join(stats['frozen_inverters'])}; "
            "its output is not included in the total."
        )

    multi_day = [inv for inv, v in stats["per_inverter"].items() if (v.get("covers_days") or 1) > 1]
    if multi_day:
        parts.append(
            f"Readings were missing before this day for {', '.join(multi_day)}, "
            "so its figure covers more than one day."
        )

    if stats["offline_events"]:
        worst = max(stats["offline_events"], key=lambda e: e["offline_readings"])
        if len(stats["offline_events"]) == 1:
            parts.append(
                f"{worst['inverter']} went offline starting around {worst['first_offline_at'].split(' ')[1][:5]}."
            )
        else:
            names = ", ".join(e["inverter"] for e in stats["offline_events"])
            parts.append(f"{len(stats['offline_events'])} inverters ({names}) had offline periods.")

    live = {
        inv: v for inv, v in stats["per_inverter"].items()
        if v["live_data"] and v["daily_energy_kwh"] is not None
    }
    if len(live) > 1:
        best = max(live.items(), key=lambda kv: kv[1]["daily_energy_kwh"])
        worst = min(live.items(), key=lambda kv: kv[1]["daily_energy_kwh"])
        if best[0] != worst[0]:
            parts.append(
                f"{best[0]} led the fleet at {best[1]['daily_energy_kwh']} kWh; "
                f"{worst[0]} trailed at {worst[1]['daily_energy_kwh']} kWh."
            )

    return " ".join(parts)


@frappe.whitelist()
def get_latest_daily_summary():
    rows = frappe.get_all(
        "GoodWe Daily Summary",
        fields=["summary_date", "summary_text"],
        order_by="summary_date desc",
        limit=1,
    )
    return rows[0] if rows else None
