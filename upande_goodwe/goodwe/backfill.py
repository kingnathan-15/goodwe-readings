"""Fill in daily solar energy for days the live sync missed or got frozen data.

The live sync only sees the present. For past days, SEMS+ still has GoodWe's own daily
total (stations/production, see sems_plus.daily_production). This module fetches it into
GoodWe Daily Energy, which energy.py, the daily summaries and the dashboard then prefer
over estimates from readings.

Manual use (System Manager):
  frappe.call("upande_goodwe.goodwe.backfill.backfill_daily_energy",
              {from_date: "2026-11-01", to_date: "2026-11-10"})
  bench --site <site> execute upande_goodwe.goodwe.backfill.backfill_daily_energy \
        --kwargs "{'from_date': '2026-11-01', 'to_date': '2026-11-10'}"
"""

import time

import frappe
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

from upande_goodwe.goodwe.auth import get_platform
from upande_goodwe.goodwe.energy import daily_energy_by_inverter

MAX_DAYS_PER_RUN = 62        # keep one run well inside SEMS+ rate limits
PAUSE_SECONDS = 1.0          # between SEMS+ calls
AUTO_BACKFILL_DAYS = 14      # the daily job looks this far back for gaps


def _stations(site=None):
    filters = {"station_id": ["is", "set"]}
    if site:
        filters["name"] = site
    return frappe.get_all("Solar Site", filters=filters, fields=["name", "station_id"])


def _days(from_date, to_date):
    day, out = getdate(from_date), []
    while day <= getdate(to_date):
        out.append(str(day))
        day = add_days(day, 1)
    return out


def _official_days(site, from_date, to_date):
    return {
        str(d)
        for d in frappe.get_all(
            "GoodWe Daily Energy",
            filters={"solar_site": site, "energy_date": ["between", [from_date, to_date]],
                     "energy_kwh": ["is", "set"]},
            pluck="energy_date",
        )
    }


def _live_days(site, from_date, to_date):
    """Days where the site's own readings gave live energy figures."""
    inverters = set(frappe.get_all("GoodWe Device", filters={"solar_site": site}, pluck="name"))
    live = set()
    for inv, by_day in daily_energy_by_inverter(from_date, to_date).items():
        if inv not in inverters:
            continue
        for day, v in by_day.items():
            if v["live"] and v["kwh"] is not None and (v.get("gap_days") or 1) <= 1:
                live.add(day)
    return live


def find_missing_days(site, from_date, to_date):
    """Days with neither an official total nor live readings."""
    have = _official_days(site, from_date, to_date) | _live_days(site, from_date, to_date)
    return [d for d in _days(from_date, to_date) if d not in have]


def _upsert(site, day, kwh, source="SEMS+ API", note=None):
    name = frappe.db.exists("GoodWe Daily Energy", {"solar_site": site, "energy_date": day})
    doc = frappe.get_doc("GoodWe Daily Energy", name) if name else frappe.new_doc("GoodWe Daily Energy")
    doc.solar_site = site
    doc.energy_date = day
    doc.energy_kwh = kwh
    doc.source = source
    doc.fetched_on = now_datetime()
    if note:
        doc.notes = note
    doc.save(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def backfill_daily_energy(from_date=None, to_date=None, only_missing=1, site=None):
    """Fetches GoodWe's daily totals from SEMS+ for a date range (default: last 14 days).

    only_missing=1 fetches only days with no official total and no live readings;
    only_missing=0 refreshes every day in the range. Today is never fetched (still changing).
    """
    frappe.only_for("System Manager")
    if get_platform() != "SEMS+":
        frappe.throw("Fetching past days needs API Platform = SEMS+ in GoodWe Credentials.")

    from upande_goodwe.goodwe.sems_plus import SemsPlusClient, SemsPlusError

    yesterday = getdate(add_days(nowdate(), -1))
    to_date = min(getdate(to_date) if to_date else yesterday, yesterday)
    from_date = getdate(from_date) if from_date else add_days(to_date, -(AUTO_BACKFILL_DAYS - 1))
    if from_date > to_date:
        return {"filled": 0, "failed": [], "days": [], "summary": "Nothing to fetch: the range has no past days."}

    only_missing = cint(only_missing)
    client = SemsPlusClient()
    filled, failed, no_data, touched = [], [], [], set()
    stopped = None

    for st in _stations(site):
        days = find_missing_days(st.name, from_date, to_date) if only_missing else _days(from_date, to_date)
        if len(days) > MAX_DAYS_PER_RUN:
            days = days[-MAX_DAYS_PER_RUN:]
            stopped = f"Only the latest {MAX_DAYS_PER_RUN} days were fetched; run again for earlier days."
        for day in days:
            try:
                kwh = client.daily_production(st.station_id, day)
            except SemsPlusError as exc:
                if exc.kind == "rate_limit":
                    stopped = "SEMS+ rate limit reached; run again later for the remaining days."
                    break
                failed.append({"site": st.name, "date": day, "error": str(exc)})
                continue
            if kwh is None:
                no_data.append(day)
            else:
                _upsert(st.name, day, round(kwh, 2))
                filled.append({"site": st.name, "date": day, "kwh": round(kwh, 2)})
                touched.add(day)
            frappe.db.commit()
            time.sleep(PAUSE_SECONDS)
        if stopped and "rate limit" in stopped:
            break

    # Rebuild the daily summaries for the days that changed.
    if touched:
        from upande_goodwe.goodwe.intelligency import build_summary

        for day in sorted(touched):
            try:
                build_summary(day)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"GoodWe summary rebuild failed for {day}")

    parts = [f"Fetched {len(filled)} day(s) from SEMS+"]
    if no_data:
        parts.append(f"SEMS+ had no figure for {len(no_data)} day(s)")
    if failed:
        parts.append(f"{len(failed)} failed")
    if stopped:
        parts.append(stopped)
    if not filled and not no_data and not failed and not stopped:
        parts = ["No missing days in this range"]
    return {
        "filled": len(filled),
        "days": filled,
        "no_data": no_data,
        "failed": failed,
        "summary": ". ".join(parts) + ".",
    }


def fetch_daily_totals():
    """Scheduled shortly after midnight: stores yesterday's official total for every station,
    then fills any other gaps in the last AUTO_BACKFILL_DAYS days."""
    if get_platform() != "SEMS+":
        return
    yesterday = add_days(nowdate(), -1)
    try:
        backfill_daily_energy(yesterday, yesterday, only_missing=0)
        backfill_daily_energy(add_days(yesterday, -(AUTO_BACKFILL_DAYS - 1)), add_days(yesterday, -1), only_missing=1)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "GoodWe daily totals fetch failed")


@frappe.whitelist()
def missing_days(from_date, to_date, site=None):
    """Lists days with no official total and no live readings, per station (nothing is fetched)."""
    if not frappe.has_permission("GoodWe Reading", "read"):
        frappe.throw("Not permitted", frappe.PermissionError)
    return {st.name: find_missing_days(st.name, from_date, min(getdate(to_date), getdate(nowdate())))
            for st in _stations(site)}
