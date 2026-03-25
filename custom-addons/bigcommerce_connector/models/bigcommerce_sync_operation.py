# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class BigCommerceSyncOperation(models.Model):
    _name = 'bigcommerce.sync.operation'
    _description = 'BigCommerce Sync Operation'
    _order = 'start_date desc'
    _rec_name = 'display_name'

    # Basic Information
    sync_type = fields.Selection([
        ('product', 'Product'),
        ('order', 'Order'),
        ('inventory', 'Inventory'),
        ('customer', 'Customer'),
        ('fulfillment', 'Fulfillment'),
    ], string='Sync Type', required=True, index=True)
    
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True, index=True)
    sync_direction = fields.Char(string='Sync Direction', help='Direction of sync (e.g., bc_to_odoo, odoo_to_bc)')
    
    # Timing
    start_date = fields.Datetime(string='Start Date', default=fields.Datetime.now, required=True, index=True)
    end_date = fields.Datetime(string='End Date')
    last_progress_date = fields.Datetime(
        string='Last Progress',
        help='Last time progress was updated. Used to detect syncs that stopped unexpectedly (timeout/crash).'
    )
    duration = fields.Float(string='Duration (seconds)', compute='_compute_duration', store=True)
    
    # Status
    state = fields.Selection([
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('completed_with_warnings', 'Completed with Warnings'),
        ('completed_with_errors', 'Completed with Errors'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='running', required=True, index=True)
    
    # Statistics
    total_items = fields.Integer(string='Total Items', default=0)
    processed_items = fields.Integer(string='Processed Items', default=0, help='Number of items processed so far')
    items_synced = fields.Integer(string='Items Synced', default=0)
    items_created = fields.Integer(string='Items Created', default=0)
    items_updated = fields.Integer(string='Items Updated', default=0)
    items_skipped = fields.Integer(string='Items Skipped', default=0)
    items_failed = fields.Integer(string='Items Failed', default=0)
    items_archived = fields.Integer(string='Items Archived', default=0,
                                    help='Number of products archived (deleted from BigCommerce, deactivated in Odoo)')
    error_count = fields.Integer(string='Errors', default=0)
    warning_count = fields.Integer(string='Warnings', default=0)
    
    # Progress tracking
    progress_percentage = fields.Float(string='Progress %', compute='_compute_progress_percentage', store=False)
    current_item = fields.Char(string='Current Item', help='Currently processing item name/description')
    
    # Logs
    log_ids = fields.One2many('bigcommerce.sync.log', 'sync_operation_id', string='Logs')
    log_count = fields.Integer(string='Log Count', compute='_compute_log_count')
    
    # Summary
    summary = fields.Text(string='Summary')
    error_summary = fields.Text(string='Error Summary', compute='_compute_error_summary')
    
    # Display
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    status_badge = fields.Char(string='Status Badge', compute='_compute_status_badge')
    
    @api.depends('start_date', 'end_date')
    def _compute_duration(self):
        for record in self:
            if record.start_date and record.end_date:
                delta = record.end_date - record.start_date
                record.duration = delta.total_seconds()
            else:
                record.duration = 0
    
    @api.depends('processed_items', 'total_items')
    def _compute_progress_percentage(self):
        for record in self:
            if record.total_items > 0:
                record.progress_percentage = (record.processed_items / record.total_items) * 100
            else:
                record.progress_percentage = 0
    
    @api.depends('log_ids')
    def _compute_log_count(self):
        for record in self:
            record.log_count = len(record.log_ids)
    
    @api.depends('sync_type', 'start_date', 'state', 'items_synced')
    def _compute_display_name(self):
        for record in self:
            sync_type_label = dict(record._fields['sync_type'].selection).get(record.sync_type, 'Unknown')
            date_str = record.start_date.strftime('%Y-%m-%d %H:%M:%S') if record.start_date else 'Unknown'
            record.display_name = f"{sync_type_label} Sync - {date_str} ({record.items_synced} items)"
    
    @api.depends('state', 'error_count', 'warning_count')
    def _compute_status_badge(self):
        for record in self:
            if record.state == 'cancelled':
                record.status_badge = 'Cancelled'
            elif record.state == 'failed':
                record.status_badge = 'Failed'
            elif record.state == 'completed_with_errors':
                record.status_badge = f'Errors ({record.error_count})'
            elif record.state == 'completed_with_warnings':
                record.status_badge = f'Warning ({record.warning_count})'
            elif record.state == 'completed':
                record.status_badge = 'Success'
            else:
                record.status_badge = 'Running'
    
    @api.depends('log_ids', 'log_ids.log_level', 'log_ids.message')
    def _compute_error_summary(self):
        for record in self:
            error_logs = record.log_ids.filtered(lambda l: l.log_level == 'error')
            if error_logs:
                messages = [log.message for log in error_logs[:5]]  # Show first 5 errors
                record.error_summary = '\n'.join(messages)
                if len(error_logs) > 5:
                    record.error_summary += f'\n... and {len(error_logs) - 5} more errors'
            else:
                record.error_summary = False
    
    def action_view_logs(self):
        """Open logs for this sync operation"""
        self.ensure_one()
        return {
            'name': f'Logs - {self.display_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'bigcommerce.sync.log',
            'view_mode': 'list,form',
            'domain': [('sync_operation_id', '=', self.id)],
            'context': {
                'default_sync_operation_id': self.id,
                'default_sync_type': self.sync_type,
                'default_config_id': self.config_id.id,
            },
        }
    
    def action_view_error_logs(self):
        """Open error logs for this sync operation"""
        self.ensure_one()
        return {
            'name': f'Error Logs - {self.display_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'bigcommerce.sync.log',
            'view_mode': 'list,form',
            'domain': [('sync_operation_id', '=', self.id), ('log_level', '=', 'error')],
            'context': {
                'default_sync_operation_id': self.id,
                'default_sync_type': self.sync_type,
                'default_config_id': self.config_id.id,
            },
        }
    
    def mark_completed(self):
        """Mark sync operation as completed"""
        for record in self:
            record.end_date = fields.Datetime.now()
            # Determine final state based on errors and warnings
            if record.error_count > 0:
                record.state = 'completed_with_errors'
            elif record.warning_count > 0:
                record.state = 'completed_with_warnings'
            else:
                record.state = 'completed'
    
    def mark_failed(self, error_message=None):
        """Mark sync operation as failed"""
        for record in self:
            record.end_date = fields.Datetime.now()
            record.state = 'failed'
            if error_message:
                record.summary = error_message
    
    def action_cancel(self):
        """Cancel a running sync operation"""
        for record in self:
            if record.state != 'running':
                raise UserError(f"Cannot cancel sync operation: it is not in 'running' state (current state: {record.state})")
            
            # Mark as cancelled - use sudo to ensure we can update even if in different transaction
            record.sudo().write({
                'state': 'cancelled',
                'end_date': fields.Datetime.now(),
                'current_item': 'Cancelled by user',
                'summary': f"Sync operation cancelled by user at {fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            
            # Commit immediately so the cancellation is visible to the running sync
            self.env.cr.commit()
            
            # Create a log entry for the cancellation
            self.env['bigcommerce.sync.log'].sudo().create({
                'sync_operation_id': record.id,
                'sync_type': record.sync_type,
                'config_id': record.config_id.id,
                'log_level': 'warning',
                'message': f"Sync operation cancelled by user. Processed {record.processed_items} of {record.total_items} items.",
                'log_date': fields.Datetime.now(),
            })
            
            # Commit the log entry as well
            self.env.cr.commit()
            
            _logger.info(f"Sync operation {record.id} ({record.sync_type}) cancelled by user - state committed to database")
        
        return True

    def _mark_stale_as_failed(self, reason):
        """Mark this sync operation as failed and update linked records (e.g. Product Sync form)."""
        self.ensure_one()
        now = fields.Datetime.now()
        self.sudo().write({
            'state': 'failed',
            'end_date': now,
            'current_item': 'Stopped unexpectedly',
            'summary': reason,
            'error_count': (self.error_count or 0) + 1,
        })
        self.env.cr.commit()

        # Create a log entry so it appears in Monitoring > Sync Operations > View Logs
        self.env['bigcommerce.sync.log'].sudo().create({
            'sync_operation_id': self.id,
            'sync_type': self.sync_type,
            'config_id': self.config_id.id,
            'log_level': 'error',
            'message': reason,
            'log_date': now,
            'error_details': 'The sync worker or task stopped without completing. This can happen due to server timeout, worker crash, or memory limit. Check server logs for details.',
        })
        self.env.cr.commit()

        # Update linked Product Sync record so the user sees the error on the sync form
        if self.sync_type == 'product':
            product_sync = self.env['bigcommerce.product.sync'].sudo().search([
                ('sync_operation_id', '=', self.id)
            ], limit=1)
            if product_sync:
                product_sync.write({
                    'state': 'error',
                    'error_message': reason,
                })
                self.env.cr.commit()

        _logger.warning(f"Marked stale sync operation {self.id} ({self.sync_type}) as failed: {reason}")

    @api.model
    def _cron_detect_stale_sync_operations(self):
        """Detect sync operations that have been 'running' with no progress and mark them as failed.

        When a sync runs in an HTTP request and the worker times out or crashes, the sync
        never reaches its exception handler, so the operation stays 'running' and the
        progress bar hangs. This cron runs periodically and marks such operations as
        failed so the user sees an error instead of a stuck progress bar.

        Stale = state is 'running' and no progress update for at least 30 minutes
        (or 30 minutes since start if last_progress_date is not set).
        """
        stale_minutes = 30
        cutoff = fields.Datetime.now() - timedelta(minutes=stale_minutes)

        # Find running syncs with no recent progress (stale = no activity for 30+ minutes)
        running = self.search([('state', '=', 'running')])
        stale = self.browse()
        for op in running:
            if op.last_progress_date:
                if op.last_progress_date < cutoff:
                    stale |= op
            elif op.start_date and op.start_date < cutoff:
                stale |= op

        for op in stale:
            reason = (
                "Sync stopped unexpectedly (no activity for over %d minutes). "
                "This may be due to a server timeout, worker crash, or memory limit. "
                "Check server logs and try running the sync again."
            ) % stale_minutes
            try:
                op._mark_stale_as_failed(reason)
            except Exception as e:
                _logger.exception("Failed to mark stale sync operation %s as failed: %s", op.id, e)
