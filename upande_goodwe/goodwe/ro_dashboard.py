# Place at: upande_goodwe/goodwe/ro_dashboard.py  (next to intelligency.py)
# Endpoints used by the "Solar & Water Plant" web page.

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

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
def get_dashboard_data(site="VV", days=120):
	"""RO logs for one site plus daily GoodWe energy and the hourly solar profile."""
	days = max(7, min(cint(days) or 120, 730))
	since = add_days(nowdate(), -days)

	logs = frappe.get_list(
		LOG_DOCTYPE,
		filters={"site": site, "log_date": [">=", since]},
		fields=LOG_FIELDS,
		order_by="log_date asc",
		limit_page_length=0,
	)
	# One earlier log so the first day in the window still gets a daily volume.
	previous = frappe.get_list(
		LOG_DOCTYPE,
		filters={"site": site, "log_date": ["<", since]},
		fields=LOG_FIELDS,
		order_by="log_date desc",
		limit_page_length=1,
	)

	return {
		"logs": previous + logs,
		"daily_energy": get_daily_energy(days),
		"solar_profile": get_hourly_solar_profile(14),
	}


def get_daily_energy(days=120):
	"""Per day: solar kWh (max daily_energy per inverter, summed) and an
	estimate of load kWh (average load x hours covered by readings)."""
	if not frappe.has_permission("GoodWe Reading", "read"):
		return []
	since = add_days(nowdate(), -cint(days))
	return frappe.db.sql(
		"""
		SELECT d AS date, SUM(e) AS solar_kwh, SUM(load_kwh) AS load_kwh
		FROM (
			SELECT inverter,
			       DATE(reading_time) AS d,
			       MAX(daily_energy_kwh) AS e,
			       AVG(ABS(load_power_kw))
			         * GREATEST(TIMESTAMPDIFF(MINUTE, MIN(reading_time), MAX(reading_time)), 0) / 60 AS load_kwh
			FROM `tabGoodWe Reading`
			WHERE reading_time >= %s
			GROUP BY inverter, DATE(reading_time)
		) t
		GROUP BY d
		ORDER BY d
		""",
		(since,),
		as_dict=True,
	)


def get_hourly_solar_profile(days=14):
	"""Average solar kW per hour of day, summed across inverters (24 values)."""
	profile = [0.0] * 24
	if not frappe.has_permission("GoodWe Reading", "read"):
		return profile
	since = add_days(nowdate(), -cint(days))
	rows = frappe.db.sql(
		"""
		SELECT h, SUM(p) AS kw
		FROM (
			SELECT inverter, HOUR(reading_time) AS h, AVG(current_power_kw) AS p
			FROM `tabGoodWe Reading`
			WHERE reading_time >= %s
			GROUP BY inverter, HOUR(reading_time)
		) t
		GROUP BY h
		""",
		(since,),
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
