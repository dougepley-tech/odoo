# -*- coding: utf-8 -*-
"""Kit component lines on RMA: one line per component so customer can return some or all."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class RmaKitLine(models.Model):
    _name = "rma.kit.line"
    _description = "RMA Kit Component Line"

    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA",
        required=True,
        ondelete="cascade",
    )
    move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Delivery Move",
        ondelete="set null",
        help="Outgoing move this component line is from.",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
    )
    product_uom_qty = fields.Float(
        string="Quantity to Return",
        digits="Product Unit of Measure",
        required=True,
        default=0,
    )
    max_quantity = fields.Float(
        string="Max Quantity",
        digits="Product Unit of Measure",
        help="Delivered quantity for this component.",
    )
    product_uom = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        required=True,
    )

    @api.constrains("product_uom_qty", "max_quantity")
    def _check_quantity(self):
        for line in self:
            if line.max_quantity and float_compare(
                line.product_uom_qty, line.max_quantity, precision_digits=6
            ) > 0:
                raise ValidationError(
                    _(
                        "Quantity to return (%(qty)s) cannot exceed delivered quantity (%(max)s) for %(product)s.",
                        qty=line.product_uom_qty,
                        max=line.max_quantity,
                        product=line.product_id.display_name,
                    )
                )
            if line.product_uom_qty < 0:
                raise ValidationError(
                    _("Quantity to return cannot be negative for %(product)s.", product=line.product_id.display_name)
                )
