# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
# Extends Odoo 19 "Add a shipping method" wizard (choose.delivery.carrier) for Shippo.

from odoo import models
from odoo.exceptions import UserError


class ChooseDeliveryCarrierShippo(models.TransientModel):
    _inherit = 'choose.delivery.carrier'

    def button_confirm(self):
        """Prevent applying $0 when Shippo is selected; user must get rates first."""
        for wizard in self:
            if wizard.carrier_id.delivery_type == 'shippo' and (wizard.delivery_price or 0) == 0:
                raise UserError(
                    'Shippo does not have a rate yet. Click "Get Shippo Rates", choose a rate, '
                    'then click Update to apply the new shipping cost.'
                )
        return super().button_confirm()

    def action_get_shippo_rates(self):
        """Open Shippo rate wizard from "Add a shipping method" dialog.
        When the user selects a rate and applies it, the choose wizard is updated
        so clicking "Add" confirms with that carrier and cost (Odoo 19 flow)."""
        self.ensure_one()
        return {
            'name': 'Get Shippo Rates',
            'type': 'ir.actions.act_window',
            'res_model': 'shippo.rate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_order_id': self.order_id.id,
                'default_delivery_carrier_id': self.carrier_id.id,
                'default_choose_carrier_wizard_id': self.id,
            },
        }
