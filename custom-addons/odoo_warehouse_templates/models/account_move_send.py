# -*- coding: utf-8 -*-
from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _get_default_pdf_report_id(self, move):
        wh = self.env['warehouse.document.helper'].warehouse_for_record(move)
        if wh and wh.invoice_report_id:
            return wh.invoice_report_id
        return super()._get_default_pdf_report_id(move)
