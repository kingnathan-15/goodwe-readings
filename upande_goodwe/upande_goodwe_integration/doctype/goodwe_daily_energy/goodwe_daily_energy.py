# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today


class GoodWeDailyEnergy(Document):
	def validate(self):
		if getdate(self.energy_date) > getdate(today()):
			frappe.throw(_("Energy Date can't be in the future."))
		if self.energy_kwh is not None and flt(self.energy_kwh) < 0:
			frappe.throw(_("Energy can't be negative."))
