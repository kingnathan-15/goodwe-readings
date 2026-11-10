# Copyright (c) 2026, Upande and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today


class TestRODailyLog(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("RO Daily Log", {"site": "Yamin", "notes": "test"})

	def make(self, days_ago, borehole):
		return frappe.get_doc(
			{
				"doctype": "RO Daily Log",
				"site": "Yamin",
				"log_date": add_days(today(), -days_ago),
				"borehole_meter": borehole,
				"notes": "test",
			}
		)

	def test_meter_cannot_go_backwards(self):
		self.make(3, 1000).insert()
		self.assertRaises(frappe.ValidationError, self.make(2, 900).insert)

	def test_meter_cannot_exceed_next_reading(self):
		self.make(1, 1200).insert()
		self.assertRaises(frappe.ValidationError, self.make(2, 1300).insert)

	def test_future_date_rejected(self):
		self.assertRaises(frappe.ValidationError, self.make(-1, 1000).insert)
