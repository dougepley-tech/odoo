# -*- coding: utf-8 -*-
from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_mail_template(self):
        if len(self) != 1:
            return super()._get_mail_template()
        move = self
        wh = self.env['warehouse.document.helper'].warehouse_for_record(move)
        if move.move_type in ('out_refund', 'in_refund'):
            if not (move.move_type == 'in_refund' and move.journal_id.is_self_billing):
                if wh.account_move_mail_template_credit_note_id:
                    return wh.account_move_mail_template_credit_note_id
        elif move.move_type in ('out_invoice', 'in_invoice', 'out_receipt', 'in_receipt'):
            if not (move.move_type == 'in_invoice' and move.journal_id.is_self_billing):
                if wh.account_move_mail_template_invoice_id:
                    return wh.account_move_mail_template_invoice_id
        return super()._get_mail_template()
