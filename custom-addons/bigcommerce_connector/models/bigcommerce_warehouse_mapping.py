# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceWarehouseMapping(models.Model):
    _name = 'bigcommerce.warehouse.mapping'
    _description = 'BigCommerce Warehouse Mapping'

    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, ondelete='cascade')
    odoo_warehouse_id = fields.Many2one('stock.warehouse', string='Odoo Warehouse', required=True)
    bc_location_id = fields.Char(string='BigCommerce Location ID', help='BigCommerce location/warehouse identifier')
    bc_location_name = fields.Char(string='BigCommerce Location Name')
    is_default = fields.Boolean(string='Default Warehouse', default=False, help='Use this warehouse as default for inventory sync')
    min_threshold = fields.Float(string='Minimum Threshold', default=0.0, help='Minimum inventory level before syncing to BigCommerce')
    active = fields.Boolean(string='Active', default=True)

    @api.constrains('is_default')
    def _check_default_warehouse(self):
        """Ensure only one default warehouse per configuration"""
        for record in self:
            if record.is_default:
                other_defaults = self.search([
                    ('config_id', '=', record.config_id.id),
                    ('is_default', '=', True),
                    ('id', '!=', record.id)
                ])
                if other_defaults:
                    raise ValidationError("Only one warehouse can be set as default per configuration.")

