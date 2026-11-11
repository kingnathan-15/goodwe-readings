frappe.ui.form.on("GoodWe Credentials", {
    refresh(frm) {
        const GROUP = __("Actions");

        // ── Authenticate ──────────────────────────────────────────────────
        frm.add_custom_button(__("Authenticate"), function () {
            frappe.call({
                method: "upande_goodwe.goodwe.auth.authenticate",
                freeze: true,
                freeze_message: __("Authenticating..."),
                callback(r) {
                    frm.reload_doc();
                    frappe.show_alert({ message: __("Authentication successful"), indicator: "green" });
                },
                error() {
                    frappe.show_alert({ message: __("Authentication failed — check Error Log"), indicator: "red" });
                }
            });
        }, GROUP);

        // ── Fetch Readings Now ────────────────────────────────────────────
        frm.add_custom_button(__("Fetch Readings Now"), function () {
            frappe.confirm(
                __("Fetch readings for all stations now? This may take a moment."),
                function () {
                    frappe.call({
                        method: "upande_goodwe.goodwe.sync.sync_all_stations",
                        freeze: true,
                        freeze_message: __("Fetching readings from GoodWe..."),
                        callback(r) {
                            frappe.show_alert({ message: __("Readings fetched successfully"), indicator: "green" });
                        },
                        error() {
                            frappe.show_alert({ message: __("Fetch failed — check Error Log"), indicator: "red" });
                        }
                    });
                }
            );
        }, GROUP);

        // ── Health Check ──────────────────────────────────────────────────
        frm.add_custom_button(__("Health Check"), function () {
            frappe.call({
                method: "upande_goodwe.goodwe.health.run_health_check_and_save",
                freeze: true,
                freeze_message: __("Running health check..."),
                callback(r) {
                    frm.reload_doc();
                    const d = r.message;
                    const frozen = (d.frozen_inverters || []).length;
                    const stopped = d.sync_stopped;
                    const indicator = (frozen || stopped) ? "orange" : "green";
                    const summary = stopped
                        ? __("Sync appears stopped — no readings in {0} min", [d.minutes_since_last_reading || "?"])
                        : frozen
                            ? __("{0} frozen inverter(s) detected", [frozen])
                            : __("All checks passed");
                    frappe.show_alert({ message: summary, indicator });
                },
                error() {
                    frappe.show_alert({ message: __("Health check failed — check Error Log"), indicator: "red" });
                }
            });
        }, GROUP);

        // ── Test SEMS+ Login ──────────────────────────────────────────────
        frm.add_custom_button(__("Test SEMS+ Login"), function () {
            frappe.call({
                method: "upande_goodwe.goodwe.sems_plus.test_login",
                freeze: true,
                freeze_message: __("Testing SEMS+ login..."),
                callback(r) {
                    const d = r.message;
                    frappe.msgprint({
                        title: __("SEMS+ Login OK"),
                        message: `<b>Gateway:</b> ${frappe.utils.escape_html(d.gateway || "—")}`,
                        indicator: "green"
                    });
                },
                error() {
                    frappe.show_alert({ message: __("SEMS+ login failed — check credentials and Error Log"), indicator: "red" });
                }
            });
        }, GROUP);

        // ── Inspect Station ───────────────────────────────────────────────
        frm.add_custom_button(__("Inspect Station"), function () {
            frappe.call({
                method: "upande_goodwe.goodwe.sems_plus.inspect_station",
                freeze: true,
                freeze_message: __("Fetching raw station snapshot from SEMS+..."),
                callback(r) {
                    const json = JSON.stringify(r.message, null, 2);
                    frappe.msgprint({
                        title: __("SEMS+ Station Snapshot"),
                        message: `<pre style="max-height:60vh;overflow:auto;font-size:12px">${frappe.utils.escape_html(json)}</pre>`,
                        wide: true
                    });
                },
                error() {
                    frappe.show_alert({ message: __("Inspect failed — check Error Log"), indicator: "red" });
                }
            });
        }, GROUP);

        // ── Reset Alerts ──────────────────────────────────────────────────
        frm.add_custom_button(__("Reset Alerts"), function () {
            frappe.confirm(
                __("Clear all open GoodWe alert state? No emails will be sent."),
                function () {
                    frappe.call({
                        method: "upande_goodwe.goodwe.health.reset_alerts",
                        callback(r) {
                            frappe.show_alert({ message: __("Alert state cleared"), indicator: "green" });
                        }
                    });
                }
            );
        }, GROUP);
    }
});
