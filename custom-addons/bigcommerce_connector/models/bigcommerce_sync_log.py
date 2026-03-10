# -*- coding: utf-8 -*-

from odoo import models, fields, api
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class BigCommerceSyncLog(models.Model):
    _name = 'bigcommerce.sync.log'
    _description = 'BigCommerce Sync Log'
    _order = 'log_date desc'
    _rec_name = 'message'

    sync_type = fields.Selection([
        ('product', 'Product'),
        ('order', 'Order'),
        ('inventory', 'Inventory'),
        ('customer', 'Customer'),
        ('fulfillment', 'Fulfillment'),
    ], string='Sync Type', required=True, index=True)
    
    sync_record_id = fields.Integer(string='Sync Record ID', help='ID of the sync record that generated this log')
    sync_operation_id = fields.Many2one('bigcommerce.sync.operation', string='Sync Operation', index=True, ondelete='cascade')
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', index=True)
    
    log_date = fields.Datetime(string='Log Date', default=fields.Datetime.now, required=True, index=True)
    log_level = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('debug', 'Debug'),
    ], string='Log Level', required=True, default='info', index=True)
    
    message = fields.Text(string='Message', required=True)
    product_id = fields.Integer(string='Product ID', help='BigCommerce Product ID if applicable')
    product_name = fields.Char(string='Product Name')
    error_details = fields.Text(string='Error Details', help='Full error traceback or additional details')
    request_url = fields.Char(string='Request URL')
    request_method = fields.Char(string='Request Method')
    response_status = fields.Integer(string='Response Status')
    response_data = fields.Text(string='Response Data')
    
    # Computed fields
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    @api.depends('log_date', 'log_level', 'message', 'sync_type')
    def _compute_display_name(self):
        for record in self:
            msg_preview = record.message[:50] + '...' if len(record.message) > 50 else record.message
            record.display_name = f"[{record.log_level.upper()}] {record.sync_type} - {msg_preview}"
    
    def action_clear_logs(self):
        """Clear old logs (older than 30 days)"""
        cutoff_date = fields.Datetime.now() - timedelta(days=30)
        old_logs = self.search([('log_date', '<', cutoff_date)])
        count = len(old_logs)
        old_logs.unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Logs Cleared',
                'message': f'Cleared {count} log entries older than 30 days.',
                'type': 'success',
                'sticky': False,
            }
        }

