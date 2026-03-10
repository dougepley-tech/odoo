# -*- coding: utf-8 -*-

from odoo import models, fields, api


class BigCommerceStateTaxMapping(models.Model):
    _name = 'bigcommerce.state.tax.mapping'
    _description = 'BigCommerce State Tax Mapping'
    _rec_name = 'state_code'

    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, ondelete='cascade')
    state_code = fields.Char(string='State Code', required=True, help='Two-letter state/province code (e.g., CA, NY)')
    state_name = fields.Char(string='State Name', help='Full state name for reference')
    tax_product_id = fields.Many2one('product.product', string='Tax Product', required=True, domain=[('type', '=', 'service')])
    
    sql_constraints = [
        ('unique_state_config', 'unique(state_code, config_id)', 'State mapping must be unique per configuration')
    ]


class BigCommerceCarrierMapping(models.Model):
    _name = 'bigcommerce.carrier.mapping'
    _description = 'BigCommerce Carrier Mapping'
    _rec_name = 'odoo_carrier_id'

    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, ondelete='cascade')
    odoo_carrier_id = fields.Many2one('delivery.carrier', string='Odoo Carrier', required=True)
    bc_carrier_code = fields.Char(string='BigCommerce Carrier Code', required=True, help='Carrier code used in BigCommerce (e.g., usps, fedex, ups)')
    priority = fields.Integer(string='Priority', default=10, help='Lower numbers have higher priority')
    
    sql_constraints = [
        ('unique_carrier_config', 'unique(odoo_carrier_id, config_id)', 'Carrier mapping must be unique per configuration')
    ]

