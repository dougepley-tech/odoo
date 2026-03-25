# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IagDirectPrintMixin(models.AbstractModel):
    """
    Mixin to add direct-print actions to any model.
    Inherit this in stock.picking, sale.order, account.move, etc.
    """
    _name = 'iag.direct.print.mixin'
    _description = 'IAG Direct Print Mixin'

    def _get_report_bytes(self, report_xml_id: str) -> tuple[bytes, str]:
        """
        Render an Odoo report for the current record(s).
        Returns (bytes, doc_type) where doc_type is 'pdf' or 'zpl'.
        """
        report = self.env.ref(report_xml_id)
        if not report:
            raise UserError(_('Report "%s" not found.') % report_xml_id)

        report_type = report.report_type  # 'qweb-pdf', 'qweb-text', etc.

        if report_type in ('qweb-pdf', 'pdf'):
            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                report_xml_id, self.ids
            )
            return pdf_content, 'pdf'

        elif report_type in ('qweb-text', 'text'):
            text_content, _ = self.env['ir.actions.report']._render_qweb_text(
                report_xml_id, self.ids
            )
            return text_content.encode() if isinstance(text_content, str) else text_content, 'zpl'

        else:
            raise UserError(_('Unsupported report type: %s') % report_type)

    def iag_direct_print(self, report_xml_id: str, printer_id: int,
                         copies: int = 1, log_job: bool = True) -> dict:
        """
        Render report and send directly to printer.
        Returns dict with job info.
        """
        self.ensure_one()

        printer = self.env['iag.printer'].browse(printer_id)
        if not printer.exists():
            raise UserError(_('Printer ID %s not found.') % printer_id)

        # Render the report
        try:
            data, doc_type = self._get_report_bytes(report_xml_id)
        except Exception as e:
            if log_job:
                self._iag_log_job(printer, report_xml_id, 'failed', str(e))
            raise

        # Send to printer
        title = f'{self._description or self._name} #{getattr(self, "name", self.id)}'
        try:
            printer.print_document(data, doc_type=doc_type, title=title, copies=copies)
        except Exception as e:
            if log_job:
                self._iag_log_job(printer, report_xml_id, 'failed', str(e), len(data))
            raise

        if log_job:
            self._iag_log_job(printer, report_xml_id, 'done', False, len(data), copies)

        return {
            'printer': printer.name,
            'report': report_xml_id,
            'bytes': len(data),
            'copies': copies,
            'doc_type': doc_type,
        }

    def _iag_log_job(self, printer, report_xml_id, state, error=False,
                     size_bytes=0, copies=1):
        report_name = report_xml_id.split('.')[-1] if report_xml_id else '?'
        self.env['iag.print.job'].sudo().create({
            'name': f'{report_name} → {printer.name}',
            'printer_id': printer.id,
            'report_xml_id': report_xml_id,
            'res_model': self._name,
            'res_id': self.id,
            'state': state,
            'error_message': error or False,
            'size_bytes': size_bytes,
            'copies': copies,
        })
