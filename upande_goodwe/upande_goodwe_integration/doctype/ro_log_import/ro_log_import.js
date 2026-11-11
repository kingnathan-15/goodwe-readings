frappe.ui.form.on("RO Log Import", {
    refresh(frm) {
        frm.add_custom_button(__("Run Import"), function () {
            if (!frm.doc.excel_file) {
                frappe.msgprint({ title: __("No file attached"), message: __("Attach an Excel workbook (.xlsx) first, then click Run Import."), indicator: "orange" });
                return;
            }

            const dry = frm.doc.dry_run ? __(" (dry run)") : "";
            const over = frm.doc.overwrite ? __(" — will overwrite existing values") : "";
            frappe.confirm(
                __("Import RO Daily Logs from the attached workbook{0}{1}?", [dry, over]),
                function () {
                    frappe.call({
                        method: "upande_goodwe.goodwe.import_ro_logs.import_from_attached_file",
                        args: {
                            file_url: frm.doc.excel_file,
                            overwrite: frm.doc.overwrite ? 1 : 0,
                            dry_run: frm.doc.dry_run ? 1 : 0,
                        },
                        freeze: true,
                        freeze_message: __("Reading workbook and importing…"),
                        callback(r) {
                            const d = r.message;
                            // Persist results on the single doc
                            frappe.call({
                                method: "frappe.client.set_value",
                                args: {
                                    doctype: "RO Log Import",
                                    name: "RO Log Import",
                                    fieldname: {
                                        last_summary: d.summary,
                                        last_run: frappe.datetime.now_datetime(),
                                        created_count: d.created,
                                        updated_count: d.updated,
                                        skipped_count: d.skipped,
                                        error_count: d.errors ? d.errors.length : 0,
                                    },
                                },
                                callback() { frm.reload_doc(); }
                            });

                            const indicator = d.errors && d.errors.length ? "orange" : "green";
                            const errNote = d.errors && d.errors.length
                                ? `<p style="margin-top:8px;color:#c4302b">${d.errors.length} error(s) — check Error Log for details.</p>` : "";
                            frappe.msgprint({
                                title: __("Import complete{0}", [dry]),
                                message: `<b>${frappe.utils.escape_html(d.summary)}</b>${errNote}`,
                                indicator,
                                wide: true,
                            });
                        },
                        error() {
                            frappe.show_alert({ message: __("Import failed — check Error Log"), indicator: "red" });
                        }
                    });
                }
            );
        }, __("Actions"));
    }
});
