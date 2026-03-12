# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceOrderWarehouseMapping(models.Model):
    """Map BigCommerce order to Odoo warehouse based on product SKU on the order.
    Rules are evaluated in sequence; first match wins. If any product on the order
    has a SKU matching a rule, the order is assigned to that warehouse.
    """
    _name = 'bigcommerce.order.warehouse.mapping'
    _description = 'Order Warehouse Mapping (SKU → Warehouse)'
    _order = 'sequence, id'

    config_id = fields.Many2one(
        'bigcommerce.config',
        string='Configuration',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='Sequence', default=10)
    filter_type = fields.Selection(
        [
            ('sku_starts_with', 'SKU starts with'),
            ('sku_contains', 'SKU contains'),
            ('sku_ends_with', 'SKU ends with'),
            ('sku_equals', 'SKU equals'),
        ],
        string='Filter type',
        required=True,
        default='sku_starts_with',
        help='Match product SKU from BigCommerce order line.',
    )
    filter_value = fields.Char(
        string='Filter value',
        required=True,
        help='Value to match against product SKU (case-insensitive). E.g. "iag-mss" matches "IAG-MSS-4200" but not "IAG MSS 4200".',
    )
    odoo_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Odoo Warehouse',
        required=True,
        help='Warehouse to assign to the order when any product SKU matches this rule.',
    )
    active = fields.Boolean(string='Active', default=True)

    def _matches(self, sku):
        """Return True if the given SKU matches this rule. Matching is case-insensitive
        only; spaces and hyphens are distinct (e.g. "iag-mss" matches "IAG-MSS-4200" but not "IAG MSS 4200")."""
        self.ensure_one()
        if not sku and sku != '':
            return False
        sku = (sku or '').strip().lower()
        val = (self.filter_value or '').strip().lower()
        if not val:
            return False
        if self.filter_type == 'sku_starts_with':
            return sku.startswith(val)
        if self.filter_type == 'sku_contains':
            return val in sku
        if self.filter_type == 'sku_ends_with':
            return sku.endswith(val)
        if self.filter_type == 'sku_equals':
            return sku == val
        return False

    @api.model
    def get_warehouse_id_for_order_products(self, config, order_products):
        """Return Odoo warehouse id for the first active rule that matches any product SKU on the order.
        order_products: list of BigCommerce order product dicts (each may have 'sku', 'product_sku', 'sku_code', 'name').
        Returns warehouse id or None if no rule matches.
        """
        if not config or not order_products:
            return None
        rules = config.order_warehouse_mapping_ids.filtered(lambda r: r.active)
        for rule in rules.sorted(key=lambda r: (r.sequence, r.id)):
            for bc_product in order_products:
                sku = bc_product.get('sku') or bc_product.get('product_sku') or bc_product.get('sku_code')
                if sku and rule._matches(sku):
                    return rule.odoo_warehouse_id.id
                # Fallback: check product name (e.g. "[IAG MSS 4200] Install Timing...") when SKU is empty
                if not sku:
                    name = bc_product.get('name') or bc_product.get('name_customer') or bc_product.get('name_merchant') or ''
                    if name and rule._matches(name):
                        return rule.odoo_warehouse_id.id
        return None
