# -*- coding: utf-8 -*-
from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _send_confirmation_email(self):
        subtype_id = self.env['ir.model.data']._xmlid_to_res_id('mail.mt_comment')
        for stock_pick in self.filtered(
            lambda p: p.company_id.stock_move_email_validation and p.picking_type_id.code == 'outgoing'
        ):
            wh = stock_pick.picking_type_id.warehouse_id or stock_pick.warehouse_id
            delivery_template = (
                wh.stock_picking_mail_template_id
                if wh and wh.stock_picking_mail_template_id
                else stock_pick.company_id.stock_mail_confirmation_template_id
            )
            if not delivery_template:
                continue
            stock_pick.with_context(force_send=True).message_post_with_source(
                delivery_template,
                email_layout_xmlid='mail.mail_notification_light',
                subtype_id=subtype_id,
            )
