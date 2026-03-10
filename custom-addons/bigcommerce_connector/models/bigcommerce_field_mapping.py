# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceFieldMapping(models.Model):
    _name = 'bigcommerce.field.mapping'
    _description = 'BigCommerce Field Mapping Configuration'

    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, ondelete='cascade')
    sync_type = fields.Selection([
        ('product', 'Product'),
        ('order', 'Order'),
        ('customer', 'Customer'),
    ], string='Sync Type', required=True)
    bc_field = fields.Char(string='BigCommerce Field', required=True, help='Field name in BigCommerce')
    odoo_field = fields.Char(string='Odoo Field', required=True, help='Field name in Odoo model')
    mapping_type = fields.Selection([
        ('direct', 'Direct Mapping'),
        ('transform', 'Transform Function'),
        ('default', 'Default Value'),
    ], string='Mapping Type', default='direct', required=True)
    transform_function = fields.Text(string='Transform Function', help='Python code to transform the value. Use "value" as the input variable.')
    default_value = fields.Char(string='Default Value', help='Default value if BigCommerce field is empty')
    active = fields.Boolean(string='Active', default=True)

    sql_constraints = [
        ('unique_field_mapping', 'unique(config_id, sync_type, bc_field)', 'Each field mapping must be unique per configuration and sync type.')
    ]

