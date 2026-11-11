// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.ui.form.on("GoodWe Daily Energy", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.solar_site) return;
		frm.add_custom_button(__("Fetch from SEMS+"), () => {
			frappe.call({
				method: "upande_goodwe.goodwe.backfill.backfill_daily_energy",
				args: { from_date: frm.doc.energy_date, to_date: frm.doc.energy_date, only_missing: 0 },
				freeze: true,
				freeze_message: __("Fetching from SEMS+…"),
				callback: (r) => {
					frappe.show_alert({ message: r.message && r.message.summary, indicator: "green" });
					frm.reload_doc();
				},
			});
		});
	},
});
