# your_app/tasks.py

import json
import frappe
from frappe.utils import nowdate, add_days, flt


def generate_daily_summary():
    """Scheduled job (run daily, e.g. 00:15) — builds a one/two sentence
    summary of yesterday's fleet performance from rule-based comparisons
    against a trailing baseline, and stores it in GoodWe Daily Summary."""
    target_date = add_days(nowdate(), -1)

    stats = _get_daily_stats(target_date)
    if not stats:
        return

    baseline = _get_baseline_energy(target_date, days=7)
    summary_text = _build_summary_text(stats, baseline)

    doc = frappe.new_doc("GoodWe Daily Summary")
    doc.summary_date = target_date
    doc.summary_text = summary_text
    doc.raw_stats = json.dumps(stats)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _get_daily_stats(target_date):
    readings = frappe.get_all(
        "GoodWe Reading",
        filters={"reading_time": ["between",
                 [f"{target_date} 00:00:00", f"{target_date} 23:59:59"]]},
        fields=["inverter", "reading_time", "current_power_kw",
                "daily_energy_kwh", "status"],
    )
    if not readings:
        return None

    by_inverter = {}
    for r in readings:
        by_inverter.setdefault(r.inverter, []).append(r)

    per_inverter_stats = {}
    fleet_daily_energy = 0
    offline_events = []

    for inv, rows in by_inverter.items():
        powers = [row.current_power_kw or 0 for row in rows]
        max_daily_energy = max((row.daily_energy_kwh or 0 for row in rows), default=0)
        fleet_daily_energy += max_daily_energy

        per_inverter_stats[inv] = {
            "avg_power_kw": round(sum(powers) / len(powers), 2),
            "peak_power_kw": round(max(powers), 2),
            "daily_energy_kwh": round(max_daily_energy, 2),
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
        "fleet_daily_energy_kwh": round(fleet_daily_energy, 2),
        "per_inverter": per_inverter_stats,
        "offline_events": offline_events,
    }


def _get_baseline_energy(target_date, days=7):
    """Average fleet daily energy over the `days` preceding target_date,
    used as the 'normal' reference point for comparison."""
    window_start = add_days(target_date, -days)

    readings = frappe.get_all(
        "GoodWe Reading",
        filters={"reading_time": ["between",
                 [f"{window_start} 00:00:00", f"{target_date} 00:00:00"]]},
        fields=["inverter", "reading_time", "daily_energy_kwh"],
    )
    if not readings:
        return None

    by_day = {}
    for r in readings:
        day_key = str(r.reading_time.date())
        by_day.setdefault(day_key, {})
        prev = by_day[day_key].get(r.inverter, 0)
        by_day[day_key][r.inverter] = max(prev, r.daily_energy_kwh or 0)

    daily_totals = [sum(inv_vals.values()) for inv_vals in by_day.values()]
    if not daily_totals:
        return None

    return sum(daily_totals) / len(daily_totals)


def _build_summary_text(stats, baseline):
    """Rule-based natural-language summary — no external API required."""
    parts = []
    energy = stats["fleet_daily_energy_kwh"]

    # Compare against baseline
    if baseline:
        pct_diff = ((energy - baseline) / baseline) * 100 if baseline else 0
        if pct_diff <= -15:
            parts.append(
                f"Fleet output was {abs(round(pct_diff))}% below the "
                f"{7}-day average ({flt(energy, 1)} kWh vs. {flt(baseline, 1)} kWh)."
            )
        elif pct_diff >= 15:
            parts.append(
                f"Fleet output was {round(pct_diff)}% above the {7}-day "
                f"average — a strong day ({flt(energy, 1)} kWh)."
            )
        else:
            parts.append(f"Fleet output was normal at {flt(energy, 1)} kWh.")
    else:
        parts.append(f"Fleet generated {flt(energy, 1)} kWh.")

    # Offline events
    if stats["offline_events"]:
        worst = max(stats["offline_events"], key=lambda e: e["offline_readings"])
        if len(stats["offline_events"]) == 1:
            parts.append(f"{worst['inverter']} went offline starting around "
                         f"{worst['first_offline_at'].split(' ')[1][:5]}.")
        else:
            names = ", ".join(e["inverter"] for e in stats["offline_events"])
            parts.append(f"{len(stats['offline_events'])} inverters "
                         f"({names}) had offline periods.")

    # Best/worst inverter by daily energy, if more than one device
    per_inv = stats["per_inverter"]
    if len(per_inv) > 1:
        best = max(per_inv.items(), key=lambda kv: kv[1]["daily_energy_kwh"])
        worst = min(per_inv.items(), key=lambda kv: kv[1]["daily_energy_kwh"])
        if best[0] != worst[0]:
            parts.append(f"{best[0]} led the fleet at {best[1]['daily_energy_kwh']} kWh; "
                         f"{worst[0]} trailed at {worst[1]['daily_energy_kwh']} kWh.")

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