from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    rma_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="partner_id",
        string="RMAs",
        compute="_compute_rma_ids",
        precompute=False,
    )
    rma_count = fields.Integer(
        string="RMA count",
        compute="_compute_rma_count",
    )

    def _compute_rma_ids(self):
        """Only expose RMA relation to RMA users; others get empty to avoid access errors (e.g. viewing delivery address)."""
        rma_model = self.env["rma"]
        if self.env.su or self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_ids = rma_model.search([("partner_id", "=", record.id)])
        else:
            empty = rma_model.browse()
            for record in self:
                record.rma_ids = empty

    def _compute_rma_count(self):
        if not self.env.su and not self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_count = 0
            return
        rma_data = self.env["rma"]._read_group(
            [("partner_id", "in", self.ids)],
            groupby=["partner_id"],
            aggregates=["__count"],
        )
        mapped_data = {r[0].id: r[1] for r in rma_data}
        for record in self:
            record.rma_count = mapped_data.get(record.id, 0)

    def action_view_rma(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_action"
        )
        rma = self.rma_ids
        if len(rma) == 1:
            action.update(
                res_id=rma.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        else:
            action["domain"] = [("partner_id", "in", self.ids)]
        return action
