# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceCategoryMapping(models.Model):
    _name = 'bigcommerce.category.mapping'
    _description = 'BigCommerce Category Mapping'
    _rec_name = 'bc_category_name'

    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, ondelete='cascade')
    bc_category_id = fields.Integer(string='BigCommerce Category ID', required=True)
    bc_category_name = fields.Char(string='BigCommerce Category Name', required=True)
    odoo_category_id = fields.Many2one('product.category', string='Odoo Category', required=True)
    active = fields.Boolean(string='Active', default=True)

    sql_constraints = [
        ('unique_bc_category', 'unique(config_id, bc_category_id)', 'Each BigCommerce category can only be mapped once per configuration.')
    ]

