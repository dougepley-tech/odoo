# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError
from ..models.shippo_api import get_carrier_accounts, ShippoAPIError


class ShippoSettingsWizard(models.TransientModel):
    _name = 'shippo.settings.wizard'
    _description = 'Shippo API Settings'

    shippo_test_api_key = fields.Char(string='Shippo Test API Key', help='Used when Odoo Environment is Test.')
    shippo_production_api_key = fields.Char(string='Shippo Production API Key', help='Used when Odoo Environment is Production.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ICP = self.env['ir.config_parameter'].sudo()
        for fname, param in (
            ('shippo_test_api_key', 'delivery_shippo_iag.test_api_key'),
            ('shippo_production_api_key', 'delivery_shippo_iag.production_api_key'),
        ):
            if fname in fields_list:
                key = ICP.get_param(param)
                if key and len(key) > 8:
                    res[fname] = key[:4] + '****' + key[-4:]
                else:
                    res[fname] = key or ''
        # Legacy: show old single key in test field if new keys not set
        if 'shippo_test_api_key' in fields_list and not res.get('shippo_test_api_key'):
            key = ICP.get_param('delivery_shippo_iag.api_key')
            if key and len(key) > 8:
                res['shippo_test_api_key'] = key[:4] + '****' + key[-4:]
            else:
                res['shippo_test_api_key'] = key or ''
        return res

    def action_save(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        for fname, param in (
            ('shippo_test_api_key', 'delivery_shippo_iag.test_api_key'),
            ('shippo_production_api_key', 'delivery_shippo_iag.production_api_key'),
        ):
            key = getattr(self, fname, None)
            if key and '****' not in (key or ''):
                ICP.set_param(param, key)
        return {'type': 'ir.actions.act_window_close'}

    def action_refresh_carrier_accounts(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = None
        use_test = True
        if self.shippo_test_api_key and '****' not in (self.shippo_test_api_key or ''):
            api_key = self.shippo_test_api_key
            use_test = True
        elif self.shippo_production_api_key and '****' not in (self.shippo_production_api_key or ''):
            api_key = self.shippo_production_api_key
            use_test = False
        if not api_key:
            api_key = ICP.get_param('delivery_shippo_iag.test_api_key')
            if api_key:
                use_test = True
            else:
                api_key = ICP.get_param('delivery_shippo_iag.production_api_key')
                use_test = False
            if not api_key:
                api_key = ICP.get_param('delivery_shippo_iag.api_key')
                use_test = ICP.get_param('delivery_shippo_iag.use_test_env') == 'True'
        if not api_key:
            raise UserError('Set and save a valid Shippo API key (Test or Production) first.')
        try:
            results = get_carrier_accounts(api_key, use_test_env=use_test)
        except ShippoAPIError as e:
            raise UserError('Shippo: %s' % str(e))
        Account = self.env['shippo.carrier.account']
        for r in results:
            oid = r.get('object_id')
            if not oid:
                continue
            carrier = r.get('carrier') or r.get('carrier_name') or ''
            name = r.get('carrier_name') or carrier or oid
            if r.get('metadata'):
                try:
                    name = str(r.get('metadata'))[:64] or name
                except Exception:
                    pass
            is_shippo = bool(r.get('is_shippo_account', False))
            existing = Account.search([('object_id', '=', oid)], limit=1)
            vals = {
                'name': name,
                'carrier': carrier,
                'active': r.get('active', True),
                'is_shippo_account': is_shippo,
            }
            if existing:
                existing.write(vals)
            else:
                Account.create(dict(vals, object_id=oid))
        n_shippo = sum(1 for r in results if r.get('is_shippo_account'))
        n_yours = len(results) - n_shippo
        if n_yours:
            message = 'Imported %s total: %s Shippo account(s), %s your connected account(s).' % (len(results), n_shippo, n_yours)
        else:
            message = 'Imported %s carrier account(s). (Only Shippo accounts returned; connect your carrier accounts in the Shippo dashboard.)' % len(results)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Carrier accounts updated',
            'message': message,
            'type': 'success',
            'sticky': False,
        }}
