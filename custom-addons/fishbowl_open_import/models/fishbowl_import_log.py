# -*- coding: utf-8 -*-

import uuid

from odoo import api, fields, models


class FishbowlImportLog(models.Model):
    _name = 'fishbowl.import.log'
    _description = 'Fishbowl Import Log'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', default=lambda self: uuid.uuid4().hex[:12], required=True)
    batch_id = fields.Char(string='Batch', index=True)
    operation = fields.Selection(
        [
            ('master', 'Master data'),
            ('inventory', 'Inventory'),
            ('so', 'Sales order'),
            ('po', 'Purchase order'),
            ('picking', 'Picking sync'),
        ],
        string='Operation',
        required=True,
    )
    level = fields.Selection(
        [
            ('info', 'Info'),
            ('warning', 'Warning'),
            ('error', 'Error'),
        ],
        string='Level',
        default='info',
        required=True,
    )
    fishbowl_ref = fields.Char(string='Fishbowl ref')
    message = fields.Text(string='Message', required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        ondelete='cascade',
    )
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', ondelete='set null')
    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order', ondelete='set null')

    @api.model
    def log_line(self, operation, message, level='info', fishbowl_ref=None, batch_id=None, sale_order=None, purchase_order=None):
        return self.sudo().create({
            'operation': operation,
            'level': level,
            'message': message,
            'fishbowl_ref': fishbowl_ref,
            'batch_id': batch_id,
            'sale_order_id': sale_order.id if sale_order else False,
            'purchase_order_id': purchase_order.id if purchase_order else False,
        })
