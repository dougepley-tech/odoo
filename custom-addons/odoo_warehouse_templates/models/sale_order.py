# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _find_mail_template(self):
        self.ensure_one()
        wh = self.warehouse_id
        if self.env.context.get('proforma'):
            return super()._find_mail_template()
        if wh:
            if self.state != 'sale':
                if wh.sale_mail_template_quotation_id:
                    return wh.sale_mail_template_quotation_id
            else:
                if wh.sale_mail_template_confirmation_id:
                    return wh.sale_mail_template_confirmation_id
        return super()._find_mail_template()
