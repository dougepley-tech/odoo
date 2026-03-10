# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class BigCommerceOrderStatus(models.Model):
    _name = 'bigcommerce.order.status'
    _description = 'BigCommerce Order Status'
    _rec_name = 'name'
    _order = 'bc_status_id'

    bc_status_id = fields.Integer(string='BigCommerce Status ID', required=True)
    name = fields.Char(string='Name', required=True)
    odoo_state = fields.Selection([
        ('draft', 'Quotation'),
        ('sent', 'Quotation Sent'),
        ('sale', 'Sales Order'),
        ('done', 'Locked'),
        ('cancel', 'Cancelled'),
    ], string='Odoo State', default='draft', required=True,
       help='Odoo sale order state to map this BigCommerce status to')
    
    @api.constrains('bc_status_id')
    def _check_bc_status_id_unique(self):
        """Ensure bc_status_id is unique"""
        for record in self:
            duplicate = self.search([
                ('bc_status_id', '=', record.bc_status_id),
                ('id', '!=', record.id)
            ], limit=1)
            if duplicate:
                raise ValidationError('BigCommerce Status ID must be unique!')

