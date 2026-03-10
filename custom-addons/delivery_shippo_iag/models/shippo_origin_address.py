# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from ..models.shippo_api import create_address, ShippoAPIError


class ShippoOriginAddress(models.Model):
    _name = 'shippo.origin.address'
    _description = 'Shippo Origin (Warehouse) Address'

    name = fields.Char(string='Name', required=True, help='e.g. Hanover, Westminster')
    street1 = fields.Char(string='Street', required=True)
    street2 = fields.Char(string='Street 2')
    city = fields.Char(string='City', required=True)
    state = fields.Char(string='State')
    zip = fields.Char(string='ZIP', required=True)
    country_id = fields.Many2one('res.country', string='Country', required=True)
    phone = fields.Char(string='Phone')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    shippo_address_id = fields.Char(string='Shippo Address ID', readonly=True, copy=False)
    validation_message = fields.Char(string='Validation Message', readonly=True)

    def _to_shippo_address_dict(self):
        self.ensure_one()
        return {
            'name': self.name or 'Origin',
            'street1': self.street1,
            'street2': self.street2 or '',
            'city': self.city,
            'state': self.state or '',
            'zip': str(self.zip),
            'country': self.country_id.code,
            'phone': self.phone or '',
            'is_residential': False,
        }

    def _get_shippo_api_key_and_env(self):
        """Use same key as Shippo delivery method: from a Shippo carrier for this company, or from ICP."""
        self.ensure_one()
        carrier = self.env['delivery.carrier'].search([
            ('delivery_type', '=', 'shippo'),
            ('company_id', 'in', (self.company_id.id, False)),
        ], limit=1)
        if carrier:
            return carrier._get_shippo_api_key_and_env()
        ICP = self.env['ir.config_parameter'].sudo()
        test_key = ICP.get_param('delivery_shippo_iag.test_api_key')
        prod_key = ICP.get_param('delivery_shippo_iag.production_api_key')
        for key in (test_key, prod_key):
            if key and '****' not in (key or ''):
                return (key, bool(key == test_key and test_key))
        fallback = ICP.get_param('delivery_shippo_iag.api_key')
        if fallback and '****' not in (fallback or ''):
            use_test = ICP.get_param('delivery_shippo_iag.use_test_env') == 'True'
            return (fallback, use_test)
        return (None, True)

    def action_validate_shippo_address(self):
        """Validate this address with Shippo and store object_id."""
        api_key, use_test = self._get_shippo_api_key_and_env()
        if not api_key:
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': 'Configuration missing',
                'message': 'Shippo API key is not configured. Set Test or Production API key on a Shippo delivery method (Delivery Methods → Shippo Shipping), or in Settings → Technical → System Parameters (delivery_shippo_iag.test_api_key / production_api_key).',
                'type': 'warning',
                'sticky': True,
            }}
        try:
            addr = create_address(api_key, self._to_shippo_address_dict(), validate=True, use_test_env=use_test)
            self.shippo_address_id = addr.get('object_id')
            self.validation_message = None
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': 'Address validated',
                'message': 'Shippo address validated and saved.',
                'type': 'success',
                'sticky': False,
            }}
        except ShippoAPIError as e:
            self.validation_message = str(e)
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': 'Validation failed',
                'message': str(e),
                'type': 'danger',
                'sticky': True,
            }}

    def action_delete(self):
        """Delete this origin address and close the form."""
        self.ensure_one()
        self.check_access_rights('unlink')
        self.check_access_rule('unlink')
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}
