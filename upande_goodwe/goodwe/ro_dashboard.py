
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_goodwe.goodwe.energy import fleet_daily_energy

LOG_DOCTYPE = "RO Daily Log"
LOG_FIELDS = [
	"name", "site", "log_date", "borehole_meter", "smart_meter",
	"permeate_m3", "reject_m3", "units_sold", "rw_tank_level", "tw_tank_level", "notes",
	"modified", "modified_by",
]
EDITABLE_FIELDS = [
	"borehole_meter", "smart_meter", "permeate_m3", "reject_m3",
	"units_sold", "rw_tank_level", "tw_tank_level", "notes",
]


@frappe.whitelist()
def get_dashboard_data(site="VV", from_date=None, to_date=None, profile_from=None, days=120):
	"""RO logs for one site plus daily GoodWe energy and the hourly solar profile.

	The page sends from_date/to_date for the selected period (already widened to include
	baseline days). `days` is kept for older callers."""
	to_date = getdate(to_date) if to_date else getdate(nowdate())
	from_date = getdate(from_date) if from_date else getdate(add_days(to_date, -max(7, min(cint(days) or 120, 730))))
	if from_date > to_date:
		from_date, to_date = to_date, from_date
	if (to_date - from_date).days > 1100:
		frappe.throw(_("Choose a period of three years or less."))
	profile_from = getdate(profile_from) if profile_from else getdate(add_days(to_date, -13))

	logs = frappe.get_list(
		LOG_DOCTYPE,
		filters={"site": site, "log_date": ["between", [from_date, to_date]]},
		fields=LOG_FIELDS,
		order_by="log_date asc",
		limit_page_length=0,
	)
	# One earlier log so the first day in the window still gets a daily volume.
	previous = frappe.get_list(
		LOG_DOCTYPE,
		filters={"site": site, "log_date": ["<", from_date]},
		fields=LOG_FIELDS,
		order_by="log_date desc",
		limit_page_length=1,
	)

	return {
		"logs": previous + logs,
		"daily_energy": get_daily_energy(from_date, to_date),
		"solar_profile": get_hourly_solar_profile(profile_from, to_date),
	}


def get_daily_energy(from_date, to_date):
	"""Per day: solar kWh from the inverters' lifetime counters (see energy.py), whether the
	data was live, and an estimate of load kWh (average load x hours covered by readings).
	Days with frozen data return solar_kwh = None so the page doesn't show a false 0."""
	if not frappe.has_permission("GoodWe Reading", "read"):
		return []
	solar = fleet_daily_energy(from_date, to_date)
	load = frappe.db.sql(
		"""
		SELECT d, SUM(load_kwh) AS load_kwh
		FROM (
			SELECT inverter,
			       DATE(reading_time) AS d,
			       AVG(ABS(load_power_kw))
			         * GREATEST(TIMESTAMPDIFF(MINUTE, MIN(reading_time), MAX(reading_time)), 0) / 60 AS load_kwh
			FROM `tabGoodWe Reading`
			WHERE reading_time >= %s AND reading_time < %s AND IFNULL(is_stale, 0) = 0
			GROUP BY inverter, DATE(reading_time)
		) t
		GROUP BY d
		""",
		(from_date, add_days(to_date, 1)),
		as_dict=True,
	)
	load_by_day = {str(r.d): flt(r.load_kwh, 2) for r in load}
	days = sorted(set(solar) | set(load_by_day))
	return [
		{
			"date": d,
			"solar_kwh": solar[d]["kwh"] if d in solar and solar[d]["live"] else None,
			"live": bool(solar.get(d, {}).get("live")),
			"load_kwh": load_by_day.get(d) if solar.get(d, {}).get("live", True) else None,
		}
		for d in days
	]


def get_hourly_solar_profile(from_date, to_date):
	"""Average solar kW per hour of day, summed across inverters (24 values)."""
	profile = [0.0] * 24
	if not frappe.has_permission("GoodWe Reading", "read"):
		return profile
	rows = frappe.db.sql(
		"""
		SELECT h, SUM(p) AS kw
		FROM (
			SELECT inverter, HOUR(reading_time) AS h, AVG(current_power_kw) AS p
			FROM `tabGoodWe Reading`
			WHERE reading_time >= %s AND reading_time < %s AND IFNULL(is_stale, 0) = 0
			GROUP BY inverter, HOUR(reading_time)
		) t
		GROUP BY h
		""",
		(from_date, add_days(to_date, 1)),
		as_dict=True,
	)
	for r in rows:
		if r.h is not None:
			profile[int(r.h)] = flt(r.kw, 3)
	return profile


@frappe.whitelist(methods=["POST"])
def save_ro_log(data):
	"""Create or update the log for (site, log_date). Permissions are enforced by doc.save().

	When editing, pass `expected_modified` (the `modified` value the page loaded) so an edit
	made by someone else in the meantime isn't silently overwritten."""
	data = frappe.parse_json(data) if isinstance(data, str) else frappe._dict(data)
	site, log_date = data.get("site"), data.get("log_date")
	if not site or not log_date:
		frappe.throw(_("Site and Log Date are required."))

	name = frappe.db.get_value(LOG_DOCTYPE, {"site": site, "log_date": getdate(log_date)})
	doc = frappe.get_doc(LOG_DOCTYPE, name) if name else frappe.new_doc(LOG_DOCTYPE)

	expected = data.get("expected_modified")
	if name and expected and str(doc.modified) != str(expected):
		frappe.throw(
			_("{0} changed this day after you opened it. Reopen the day to see their values.").format(
				frappe.utils.get_fullname(doc.modified_by)
			),
			title=_("Edited by someone else"),
		)
	if not name:
		doc.site = site
		doc.log_date = getdate(log_date)

	for field in EDITABLE_FIELDS:
		if field in data:
			value = data.get(field)
			doc.set(field, None if value in ("", None) else value)

	doc.save()
	return doc.as_dict()
