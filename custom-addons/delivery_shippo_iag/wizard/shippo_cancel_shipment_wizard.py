# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models
from odoo.exceptions import UserError
from ..models.shippo_api import create_refund, ShippoAPIError


class ShippoCancelShipmentWizard(models.TransientModel):
    _name = 'shippo.cancel.shipment.wizard'
    _description = 'Cancel Shippo Shipment'

    picking_id = fields.Many2one('stock.picking', required=True, ondelete='cascade')

    def _get_shippo_api_key_and_env(self):
        """Use same key as when the label was created: stored shippo_carrier_id, then picking's carrier, then ICP."""
        picking = self.picking_id
        # Prefer the delivery method stored when the label was created (fixes refund when picking has no carrier)
        carrier = getattr(picking, 'shippo_carrier_id', None)
        if carrier and getattr(carrier, 'delivery_type', None) == 'shippo':
            api_key, use_test = carrier._get_shippo_api_key_and_env()
            if api_key:
                return (api_key, use_test)
        carrier = getattr(picking, 'carrier_id', None)
        if carrier and getattr(carrier, 'delivery_type', None) == 'shippo':
            api_key, use_test = carrier._get_shippo_api_key_and_env()
            if api_key:
                return (api_key, use_test)
        ICP = self.env['ir.config_parameter'].sudo()
        test_key = ICP.get_param('delivery_shippo_iag.test_api_key')
        prod_key = ICP.get_param('delivery_shippo_iag.production_api_key')
        if test_key and '****' not in (test_key or ''):
            return (test_key, True)
        if prod_key and '****' not in (prod_key or ''):
            return (prod_key, False)
        key = ICP.get_param('delivery_shippo_iag.api_key')
        if key and '****' not in (key or ''):
            use_test = ICP.get_param('delivery_shippo_iag.use_test_env') == 'True'
            return (key, use_test)
        return (None, True)

    def action_cancel_shipment(self):
        self.ensure_one()
        picking = self.picking_id
        if not picking.shippo_transaction_id:
            raise UserError('No Shippo transaction on this delivery order.')
        api_key, use_test = self._get_shippo_api_key_and_env()
        if not api_key:
            raise UserError(
                'Shippo API key is not configured. Use the same key as when the label was created: '
                'set Test or Production key on the delivery method (Delivery Methods → Shippo Shipping) '
                'or in Settings → Technical → System Parameters.'
            )
        try:
            create_refund(api_key, picking.shippo_transaction_id, async_=False, use_test_env=use_test)
        except ShippoAPIError as e:
            msg = str(e)
            if 'token' in msg.lower() or 'exist' in msg.lower():
                raise UserError(
                    'Shippo refund failed: %s. '
                    'Use the same API key and environment (Test/Production) as when the label was created.'
                    % msg
                )
            raise UserError('Shippo refund failed: %s' % msg)
        picking.message_post(body='Shippo shipment cancelled (refund requested).')
        picking.write({
            'shippo_transaction_id': False,
            'shippo_tracking_url': False,
            'shippo_carrier_id': False,
            'carrier_tracking_ref': False,
        })
        return {'type': 'ir.actions.act_window_close'}
