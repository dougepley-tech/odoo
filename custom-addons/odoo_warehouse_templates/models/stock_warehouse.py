# -*- coding: utf-8 -*-
from odoo import api, fields, models


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    # --- PDF / document branding (aligned with base.document.layout / res.company) ---
    report_layout_id = fields.Many2one(
        'report.layout',
        string='Layout',
        help='Same layout presets as Settings → Configure your document layout (Light, Boxed, Bold, …).',
    )
    external_report_layout_id = fields.Many2one(
        'ir.ui.view',
        string='Document Template',
        help='Technical: QWeb view used for the PDF shell. Kept in sync with Layout.',
    )
    logo = fields.Binary(string='Report Logo', attachment=True)
    report_logo_max_height = fields.Integer(
        string='Logo max height (PDF)',
        default=0,
        help='Maximum height of the logo on PDFs (pixels). 0 uses the layout default. '
        'Typical “large header” logos use ~56–72; try 80–120 if your artwork reads small.',
    )
    font = fields.Selection(
        selection=[
            ('Lato', 'Lato'),
            ('Roboto', 'Roboto'),
            ('Open_Sans', 'Open Sans'),
            ('Montserrat', 'Montserrat'),
            ('Oswald', 'Oswald'),
            ('Raleway', 'Raleway'),
            ('Tajawal', 'Tajawal'),
            ('Fira_Mono', 'Fira Mono'),
        ],
        string='Font',
        help='If set, overrides the company font in reports for this warehouse.',
    )
    primary_color = fields.Char(string='Primary Color')
    secondary_color = fields.Char(string='Secondary Color')
    layout_background = fields.Selection(
        selection=[
            ('Blank', 'Blank'),
            ('Demo logo', 'Demo logo'),
            ('Custom', 'Custom'),
        ],
        string='Layout Background',
        required=False,
        help='If set, overrides the company layout background for this warehouse.',
    )
    layout_background_image = fields.Binary(string='Background Image', attachment=True)
    report_header = fields.Html(string='Document Tagline', translate=True)
    report_footer = fields.Html(string='Report Footer', translate=True)
    report_address = fields.Text(
        string='Address',
        translate=True,
        help='Multi-line address shown on PDFs (line breaks preserved). When set, this is used instead of the HTML address below.',
    )
    company_details = fields.Html(
        string='Address (HTML)',
        translate=True,
        help='Optional rich-text address; used on PDFs only if Address above is empty.',
    )
    paperformat_id = fields.Many2one(
        'report.paperformat',
        string='Paper format',
        help='If set, used when printing reports resolved to this warehouse.',
    )
    # Same optional fields as account’s extension of the document layout wizard (invoice-related).
    document_vat = fields.Char(
        string='Tax ID',
        help='Optional override shown on documents for this warehouse when your reports use it.',
    )
    document_account_number = fields.Char(
        string='Bank Account Number',
        help='Optional bank account reference for documents printed for this warehouse (e.g. invoice QR / payment).',
    )
    document_qr_code = fields.Boolean(
        string='QR Code',
        help='When set, signals invoice-style documents to allow QR usage for this warehouse (same idea as on company).',
    )

    # --- Mail templates ---
    sale_mail_template_quotation_id = fields.Many2one(
        'mail.template',
        string='Quotation Email',
        domain="[('model', '=', 'sale.order')]",
    )
    sale_mail_template_confirmation_id = fields.Many2one(
        'mail.template',
        string='Sales Confirmation Email',
        domain="[('model', '=', 'sale.order')]",
    )
    account_move_mail_template_invoice_id = fields.Many2one(
        'mail.template',
        string='Customer Invoice Email',
        domain="[('model', '=', 'account.move')]",
    )
    account_move_mail_template_credit_note_id = fields.Many2one(
        'mail.template',
        string='Credit Note Email',
        domain="[('model', '=', 'account.move')]",
    )
    stock_picking_mail_template_id = fields.Many2one(
        'mail.template',
        string='Delivery Confirmation Email',
        domain="[('model', '=', 'stock.picking')]",
        help='Overrides the company delivery confirmation template when set.',
    )

    # --- Optional PDF report actions ---
    invoice_report_id = fields.Many2one(
        'ir.actions.report',
        string='Invoice PDF Report',
        domain="[('model', '=', 'account.move')]",
    )
    delivery_report_id = fields.Many2one(
        'ir.actions.report',
        string='Delivery / Picking PDF Report',
        domain="[('model', '=', 'stock.picking')]",
    )

    @api.onchange('report_layout_id')
    def _onchange_report_layout_id(self):
        for wh in self:
            if wh.report_layout_id:
                wh.external_report_layout_id = wh.report_layout_id.view_id
            else:
                wh.external_report_layout_id = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_document_layout_sync(vals)
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        self._apply_document_layout_sync(vals)
        return super().write(vals)

    def _apply_document_layout_sync(self, vals):
        """Keep report_layout_id and external_report_layout_id aligned (same as base.document.layout)."""
        if 'report_layout_id' in vals:
            if vals.get('report_layout_id'):
                layout = self.env['report.layout'].browse(vals['report_layout_id'])
                vals['external_report_layout_id'] = layout.view_id.id
            else:
                vals['external_report_layout_id'] = False
        elif vals.get('external_report_layout_id'):
            layout = self.env['report.layout'].search(
                [('view_id', '=', vals['external_report_layout_id'])], limit=1
            )
            vals['report_layout_id'] = layout.id if layout else False
        elif 'external_report_layout_id' in vals and not vals.get('external_report_layout_id'):
            vals['report_layout_id'] = False

    def _has_any_branding(self):
        self.ensure_one()
        return bool(
            self.report_layout_id
            or self.external_report_layout_id
            or self.logo
            or self.font
            or self.primary_color
            or self.secondary_color
            or self.layout_background
            or self.layout_background_image
            or self.report_header
            or self.report_footer
            or self.report_address
            or self.company_details
        )
