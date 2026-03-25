# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockDeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('shippo', 'Shippo')],
        ondelete={'shippo': 'set default'},
    )
    # Shippo API keys: which one is used is determined by Odoo's Environment toggle (prod_environment) on this form
    shippo_test_api_key = fields.Char(
        string='Shippo Test API Key',
        help='Used when Environment is Test (Odoo button at top). Leave empty to use Shippo API Settings.',
    )
    shippo_production_api_key = fields.Char(
        string='Shippo Production API Key',
        help='Used when Environment is Production (Odoo button at top). Leave empty to use Shippo API Settings.',
    )
    # Origin and package defaults (configured on delivery method)
    shippo_default_origin_id = fields.Many2one(
        'shippo.origin.address',
        string='Default Origin Address',
        help='Default warehouse/origin for rate requests.',
    )
    shippo_default_package_template_id = fields.Many2one(
        'stock.package.type',
        string='Default Package Template',
        help='Default package type (Inventory → Configuration → Package Types) used for Get Shippo Rates when no packs are on the delivery order.',
    )
    # Shippo-specific: scope to these carrier accounts; empty = all (Shippo + your connected)
    shippo_carrier_account_ids = fields.Many2many(
        'shippo.carrier.account',
        'delivery_carrier_shippo_account_rel',
        'carrier_id',
        'account_id',
        string='Shippo Carrier Accounts',
        help='Shippo and your own connected accounts (after Refresh). Leave empty to use all for negotiated rates. '
             'Connect more in Shippo: Carriers → Your Accounts (see docs.goshippo.com/docs/carriers/carriercapabilities/).',
    )
    # Map origin address -> carrier accounts; when set, Get Shippo Rates uses only accounts for the selected origin
    shippo_origin_carrier_mapping_ids = fields.One2many(
        'shippo.origin.carrier.mapping',
        'delivery_carrier_id',
        string='Carrier Accounts per Origin',
        help='Map each origin address to the carrier accounts to use for rates. When set, only these accounts are used for that origin; leave empty to use Shippo Carrier Accounts above.',
    )
    shippo_default_rate_type = fields.Selection(
        [('negotiated', 'Negotiated (Account)'), ('published', 'Published (Retail)')],
        string='Default Rate Type',
        default='negotiated',
    )
    shippo_label_format = fields.Selection(
        [
            ('PDF_4x6', 'PDF 4x6 (thermal)'),
            ('PDF', 'PDF (letter)'),
            ('PDF_2.3x7.5', 'PDF 2.3x7.5'),
            ('PDF_4x8', 'PDF 4x8'),
            ('PNG', 'PNG'),
            ('ZPLII', 'ZPLII'),
        ],
        string='Label Format',
        default='PDF_4x6',
    )
    shippo_default_insurance = fields.Boolean(string='Default Insurance', default=False)
    shippo_default_delivery_type = fields.Selection(
        [('residential', 'Residential'), ('commercial', 'Commercial')],
        string='Default Delivery Type',
        default='residential',
    )
    shippo_default_signature = fields.Selection(
        [('none', 'None'), ('standard', 'Standard'), ('adult', 'Adult Signature')],
        string='Default Signature',
        default='none',
    )
    shippo_markup_rule_ids = fields.One2many(
        'shippo.markup.rule',
        'delivery_carrier_id',
        string='Markup Rules',
    )

    def _get_shippo_api_key_and_env(self):
        """Return (api_key, use_test_env) from this carrier. use_test_env follows Odoo Environment (prod_environment)."""
        self.ensure_one()
        use_test = not self.prod_environment
        raw_key = self.shippo_test_api_key if use_test else self.shippo_production_api_key
        if raw_key and '****' not in (raw_key or ''):
            return (raw_key, use_test)
        ICP = self.env['ir.config_parameter'].sudo()
        test_key = ICP.get_param('delivery_shippo_iag.test_api_key')
        prod_key = ICP.get_param('delivery_shippo_iag.production_api_key')
        if use_test and test_key and '****' not in (test_key or ''):
            return (test_key, True)
        if not use_test and prod_key and '****' not in (prod_key or ''):
            return (prod_key, False)
        if test_key and '****' not in (test_key or ''):
            return (test_key, True)
        if prod_key and '****' not in (prod_key or ''):
            return (prod_key, False)
        # Legacy single key
        fallback = ICP.get_param('delivery_shippo_iag.api_key')
        if fallback:
            return (fallback, ICP.get_param('delivery_shippo_iag.use_test_env') == 'True')
        return (None, use_test)

    def action_shippo_refresh_carrier_accounts(self):
        """Refresh carrier accounts from Shippo API (same as in Settings wizard)."""
        from ..models.shippo_api import get_carrier_accounts, ShippoAPIError
        self.ensure_one()
        api_key, use_test = self._get_shippo_api_key_and_env()
        if not api_key:
            raise UserError('Set a valid Shippo API key (Test or Production) on this delivery method or in Shippo API Settings.')
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
            # Name: prefer carrier_name; for your own accounts use metadata or carrier + account_id hint
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
            message = ('Imported %s total: %s Shippo account(s), %s your connected account(s). '
                       'To add more, connect carriers in Shippo: Carriers → Your Accounts (see carrier capabilities in docs).') % (len(results), n_shippo, n_yours)
        else:
            message = ('Imported %s carrier account(s) from Shippo. Only Shippo accounts returned. '
                       'Enable test mode on carrier accounts in Shippo to get them to connect. '
                       'Connect your carrier accounts in Shippo: Carriers → Your Accounts. See docs.goshippo.com/docs/carriers/carriercapabilities/') % len(results)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Carrier accounts updated',
            'message': message,
            'type': 'success',
            'sticky': False,
        }}

    def rate_shipment(self, order):
        """Odoo 19 delivery carrier contract: return rates for the "Add a shipping method" wizard.
        Shippo uses multiple rates; we return no_rate=True so the user clicks "Get Shippo Rates"."""
        self.ensure_one()
        if self.delivery_type != 'shippo':
            return super().rate_shipment(order)
        return {
            'success': True,
            'price': 0.0,
            'carrier_price': 0.0,
            'no_rate': True,
            'warning_message': None,
            'error_message': None,
        }

    def shippo_send_shipping(self, pickings):
        """Return list of dicts with exact_price and tracking_number for each picking.
        Uses the Shippo transaction when present; otherwise returns zero price (no label)."""
        from ..models.shippo_api import get_transaction, ShippoAPIError
        self.ensure_one()
        res = []
        for picking in pickings:
            if not picking.shippo_transaction_id:
                _logger.warning(
                    'Shippo: validating %s without shippo_transaction_id (no label; price set to 0)',
                    picking.name,
                )
                res.append({'exact_price': 0.0, 'tracking_number': ''})
                continue
            try:
                api_key, use_test = self._get_shippo_api_key_and_env()
                txn = get_transaction(
                    api_key,
                    picking.shippo_transaction_id,
                    use_test_env=use_test,
                    expand='rate',
                )
            except ShippoAPIError as e:
                raise UserError(_('Shippo: %s') % str(e))
            rate_data = txn.get('rate') if isinstance(txn.get('rate'), dict) else {}
            amount = float(rate_data.get('amount_local') or rate_data.get('amount') or txn.get('amount_local') or 0.0)
            tracking = (txn.get('tracking_number') or picking.carrier_tracking_ref or '') or ''
            res.append({'exact_price': amount, 'tracking_number': tracking})
        return res

    def shippo_get_tracking_link(self, picking):
        """Return tracking URL for the picking (used by carrier_tracking_url compute)."""
        self.ensure_one()
        if not picking.carrier_tracking_ref:
            return False
        url = getattr(picking, 'shippo_tracking_url', None) or ''
        if url:
            return url
        return False

    def shippo_cancel_shipment(self, picking):
        """Cancel/refund the Shippo label. Picking must have shippo_transaction_id."""
        from ..models.shippo_api import create_refund, ShippoAPIError
        self.ensure_one()
        if not picking.shippo_transaction_id:
            return
        try:
            api_key, use_test = self._get_shippo_api_key_and_env()
            create_refund(api_key, picking.shippo_transaction_id, async_=False, use_test_env=use_test)
        except ShippoAPIError as e:
            raise UserError(_('Shippo: %s') % str(e))
        picking.write({
            'shippo_transaction_id': False,
            'shippo_tracking_url': False,
            'carrier_tracking_ref': False,
        })
