# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    shippo_transaction_id = fields.Char(string='Shippo Transaction ID', copy=False, readonly=True)
    shippo_tracking_url = fields.Char(string='Tracking URL', copy=False, readonly=True)
    shippo_origin_address_id = fields.Many2one(
        'shippo.origin.address',
        string='Shippo Origin',
        copy=False,
        help='Origin address used for this shipment.',
    )
    shippo_rate_type = fields.Selection(
        [('negotiated', 'Negotiated'), ('published', 'Published')],
        string='Rate Type Used',
        copy=False,
    )
    shippo_options_json = fields.Char(string='Shipment Options (JSON)', copy=False, readonly=True)
    shippo_selected_service = fields.Char(
        string='Delivery Service',
        copy=False,
        help='Carrier and service chosen for this shipment (e.g. UPS Ground). Shown so the shipping team knows which rate to use when creating the label.',
    )
    shippo_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Shippo Delivery Method',
        copy=False,
        help='Delivery method used when this label was created. Refunds use its API key.',
    )
    is_shippo_carrier = fields.Boolean(
        string='Is Shippo',
        compute='_compute_is_shippo_carrier',
        help='True when the delivery carrier is Shippo; used for view visibility.',
    )

    def _compute_is_shippo_carrier(self):
        for picking in self:
            carrier = getattr(picking, 'carrier_id', None)
            picking.is_shippo_carrier = bool(carrier and getattr(carrier, 'delivery_type', None) == 'shippo')

    def _get_sale_order_from_picking(self):
        """Return the sale order linked to this picking, if any."""
        self.ensure_one()
        if hasattr(self, 'sale_id') and self.sale_id:
            return self.sale_id
        group_id = getattr(self, 'group_id', None)
        if group_id and getattr(group_id, 'sale_id', None):
            return group_id.sale_id
        if self.move_ids:
            sale_lines = self.move_ids.mapped('sale_line_id')
            if sale_lines:
                return sale_lines.mapped('order_id')[:1]
        if self.origin:
            return self.env['sale.order'].search([('name', '=', self.origin)], limit=1)
        return self.env['sale.order']

    def _set_shippo_selected_service_from_sale(self):
        """Set shippo_selected_service from linked sale order.

        Prefer shippo_rate_wizard_data (Odoo Get Shippo Rates flow). Otherwise, for
        imported orders (BigCommerce/Amazon): the shipping service (e.g. "USPS Ground
        Advantage") is stored on the order line that has the Shippo shipping product. Find
        that line by product (no need for carrier_id or is_delivery) and use its name.
        """
        for picking in self:
            if picking.shippo_selected_service:
                continue
            order = picking._get_sale_order_from_picking()
            if not order:
                continue
            # 1) From rate wizard data (Odoo Get Shippo Rates flow)
            if getattr(order, 'shippo_rate_wizard_data', None):
                try:
                    data = json.loads(order.shippo_rate_wizard_data)
                    carrier = data.get('carrier') or ''
                    service = data.get('service') or ''
                    if carrier or service:
                        picking.shippo_selected_service = (
                            '%s - %s' % (carrier, service) if service else (carrier or 'Shippo')
                        )
                        continue
                except (ValueError, TypeError):
                    pass
            # 2) From order line: any line whose product is a Shippo carrier's product
            #    (imports add Shippo shipping as a product line with service name in line name)
            ShippoCarrier = self.env['delivery.carrier'].with_context(active_test=False)
            shippo_carriers = ShippoCarrier.search([('delivery_type', '=', 'shippo')])
            shippo_product_ids = shippo_carriers.mapped('product_id').ids
            if not shippo_product_ids:
                continue
            shippo_lines = order.order_line.filtered(
                lambda l: l.product_id and l.product_id.id in shippo_product_ids
            )
            # Prefer a line whose name looks like a service (e.g. "USPS Ground Advantage")
            # over the generic product name (e.g. "Shipping")
            product_default_name = None
            for line in shippo_lines:
                name = (line.name or '').strip()
                if not name:
                    continue
                if product_default_name is None and line.product_id:
                    product_default_name = (line.product_id.display_name or line.product_id.name or '').strip()
                if product_default_name and name != product_default_name:
                    picking.shippo_selected_service = name
                    break
            else:
                # fallback: first line with a name
                for line in shippo_lines:
                    name = (line.name or '').strip()
                    if name:
                        picking.shippo_selected_service = name
                        break

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings._set_shippo_selected_service_from_sale()
        return pickings

    def action_get_shippo_rates(self):
        self.ensure_one()
        return {
            'name': 'Get Shippo Rates',
            'type': 'ir.actions.act_window',
            'res_model': 'shippo.rate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.id},
        }

    def action_cancel_shippo_shipment(self):
        self.ensure_one()
        if not self.shippo_transaction_id:
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': 'No label',
                'message': 'No Shippo transaction on this delivery order.',
                'type': 'warning',
                'sticky': False,
            }}
        return {
            'name': 'Cancel Shippo Shipment',
            'type': 'ir.actions.act_window',
            'res_model': 'shippo.cancel.shipment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.id},
        }
