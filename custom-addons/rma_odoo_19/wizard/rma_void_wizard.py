from odoo import fields, models


class RmaVoidWizard(models.TransientModel):
    _name = "rma.void.wizard"
    _description = "RMA Void Wizard"

    void_reason_id = fields.Many2one(
        comodel_name="rma.void.reason",
        string="Void Reason",
        required=True,
    )

    def action_void_confirm(self):
        self.ensure_one()
        rma_ids = self.env.context.get("active_ids")
        rmas = self.env["rma"].browse(rma_ids)
        rmas.action_void_apply(self.void_reason_id)
        return {}
