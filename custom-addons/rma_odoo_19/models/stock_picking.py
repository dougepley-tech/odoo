from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    rma_count = fields.Integer(
        string="RMA count",
        compute="_compute_rma_count",
    )

    def _compute_rma_count(self):
        if not self.env.su and not self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for rec in self:
                rec.rma_count = 0
            return
        for rec in self:
            rec.rma_count = len(rec.move_ids.mapped("rma_ids"))

    def action_view_rma(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_action"
        )
        rma = self.move_ids.rma_ids
        if len(rma) == 1:
            action.update(
                res_id=rma.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        else:
            action["domain"] = [("id", "in", rma.ids)]
        return action
