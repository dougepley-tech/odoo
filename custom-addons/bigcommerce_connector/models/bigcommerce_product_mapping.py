# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class BigCommerceProductMapping(models.Model):
    """Store the mapping between Odoo products and BigCommerce products across multiple stores"""
    _name = 'bigcommerce.product.mapping'
    _description = 'BigCommerce Product Mapping'
    _rec_name = 'display_name'
    _order = 'config_id'

    product_tmpl_id = fields.Many2one('product.template', string='Product', required=True, ondelete='cascade', index=True)
    config_id = fields.Many2one('bigcommerce.config', string='BigCommerce Configuration', required=True, ondelete='cascade', index=True)
    bigcommerce_id = fields.Integer(string='BigCommerce Product ID', required=True, index=True)
    bigcommerce_last_sync = fields.Datetime(string='Last Sync with BigCommerce')
    bigcommerce_synced = fields.Boolean(string='Synced with BigCommerce', default=False)
    
    # Variant mappings stored separately
    variant_mapping_ids = fields.One2many('bigcommerce.variant.mapping', 'product_mapping_id', string='Variant Mappings')
    
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    # Odoo 19: Use models.Constraint instead of _sql_constraints
    _unique_product_config = models.Constraint(
        'unique (product_tmpl_id, config_id)',
        'A product can only have one BigCommerce mapping per configuration!'
    )
    # Note: We do NOT enforce unique(bigcommerce_id, config_id) because:
    # - Different products in different stores can have the same BigCommerce ID
    # - Products are matched by SKU, not by BigCommerce ID
    # - BigCommerce IDs are unique per store, but the same ID can represent different products across stores
    
    @api.depends('product_tmpl_id', 'config_id', 'bigcommerce_id')
    def _compute_display_name(self):
        for record in self:
            product_name = record.product_tmpl_id.name if record.product_tmpl_id else 'Unknown'
            config_name = record.config_id.name if record.config_id else 'Unknown'
            record.display_name = f"{product_name} - {config_name} (BC ID: {record.bigcommerce_id})"
    
    @api.model
    def get_or_create_mapping(self, product_tmpl_id, config_id, bigcommerce_id):
        """Get existing mapping or create a new one"""
        mapping = self.search([
            ('product_tmpl_id', '=', product_tmpl_id),
            ('config_id', '=', config_id),
        ], limit=1)
        
        if mapping:
            # Update BigCommerce ID if it changed
            if mapping.bigcommerce_id != bigcommerce_id:
                mapping.bigcommerce_id = bigcommerce_id
            return mapping
        
        # Create new mapping
        return self.create({
            'product_tmpl_id': product_tmpl_id,
            'config_id': config_id,
            'bigcommerce_id': bigcommerce_id,
        })


class BigCommerceVariantMapping(models.Model):
    """Store the mapping between Odoo product variants and BigCommerce variants across multiple stores"""
    _name = 'bigcommerce.variant.mapping'
    _description = 'BigCommerce Variant Mapping'
    _rec_name = 'display_name'
    _order = 'config_id'

    product_mapping_id = fields.Many2one('bigcommerce.product.mapping', string='Product Mapping', required=True, ondelete='cascade', index=True)
    product_variant_id = fields.Many2one('product.product', string='Product Variant', required=True, ondelete='cascade', index=True)
    config_id = fields.Many2one('bigcommerce.config', string='BigCommerce Configuration', required=True, related='product_mapping_id.config_id', store=True, readonly=True)
    bigcommerce_variant_id = fields.Integer(string='BigCommerce Variant ID', required=True, index=True)
    
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    # Odoo 19: Use models.Constraint instead of _sql_constraints
    _unique_variant_config = models.Constraint(
        'unique (product_variant_id, config_id)',
        'A product variant can only have one BigCommerce mapping per configuration!'
    )
    _unique_bc_variant_id_config = models.Constraint(
        'unique (bigcommerce_variant_id, config_id)',
        'A BigCommerce variant ID can only be mapped once per configuration!'
    )
    
    @api.depends('product_variant_id', 'config_id', 'bigcommerce_variant_id')
    def _compute_display_name(self):
        for record in self:
            variant_name = record.product_variant_id.name if record.product_variant_id else 'Unknown'
            config_name = record.config_id.name if record.config_id else 'Unknown'
            record.display_name = f"{variant_name} - {config_name} (BC Variant ID: {record.bigcommerce_variant_id})"
