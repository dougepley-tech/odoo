from odoo import fields, models


class RmaVoidReason(models.Model):
    _description = "RMA Void Reason"
    _name = "rma.void.reason"
    _order = "sequence, name"

    name = fields.Char(string="Reason", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(comodel_name="res.company")

    _name_company_uniq = models.Constraint(
        "UNIQUE (name, company_id)",
        "Void reason name already exists.",
    )
