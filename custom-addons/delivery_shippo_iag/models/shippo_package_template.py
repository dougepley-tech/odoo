# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ShippoPackageTemplate(models.Model):
    _name = 'shippo.package.template'
    _description = 'Shippo Package Dimension Template'

    name = fields.Char(string='Template Name', required=True)
    length = fields.Float(string='Length', required=True)
    width = fields.Float(string='Width', required=True)
    height = fields.Float(string='Height', required=True)
    distance_unit = fields.Selection(
        [('in', 'in'), ('cm', 'cm')],
        string='Distance Unit',
        default='in',
        required=True,
    )
    weight_max = fields.Float(string='Max Weight', help='Maximum weight for this package type')
    mass_unit = fields.Selection(
        [('lb', 'lb'), ('kg', 'kg'), ('oz', 'oz'), ('g', 'g')],
        string='Mass Unit',
        default='lb',
        required=True,
    )
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
