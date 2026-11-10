frappe.ui.form.on("GoodWe Credentials", {
    refresh(frm) {

        frm.add_custom_button(__("Authenticate"), function () {

            frappe.call({
                method: "upande_goodwe.goodwe.auth.authenticate",
                freeze: true,
                freeze_message: __("Authenticating...")
            }).then(() => {

                frm.reload_doc();

                frappe.show_alert({
                    message: __("Authentication Successful"),
                    indicator: "green"
                });

            });

        });

        frm.add_custom_button(__("Fetch Readings Now"), function () {

            frappe.confirm(
                __("Fetch readings for all stations now? This may take a moment."),
                function () {
                    frappe.call({
                        method: "upande_goodwe.goodwe.sync.sync_all_stations",
                        freeze: true,
                        freeze_message: __("Fetching readings from GoodWe..."),
                        callback: function (r) {
                            frappe.show_alert({
                                message: __("Readings fetched successfully"),
                                indicator: "green"
                            });
                        },
                        error: function (r) {
                            frappe.show_alert({
                                message: __("Fetch failed — check Error Log for details"),
                                indicator: "red"
                            });
                        }
                    });
                }
            );

        });

    }
});