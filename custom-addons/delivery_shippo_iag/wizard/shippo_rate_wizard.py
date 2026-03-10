# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from odoo import api, fields, models
from odoo.exceptions import UserError
from ..models.shippo_api import (
    create_address,
    create_shipment,
    create_transaction,
    get_transaction,
    list_transactions_by_rate,
    create_refund,
    ShippoAPIError,
    _address_is_valid_for_shipment,
)

_logger = logging.getLogger(__name__)


class ShippoRateWizard(models.TransientModel):
    _name = 'shippo.rate.wizard'
    _description = 'Shippo Get Shipping Rates'

    picking_id = fields.Many2one('stock.picking', string='Delivery Order', ondelete='cascade')
    order_id = fields.Many2one('sale.order', string='Sales Order', ondelete='cascade')
    choose_carrier_wizard_id = fields.Many2one(
        'choose.delivery.carrier',
        string='Add Shipping Wizard',
        help='When set, applying a rate updates this wizard so "Add" uses the selected cost (Odoo 19).',
    )
    # Origin: for SO we may have multiple origins (split warehouse); for picking one origin
    origin_address_id = fields.Many2one('shippo.origin.address', string='Origin Address', required=True)
    delivery_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Delivery Method',
        domain=[('delivery_type', '=', 'shippo')],
        required=True,
    )
    rate_type = fields.Selection(
        [('negotiated', 'Negotiated'), ('published', 'Published')],
        string='Rate Type',
        default='negotiated',
        required=True,
    )
    package_type_id = fields.Many2one(
        'stock.package.type',
        string='Package Type',
        help='Odoo package type (Inventory → Package Types). Used when no packs on the delivery order.',
    )
    custom_package = fields.Boolean(
        string='Custom Package',
        default=False,
        help='Check to enter length, width, and height manually instead of using a package type.',
    )
    length = fields.Float(string='Length (in)', default=10)
    width = fields.Float(string='Width (in)', default=8)
    height = fields.Float(string='Height (in)', default=6)
    weight = fields.Float(string='Weight (lb)', default=1)
    distance_unit = fields.Selection([('in', 'in'), ('cm', 'cm')], default='in')
    mass_unit = fields.Selection([('lb', 'lb'), ('kg', 'kg')], default='lb')
    # Shipment options
    insurance_amount = fields.Float(string='Insurance / Declared Value')
    is_residential = fields.Boolean(string='Residential', default=True)
    signature_confirmation = fields.Selection(
        [('none', 'None'), ('STANDARD', 'Standard'), ('ADULT', 'Adult Signature')],
        string='Signature',
        default='none',
    )
    # Results
    rate_line_ids = fields.One2many('shippo.rate.wizard.line', 'wizard_id', string='Rates')
    selected_rate_id = fields.Many2one('shippo.rate.wizard.line', string='Selected Rate')
    state = fields.Selection([('draft', 'Draft'), ('rates', 'Rates Loaded')], default='draft')
    pack_summary = fields.Char(string='Packs', compute='_compute_pack_summary', help='Shown when this delivery order has packs; rates use these pack dimensions.')

    @api.depends('picking_id', 'picking_id.move_line_ids', 'picking_id.move_line_ids.result_package_id')
    def _compute_pack_summary(self):
        for wizard in self:
            if not wizard.picking_id:
                wizard.pack_summary = False
                continue
            try:
                move_lines = getattr(wizard.picking_id, 'move_line_ids', None)
                if not move_lines:
                    wizard.pack_summary = False
                    continue
                result_packages = move_lines.mapped('result_package_id')
                if not result_packages:
                    wizard.pack_summary = False
                    continue
                outermost = result_packages.mapped('outermost_package_id')
                if not outermost:
                    outermost = result_packages
                parcels = wizard._get_parcels_from_picking_packages(wizard.picking_id) or []
                parts = []
                for i, package in enumerate(outermost):
                    name = getattr(package, 'name', None) or ('Pack %s' % package.id)
                    if i < len(parcels):
                        p = parcels[i]
                        parts.append('%s (%s×%s×%s in, %s lb)' % (name, p['length'], p['width'], p['height'], p['weight']))
                    else:
                        parts.append(name)
                wizard.pack_summary = 'Using %s pack(s) from this delivery order: %s' % (len(outermost), ', '.join(parts))
            except Exception:
                wizard.pack_summary = False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if res.get('picking_id'):
            picking = self.env['stock.picking'].browse(res['picking_id'])
            carrier = getattr(picking, 'carrier_id', None)
            if carrier and getattr(carrier, 'delivery_type', None) == 'shippo' and getattr(carrier, 'shippo_default_origin_id', None):
                res['origin_address_id'] = carrier.shippo_default_origin_id.id
            elif picking.location_id.warehouse_id:
                origin = self.env['shippo.origin.address'].search([
                    ('company_id', 'in', (picking.company_id.id, False)),
                ], limit=1)
                if origin:
                    res['origin_address_id'] = origin.id
            if carrier and getattr(carrier, 'delivery_type', None) == 'shippo':
                res['delivery_carrier_id'] = carrier.id
                res['rate_type'] = getattr(carrier, 'shippo_default_rate_type', None) or 'negotiated'
                res['is_residential'] = (getattr(carrier, 'shippo_default_delivery_type', None) == 'residential')
                sig = getattr(carrier, 'shippo_default_signature', None) or 'none'
                res['signature_confirmation'] = 'STANDARD' if sig == 'standard' else ('ADULT' if sig == 'adult' else 'none')
                if getattr(carrier, 'shippo_default_package_template_id', None):
                    res['package_type_id'] = carrier.shippo_default_package_template_id.id
            # Default insurance/weight from linked sale order when opening from delivery order (out)
            order = picking._get_sale_order_from_picking() if hasattr(picking, '_get_sale_order_from_picking') else None
            if order:
                weight_lines = order.order_line.filtered(
                    lambda l: l.product_id
                    and l.product_id.type in ('product', 'consu')
                    and not getattr(l, 'is_delivery', False)
                    and not getattr(l, 'display_type', False)
                )
                if weight_lines and 'weight' in fields_list:
                    w = sum(
                        line.product_id.weight * (line.product_qty if hasattr(line, 'product_qty') else line.product_uom_qty)
                        for line in weight_lines
                    )
                    res['weight'] = w or 1
                goods_lines = order.order_line.filtered(
                    lambda l: l.product_id
                    and l.product_id.type in ('product', 'consu')
                    and not getattr(l, 'is_delivery', False)
                    and not getattr(l, 'display_type', False)
                )
                if goods_lines and 'insurance_amount' in fields_list:
                    res['insurance_amount'] = sum(goods_lines.mapped('price_subtotal'))
        if res.get('order_id'):
            order = self.env['sale.order'].browse(res['order_id'])
            # Total weight of all shippable goods (exclude services, delivery line, sections) so rate is for full order
            weight_lines = order.order_line.filtered(
                lambda l: l.product_id
                and l.product_id.type in ('product', 'consu')
                and not l.is_delivery
                and not l.display_type
            )
            weight = sum(
                line.product_id.weight * (line.product_qty if hasattr(line, 'product_qty') else line.product_uom_qty)
                for line in weight_lines
            )
            res['weight'] = weight or 1
            # Default insurance/declared value = total value of goods (exclude services, delivery, taxes)
            goods_lines = order.order_line.filtered(
                lambda l: l.product_id
                and l.product_id.type in ('product', 'consu')
                and not l.is_delivery
                and not l.display_type
            )
            if goods_lines:
                res['insurance_amount'] = sum(goods_lines.mapped('price_subtotal'))
            # When opened from "Add a shipping method" (Odoo 19), use that wizard's carrier
            choose_wizard_id = self.env.context.get('default_choose_carrier_wizard_id')
            if choose_wizard_id:
                res['choose_carrier_wizard_id'] = choose_wizard_id
            carrier = None
            if choose_wizard_id:
                choose = self.env['choose.delivery.carrier'].browse(choose_wizard_id)
                if choose.exists() and choose.carrier_id and choose.carrier_id.delivery_type == 'shippo':
                    carrier = choose.carrier_id
            if not carrier:
                carrier = self.env['delivery.carrier'].search([('delivery_type', '=', 'shippo')], limit=1)
            if carrier:
                res['delivery_carrier_id'] = carrier.id
                res['rate_type'] = carrier.shippo_default_rate_type or 'negotiated'
                if carrier.shippo_default_origin_id:
                    res['origin_address_id'] = carrier.shippo_default_origin_id.id
                if carrier.shippo_default_package_template_id:
                    res['package_type_id'] = carrier.shippo_default_package_template_id.id
            if not res.get('origin_address_id'):
                origin = self.env['shippo.origin.address'].search([('company_id', 'in', (order.company_id.id, False))], limit=1)
                if origin:
                    res['origin_address_id'] = origin.id
        # Default to Shippo Shipping when opened from picking/order and no carrier set yet
        if (res.get('picking_id') or res.get('order_id')) and not res.get('delivery_carrier_id') and 'delivery_carrier_id' in fields_list:
            shippo_carrier = self.env['delivery.carrier'].search([('delivery_type', '=', 'shippo')], limit=1)
            if shippo_carrier:
                res['delivery_carrier_id'] = shippo_carrier.id
        return res

    def _get_api_key(self):
        carrier = self.delivery_carrier_id
        if carrier:
            api_key, _ = carrier._get_shippo_api_key_and_env()
            if api_key:
                return api_key
        key = self.env['ir.config_parameter'].sudo().get_param('delivery_shippo_iag.api_key')
        if not key:
            raise UserError('Shippo API key is not configured. Set Test and/or Production key on the delivery method or in Shippo API Settings.')
        return key

    def _use_test_env(self):
        carrier = self.delivery_carrier_id
        if carrier:
            _, use_test = carrier._get_shippo_api_key_and_env()
            return use_test
        return self.env['ir.config_parameter'].sudo().get_param('delivery_shippo_iag.use_test_env') == 'True'

    def _get_parcels_from_picking_packages(self, picking):
        """Build Shippo parcel dicts from picking's packs (stock.package). One parcel per outermost pack.
        Uses pack's package_type_id dimensions and pack weight. Converts to inch/lb for Shippo."""
        try:
            move_lines = getattr(picking, 'move_line_ids', None)
            if not move_lines:
                return None
            result_packages = move_lines.mapped('result_package_id')
            if not result_packages:
                return None
            # Outermost packages only (one per physical pack the user sees)
            outermost = result_packages.mapped('outermost_package_id')
            if not outermost:
                outermost = result_packages
        except Exception:
            return None
        parcels = []
        for package in outermost:
            try:
                pt = getattr(package, 'package_type_id', None)
                length_in = width_in = height_in = 1.0
                weight_lb = 1.0
                w_uom = 'lb'
                if pt:
                    length_uom = getattr(pt, 'length_uom_name', None) or 'in'
                    w_uom = getattr(pt, 'weight_uom_name', None) or 'lb'
                    length_val = float(getattr(pt, 'packaging_length', 0) or 1)
                    width_val = float(getattr(pt, 'width', 0) or 1)
                    height_val = float(getattr(pt, 'height', 0) or 1)
                    if length_uom and 'cm' in (length_uom or '').lower():
                        length_in = length_val * 0.393701
                        width_in = width_val * 0.393701
                        height_in = height_val * 0.393701
                    else:
                        length_in, width_in, height_in = length_val, width_val, height_val
                # Weight: package.shipping_weight (total) else package_type.base_weight
                pkg_weight = getattr(package, 'shipping_weight', None)
                if pkg_weight is not None and pkg_weight > 0:
                    weight_lb = pkg_weight * 2.20462 if (w_uom and 'kg' in (w_uom or '').lower()) else pkg_weight
                elif pt:
                    base_w = float(getattr(pt, 'base_weight', 0) or 0)
                    weight_lb = base_w * 2.20462 if (w_uom and 'kg' in (w_uom or '').lower()) else base_w
                if weight_lb <= 0:
                    weight_lb = 1.0
                parcels.append({
                    'length': str(round(max(0.1, length_in), 2)),
                    'width': str(round(max(0.1, width_in), 2)),
                    'height': str(round(max(0.1, height_in), 2)),
                    'distance_unit': 'in',
                    'weight': str(round(max(0.1, weight_lb), 2)),
                    'mass_unit': 'lb',
                })
            except Exception:
                parcels.append({
                    'length': '10', 'width': '8', 'height': '6',
                    'distance_unit': 'in', 'weight': '1', 'mass_unit': 'lb',
                })
        return parcels if parcels else None

    def _build_parcel(self):
        # When delivery order has packs, use pack dimensions and weight (one parcel per pack)
        if self.picking_id:
            parcels = self._get_parcels_from_picking_packages(self.picking_id)
            if parcels:
                return parcels
        # Custom dimensions (when Custom Package is checked)
        if self.custom_package:
            return [{
                'length': str(self.length),
                'width': str(self.width),
                'height': str(self.height),
                'distance_unit': self.distance_unit,
                'weight': str(self.weight),
                'mass_unit': self.mass_unit,
            }]
        # Default package type (stock.package.type) from delivery method or wizard selection
        if self.package_type_id:
            pt = self.package_type_id
            length_uom = getattr(pt, 'length_uom_name', None) or 'in'
            w_uom = getattr(pt, 'weight_uom_name', None) or 'lb'
            length_val = float(getattr(pt, 'packaging_length', 0) or 1)
            width_val = float(getattr(pt, 'width', 0) or 1)
            height_val = float(getattr(pt, 'height', 0) or 1)
            if length_uom and 'cm' in (length_uom or '').lower():
                length_in = length_val * 0.393701
                width_in = width_val * 0.393701
                height_in = height_val * 0.393701
            else:
                length_in, width_in, height_in = length_val, width_val, height_val
            base_w = float(getattr(pt, 'base_weight', 0) or 0)
            if w_uom and 'kg' in (w_uom or '').lower():
                weight_lb = base_w * 2.20462
            else:
                weight_lb = base_w
            if weight_lb <= 0:
                weight_lb = self.weight or 1.0
            return [{
                'length': str(round(max(0.1, length_in), 2)),
                'width': str(round(max(0.1, width_in), 2)),
                'height': str(round(max(0.1, height_in), 2)),
                'distance_unit': 'in',
                'weight': str(round(max(0.1, weight_lb), 2)),
                'mass_unit': 'lb',
            }]
        return [{
            'length': str(self.length),
            'width': str(self.width),
            'height': str(self.height),
            'distance_unit': self.distance_unit,
            'weight': str(self.weight),
            'mass_unit': self.mass_unit,
        }]

    def _build_destination_address(self):
        """From picking partner_id or order partner_shipping_id."""
        partner = None
        if self.picking_id:
            partner = self.picking_id.partner_id
        elif self.order_id:
            partner = self.order_id.partner_shipping_id or self.order_id.partner_id
        if not partner:
            raise UserError('No destination address.')
        state_code = ''
        if hasattr(partner, 'state_id') and partner.state_id:
            state_code = partner.state_id.code or partner.state_id.name or ''
        elif hasattr(partner, 'state') and partner.state:
            state_code = partner.state
        return {
            'name': partner.name or 'Recipient',
            'street1': partner.street or ' ',
            'street2': partner.street2 or '',
            'city': partner.city or ' ',
            'state': state_code,
            'zip': partner.zip or ' ',
            'country': partner.country_id.code if partner.country_id else 'US',
            'phone': partner.phone or '',
            'is_residential': self.is_residential,
        }

    def _get_carrier_account_ids(self):
        """List of Shippo carrier_account object_ids to pass. Empty = all (published behavior when rate_type=published).
        When origin–carrier mapping is configured, only accounts mapped to the current origin are used."""
        if self.rate_type == 'published':
            return []  # omit for retail rates
        carrier = self.delivery_carrier_id
        origin = self.origin_address_id
        # Prefer origin-specific mapping when configured
        if carrier.shippo_origin_carrier_mapping_ids and origin:
            mapping = carrier.shippo_origin_carrier_mapping_ids.filtered(lambda m: m.origin_id == origin)
            if mapping:
                accounts = mapping.carrier_account_ids.filtered('active')
                if accounts:
                    return [a.object_id for a in accounts]
                return []
            # Mappings exist but no match for this origin: show no rates for wrong origin
            return []
        # No origin mapping: use global Shippo Carrier Accounts or all
        if carrier.shippo_carrier_account_ids:
            return [a.object_id for a in carrier.shippo_carrier_account_ids if a.active]
        accounts = self.env['shippo.carrier.account'].search([('company_id', 'in', (self.env.company.id, False)), ('active', '=', True)])
        return [a.object_id for a in accounts]

    def _apply_markup(self, carrier_token, servicelevel_token, amount):
        """Apply markup from delivery method: Shippo markup rules + standard Margin on Rate / Additional margin."""
        carrier = self.delivery_carrier_id
        markup_amount = 0
        # 1) Shippo markup rules (per carrier/service)
        for rule in carrier.shippo_markup_rule_ids:
            if rule.carrier and rule.carrier != carrier_token:
                continue
            if rule.service_level and rule.service_level != servicelevel_token:
                continue
            if rule.markup_type == 'flat':
                markup_amount += rule.markup_value
            else:
                markup_amount += amount * (rule.markup_value / 100.0)
        # 2) Standard "Margin on Rate" (percentage) and "Additional margin" (fixed) from delivery method
        # Odoo stores margin as decimal (0.25 = 25%); see delivery.carrier _apply_margins: price * (1.0 + margin)
        margin_pct = getattr(carrier, 'margin', None)
        if margin_pct is not None and margin_pct != 0:
            if margin_pct > 1:
                markup_amount += amount * (margin_pct / 100.0)  # value entered as 25 for 25%
            else:
                markup_amount += amount * margin_pct  # value stored as 0.25 for 25%
        fixed = getattr(carrier, 'fixed_margin', None)
        if fixed is not None and fixed != 0:
            markup_amount += fixed
        return (markup_amount, amount + markup_amount)

    def action_get_rates(self):
        self.ensure_one()
        origin = self.origin_address_id
        api_key = self._get_api_key()
        use_test = self._use_test_env()
        # Shippo requires addresses in state VALID. Always create/validate both origin and destination now.
        try:
            addr_from = create_address(
                api_key,
                origin._to_shippo_address_dict(),
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            _msg = str(e)
            if 'token' in _msg.lower() or 'authentication' in _msg.lower():
                raise UserError(
                    'Shippo API key invalid or wrong environment. Check Delivery Methods → Shippo Shipping: '
                    'set the correct Test or Production API key and match the Test Environment toggle. (%s)'
                    % _msg
                )
            raise UserError(
                'Origin address "%s": Shippo validation failed. Fix the origin or use Validate with Shippo. (%s)'
                % (origin.name, _msg)
            )
        if not _address_is_valid_for_shipment(addr_from):
            raise UserError(
                'Origin address "%s" could not be validated by Shippo (address invalid or incomplete). '
                'Check the origin address and use Validate with Shippo on it.'
                % origin.name
            )
        address_from = addr_from.get('object_id')
        if not origin.shippo_address_id:
            origin.shippo_address_id = address_from
            origin.validation_message = False
        address_to_dict = self._build_destination_address()
        try:
            addr_to = create_address(
                api_key,
                address_to_dict,
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            _msg = str(e)
            if 'VALID' in _msg or 'state' in _msg.lower():
                raise UserError(
                    'Shippo could not validate the destination address. Check the shipping address on the order. (%s)'
                    % _msg
                )
            raise UserError('Shippo: %s' % _msg)
        if not _address_is_valid_for_shipment(addr_to):
            raise UserError(
                'Destination address could not be validated by Shippo (invalid or incomplete). '
                'Check the shipping address on the order.'
            )
        address_to = addr_to.get('object_id')
        if not address_to:
            raise UserError('Shippo did not return a valid destination address.')
        parcels = self._build_parcel()
        carrier_accounts = self._get_carrier_account_ids()
        extra = {}
        if self.insurance_amount:
            extra['insurance'] = {'amount': str(self.insurance_amount), 'currency': 'USD', 'content': 'Order'}
        if self.signature_confirmation and self.signature_confirmation != 'none':
            extra['signature_confirmation'] = self.signature_confirmation
        try:
            shipment = create_shipment(
                api_key,
                address_from=address_from,
                address_to=address_to,
                parcels=parcels,
                carrier_accounts=carrier_accounts if carrier_accounts else None,
                extra=extra if extra else None,
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            _msg = str(e)
            if 'token' in _msg.lower() or 'authentication' in _msg.lower():
                raise UserError(
                    'Shippo API key invalid or wrong environment. Check Delivery Methods → Shippo Shipping: '
                    'set the correct Test or Production API key and match the Test Environment toggle. (%s)'
                    % _msg
                )
            raise UserError('Shippo: %s' % _msg)
        rates = shipment.get('rates') or []
        # Group by carrier; within each carrier, cheapest first, most expensive last
        def _rate_sort_key(r):
            provider = r.get('provider') or r.get('carrier') or ''
            amount = float(r.get('amount_local') or r.get('amount') or 0)
            return (provider, amount)
        rates = sorted(rates, key=_rate_sort_key)
        # Build lines with markup
        Line = self.env['shippo.rate.wizard.line']
        self.rate_line_ids.unlink()
        for r in rates:
            amount = float(r.get('amount_local') or r.get('amount') or 0)
            provider = r.get('provider') or r.get('carrier') or ''
            sl = r.get('servicelevel') or {}
            token = sl.get('token') if isinstance(sl, dict) else getattr(sl, 'token', '')
            markup_amt, final = self._apply_markup(provider, token, amount)
            Line.create({
                'wizard_id': self.id,
                'rate_object_id': r.get('object_id'),
                'carrier': provider,
                'service_name': sl.get('name') if isinstance(sl, dict) else getattr(sl, 'name', ''),
                'estimated_days': r.get('estimated_days'),
                'amount': amount,
                'markup': markup_amt,
                'final_amount': final,
                'currency': r.get('currency_local') or r.get('currency') or 'USD',
            })
        self.state = 'rates'
        # Reopen the wizard so the form reloads and the Rates notebook is visible (avoids client not refreshing)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Get Shippo Rates',
            'res_model': 'shippo.rate.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self.env.context),
        }

    def action_apply_estimate(self):
        """Write selected rate to SO: set carrier, add/update delivery line, and store estimate."""
        self.ensure_one()
        if not self.order_id:
            raise UserError('No sales order.')
        if not self.selected_rate_id:
            raise UserError('Select a rate first.')
        order = self.order_id
        amount = self.selected_rate_id.final_amount
        # Apply "Free if order amount is above" from the delivery method
        carrier = self.delivery_carrier_id
        if getattr(carrier, 'free_over', False) and getattr(carrier, 'amount', None) is not None:
            order_amount = sum(
                line.price_subtotal
                for line in order.order_line
                if not getattr(line, 'is_delivery', False)
            )
            if order_amount >= carrier.amount:
                amount = 0.0
        order.shippo_estimated_shipping = amount
        order.shippo_rate_wizard_data = json.dumps({
            'rate_type': self.rate_type,
            'carrier': self.selected_rate_id.carrier,
            'service': self.selected_rate_id.service_name,
            'amount': amount,
        })
        # Set carrier and delivery line for the whole order (all products); clear recompute so orange highlight goes away
        if hasattr(order, 'set_delivery_line'):
            order.set_delivery_line(self.delivery_carrier_id, amount)
        else:
            order.carrier_id = self.delivery_carrier_id
        if hasattr(order, 'recompute_delivery_price'):
            order.write({'recompute_delivery_price': False})
        # Show selected shipping service on the delivery line (e.g. "USPS - Ground Advantage")
        delivery_lines = order.order_line.filtered(
            lambda l: l.is_delivery and l.product_id == self.delivery_carrier_id.product_id
        )
        if delivery_lines:
            carrier_name = self.selected_rate_id.carrier or 'Shippo'
            service_name = self.selected_rate_id.service_name or ''
            line_name = '%s - %s' % (carrier_name, service_name) if service_name else carrier_name
            delivery_lines[0].write({'name': line_name})
        if amount == 0:
            order.message_post(
                body='Shippo rate applied: %s %s - %s (free: order amount above threshold)' % (
                    self.selected_rate_id.carrier,
                    self.selected_rate_id.service_name,
                    self.rate_type,
                ),
            )
        else:
            order.message_post(
                body='Shippo rate applied: %s %s - %s (%.2f %s)' % (
                    self.selected_rate_id.carrier,
                    self.selected_rate_id.service_name,
                    self.rate_type,
                    amount,
                    self.selected_rate_id.currency,
                ),
            )
        # When opened from Odoo 19 "Add a shipping method" wizard, update it so "Add" uses this cost
        if self.choose_carrier_wizard_id:
            self.choose_carrier_wizard_id.write({
                'delivery_price': amount,
                'display_price': amount,
            })
        return {'type': 'ir.actions.act_window_close'}

    def action_generate_label(self):
        """Create Shippo transaction for selected rate and attach label to picking."""
        self.ensure_one()
        if not self.picking_id:
            raise UserError('No delivery order.')
        if not self.selected_rate_id:
            raise UserError('Select a rate first.')
        api_key = self._get_api_key()
        use_test = self._use_test_env()
        label_format = self.delivery_carrier_id.shippo_label_format or 'PDF_4x6'
        try:
            txn = create_transaction(
                api_key,
                self.selected_rate_id.rate_object_id,
                label_file_type=label_format,
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError('Shippo: %s' % str(e))
        status = txn.get('status')
        if status != 'SUCCESS':
            # May be QUEUED/WAITING
            raise UserError('Label not ready yet. Status: %s. Try again in a moment or check Shippo dashboard.' % status)
        tracking = txn.get('tracking_number') or ''
        tracking_url = txn.get('tracking_url_provider') or ''
        label_url = txn.get('label_url') or ''
        carrier_name = self.selected_rate_id.carrier or ''
        service_name = self.selected_rate_id.service_name or ''
        shippo_selected_service = '%s - %s' % (carrier_name, service_name) if service_name else (carrier_name or 'Shippo')
        self.picking_id.write({
            'shippo_transaction_id': txn.get('object_id'),
            'shippo_tracking_url': tracking_url,
            'shippo_origin_address_id': self.origin_address_id.id,
            'shippo_rate_type': self.rate_type,
            'shippo_selected_service': shippo_selected_service,
            'shippo_carrier_id': self.delivery_carrier_id.id,
            'shippo_options_json': json.dumps({
                'insurance': self.insurance_amount,
                'is_residential': self.is_residential,
                'signature': self.signature_confirmation,
            }),
        })
        if tracking:
            self.picking_id.carrier_tracking_ref = tracking
        num_parcels = len(self._build_parcel())
        # Multi-parcel: Shippo creates one transaction per parcel; GET list by rate to fetch all labels
        transactions_to_attach = []
        if num_parcels > 1:
            import time
            time.sleep(2)  # give Shippo time to create the other parcel transactions
            try:
                txns_list = list_transactions_by_rate(api_key, self.selected_rate_id.rate_object_id, use_test_env=use_test)
                if txns_list:
                    transactions_to_attach = [t for t in txns_list if t.get('status') == 'SUCCESS' and t.get('label_url')]
            except Exception as e:
                _logger.debug('Could not list transactions by rate for multi-parcel labels: %s', e)
        if not transactions_to_attach:
            # Single parcel or list failed: use initial transaction
            if not label_url and status == 'SUCCESS' and txn.get('object_id'):
                import time
                time.sleep(1)
                try:
                    txn = get_transaction(api_key, txn['object_id'], use_test_env=use_test)
                    label_url = txn.get('label_url') or ''
                except Exception as e:
                    _logger.debug('Could not refetch transaction for label_url: %s', e)
            if label_url:
                transactions_to_attach = [{'label_url': label_url, 'object_id': txn.get('object_id')}]
        attachment_ids = []
        import base64
        import requests
        for idx, t in enumerate(transactions_to_attach):
            url = t.get('label_url')
            if not url:
                continue
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200 and resp.content:
                    name = 'Shippo_Shipping_Label_%s.pdf' % (self.picking_id.name or '')
                    if len(transactions_to_attach) > 1:
                        name = 'Shippo_Label_%s_of_%s_%s.pdf' % (idx + 1, len(transactions_to_attach), (self.picking_id.name or '').replace('/', '_'))
                    att = self.env['ir.attachment'].create({
                        'name': name,
                        'datas': base64.b64encode(resp.content).decode('ascii'),
                        'res_model': 'stock.picking',
                        'res_id': self.picking_id.id,
                        'type': 'binary',
                        'mimetype': 'application/pdf',
                    })
                    attachment_ids.append(att.id)
            except Exception as e:
                _logger.warning('Could not attach label PDF %s: %s', idx + 1, e)
        msg_body = 'Shippo label created. Transaction: %s. Tracking: %s' % (txn.get('object_id'), tracking)
        if num_parcels > 1:
            msg_body += '\n\n(%s pack(s): %s label(s) attached.)' % (num_parcels, len(attachment_ids))
        if not attachment_ids and (transactions_to_attach or label_url):
            msg_body += '\n\n(Label PDF(s) could not be saved; you can download from the Shippo dashboard.)'
        elif not transactions_to_attach and not label_url:
            msg_body += '\n\n(Label URL not yet available; you can download the label from the Shippo dashboard.)'
        self.picking_id.message_post(
            body=msg_body,
            attachment_ids=attachment_ids,
        )
        return {'type': 'ir.actions.act_window_close'}


class ShippoRateWizardLine(models.TransientModel):
    _name = 'shippo.rate.wizard.line'
    _description = 'Shippo Rate Line'

    wizard_id = fields.Many2one('shippo.rate.wizard', required=True, ondelete='cascade')
    rate_object_id = fields.Char(required=True)
    carrier = fields.Char()
    service_name = fields.Char()
    estimated_days = fields.Integer()
    amount = fields.Float()
    markup = fields.Float()
    final_amount = fields.Float()
    currency = fields.Char(default='USD')

    def action_select_rate(self):
        """Set this rate as selected; then apply in one click: SO → apply estimate; delivery order → generate label."""
        self.ensure_one()
        wizard = self.wizard_id
        wizard.selected_rate_id = self
        if wizard.order_id:
            return wizard.action_apply_estimate()
        if wizard.picking_id:
            # One click: create Shippo label and close
            return wizard.action_generate_label()
        return True
