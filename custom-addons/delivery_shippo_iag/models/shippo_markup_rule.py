# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ShippoMarkupRule(models.Model):
    _name = 'shippo.markup.rule'
    _description = 'Shippo Rate Markup Rule'
    _order = 'delivery_carrier_id, carrier, service_level'

    delivery_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Delivery Method',
        required=True,
        ondelete='cascade',
        domain=[('delivery_type', '=', 'shippo')],
    )
    carrier = fields.Char(string='Carrier (optional)', help='Leave empty for all carriers. Shippo provider token e.g. usps, fedex.')
    service_level = fields.Char(string='Service Level (optional)', help='Shippo servicelevel token. Leave empty for all services.')
    markup_type = fields.Selection(
        [('flat', 'Flat Amount'), ('percentage', 'Percentage')],
        string='Markup Type',
        required=True,
        default='percentage',
    )
    markup_value = fields.Float(string='Markup Value', required=True)
    company_id = fields.Many2one('res.company', related='delivery_carrier_id.company_id', store=True)
