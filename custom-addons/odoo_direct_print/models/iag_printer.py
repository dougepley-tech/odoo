# -*- coding: utf-8 -*-
import socket
import subprocess
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PRINTER_TYPE = [
    ('pdf_cups', 'PDF via CUPS'),
    ('pdf_raw', 'PDF via Raw Socket'),
    ('zpl_raw', 'ZPL via Raw Socket (Zebra/Thermal)'),
    ('ipp', 'IPP (Internet Printing Protocol)'),
]

PAPER_FORMAT = [
    ('A4', 'A4'),
    ('Letter', 'US Letter'),
    ('Legal', 'Legal'),
    ('label_4x6', 'Label 4×6"'),
    ('label_4x4', 'Label 4×4"'),
    ('label_2x1', 'Label 2×1"'),
    ('label_3x2', 'Label 3×2"'),
]


class IagPrinter(models.Model):
    _name = 'iag.printer'
    _description = 'IAG Direct Printer'
    _order = 'name'

    name = fields.Char(string='Printer Name', required=True)
    active = fields.Boolean(default=True)
    printer_type = fields.Selection(PRINTER_TYPE, string='Connection Type', required=True, default='zpl_raw')
    location = fields.Char(string='Location', help='e.g. Shipping Desk, Warehouse, Office')

    # Network settings
    host = fields.Char(string='IP Address / Hostname', help='Printer IP or hostname on the network')
    port = fields.Integer(string='Port', default=9100, help='9100 for RAW/ZPL, 631 for IPP')
    timeout = fields.Integer(string='Timeout (s)', default=10)

    # CUPS settings
    cups_name = fields.Char(string='CUPS Printer Name', help='Exact name as shown in `lpstat -p`')
    cups_host = fields.Char(string='CUPS Server Host', default='localhost')
    cups_port = fields.Integer(string='CUPS Server Port', default=631)

    # Paper & format
    paper_format = fields.Selection(PAPER_FORMAT, string='Default Paper Format', default='A4')
    dpi = fields.Integer(string='DPI', default=203)
    copies = fields.Integer(string='Default Copies', default=1)

    # Status
    status = fields.Selection([
        ('unknown', 'Unknown'),
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('error', 'Error'),
    ], string='Status', default='unknown', readonly=True)
    last_check = fields.Datetime(string='Last Status Check', readonly=True)
    last_error = fields.Char(string='Last Error', readonly=True)

    # Jobs
    job_ids = fields.One2many('iag.print.job', 'printer_id', string='Print Jobs')
    job_count = fields.Integer(compute='_compute_job_count', string='Jobs')

    @api.depends('job_ids')
    def _compute_job_count(self):
        for rec in self:
            rec.job_count = len(rec.job_ids)

    # ── Status check ─────────────────────────────────────────────────────────

    def action_check_status(self):
        for printer in self:
            printer._check_status()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': 'Printer status refreshed.',
                'type': 'success',
            }
        }

    def _check_status(self):
        self.ensure_one()
        import datetime
        self.last_check = fields.Datetime.now()

        if self.printer_type in ('zpl_raw', 'pdf_raw'):
            if not self.host:
                self.status = 'error'
                self.last_error = 'No IP address configured'
                return
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                sock.close()
                self.status = 'online'
                self.last_error = False
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                self.status = 'offline'
                self.last_error = str(e)

        elif self.printer_type == 'pdf_cups':
            if not self.cups_name:
                self.status = 'error'
                self.last_error = 'No CUPS printer name configured'
                return
            try:
                import cups
                conn = cups.Connection(host=self.cups_host or 'localhost', port=self.cups_port or 631)
                printers = conn.getPrinters()
                if self.cups_name in printers:
                    info = printers[self.cups_name]
                    state = info.get('printer-state', 0)
                    # CUPS states: 3=idle, 4=processing, 5=stopped
                    self.status = 'online' if state in (3, 4) else 'offline'
                    self.last_error = info.get('printer-state-message') or False
                else:
                    self.status = 'error'
                    self.last_error = f'Printer "{self.cups_name}" not found in CUPS'
            except Exception as e:
                self.status = 'error'
                self.last_error = str(e)

        elif self.printer_type == 'ipp':
            if not self.host:
                self.status = 'error'
                self.last_error = 'No hostname configured'
                return
            try:
                # Simple TCP probe on IPP port
                sock = socket.create_connection((self.host, self.port or 631), timeout=self.timeout)
                sock.close()
                self.status = 'online'
                self.last_error = False
            except Exception as e:
                self.status = 'offline'
                self.last_error = str(e)

    # ── Core print dispatch ──────────────────────────────────────────────────

    def _send_raw(self, data: bytes):
        """Send raw bytes to the printer via TCP socket (ZPL or raw PDF)."""
        self.ensure_one()
        if not self.host:
            raise UserError(_('Printer "%s" has no IP address configured.') % self.name)
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.sendall(data)
            sock.close()
            _logger.info('IAG Direct Print: sent %d bytes to %s:%s', len(data), self.host, self.port)
        except Exception as e:
            raise UserError(_('Failed to send print job to %s (%s:%s): %s') % (
                self.name, self.host, self.port, str(e)))

    def _send_cups(self, data: bytes, title: str = 'Odoo Print Job', copies: int = 1):
        """Send PDF bytes to a CUPS printer."""
        self.ensure_one()
        if not self.cups_name:
            raise UserError(_('Printer "%s" has no CUPS printer name configured.') % self.name)
        try:
            import cups
        except ImportError:
            raise UserError(_('pycups is required for CUPS printing. Install with: pip install pycups'))
        import tempfile, os
        try:
            conn = cups.Connection(host=self.cups_host or 'localhost', port=self.cups_port or 631)
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                f.write(data)
                tmp_path = f.name
            options = {}
            if copies > 1:
                options['copies'] = str(copies)
            if self.paper_format:
                options['media'] = self.paper_format
            job_id = conn.printFile(self.cups_name, tmp_path, title, options)
            os.unlink(tmp_path)
            _logger.info('IAG Direct Print: CUPS job %s submitted to %s', job_id, self.cups_name)
            return job_id
        except Exception as e:
            raise UserError(_('Failed to print via CUPS to %s: %s') % (self.cups_name, str(e)))

    def _send_ipp(self, data: bytes, title: str = 'Odoo Print Job', copies: int = 1):
        """Send PDF to an IPP printer using lp command (fallback)."""
        self.ensure_one()
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            uri = f'ipp://{self.host}:{self.port or 631}/ipp/print'
            cmd = ['lp', '-d', uri, '-t', title, '-n', str(copies), tmp_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise UserError(_('lp command failed: %s') % result.stderr)
            _logger.info('IAG Direct Print: IPP job submitted via lp to %s', uri)
        finally:
            os.unlink(tmp_path)

    def print_document(self, data: bytes, doc_type: str = 'pdf',
                       title: str = 'Odoo Print Job', copies: int = 1):
        """
        Main entry point. Routes to the correct print method based on printer_type.
        :param data: Raw bytes of the document (PDF or ZPL)
        :param doc_type: 'pdf' or 'zpl'
        :param title: Job title for log/CUPS
        :param copies: Number of copies
        """
        self.ensure_one()
        copies = copies or self.copies or 1

        if self.printer_type == 'zpl_raw':
            if doc_type != 'zpl':
                raise UserError(_('Printer "%s" is ZPL-only but document type is PDF.') % self.name)
            for _ in range(copies):
                self._send_raw(data)

        elif self.printer_type == 'pdf_raw':
            if doc_type != 'pdf':
                raise UserError(_('Printer "%s" is PDF raw but document type is ZPL.') % self.name)
            self._send_raw(data)

        elif self.printer_type == 'pdf_cups':
            if doc_type != 'pdf':
                raise UserError(_('Printer "%s" is a PDF/CUPS printer but document type is ZPL.') % self.name)
            self._send_cups(data, title=title, copies=copies)

        elif self.printer_type == 'ipp':
            if doc_type != 'pdf':
                raise UserError(_('Printer "%s" is an IPP printer but document type is ZPL.') % self.name)
            self._send_ipp(data, title=title, copies=copies)

    def action_test_print(self):
        """Print a quick test page."""
        self.ensure_one()
        if self.printer_type == 'zpl_raw':
            # Minimal ZPL test label
            zpl = (
                "^XA"
                "^FO50,50^A0N,40,40^FDIAGDirectPrint Test^FS"
                "^FO50,110^A0N,28,28^FD" + self.name + "^FS"
                "^FO50,150^A0N,22,22^FDOdoo 19 - IAG Performance^FS"
                "^XZ"
            )
            self.print_document(zpl.encode(), doc_type='zpl', title='Test Label')
        else:
            # Minimal single-page PDF via reportlab
            try:
                from reportlab.pdfgen import canvas
                from reportlab.lib.pagesizes import letter
                import io
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=letter)
                c.setFont('Helvetica-Bold', 24)
                c.drawString(72, 700, 'IAG Direct Print — Test Page')
                c.setFont('Helvetica', 14)
                c.drawString(72, 660, f'Printer: {self.name}')
                c.drawString(72, 640, f'Type: {self.printer_type}')
                c.drawString(72, 620, 'Odoo 19 — IAG Performance')
                c.save()
                self.print_document(buf.getvalue(), doc_type='pdf', title='Test Page')
            except ImportError:
                raise UserError(_('reportlab is required for PDF test pages. Install it with: pip install reportlab'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': f'Test job sent to {self.name}', 'type': 'success'},
        }
