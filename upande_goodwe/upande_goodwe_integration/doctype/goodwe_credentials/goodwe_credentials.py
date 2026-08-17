from frappe.model.document import Document
from upande_goodwe.goodwe.auth import authenticate


class GoodWeCredentials(Document):

    def on_update(self):

        if (
            self.has_value_changed("email")
            or self.has_value_changed("password")
        ):
            authenticate()