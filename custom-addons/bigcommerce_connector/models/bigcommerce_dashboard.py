# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class BigCommerceDashboard(models.Model):
    _name = 'bigcommerce.dashboard'
    _description = 'BigCommerce Sync Dashboard'
    _rec_name = 'config_id'
    _order = 'config_id'

    config_id = fields.Many2one('bigcommerce.config', string='Store Configuration', required=True, ondelete='cascade')
    
    # Sync Status - Last Sync Times
    last_product_sync = fields.Datetime(string='Last Product Sync', related='config_id.last_product_sync', readonly=True)
    last_order_sync = fields.Datetime(string='Last Order Sync', related='config_id.last_order_sync', readonly=True)
    last_inventory_sync = fields.Datetime(string='Last Inventory Sync', related='config_id.last_inventory_sync', readonly=True)
    last_customer_sync = fields.Datetime(string='Last Customer Sync', related='config_id.last_customer_sync', readonly=True)
    last_fulfillment_sync = fields.Datetime(string='Last Fulfillment Sync', related='config_id.last_fulfillment_sync', readonly=True)
    
    # Last Sync Statistics - Products
    last_product_sync_total = fields.Integer(string='Total Items', related='config_id.last_product_sync_total', readonly=True)
    last_product_sync_updated = fields.Integer(string='Updated', related='config_id.last_product_sync_updated', readonly=True)
    last_product_sync_failed = fields.Integer(string='Failed', related='config_id.last_product_sync_failed', readonly=True)
    last_product_sync_warnings = fields.Integer(string='Warnings', related='config_id.last_product_sync_warnings', readonly=True)
    
    # Last Sync Statistics - Orders
    last_order_sync_total = fields.Integer(string='Total Items', related='config_id.last_order_sync_total', readonly=True)
    last_order_sync_updated = fields.Integer(string='Updated', related='config_id.last_order_sync_updated', readonly=True)
    last_order_sync_failed = fields.Integer(string='Failed', related='config_id.last_order_sync_failed', readonly=True)
    last_order_sync_warnings = fields.Integer(string='Warnings', related='config_id.last_order_sync_warnings', readonly=True)
    
    # Last Sync Statistics - Inventory
    last_inventory_sync_total = fields.Integer(string='Total Items', related='config_id.last_inventory_sync_total', readonly=True)
    last_inventory_sync_updated = fields.Integer(string='Updated', related='config_id.last_inventory_sync_updated', readonly=True)
    last_inventory_sync_failed = fields.Integer(string='Failed', related='config_id.last_inventory_sync_failed', readonly=True)
    last_inventory_sync_warnings = fields.Integer(string='Warnings', related='config_id.last_inventory_sync_warnings', readonly=True)
    
    # Last Sync Statistics - Customers
    last_customer_sync_total = fields.Integer(string='Total Items', related='config_id.last_customer_sync_total', readonly=True)
    last_customer_sync_updated = fields.Integer(string='Updated', related='config_id.last_customer_sync_updated', readonly=True)
    last_customer_sync_failed = fields.Integer(string='Failed', related='config_id.last_customer_sync_failed', readonly=True)
    last_customer_sync_warnings = fields.Integer(string='Warnings', related='config_id.last_customer_sync_warnings', readonly=True)
    
    # Last Sync Statistics - Fulfillments
    last_fulfillment_sync_total = fields.Integer(string='Total Items', related='config_id.last_fulfillment_sync_total', readonly=True)
    last_fulfillment_sync_updated = fields.Integer(string='Updated', related='config_id.last_fulfillment_sync_updated', readonly=True)
    last_fulfillment_sync_failed = fields.Integer(string='Failed', related='config_id.last_fulfillment_sync_failed', readonly=True)
    last_fulfillment_sync_warnings = fields.Integer(string='Warnings', related='config_id.last_fulfillment_sync_warnings', readonly=True)
    
    # Running Sync Operations
    running_sync_ids = fields.One2many('bigcommerce.sync.operation', compute='_compute_running_syncs', string='Running Syncs')
    has_running_syncs = fields.Boolean(string='Has Running Syncs', compute='_compute_running_syncs')
    running_sync_count = fields.Integer(string='Running Sync Count', compute='_compute_running_syncs')
    running_sync_display = fields.Html(string='Running Syncs Display', compute='_compute_running_syncs', sanitize=False)
    
    @api.depends('config_id')
    def _compute_running_syncs(self):
        """Get all currently running sync operations for this configuration
        
        Note: This computed field depends only on config_id, but it reads directly from the database
        to get the latest sync operation data. The field will be recomputed when:
        1. The dashboard record is invalidated and reloaded
        2. action_recompute_running_syncs() is called
        3. The config_id changes
        """
        for record in self:
            try:
                if record.config_id:
                    # Always get fresh data - search for running syncs without cache
                    # Use sudo to bypass any access restrictions and ensure we get all syncs
                    # Force fresh read by using SQL or bypassing ORM cache
                    try:
                        self.env.cr.execute("""
                            SELECT id FROM bigcommerce_sync_operation
                            WHERE config_id = %s AND state = 'running'
                            ORDER BY start_date DESC
                        """, (record.config_id.id,))
                        op_ids = [row[0] for row in self.env.cr.fetchall()]
                    except Exception as sql_error:
                        _logger.warning(f"Error fetching running syncs for config {record.config_id.id}: {str(sql_error)}")
                        op_ids = []
                    
                    if op_ids:
                        try:
                            # Browse fresh records and invalidate cache
                            # Use with_context to bypass prefetch and force fresh read
                            running_ops = self.env['bigcommerce.sync.operation'].sudo().with_context(prefetch_fields=False).browse(op_ids)
                            # Invalidate all fields to force fresh read
                            running_ops.invalidate_recordset()
                            
                            # Force read from database by accessing fields
                            # This ensures we get the latest progress data
                            for op in running_ops:
                                try:
                                    # Force fresh read by reading directly from database
                                    self.env.cr.execute("""
                                        SELECT state, processed_items, total_items, current_item, 
                                               items_synced, items_created, items_updated, items_failed
                                        FROM bigcommerce_sync_operation
                                        WHERE id = %s
                                    """, (op.id,))
                                    db_data = self.env.cr.dictfetchone()
                                    
                                    if db_data:
                                        # Access fields directly without write to avoid transaction issues
                                        # The ORM will cache these values for display
                                        pass
                                    
                                    # Access fields to trigger fresh read
                                    _ = op.state
                                    _ = op.processed_items
                                    _ = op.total_items
                                    _ = op.current_item
                                    _ = op.end_date
                                    # Recompute progress
                                    if hasattr(op, '_compute_progress_percentage'):
                                        op._compute_progress_percentage()
                                except Exception as op_error:
                                    _logger.warning(f"Error reading sync operation {op.id}: {str(op_error)}")
                                    continue
                        except Exception as browse_error:
                            _logger.warning(f"Error browsing sync operations: {str(browse_error)}")
                            running_ops = self.env['bigcommerce.sync.operation'].sudo()
                    else:
                        running_ops = self.env['bigcommerce.sync.operation'].sudo()
                    
                    record.running_sync_ids = running_ops
                    record.has_running_syncs = len(running_ops) > 0
                    record.running_sync_count = len(running_ops)
                    
                    # Build HTML display for kanban view - show ALL running syncs
                    if running_ops:
                        html_parts = []
                        for idx, op in enumerate(running_ops):
                            try:
                                sync_type = dict(op._fields['sync_type'].selection).get(op.sync_type, op.sync_type)
                                progress = op.progress_percentage if op.total_items > 0 else 0
                                start_time = op.start_date.strftime('%Y-%m-%d %H:%M:%S') if op.start_date else 'Unknown'
                                current_item_display = f'<small class="text-muted">Current: {op.current_item}</small>' if op.current_item else ''
                                
                                # Add a separator between multiple syncs (except for the first one)
                                separator = '<hr class="my-2" style="border-color: #dee2e6;">' if idx > 0 else ''
                                
                                html = f'''
                                {separator}
                                <div class="mb-2 p-2 border rounded" style="background-color: #1a1a1a !important; border-color: #444 !important;">
                                    <div class="d-flex justify-content-between align-items-center mb-1">
                                        <strong class="text-primary">{sync_type} Sync</strong>
                                        <span class="badge badge-info">{int(progress)}%</span>
                                    </div>
                                    <div class="mb-1">
                                        <small class="text-muted">
                                            <i class="fa fa-clock-o mr-1"></i>Started: {start_time}
                                        </small>
                                    </div>
                                    <div class="progress mb-1" style="height: 18px; background-color: #333;">
                                        <div class="progress-bar progress-bar-striped progress-bar-animated bg-primary" 
                                             style="width: {progress}%">
                                        </div>
                                    </div>
                                    <div class="d-flex justify-content-between align-items-center mb-2">
                                        <small class="text-muted">{op.processed_items} / {op.total_items} items</small>
                                        {current_item_display}
                                    </div>
                                    <div class="text-right">
                                        <button type="button" class="btn btn-sm btn-danger cancel-sync-btn" data-sync-op-id="{op.id}">
                                            <i class="fa fa-times mr-1"></i>Cancel
                                        </button>
                                    </div>
                                </div>
                                '''
                                html_parts.append(html)
                            except Exception as html_error:
                                _logger.warning(f"Error building HTML for sync operation {op.id}: {str(html_error)}")
                                continue
                        
                        record.running_sync_display = ''.join(html_parts) if html_parts else False
                    else:
                        record.running_sync_display = False
                else:
                    record.running_sync_ids = False
                    record.has_running_syncs = False
                    record.running_sync_count = 0
                    record.running_sync_display = False
            except Exception as e:
                _logger.error(f"Error in _compute_running_syncs for record {record.id}: {str(e)}", exc_info=True)
                # Set safe defaults on error
                record.running_sync_ids = False
                record.has_running_syncs = False
                record.running_sync_count = 0
                record.running_sync_display = False
    
    def action_recompute_running_syncs(self):
        """Force recomputation of running syncs - called from JavaScript auto-refresh"""
        self.ensure_one()
        # Force recomputation by invalidating the computed fields
        self.invalidate_recordset(['running_sync_ids', 'has_running_syncs', 'running_sync_count', 'running_sync_display'])
        # Trigger recomputation - this will read fresh data from database
        # The computed field reads directly from the database, so it will get the latest sync operation data
        self._compute_running_syncs()
        # Access the computed fields to ensure they're computed
        _ = self.running_sync_display
        _ = self.has_running_syncs
        _ = self.running_sync_count
        _ = self.running_sync_ids
        return True
    
    @api.model
    def _ensure_dashboard_records(self):
        """Ensure dashboard records exist for all active configurations"""
        try:
            # Use a flag to prevent recursion
            if hasattr(self.env, '_dashboard_checking'):
                return
            self.env._dashboard_checking = True
            
            try:
                active_configs = self.env['bigcommerce.config'].search([('active', '=', True)])
                if not active_configs:
                    _logger.debug("No active BigCommerce configurations found")
                    delattr(self.env, '_dashboard_checking')
                    return
                
                # Use SQL to check existing records to avoid recursion - use model's table name
                table_name = self._table
                self.env.cr.execute(f"""
                    SELECT config_id FROM {table_name}
                """)
                existing_config_ids = [row[0] for row in self.env.cr.fetchall() if row[0]]
                
                _logger.debug(f"Found {len(active_configs)} active configs, {len(existing_config_ids)} existing dashboard records")
                
                for config in active_configs:
                    if config.id not in existing_config_ids:
                        try:
                            # Use direct SQL to insert dashboard record
                            table_name = self._table
                            self.env.cr.execute(f"""
                                INSERT INTO {table_name} (config_id, create_uid, create_date, write_uid, write_date)
                                VALUES (%s, %s, NOW() AT TIME ZONE 'UTC', %s, NOW() AT TIME ZONE 'UTC')
                            """, (config.id, self.env.user.id, self.env.user.id))
                            _logger.info(f"Created dashboard record for config {config.id} ({config.name})")
                        except Exception as e:
                            _logger.warning(f"Could not create dashboard record for config {config.id}: {str(e)}")
                            # Continue processing other configs even if one fails
                
                # Remove dashboards for inactive or deleted configs
                if existing_config_ids:
                    for config_id in existing_config_ids:
                        try:
                            config = self.env['bigcommerce.config'].browse(config_id)
                            if not config.exists() or not config.active:
                                table_name = self._table
                                self.env.cr.execute(f"""
                                    DELETE FROM {table_name} WHERE config_id = %s
                                """, (config_id,))
                                _logger.info(f"Removed dashboard record for inactive config {config_id}")
                        except Exception as e:
                            _logger.warning(f"Could not remove dashboard record {config_id}: {str(e)}")
                            # Continue processing other configs even if one fails
                
                # Do NOT commit here - Odoo handles transaction management automatically
                # Manual commits can cause "transaction aborted" errors
                delattr(self.env, '_dashboard_checking')
                
            except Exception as inner_e:
                # If there's an error, make sure we clean up the flag
                if hasattr(self.env, '_dashboard_checking'):
                    delattr(self.env, '_dashboard_checking')
                _logger.error(f"Error in _ensure_dashboard_records: {str(inner_e)}", exc_info=True)
                # Don't re-raise - allow the search to continue even if dashboard setup fails
                
        except Exception as e:
            _logger.error(f"Outer error in _ensure_dashboard_records: {str(e)}", exc_info=True)
            if hasattr(self.env, '_dashboard_checking'):
                delattr(self.env, '_dashboard_checking')
    
    @api.model
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None):
        """Override search_read to ensure dashboard records exist for all active configs"""
        try:
            self._ensure_dashboard_records()
        except Exception as e:
            _logger.error(f"Error ensuring dashboard records in search_read: {str(e)}", exc_info=True)
        return super().search_read(domain=domain, fields=fields, offset=offset, limit=limit, order=order)
    
    @api.model
    def search(self, domain=None, offset=0, limit=None, order=None, access_rights_uid=None):
        """Override search to ensure dashboard records exist for all active configs"""
        try:
            self._ensure_dashboard_records()
        except Exception as e:
            _logger.error(f"Error ensuring dashboard records in search: {str(e)}", exc_info=True)
        return super().search(domain=domain, offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)
    
    @api.model
    def web_search_read(self, domain=None, offset=0, limit=None, order=None, **kwargs):
        """Override web_search_read to ensure dashboard records exist for all active configs"""
        try:
            self._ensure_dashboard_records()
        except Exception as e:
            _logger.error(f"Error ensuring dashboard records in web_search_read: {str(e)}", exc_info=True)
        return super().web_search_read(domain=domain, offset=offset, limit=limit, order=order, **kwargs)
    
    
    @api.model
    def action_initialize_dashboards(self):
        """Manually initialize dashboard records for all active configs"""
        try:
            self._ensure_dashboard_records()
            # Refresh the view
            return {
                'type': 'ir.actions.act_window',
                'name': 'BigCommerce Dashboard',
                'res_model': 'bigcommerce.dashboard',
                'view_mode': 'kanban,form',
                'target': 'current',
            }
        except Exception as e:
            _logger.error(f"Error initializing dashboards: {str(e)}", exc_info=True)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': f'Error initializing dashboards: {str(e)}',
                    'type': 'danger',
                    'sticky': True,
                }
            }
    
    def action_open_config(self):
        """Open the configuration for this dashboard"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.config_id.name,
            'res_model': 'bigcommerce.config',
            'res_id': self.config_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def action_view_running_syncs(self):
        """View all running sync operations for this configuration"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Running Syncs - {self.config_id.name}',
            'res_model': 'bigcommerce.sync.operation',
            'view_mode': 'list,form',
            'domain': [('config_id', '=', self.config_id.id), ('state', '=', 'running')],
            'context': {
                'default_config_id': self.config_id.id,
            },
        }

