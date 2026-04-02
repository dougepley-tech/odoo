from odoo import fields, models


class RmaTag(models.Model):
    _description = "RMA Tags"
    _name = "rma.tag"
    _order = "name"

    active = fields.Boolean(default=True)
    name = fields.Char(
        string="Tag Name",
        required=True,
        translate=True,
        copy=False,
    )
    is_public = fields.Boolean(
        string="Public Tag",
        help="The tag is visible in the portal view",
    )
    color = fields.Integer(string="Color Index")
    rma_ids = fields.Many2many(comodel_name="rma")

    _name_uniq = models.Constraint(
        "UNIQUE (name)",
        "Tag name already exists !",
    )
