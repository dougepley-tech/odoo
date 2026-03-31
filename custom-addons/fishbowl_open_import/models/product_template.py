# -*- coding: utf-8 -*-

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    fishbowl_part_id = fields.Integer(string='Fishbowl part id', index=True, copy=False)
