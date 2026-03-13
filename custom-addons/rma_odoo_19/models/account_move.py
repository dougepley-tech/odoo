import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    rma_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="refund_id",
        string="RMAs",
        compute="_compute_rma_ids",
        precompute=False,
    )

    def _compute_rma_ids(self):
        """Only expose RMA relation to RMA users; others get empty so credit notes are viewable without RMA access."""
        rma_model = self.env["rma"]
        if self.env.su or self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_ids = rma_model.search([("refund_id", "=", record.id)])
        else:
            empty = rma_model.browse()
            for record in self:
                record.rma_ids = empty
    rma_return_shipping_line_id = fields.Many2one(
        comodel_name="account.move.line",
        string="Return Shipping Line",
        help="The credit note line holding the return shipping deduction",
        copy=False,
    )

    def _check_rma_invoice_lines_qty(self):
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        return (
            self.sudo()
            .mapped("invoice_line_ids")
            .filtered(
                lambda r: (
                    r.rma_id
                    and float_compare(
                        r.quantity, r.rma_id.product_uom_qty, precision
                    )
                    < 0
                )
            )
        )

    def action_post(self):
        if self._check_rma_invoice_lines_qty():
            raise ValidationError(
                self.env._(
                    "There is at least one invoice line whose quantity is "
                    "less than the quantity specified in its linked RMA."
                )
            )
        res = super().action_post()
        for move in self.filtered(lambda m: m.move_type == "out_refund"):
            rmas = move.sudo().rma_ids | move.sudo().invoice_line_ids.mapped("rma_id")
            rmas = rmas.filtered(lambda r: r.state != "refunded")
            if rmas:
                rmas.write({"state": "refunded"})
                for rma in rmas.filtered("sale_line_id"):
                    rma._link_refund_with_reception_move()
        return res

    def button_cancel(self):
        for rma in self.env["rma"].sudo().search([("refund_id", "in", self.ids)]):
            rma.write({"state": "confirmed"})
            if rma.sale_line_id:
                rma._unlink_refund_with_reception_move()
        return super().button_cancel()

    def button_draft(self):
        for rma in self.env["rma"].sudo().search([("refund_id", "in", self.ids)]):
            if rma.sale_line_id:
                rma._link_refund_with_reception_move()
        return super().button_draft()

    def unlink(self):
        rma_from_lines = self.mapped("invoice_line_ids.rma_id")
        rma_from_lines.write({"state": "confirmed"})
        for rma in rma_from_lines.filtered("sale_line_id"):
            rma._unlink_refund_with_reception_move()
        return super().unlink()

    # ── Generate return label from credit note (rate selection + label + deduction) ─

    def action_open_generate_return_label(self):
        """Open wizard to select Shippo return rate, then generate label and add deduction."""
        self.ensure_one()
        if self.move_type != "out_refund":
            raise UserError(_("This action is only for credit notes."))
        rma = self.rma_ids[:1] or self.invoice_line_ids.mapped("rma_id")[:1]
        if not rma:
            raise UserError(_("No RMA linked to this credit note."))
        wizard = self.env["rma.credit.note.shipping.wizard"].create({
            "credit_note_id": self.id,
            "rma_id": rma.id,
        })
        return {
            "name": _("Generate Return Label"),
            "type": "ir.actions.act_window",
            "res_model": "rma.credit.note.shipping.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA",
    )
