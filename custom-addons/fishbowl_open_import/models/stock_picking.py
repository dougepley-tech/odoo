# -*- coding: utf-8 -*-

from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def message_post(self, **kwargs):
        if self.env.context.get('fishbowl_import'):
            kwargs.setdefault('subtype_xmlid', False)
        return super().message_post(**kwargs)
