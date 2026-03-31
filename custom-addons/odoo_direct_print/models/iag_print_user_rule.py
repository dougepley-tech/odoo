# -*- coding: utf-8 -*-
from odoo import models, fields, api


class IagPrintUserRule(models.Model):
    _name = 'iag.print.user.rule'
    _description = 'IAG User Printer Rule'
    _order = 'user_id, report_id'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade')
    report_id = fields.Many2one('ir.actions.report', string='Report', required=True)
    printer_id = fields.Many2one('iag.printer', string='Printer', required=True)
    copies = fields.Integer(string='Copies', default=1)

    _user_report_uniq = models.Constraint(
        'UNIQUE(user_id, report_id)',
        'A user can only have one default printer per report.',
    )

    @api.model
    def get_printer_for_user(self, user_id: int, report_xml_id: str):
        """
        Look up the default printer for a given user + report.
        Falls back to None if no rule exists.
        """
        report = self.env.ref(report_xml_id, raise_if_not_found=False)
        if not report:
            return None
        rule = self.search([
            ('user_id', '=', user_id),
            ('report_id', '=', report.id),
        ], limit=1)
        return rule.printer_id if rule else None
