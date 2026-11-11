"""Daily energy worked out from each inverter's lifetime counter (total_energy_kwh).

Taking the day's highest `daily_energy_kwh` is unreliable: the first readings after
midnight still carry the previous day's total, and missed syncs leave the maximum
short. The lifetime counter only ever goes up, so

    energy(day) = last lifetime value on the day - last lifetime value before the day

is correct as long as there is one reading on the day and one before it.
"""

import frappe
from frappe.utils import add_days, flt, getdate

# A day with at least this many readings but no change in the lifetime counter
# had no live data (frozen API data) or no generation at all.
MIN_READINGS_FOR_LIVE_CHECK = 8
FROZEN_THRESHOLD_KWH = 0.05
# More than this per inverter per day means the counter jumped (e.g. switching from the
# classic SEMS Portal's frozen counter to SEMS+), not real generation. 15 kW x 10 h.
MAX_KWH_PER_INVERTER_DAY = 150
LOOKBACK_DAYS = 10


def daily_energy_by_inverter(from_date, to_date):
    """Returns {inverter: {date_str: {...}}} for every day in [from_date, to_date] with readings.

    Each day has:
      kwh        energy for the day from the lifetime counter (None if it can't be worked out)
      live       False when the counter didn't move despite enough readings (frozen / no generation)
      gap_days   days since the previous reading day; > 1 means kwh covers the missing days too
      readings   number of readings
      lifetime   counter value at the end of the day
    """
    from_date, to_date = getdate(from_date), getdate(to_date)
    rows = frappe.db.sql(
        """
        SELECT inverter,
               DATE(reading_time) AS d,
               MAX(total_energy_kwh) AS emax,
               MIN(total_energy_kwh) AS emin,
               COUNT(*) AS n
        FROM `tabGoodWe Reading`
        WHERE reading_time >= %s AND reading_time < %s
          AND total_energy_kwh > 0
        GROUP BY inverter, DATE(reading_time)
        ORDER BY inverter, d
        """,
        (add_days(from_date, -LOOKBACK_DAYS), add_days(to_date, 1)),
        as_dict=True,
    )

    result = {}
    prev = {}
    for r in rows:
        inv, day = r.inverter, getdate(r.d)
        emax, emin = flt(r.emax), flt(r.emin)
        before = prev.get(inv)
        if before:
            kwh = emax - before["emax"]
            gap = (day - before["day"]).days
        else:
            kwh = emax - emin  # first day we know about: only the part of the day we saw
            gap = None
        prev[inv] = {"day": day, "emax": emax}

        if day < from_date:
            continue
        if kwh < 0 or kwh > MAX_KWH_PER_INVERTER_DAY * max(gap or 1, 1):
            kwh = None  # counter reset, inverter replaced, or a switch of data source
        live = not (kwh is not None and kwh < FROZEN_THRESHOLD_KWH and r.n >= MIN_READINGS_FOR_LIVE_CHECK)
        result.setdefault(inv, {})[str(day)] = {
            "kwh": round(kwh, 2) if kwh is not None else None,
            "live": live,
            "gap_days": gap,
            "readings": r.n,
            "lifetime": round(emax, 1),
        }
    return result


def official_daily_energy(from_date, to_date):
    """{date_str: {"kwh": total of all stations, "source": ...}} from GoodWe Daily Energy
    (GoodWe's own daily totals fetched from SEMS+, or entered by hand)."""
    if not frappe.db.table_exists("GoodWe Daily Energy"):
        return {}
    rows = frappe.get_all(
        "GoodWe Daily Energy",
        filters={"energy_date": ["between", [getdate(from_date), getdate(to_date)]], "energy_kwh": ["is", "set"]},
        fields=["energy_date", "energy_kwh", "source"],
    )
    out = {}
    for r in rows:
        day = out.setdefault(str(r.energy_date), {"kwh": 0.0, "sources": set()})
        day["kwh"] = round(day["kwh"] + flt(r.energy_kwh), 2)
        day["sources"].add(r.source or "Manual")
    return {d: {"kwh": v["kwh"], "source": ", ".join(sorted(v["sources"]))} for d, v in out.items()}


def fleet_daily_energy(from_date, to_date):
    """Returns {date_str: {"kwh", "live", "source", "inverters"}} per day.

    Official daily totals (GoodWe Daily Energy) win where they exist; otherwise the total of
    live inverters from the lifetime counters ("Readings"). Frozen days without an official
    total have kwh None and live False."""
    per_inv = daily_energy_by_inverter(from_date, to_date)
    days = {}
    for inv, by_day in per_inv.items():
        for d, v in by_day.items():
            day = days.setdefault(d, {"kwh": None, "live": False, "source": "Readings", "inverters": {}})
            day["inverters"][inv] = v
            if v["live"] and v["kwh"] is not None:
                day["kwh"] = round((day["kwh"] or 0) + v["kwh"], 2)
                day["live"] = True
    for d, off in official_daily_energy(from_date, to_date).items():
        day = days.setdefault(d, {"kwh": None, "live": False, "source": "Readings", "inverters": {}})
        day["estimate_kwh"] = day["kwh"]
        day["kwh"] = off["kwh"]
        day["live"] = True
        day["source"] = off["source"]
    return days
