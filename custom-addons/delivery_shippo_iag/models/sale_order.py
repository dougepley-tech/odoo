# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    shippo_estimated_shipping = fields.Float(string='Shippo Estimated Shipping', readonly=True, copy=False)
    shippo_rate_wizard_data = fields.Text(string='Shippo Rate Wizard Data', readonly=True, copy=False)

    def action_get_shippo_rates(self):
        self.ensure_one()
        return {
            'name': 'Get Shippo Rates',
            'type': 'ir.actions.act_window',
            'res_model': 'shippo.rate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }
