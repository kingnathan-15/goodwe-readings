frappe.ui.form.on("GoodWe Credentials", {
    refresh(frm) {

        frm.add_custom_button("Authenticate", function () {

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

    }
});