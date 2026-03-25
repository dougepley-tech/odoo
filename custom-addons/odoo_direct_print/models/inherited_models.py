# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'iag.direct.print.mixin']

    def _get_shippo_label_attachment(self):
        """
        Find the Shippo-generated shipping label PDF attached to this delivery.
        Returns the attachment record or None.
        """
        self.ensure_one()
        return self.env['ir.attachment'].search([
            ('res_model', '=', 'stock.picking'),
            ('res_id', '=', self.id),
            ('mimetype', '=', 'application/pdf'),
            '|',
            ('name', 'ilike', 'shippo'),
            ('name', 'ilike', 'shipping label'),
        ], order='create_date desc', limit=1)

    def _get_report_bytes(self, report_xml_id: str) -> tuple[bytes, str]:
        """
        For "Shipping Labels" report, use the Shippo-generated label attachment
        if available, instead of rendering the standard report.
        """
        report = self.env.ref(report_xml_id, raise_if_not_found=False)
        if report and 'shipping' in (report.name or '').lower() and 'label' in (report.name or '').lower():
            attachment = self._get_shippo_label_attachment()
            if attachment and attachment.datas:
                try:
                    data = base64.b64decode(attachment.datas)
                    if data:
                        return data, 'pdf'
                except Exception as e:
                    _logger.warning('Could not decode Shippo label attachment: %s', e)
        return super()._get_report_bytes(report_xml_id)

    def button_validate(self):
        result = super().button_validate()
        # Only fire after a clean validate (not wizard return)
        if result is True or (isinstance(result, dict) and result.get('res_model') != 'stock.immediate.transfer'):
            self.env['iag.print.scenario'].fire_scenarios('stock.picking.button_validate', self)
        return result

    def _action_done(self):
        result = super()._action_done()
        # Only fire for outgoing (delivery) transfers, not picks or internal moves
        if self.picking_type_code == 'outgoing':
            self.env['iag.print.scenario'].fire_scenarios('stock.picking.do_transfer', self)
        return result

    def action_put_in_pack(self):
        result = super().action_put_in_pack()
        self.env['iag.print.scenario'].fire_scenarios('stock.picking.action_put_in_pack', self)
        return result
