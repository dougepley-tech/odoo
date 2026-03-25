# -*- coding: utf-8 -*-
from odoo import models, fields, api


class IagPrintJob(models.Model):
    _name = 'iag.print.job'
    _description = 'IAG Print Job Log'
    _order = 'create_date desc'
    _rec_name = 'name'

    name = fields.Char(string='Job', required=True)
    printer_id = fields.Many2one('iag.printer', string='Printer', ondelete='set null')
    report_xml_id = fields.Char(string='Report')
    res_model = fields.Char(string='Document Model')
    res_id = fields.Integer(string='Document ID')
    res_name = fields.Char(string='Document', compute='_compute_res_name', store=True)

    state = fields.Selection([
        ('queued', 'Queued'),
        ('done', 'Sent'),
        ('failed', 'Failed'),
    ], default='queued', string='Status')

    error_message = fields.Text(string='Error')
    size_bytes = fields.Integer(string='Size (bytes)')
    copies = fields.Integer(string='Copies', default=1)
    create_date = fields.Datetime(string='Time', readonly=True)

    @api.depends('res_model', 'res_id')
    def _compute_res_name(self):
        for rec in self:
            if rec.res_model and rec.res_id:
                try:
                    obj = self.env[rec.res_model].browse(rec.res_id)
                    rec.res_name = obj.display_name if obj.exists() else f'#{rec.res_id}'
                except Exception:
                    rec.res_name = f'#{rec.res_id}'
            else:
                rec.res_name = False
