# Copyright 2024 ForgeFlow S.L.
#   (http://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo import _, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _has_sale_unpaid_invoices(self):
        """Return True if this picking's sale order has any posted invoice that is not paid."""
        self.ensure_one()
        if not self.sale_id:
            return False
        invoices = self.sale_id.invoice_ids.filtered(
            lambda m: m.state == "posted" and m.payment_state != "paid"
        )
        return bool(invoices)

    def button_validate(self):
        if self.env.context.get("skip_unpaid_invoice_warning"):
            return super().button_validate()

        pickings_with_unpaid = self.filtered(
            lambda p: p.sale_id and p._has_sale_unpaid_invoices()
        )
        if pickings_with_unpaid:
            return {
                "name": _("Unpaid Invoices Warning"),
                "type": "ir.actions.act_window",
                "res_model": "stock.picking.unpaid.invoice.warning",
                "view_mode": "form",
                "target": "new",
                "context": {
                    "default_pick_ids": [(6, 0, pickings_with_unpaid.ids)],
                    "default_has_unpaid_invoices": True,
                },
            }
        return super().button_validate()
