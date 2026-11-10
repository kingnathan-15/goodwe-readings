// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

// Shows the daily volume implied by the meter readings against the previous log,
// so mistakes are visible before saving.
frappe.ui.form.on("RO Daily Log", {
	refresh(frm) {
		frm.trigger("show_volumes");
	},
	site(frm) {
		frm.trigger("show_volumes");
	},
	log_date(frm) {
		frm.trigger("show_volumes");
	},
	borehole_meter(frm) {
		frm.trigger("show_volumes");
	},
	smart_meter(frm) {
		frm.trigger("show_volumes");
	},
	permeate_m3(frm) {
		frm.trigger("show_volumes");
	},
	reject_m3(frm) {
		frm.trigger("show_volumes");
	},
	units_sold(frm) {
		frm.trigger("show_volumes");
	},

	async show_volumes(frm) {
		frm.set_intro("");
		if (!frm.doc.site || !frm.doc.log_date) return;

		const prev = await frappe.db.get_list("RO Daily Log", {
			filters: [
				["site", "=", frm.doc.site],
				["log_date", "<", frm.doc.log_date],
				["name", "!=", frm.doc.name || ""],
			],
			fields: ["log_date", "borehole_meter", "smart_meter"],
			order_by: "log_date desc",
			limit: 1,
		});

		const parts = [];
		let pumped = null;
		let pumpedSmart = null;
		if (prev.length) {
			const p = prev[0];
			const gap = frappe.datetime.get_day_diff(frm.doc.log_date, p.log_date) || 1;
			if (frm.doc.borehole_meter && p.borehole_meter) {
				pumped = (frm.doc.borehole_meter - p.borehole_meter) / gap;
				parts.push(`Borehole: ${format_number(pumped, null, 1)} m³/day`);
			}
			if (frm.doc.smart_meter && p.smart_meter) {
				pumpedSmart = (frm.doc.smart_meter - p.smart_meter) / gap;
				parts.push(`Smart meter: ${format_number(pumpedSmart, null, 1)} m³/day`);
			}
			if (pumped > 0 && pumpedSmart != null) {
				parts.push(`Deviation: ${format_number(((pumpedSmart - pumped) / pumped) * 100, null, 1)}%`);
			}
			if (gap > 1) parts.push(`averaged over ${gap} days since ${frappe.datetime.str_to_user(p.log_date)}`);
		}

		const perm = frm.doc.permeate_m3;
		const rej = frm.doc.reject_m3;
		if (perm != null && rej != null && perm + rej > 0) {
			parts.push(`RO recovery: ${format_number((perm / (perm + rej)) * 100, null, 1)}%`);
		}
		const supplied = frm.doc.site === "VV" ? perm : pumped;
		if (supplied > 0 && frm.doc.units_sold != null) {
			parts.push(`Non-revenue water: ${format_number(((supplied - frm.doc.units_sold) / supplied) * 100, null, 1)}%`);
		}

		if (parts.length) frm.set_intro(parts.join(" · "), pumped != null && pumped < 0 ? "red" : "blue");
	},
});
