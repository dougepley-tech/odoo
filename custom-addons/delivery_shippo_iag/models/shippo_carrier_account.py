# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ShippoCarrierAccount(models.Model):
    _name = 'shippo.carrier.account'
    _description = 'Shippo Carrier Account (cached from API)'

    name = fields.Char(string='Account Name', required=True)
    carrier = fields.Char(string='Carrier Token', required=True, index=True)
    carrier_display = fields.Char(string='Carrier', compute='_compute_carrier_display', store=False)
    object_id = fields.Char(string='Shippo Object ID', required=True, index=True, help='Shippo API carrier_account object_id')
    active = fields.Boolean(default=True)
    is_shippo_account = fields.Boolean(
        string='Shippo Account',
        default=False,
        help='True = Shippo\'s account (billed via Shippo). False = Your own connected carrier account.',
    )
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    @api.depends('carrier')
    def _compute_carrier_display(self):
        for rec in self:
            rec.carrier_display = (rec.carrier or '').replace('_', ' ').title()
