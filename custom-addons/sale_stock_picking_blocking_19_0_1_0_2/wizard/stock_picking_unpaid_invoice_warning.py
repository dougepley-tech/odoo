# Copyright 2024 ForgeFlow S.L.
#   (http://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo import fields, models


class StockPickingUnpaidInvoiceWarning(models.TransientModel):
    _name = "stock.picking.unpaid.invoice.warning"
    _description = "Warn about unpaid invoices before validating delivery"

    pick_ids = fields.Many2many(
        "stock.picking",
        string="Deliveries",
        required=True,
        readonly=True,
    )
    has_unpaid_invoices = fields.Boolean(default=True, readonly=True)

    def action_ship_without_payment(self):
        """Proceed to validate the picking(s) without requiring payment."""
        result = self.pick_ids.with_context(
            skip_unpaid_invoice_warning=True
        ).button_validate()
        return result if isinstance(result, dict) else True

    def action_cancel(self):
        """Close the wizard without validating."""
        return True
