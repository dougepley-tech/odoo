# -*- coding: utf-8 -*-

from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    fishbowl_po_id = fields.Integer(string='Fishbowl PO id', index=True, copy=False)
    fishbowl_num = fields.Char(string='Fishbowl PO number', copy=False)
    fishbowl_status_name = fields.Char(string='Fishbowl status', readonly=True, copy=False)

    def message_post(self, **kwargs):
        if self.env.context.get('fishbowl_import'):
            kwargs.setdefault('subtype_xmlid', False)
        return super().message_post(**kwargs)
