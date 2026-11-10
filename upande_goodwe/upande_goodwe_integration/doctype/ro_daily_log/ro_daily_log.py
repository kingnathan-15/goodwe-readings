# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

VOLUME_FIELDS = ("borehole_meter", "smart_meter", "permeate_m3", "reject_m3", "units_sold")
METER_FIELDS = (("borehole_meter", "Borehole meter"), ("smart_meter", "Smart meter"))


class RODailyLog(Document):
	def validate(self):
		if getdate(self.log_date) > getdate(today()):
			frappe.throw(_("Log Date can't be in the future."))

		for field in VOLUME_FIELDS:
			if self.get(field) is not None and flt(self.get(field)) < 0:
				frappe.throw(_("{0} can't be negative.").format(self.meta.get_label(field)))

		for field in ("rw_tank_level", "tw_tank_level"):
			if self.get(field) is not None and not 0 <= flt(self.get(field)) <= 100:
				frappe.throw(_("{0} must be between 0 and 100%.").format(self.meta.get_label(field)))

		self.validate_meter_progression()

	def validate_meter_progression(self):
		"""Cumulative meters only count up: a reading must sit between its neighbours."""
		for field, label in METER_FIELDS:
			value = self.get(field)
			if value in (None, ""):
				continue
			value = flt(value)
			base = {"site": self.site, field: ["is", "set"], "name": ["!=", self.name or ""]}

			prev = frappe.get_all(
				"RO Daily Log",
				filters={**base, "log_date": ["<", self.log_date]},
				fields=[field, "log_date"],
				order_by="log_date desc",
				limit=1,
			)
			if prev and value < flt(prev[0][field]):
				frappe.throw(
					_("{0} reading {1} is lower than the reading on {2} ({3}).").format(
						label, value, frappe.format(prev[0].log_date, "Date"), prev[0][field]
					)
				)

			nxt = frappe.get_all(
				"RO Daily Log",
				filters={**base, "log_date": [">", self.log_date]},
				fields=[field, "log_date"],
				order_by="log_date asc",
				limit=1,
			)
			if nxt and value > flt(nxt[0][field]):
				frappe.throw(
					_("{0} reading {1} is higher than the reading on {2} ({3}).").format(
						label, value, frappe.format(nxt[0].log_date, "Date"), nxt[0][field]
					)
				)
