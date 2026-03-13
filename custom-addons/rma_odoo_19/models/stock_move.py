from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class StockMove(models.Model):
    _inherit = "stock.move"

    rma_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="move_id",
        string="RMAs",
        copy=False,
        compute="_compute_rma_relation_ids",
        precompute=False,
    )
    rma_receiver_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="reception_move_id",
        string="RMA receivers",
        copy=False,
        compute="_compute_rma_relation_ids",
        precompute=False,
    )
    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA return",
        copy=False,
        index=True,
    )

    def _compute_rma_relation_ids(self):
        """Only expose RMA relations to RMA users; others get empty so deliveries/moves are viewable without RMA access."""
        rma_model = self.env["rma"]
        if self.env.su or self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_ids = rma_model.search([("move_id", "=", record.id)])
                record.rma_receiver_ids = rma_model.search(
                    [("reception_move_id", "=", record.id)]
                )
        else:
            empty = rma_model.browse()
            for record in self:
                record.rma_ids = empty
                record.rma_receiver_ids = empty

    def unlink(self):
        rma_receiver = self.sudo().rma_receiver_ids
        rma = self.sudo().rma_id
        res = super().unlink()
        rma_receiver.filtered(lambda x: x.state != "cancelled").write(
            {"state": "draft"}
        )
        rma.update_received_state()
        rma.update_replaced_state()
        return res

    def _action_cancel(self):
        res = super()._action_cancel()
        cancelled_moves = self.filtered(lambda r: r.state == "cancel").sudo()
        cancelled_moves.mapped("rma_receiver_ids").write({"state": "draft"})
        cancelled_moves.mapped("rma_id").update_received_state()
        cancelled_moves.mapped("rma_id").update_replaced_state()
        return res

    def _action_done(self, cancel_backorder=False):
        for move in self.filtered(lambda r: r.state not in ("done", "cancel")):
            rma_receiver = move.sudo().rma_receiver_ids
            qty_prec = self.env["decimal.precision"].precision_get(
                "Product Unit of Measure"
            )
            if (
                rma_receiver
                and float_compare(
                    move.quantity,
                    rma_receiver.product_uom_qty,
                    precision_digits=qty_prec,
                )
                != 0
            ):
                raise ValidationError(
                    self.env._(
                        "The quantity done for the product '%(id)s' must "
                        "be equal to its initial demand because the "
                        "stock move is linked to an RMA (%(name)s)."
                    )
                    % {
                        "id": move.product_id.name,
                        "name": move.rma_receiver_ids.name,
                    }
                )
        res = super()._action_done(cancel_backorder=cancel_backorder)
        move_done = self.filtered(lambda r: r.state == "done").sudo()
        to_be_received = (
            move_done.sudo()
            .mapped("rma_receiver_ids")
            .filtered(lambda r: r.state == "confirmed")
        )
        to_be_received.update_received_state_on_reception()
        move_done.mapped("rma_id").update_replaced_state()
        move_done.mapped("rma_id").update_returned_state()
        return res

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        return super()._prepare_merge_moves_distinct_fields() + [
            "rma_id",
            "rma_receiver_ids",
        ]

    def _prepare_move_split_vals(self, qty):
        res = super()._prepare_move_split_vals(qty)
        res["rma_id"] = self.sudo().rma_id.id
        return res

    def _prepare_procurement_values(self):
        res = super()._prepare_procurement_values()
        if self.rma_id:
            res["rma_id"] = self.rma_id.id
        return res


class StockRule(models.Model):
    _inherit = "stock.rule"

    def _get_custom_move_fields(self):
        move_fields = super()._get_custom_move_fields()
        move_fields += [
            "rma_id",
            "origin_returned_move_id",
            "move_orig_ids",
            "rma_receiver_ids",
            "sale_line_id",
        ]
        return move_fields
