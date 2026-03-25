# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TRIGGER_EVENTS = [
    ('stock.picking.do_transfer', 'After Delivery Order Validation'),
    ('stock.picking.do_unreserve', 'After Transfer Unreserve'),
    ('stock.picking.button_validate', 'After Any Transfer Validate'),
    ('sale.order.action_confirm', 'After Sales Order Confirmation'),
    ('account.move.action_post', 'After Invoice/Bill Confirmation'),
    ('purchase.order.button_confirm', 'After Purchase Order Confirmation'),
    ('mrp.production.button_mark_done', 'After Manufacturing Order Done'),
    ('stock.picking.action_put_in_pack', 'After Put in Pack'),
]


class IagPrintScenario(models.Model):
    _name = 'iag.print.scenario'
    _description = 'IAG Auto-Print Scenario'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    trigger_event = fields.Selection(
        TRIGGER_EVENTS,
        string='Trigger Event',
        required=True,
        help='The Odoo action that will automatically trigger this print job.',
    )

    # Report
    report_id = fields.Many2one(
        'ir.actions.report',
        string='Report',
        required=True,
        domain="[('report_type', 'in', ['qweb-pdf', 'qweb-text'])]",
    )
    report_xml_id = fields.Char(
        string='Report XML ID',
        compute='_compute_report_xml_id', store=True,
        help='Resolved external ID for the selected report.',
    )

    # Printer
    printer_id = fields.Many2one('iag.printer', string='Printer', required=True)
    copies = fields.Integer(default=1)

    # Optional domain filter (e.g. only for a specific warehouse)
    domain_filter = fields.Char(
        string='Domain Filter',
        help='Optional Odoo domain to restrict when this scenario fires. '
             'E.g. [("picking_type_code","=","outgoing")] to only fire on deliveries.',
    )

    # Stats
    run_count = fields.Integer(string='Times Triggered', default=0, readonly=True)
    last_run = fields.Datetime(string='Last Triggered', readonly=True)

    @api.depends('report_id')
    def _compute_report_xml_id(self):
        for rec in self:
            if rec.report_id:
                ext_id = rec.report_id.get_external_id().get(rec.report_id.id)
                rec.report_xml_id = ext_id or False
            else:
                rec.report_xml_id = False

    def _matches_record(self, record):
        """Check if the given record matches this scenario's domain filter."""
        self.ensure_one()
        if not self.domain_filter:
            return True
        try:
            domain = eval(self.domain_filter)  # safe: admin-configured
            return bool(record.filtered_domain(domain))
        except Exception as e:
            _logger.warning('IAG Direct Print: domain filter eval failed on scenario %s: %s', self.name, e)
            return False

    def execute_for_record(self, record):
        """Fire this scenario for a given record."""
        self.ensure_one()
        if not self._matches_record(record):
            return

        try:
            record.iag_direct_print(
                report_xml_id=self.report_xml_id,
                printer_id=self.printer_id.id,
                copies=self.copies,
            )
            self.sudo().write({
                'run_count': self.run_count + 1,
                'last_run': fields.Datetime.now(),
            })
            _logger.info('IAG Direct Print: scenario "%s" fired for %s #%s', self.name, record._name, record.id)
        except Exception as e:
            _logger.error('IAG Direct Print: scenario "%s" failed for %s #%s: %s',
                          self.name, record._name, record.id, str(e))

    @api.model
    def fire_scenarios(self, trigger_event: str, record):
        """
        Called from model overrides. Finds all active scenarios matching
        the trigger and executes them.
        """
        scenarios = self.search([
            ('trigger_event', '=', trigger_event),
            ('active', '=', True),
        ])
        # Ensure record has the mixin
        if not hasattr(record, 'iag_direct_print'):
            _logger.debug('IAG Direct Print: record %s does not have print mixin, skipping', record._name)
            return
        for scenario in scenarios:
            scenario.execute_for_record(record)
