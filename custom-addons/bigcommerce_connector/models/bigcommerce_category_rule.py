# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceCategoryRule(models.Model):
    """Filter-based rules to set Odoo product category from product SKU (Internal Reference).
    No category sync between BigCommerce and Odoo; rules are evaluated in sequence, first match wins.
    """
    _name = 'bigcommerce.category.rule'
    _description = 'BigCommerce Category Rule (filter → Odoo category)'
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
        help='Match product Internal Reference (SKU) from BigCommerce or Odoo.',
    )
    filter_value = fields.Char(
        string='Filter value',
        required=True,
        help='Value to match against product SKU / Internal Reference (case-sensitive).',
    )
    odoo_category_id = fields.Many2one(
        'product.category',
        string='Odoo Category',
        required=True,
    )
    active = fields.Boolean(string='Active', default=True)

    def _matches(self, sku):
        """Return True if the given SKU (Internal Reference) matches this rule."""
        self.ensure_one()
        if not sku and sku != '':
            return False
        sku = (sku or '').strip()
        val = (self.filter_value or '').strip()
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
    def get_category_id_for_sku(self, config, sku):
        """Return Odoo category id for the first active rule that matches sku, or None."""
        if not config or not sku and sku != '':
            return None
        sku = (sku or '').strip()
        rules = config.category_rule_ids.filtered(lambda r: r.active)
        for rule in rules.sorted(key=lambda r: (r.sequence, r.id)):
            if rule._matches(sku):
                return rule.odoo_category_id.id
        return None
