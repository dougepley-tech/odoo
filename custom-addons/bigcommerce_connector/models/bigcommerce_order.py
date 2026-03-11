# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.fields import Domain
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import logging

_logger = logging.getLogger(__name__)

# BigCommerce customer group names that should be created as companies (is_company=True) in Odoo
WHOLESALE_COMPANY_GROUP_NAMES = ('Wholesale Jobber', 'Wholesale Silver', 'Wholesale Gold')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    bigcommerce_id = fields.Integer(string='BigCommerce Order ID', copy=False, index=True)
    bigcommerce_synced = fields.Boolean(string='Synced with BigCommerce', default=False)
    bigcommerce_last_sync = fields.Datetime(string='Last Sync with BigCommerce')
    bigcommerce_config_id = fields.Many2one('bigcommerce.config', string='BigCommerce Config')


class BigCommerceOrderSync(models.Model):
    _name = 'bigcommerce.order.sync'
    _description = 'BigCommerce Order Sync'
    _order = 'sync_date desc'

    name = fields.Char(string='Sync Name', required=True, default=lambda self: f"Order Sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='State', default='draft')
    
    orders_created = fields.Integer(string='Orders Created', default=0)
    orders_updated = fields.Integer(string='Orders Updated', default=0)
    orders_failed = fields.Integer(string='Orders Failed', default=0)
    error_message = fields.Text(string='Error Message')
    
    # Progress tracking
    total_items = fields.Integer(string='Total Items', default=0, help='Total number of items to process')
    processed_items = fields.Integer(string='Processed Items', default=0, help='Number of items processed so far')
    progress_percentage = fields.Float(string='Progress', compute='_compute_progress', store=False, help='Progress percentage')
    current_item = fields.Char(string='Current Item', help='Currently processing item')
    
    # Link to sync operation for dashboard tracking
    sync_operation_id = fields.Many2one('bigcommerce.sync.operation', string='Sync Operation', ondelete='set null')
    
    # Specific order sync (bypasses status and date filters)
    sync_specific_order = fields.Boolean(
        string='Sync specific order',
        default=False,
        help='When enabled, sync only the BigCommerce order with the given Order ID, regardless of status or date filters.',
    )
    bigcommerce_order_id = fields.Integer(
        string='BigCommerce Order ID',
        help='The BigCommerce order ID (order number) to sync. Used when "Sync specific order" is enabled.',
    )
    
    # Filters (ignored when Sync specific order is enabled)
    date_from = fields.Datetime(string='Date From', help='Sync orders from this date')
    date_to = fields.Datetime(string='Date To', help='Sync orders until this date')
    min_date_modified = fields.Datetime(string='Min Date Modified', help='Sync orders modified after this date')
    
    @api.depends('total_items', 'processed_items')
    def _compute_progress(self):
        """Compute progress percentage"""
        for record in self:
            if record.total_items > 0:
                record.progress_percentage = (record.processed_items / record.total_items) * 100
            else:
                record.progress_percentage = 0.0
    
    def _check_cancelled(self):
        """Check if the sync operation has been cancelled
        
        This method reads fresh data from the database to ensure we see
        cancellation even if the sync is running in a long transaction.
        """
        if self.sync_operation_id:
            op_id = self.sync_operation_id.id
            # Read fresh state directly from database using SQL to bypass all caching
            self.env.cr.execute(
                "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                (op_id,)
            )
            result = self.env.cr.fetchone()
            if result and result[0] == 'cancelled':
                _logger.info(f"Sync operation {op_id} has been cancelled - stopping sync")
                return True
        return False
    
    def _update_sync_operation(self):
        """Update the linked sync operation record with current progress"""
        if self.sync_operation_id:
            try:
                self.sync_operation_id.write({
                    'total_items': self.total_items,
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                    'items_created': self.orders_created,
                    'items_updated': self.orders_updated,
                    'items_failed': self.orders_failed,
                })
                # Also update the sync record itself to ensure UI sees progress
                self.write({
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                })
                self.env.cr.commit()
            except Exception as e:
                _logger.warning(f"Failed to update sync operation: {str(e)}")
    
    def action_sync_orders(self):
        """Sync orders from BigCommerce to Odoo"""
        self.ensure_one()
        if self.sync_specific_order:
            if not self.bigcommerce_order_id:
                raise UserError('Please enter the BigCommerce Order ID when "Sync specific order" is enabled.')
        
        # Create sync operation record for dashboard tracking
        sync_operation = self.env['bigcommerce.sync.operation'].create({
            'sync_type': 'order',
            'config_id': self.config_id.id,
            'sync_direction': 'bc_to_odoo',
            'state': 'running',
            'start_date': fields.Datetime.now(),
            'current_item': 'Initializing...',
        })
        self.sync_operation_id = sync_operation.id
        
        self.state = 'running'
        self.env.cr.commit()
        
        try:
            api = self.config_id.get_api_client()
            if self.sync_specific_order and self.bigcommerce_order_id:
                self._sync_specific_order_from_bigcommerce(api)
            else:
                self._sync_from_bigcommerce(api)
            
            self.state = 'done'
            total_items = self.orders_created + self.orders_updated + self.orders_failed
            warnings_count = self.env['bigcommerce.sync.log'].search_count([
                ('config_id', '=', self.config_id.id),
                ('sync_type', '=', 'order'),
                ('log_level', '=', 'WARNING'),
                ('log_date', '>=', self.config_id.last_order_sync or fields.Datetime.now() - timedelta(days=1))
            ])
            self.config_id.write({
                'last_order_sync_total': total_items,
                'last_order_sync_updated': self.orders_updated,
                'last_order_sync_failed': self.orders_failed,
                'last_order_sync_warnings': warnings_count,
            })
            
            # Update sync operation record
            if self.sync_operation_id:
                state = 'completed_with_warnings' if warnings_count > 0 else 'completed'
                self.sync_operation_id.write({
                    'state': state,
                    'end_date': fields.Datetime.now(),
                    'total_items': total_items,
                    'processed_items': total_items,
                    'items_synced': self.orders_created + self.orders_updated,
                    'items_created': self.orders_created,
                    'items_updated': self.orders_updated,
                    'items_failed': self.orders_failed,
                    'error_count': self.orders_failed,
                    'warning_count': warnings_count,
                })
            
            if self.orders_failed > 0 and self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Order',
                        error_message=f"Sync completed with {self.orders_failed} failed order(s).",
                        created=self.orders_created,
                        updated=self.orders_updated,
                        failed=self.orders_failed,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send order sync failure notification: %s", mail_e)
            
        except UserError as e:
            # UserError from API client already has detailed message
            self.state = 'error'
            self.error_message = str(e)
            
            # Update sync operation record on error
            if self.sync_operation_id:
                self.sync_operation_id.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_count': self.orders_failed + 1,
                })
            
            _logger.error(f"Order sync error: {str(e)}")
            raise
        except Exception as e:
            # Other unexpected errors
            self.state = 'error'
            error_msg = f"Order sync failed: {str(e)}"
            self.error_message = error_msg
            if self.config_id:
                try:
                    import traceback
                    self.config_id._send_sync_failure_email(
                        'Order',
                        error_message=error_msg,
                        details=traceback.format_exc(),
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send order sync failure notification: %s", mail_e)
            
            # Update sync operation record on error (only if not cancelled)
            if self.sync_operation_id and self.sync_operation_id.state != 'cancelled':
                self.sync_operation_id.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_count': self.orders_failed + 1,
                })
            
            _logger.error(f"Order sync error: {str(e)}", exc_info=True)
            raise UserError(error_msg)
    
    def action_cancel_sync(self):
        """Cancel the running sync operation"""
        self.ensure_one()
        if not self.sync_operation_id:
            raise UserError("No sync operation linked to this sync record")
        if self.sync_operation_id.state != 'running':
            raise UserError(f"Cannot cancel sync: sync operation is not in 'running' state (current state: {self.sync_operation_id.state})")
        
        # Cancel the sync operation
        self.sync_operation_id.action_cancel()
        
        # Immediately update this sync record's state
        self.write({
            'state': 'error',
            'error_message': 'Sync operation was cancelled by user',
        })
        self.env.cr.commit()
        
        return True
    
    def _sync_specific_order_from_bigcommerce(self, api):
        """Sync a single order from BigCommerce by ID, regardless of status or date filters."""
        self.ensure_one()
        order_id = self.bigcommerce_order_id
        self.total_items = 1
        self.current_item = f"Fetching Order #{order_id}..."
        self._update_sync_operation()
        self.env.cr.commit()
        bc_order = api.get_order(order_id)
        if not bc_order:
            raise UserError(f"BigCommerce order #{order_id} not found.")
        self.current_item = f"Processing Order #{order_id}..."
        self._update_sync_operation()
        self.env.cr.commit()
        try:
            result = self._create_or_update_order_from_bc(api, bc_order)
            if result == 'created':
                self.orders_created += 1
            elif result == 'updated':
                self.orders_updated += 1
            elif result == 'skipped':
                pass
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error syncing order {order_id}: {str(e)}", exc_info=True)
            self._create_log(
                'error',
                f"Error syncing order: {str(e)}",
                order_id=order_id,
                order_name=f"Order #{order_id}",
                error_details=error_trace,
            )
            self.orders_failed += 1
        self.processed_items = 1
        self._update_sync_operation()
    
    def _sync_from_bigcommerce(self, api):
        """Sync orders from BigCommerce to Odoo"""
        page = 1
        limit = 250
        filters = {}
        total_processed = 0
        
        if self.min_date_modified:
            filters['min_date_modified'] = self.min_date_modified.strftime('%Y-%m-%d %H:%M:%S')
        
        if self.date_from:
            filters['min_date_created'] = self.date_from.strftime('%Y-%m-%d %H:%M:%S')
        
        if self.date_to:
            filters['max_date_created'] = self.date_to.strftime('%Y-%m-%d %H:%M:%S')
        
        # Get initial estimate
        try:
            first_page = api.get_orders(page=1, limit=limit, **filters)
            if first_page:
                self.total_items = len(first_page)
                self.current_item = "Initializing..."
                self._update_sync_operation()
                self.env.cr.commit()
        except:
            pass
        
        while True:
            try:
                orders = api.get_orders(page=page, limit=limit, **filters)
                
                # Handle 404 - no orders found (normal case)
                if orders is None:
                    if page == 1:
                        # First page returned None - could be 404 (no orders) or 403 (permission denied)
                        # The API client should have raised UserError for 403, so this is likely 404
                        _logger.info("No orders found in BigCommerce (404)")
                        break
                    else:
                        # Subsequent pages returning None means we've reached the end
                        break
                
                if not orders:
                    break
                
                if page > 1:
                    self.total_items += len(orders)
                    self.env.cr.commit()
                
                for idx, bc_order in enumerate(orders, 1):
                    order_id = bc_order.get('id')
                    order_number = bc_order.get('id', 'Unknown')
                    
                    # Filter by order status if configured
                    bc_status_id = bc_order.get('status_id')
                    if bc_status_id is not None:
                        # Check if status filtering is enabled and if this status should be synced
                        config = self.config_id
                        if config.sync_order_status_ids:
                            allowed_status_ids = config.sync_order_status_ids.mapped('bc_status_id')
                            if bc_status_id not in allowed_status_ids:
                                _logger.debug(f"Skipping order {order_id} with status {bc_status_id} (not in allowed statuses)")
                                continue
                    
                    # Check if sync has been cancelled
                    if self._check_cancelled():
                        _logger.info("Order sync cancelled by user")
                        raise UserError("Sync operation was cancelled by user")
                    
                    # Update progress
                    self.current_item = f"Processing Order #{order_number}..."
                    self.processed_items = total_processed + idx
                    self._update_sync_operation()
                    self.env.cr.commit()
                    
                    try:
                        result = self._create_or_update_order_from_bc(api, bc_order)
                        if result == 'created':
                            self.orders_created += 1
                        elif result == 'updated':
                            self.orders_updated += 1
                        elif result == 'skipped':
                            # Order already exists, skip silently
                            pass
                    except Exception as e:
                        import traceback
                        error_trace = traceback.format_exc()
                        _logger.error(f"Error syncing order {bc_order.get('id')}: {str(e)}", exc_info=True)
                        self._create_log('error', f"Error syncing order: {str(e)}", 
                                        order_id=order_id, order_name=f"Order #{order_number}",
                                        error_details=error_trace)
                        self.orders_failed += 1
                
                total_processed += len(orders)
                
                if len(orders) < limit:
                    break
                page += 1
                
            except UserError as e:
                # Re-raise UserError (like 403 permission denied) to stop sync
                _logger.error(f"Error fetching orders page {page}: {str(e)}")
                raise
            except Exception as e:
                error_msg = f"Error fetching orders page {page}: {str(e)}"
                _logger.error(error_msg)
                import traceback
                error_trace = traceback.format_exc()
                self._create_log('error', error_msg, error_details=error_trace)
                break
    
    def _create_log(self, log_level, message, order_id=None, order_name=None, error_details=None):
        """Create a sync log entry"""
        try:
            log_vals = {
                'sync_type': 'order',
                'sync_record_id': self.id,
                'sync_operation_id': self.sync_operation_id.id if self.sync_operation_id else False,
                'config_id': self.config_id.id,
                'log_level': log_level,
                'message': message,
                'product_id': order_id,  # Using product_id field to store order ID
                'product_name': order_name,  # Using product_name field to store order name
                'error_details': error_details,
            }
            return self.env['bigcommerce.sync.log'].sudo().create(log_vals)
        except Exception as e:
            _logger.warning(f"Failed to create sync log entry: {str(e)}")
            return False
    
    def _parse_bc_datetime(self, date_string):
        """Parse BigCommerce datetime string (RFC 2822 format) to Odoo datetime format string"""
        if not date_string:
            return fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            # Try parsing RFC 2822 format (e.g., 'Fri, 15 Jan 2021 16:48:36 +0000')
            dt = parsedate_to_datetime(date_string)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError) as e:
            # If parsing fails, try Odoo's standard format
            try:
                dt = datetime.strptime(date_string, '%Y-%m-%d %H:%M:%S')
                return date_string
            except (ValueError, TypeError):
                warning_msg = f"Could not parse date string '{date_string}', using current time"
                _logger.warning(warning_msg)
                # Note: This is a minor warning, so we don't log it to sync log to avoid clutter
                # Only log if it's critical for order processing
                return fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    def _update_existing_order(self, order, api, bc_order):
        """Update an existing order with new status and addresses from BigCommerce.
        Refreshes invoice and shipping addresses so dropship/different-ship orders get the correct delivery address.
        Returns: 'updated' or 'skipped'
        """
        bc_order_id = bc_order.get('id')
        bc_status_id = bc_order.get('status_id')
        
        # Refresh billing and shipping addresses (e.g. first sync may have missed shipping; dropship has different ship-to)
        partner = order.partner_id
        billing_address = self._extract_address(bc_order.get('billing_address', {}))
        shipping_addresses = bc_order.get('shipping_addresses', [])
        if shipping_addresses and isinstance(shipping_addresses, list) and len(shipping_addresses) > 0:
            shipping_address = self._extract_address(shipping_addresses[0])
        else:
            try:
                api_addresses = api.get_order_shipping_addresses(bc_order_id) or []
                if api_addresses and isinstance(api_addresses, list) and len(api_addresses) > 0:
                    shipping_address = self._extract_address(api_addresses[0])
                else:
                    shipping_address = {}
            except Exception as e:
                _logger.debug("Could not fetch shipping addresses for order %s on update: %s", bc_order_id, e)
                shipping_address = {}
        # Use company (or same-address partner) for invoice; only create invoice sub-contact when needed
        invoice_partner = partner
        if billing_address and not self._should_use_partner_for_invoice(billing_address, partner):
            invoice_partner = self._get_or_create_address_partner(partner, billing_address, 'invoice')
        shipping_partner = partner
        if shipping_address and not self._address_equals_partner(shipping_address, partner):
            shipping_partner = self._get_or_create_address_partner(partner, shipping_address, 'delivery')
        
        write_vals = {
            'bigcommerce_last_sync': fields.Datetime.now(),
            'fiscal_position_id': False,
            'partner_invoice_id': invoice_partner.id,
            'partner_shipping_id': shipping_partner.id,
        }
        
        # Map BigCommerce status to Odoo state
        odoo_state = 'draft'
        if bc_status_id is not None:
            status_mapping = self.env['bigcommerce.order.status'].search([('bc_status_id', '=', bc_status_id)], limit=1)
            if status_mapping:
                odoo_state = status_mapping.odoo_state
        
        order.write(write_vals)
        
        # Handle state changes
        if odoo_state == 'cancel' and order.state != 'cancel':
            try:
                if order.state == 'draft':
                    order.with_user(1).sudo().action_cancel()
                    _logger.info(f"Cancelled order {order.name} based on BigCommerce status update")
                else:
                    order.with_user(1).sudo()._action_cancel()
                    _logger.info(f"Force cancelled order {order.name} based on BigCommerce status update")
            except Exception as e:
                _logger.warning(f"Could not cancel order {order.name}: {str(e)}")
        elif odoo_state in ('sale', 'done') and order.state == 'draft':
            try:
                order.with_user(1).sudo().action_confirm()
                _logger.info(f"Confirmed order {order.name} based on BigCommerce status update")
            except Exception as e:
                _logger.warning(f"Could not confirm order {order.name}: {str(e)}")
        
        _logger.info(f"Updated existing order {order.name} (BC ID: {bc_order_id})")
        return 'updated'
    
    def _create_or_update_order_from_bc(self, api, bc_order):
        """Create or update Odoo sale order from BigCommerce order
        Returns: 'created', 'updated', or 'skipped'
        """
        order_obj = self.env['sale.order']
        bc_order_id = bc_order.get('id')
        
        # Use database-level advisory lock to prevent concurrent creation of the same order
        # This ensures only one process can create an order with this BC ID at a time
        try:
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (hash(f"bc_order_{bc_order_id}") % (2**31),))
        except Exception as lock_err:
            _logger.warning(f"Could not acquire advisory lock for order {bc_order_id}: {lock_err}")
        
        # Search for existing order by BigCommerce ID (after acquiring lock)
        existing = order_obj.search([('bigcommerce_id', '=', bc_order_id)], limit=1)
        
        if existing:
            _logger.info(f"Order {bc_order_id} already exists in Odoo (ID: {existing.id}), updating instead of creating")
            # Update the existing order status if needed
            return self._update_existing_order(existing, api, bc_order)
        
        # Get customer
        customer_id = bc_order.get('customer_id', 0)
        try:
            partner = self._get_or_create_customer(api, customer_id, bc_order)
            if not partner:
                raise ValueError(f"Could not get or create customer for customer_id={customer_id}")
        except Exception as e:
            _logger.error(f"Error getting/creating customer for order {bc_order_id}: {str(e)}")
            self._create_log('error', f"Error getting/creating customer: {str(e)}", 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                          error_details=str(e))
            raise
        
        # Get order products
        try:
            order_products = api.get_order_products(bc_order.get('id'))
            if order_products is None:
                order_products = []
                warning_msg = f"Order {bc_order_id} returned None for products"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, 
                              order_id=bc_order_id, order_name=f"Order #{bc_order_id}")
            elif not isinstance(order_products, list):
                warning_msg = f"Order {bc_order_id} products is not a list: {type(order_products)}"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, 
                              order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                              error_details=f"Expected list, got {type(order_products)}")
                order_products = []
            if not order_products:
                warning_msg = f"Order {bc_order_id} has no products"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, 
                              order_id=bc_order_id, order_name=f"Order #{bc_order_id}")
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error fetching products for order {bc_order_id}: {str(e)}", exc_info=True)
            self._create_log('error', f"Error fetching order products: {str(e)}", 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                          error_details=error_trace)
            raise
        
        # Get billing and shipping addresses
        billing_address = self._extract_address(bc_order.get('billing_address', {}))
        
        # Shipping address: use inline payload first; fallback to API for dropship/different-ship orders
        shipping_addresses = bc_order.get('shipping_addresses', [])
        if shipping_addresses and isinstance(shipping_addresses, list) and len(shipping_addresses) > 0:
            shipping_address = self._extract_address(shipping_addresses[0])
        else:
            # List endpoint often omits shipping_addresses; fetch so delivery address is correct (e.g. dropship)
            try:
                api_addresses = api.get_order_shipping_addresses(bc_order_id) or []
                if api_addresses and isinstance(api_addresses, list) and len(api_addresses) > 0:
                    shipping_address = self._extract_address(api_addresses[0])
                else:
                    shipping_address = {}
            except Exception as e:
                _logger.debug("Could not fetch shipping addresses for order %s: %s", bc_order_id, e)
                shipping_address = {}
        
        # Use company (or same-address partner) for invoice; only create invoice sub-contact when needed
        invoice_partner = partner
        if billing_address and not self._should_use_partner_for_invoice(billing_address, partner):
            invoice_partner = self._get_or_create_address_partner(partner, billing_address, 'invoice')
        
        # Create or get shipping partner address only if different from customer address
        shipping_partner = partner
        if shipping_address and not self._address_equals_partner(shipping_address, partner):
            shipping_partner = self._get_or_create_address_partner(partner, shipping_address, 'delivery')
        
        # Map BigCommerce status to Odoo state
        bc_status_id = bc_order.get('status_id')
        odoo_state = 'draft'  # Default state
        if bc_status_id is not None and not self.config_id.import_order_without_status:
            status_mapping = self.env['bigcommerce.order.status'].search([('bc_status_id', '=', bc_status_id)], limit=1)
            if status_mapping:
                odoo_state = status_mapping.odoo_state
        
        # Prepare order data
        date_created = self._parse_bc_datetime(bc_order.get('date_created'))
        config = self.config_id
        
        # Set order name with prefix if configured
        order_name = None
        if config.order_number_prefix:
            # Use prefix + BigCommerce order ID
            order_name = f"{config.order_number_prefix}{bc_order_id}"
        
        # Get company from config or current user's company
        company = config.company_id or self.env.company
        
        order_vals = {
            'partner_id': partner.id,
            'partner_invoice_id': invoice_partner.id,
            'partner_shipping_id': shipping_partner.id,
            'date_order': date_created,
            'bigcommerce_id': bc_order.get('id'),
            'bigcommerce_synced': True,
            'bigcommerce_last_sync': fields.Datetime.now(),
            'bigcommerce_config_id': config.id,
            'client_order_ref': self._get_order_reference(bc_order),
            'note': self._get_order_notes(bc_order),
            'user_id': False,  # Ensure salesperson is not set
            'company_id': company.id,
            'fiscal_position_id': False,  # Clear fiscal position to prevent Avalara from applying taxes (taxes already calculated in BigCommerce)
        }
        
        # Set order name with prefix if configured (try to set in create, will override after if needed)
        if order_name:
            order_vals['name'] = order_name
        
        # Set sales team if configured
        if config.default_sales_team_id:
            order_vals['team_id'] = config.default_sales_team_id.id
        
        # Set delivery warehouse if configured
        if config.order_warehouse_id:
            order_vals['warehouse_id'] = config.order_warehouse_id.id
        
        # Resolve BC shipping method name early so we can choose Shippo Wholesale when method ends with *
        # Prefer shipping_addresses API so we get the exact method string (e.g. "UPS Ground*") that BC stored
        bc_shipping_method_name = self._get_bc_shipping_method_name(bc_order)
        if bc_shipping_method_name == 'Shipping' and api and bc_order.get('id'):
            try:
                full = api.get_order(bc_order['id'])
                if full and isinstance(full, dict):
                    bc_shipping_method_name = self._get_bc_shipping_method_name(full)
            except Exception:
                pass
        if api and bc_order.get('id'):
            from_api = self._get_bc_shipping_method_name_from_api(api, bc_order['id'])
            if from_api and from_api != 'Shipping':
                bc_shipping_method_name = from_api
        bc_shipping_method_name = (bc_shipping_method_name or 'Shipping').strip()
        # Use wholesale carrier when BC method ends with * (or " *") and config has a wholesale delivery method
        is_wholesale_method = bc_shipping_method_name.endswith('*') or bc_shipping_method_name.rstrip().endswith('*')
        effective_carrier = None
        if is_wholesale_method and config.delivery_carrier_wholesale_id:
            effective_carrier = config.delivery_carrier_wholesale_id
        elif config.delivery_carrier_id:
            effective_carrier = config.delivery_carrier_id
        if effective_carrier and 'carrier_id' in order_obj._fields:
            order_vals['carrier_id'] = effective_carrier.id
        
        # Set delivery block reason to "BigCommerce Order Review" for all BigCommerce orders
        try:
            # Check if the sale.delivery.block.reason model exists
            if 'sale.delivery.block.reason' in self.env:
                delivery_block = self.env['sale.delivery.block.reason'].sudo().search([
                    ('name', '=', 'BigCommerce Order Review')
                ], limit=1)
                if not delivery_block:
                    # Create the delivery block reason if it doesn't exist
                    delivery_block = self.env['sale.delivery.block.reason'].sudo().create({
                        'name': 'BigCommerce Order Review',
                    })
                if delivery_block:
                    order_vals['delivery_block_id'] = delivery_block.id
                    _logger.info(f"Setting delivery block reason to 'BigCommerce Order Review' (ID: {delivery_block.id}) for order {bc_order_id}")
            else:
                _logger.warning(f"Model 'sale.delivery.block.reason' not found - delivery block module may not be installed")
        except Exception as e:
            _logger.warning(f"Error setting delivery block reason for order {bc_order_id}: {str(e)}")
        
        # Create order lines
        order_lines = []
        subtotal = 0.0
        
        if not order_products:
            _logger.warning(f"Order {bc_order_id} has no products, creating empty order")
            self._create_log('warning', f"Order has no products", 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}")
        
        for bc_product in order_products:
            try:
                product_template = False
                product_name = bc_product.get('name', 'Unknown')
                
                # Try to find product by multiple methods
                # 1. Try by SKU first (most reliable)
                sku = bc_product.get('sku') or bc_product.get('product_sku') or bc_product.get('sku_code')
                if sku:
                    product_template = self.env['product.template'].search([
                        ('product_variant_ids.default_code', '=', sku)
                    ], limit=1)
                    if product_template:
                        _logger.debug(f"Order {bc_order_id}: Found product by SKU '{sku}'")
                
                # 2. Try by BigCommerce product ID (if not found by SKU)
                if not product_template:
                    bc_product_id = bc_product.get('product_id')
                    if bc_product_id:
                        product_template = self._get_product_by_bc_id(bc_product_id)
                        if product_template:
                            _logger.debug(f"Order {bc_order_id}: Found product by BigCommerce ID {bc_product_id}")
                
                # 3. If still not found, log all available fields for debugging
                if not product_template:
                    available_fields = {k: v for k, v in bc_product.items() if k in ['id', 'product_id', 'order_product_id', 'sku', 'product_sku', 'sku_code', 'name', 'variant_id', 'product_variant_id']}
                    error_msg = f"Product not found in Odoo: {product_name}"
                    _logger.error(f"Order {bc_order_id}: {error_msg}. Available product fields: {available_fields}")
                    self._create_log('error', error_msg, 
                                  order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                                  error_details=f"Product Name: {product_name}, Available fields: {available_fields}, Tried SKU: {sku}, Tried Product ID: {bc_product.get('product_id')}")
                    continue
                
                # Try to find the specific variant from BigCommerce
                product = False
                bc_variant_id = bc_product.get('variant_id') or bc_product.get('product_variant_id')
                
                if bc_variant_id:
                    # Try to find variant by BigCommerce variant ID
                    # First try bigcommerce_variant_id field (if it exists)
                    product = self.env['product.product'].search([
                        ('product_tmpl_id', '=', product_template.id),
                        ('bigcommerce_variant_id', '=', bc_variant_id)
                    ], limit=1)
                    # If not found, try bigcommerce_id field (some setups might use this for variants)
                    if not product:
                        product = self.env['product.product'].search([
                            ('product_tmpl_id', '=', product_template.id),
                            ('bigcommerce_id', '=', bc_variant_id)
                        ], limit=1)
                    if product:
                        _logger.debug(f"Order {bc_order_id}: Found variant by BigCommerce variant ID {bc_variant_id}")
                
                # If variant not found by ID, try by SKU (variant-specific SKU)
                if not product and sku:
                    product = self.env['product.product'].search([
                        ('product_tmpl_id', '=', product_template.id),
                        ('default_code', '=', sku)
                    ], limit=1)
                    if product:
                        _logger.debug(f"Order {bc_order_id}: Found variant by SKU '{sku}'")
                
                # Fallback to first variant if no specific variant found
                if not product:
                    product = product_template.product_variant_ids[0] if product_template.product_variant_ids else False
                    if product:
                        warning_msg = (
                            f"Could not find specific variant (variant_id={bc_variant_id}, sku={sku}), "
                            f"using first variant: {product.name}"
                        )
                        _logger.warning(f"Order {bc_order_id}: {warning_msg}")
                        self._create_log('warning', warning_msg, 
                                      order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                                      error_details=f"Variant ID: {bc_variant_id}, SKU: {sku}, Using: {product.name}")
                
                if not product:
                    # Template exists but has no variants - skip and log error
                    error_msg = f"Product template found but has no variants: {product_template.name} (BigCommerce ID: {bc_product.get('product_id')})"
                    _logger.error(f"Order {bc_order_id}: {error_msg}")
                    self._create_log('error', error_msg, 
                                  order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                                  error_details=f"Product Template: {product_template.name}, BigCommerce Product ID: {bc_product.get('product_id')}")
                    continue
                
                qty = float(bc_product.get('quantity', 1))
                # Use base_price (or price_ex_tax as fallback) so tax is calculated separately
                # base_price is the unit price before any discounts or tax
                # price_ex_tax is the final unit price after discounts but before tax
                price = float(bc_product.get('base_price') or bc_product.get('price_ex_tax', 0))
                if not price:
                    # Final fallback if neither field is available
                    price = float(bc_product.get('price_inc_tax', 0))
                    _logger.warning(f"Using price_inc_tax for order {bc_order_id} product {bc_product.get('name')} - base_price and price_ex_tax not available")
                subtotal += price * qty
                
                # Get product UOM (unit of measure) - default to product's UOM or Units
                product_uom = product.uom_id if product.uom_id else self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
                if not product_uom:
                    # Fallback: search for Unit UOM
                    product_uom = self.env['uom.uom'].search([('name', '=', 'Units')], limit=1)
                    if not product_uom:
                        # Last resort: get first UOM
                        product_uom = self.env['uom.uom'].search([], limit=1)
                
                order_lines.append((0, 0, {
                    'product_id': product.id,
                    'name': bc_product.get('name', product.name),
                    'product_uom_qty': qty,
                    'product_uom_id': product_uom.id if product_uom else False,
                    'price_unit': price,
                    'discount': 0,  # Prevent Odoo from applying pricelist discounts - BigCommerce price is already final
                }))
            except Exception as e:
                _logger.error(f"Error processing product for order {bc_order_id}: {str(e)}")
                self._create_log('error', f"Error processing order product: {str(e)}", 
                              order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                              error_details=str(e))
                # Continue with other products rather than failing the entire order
                continue
        
        if not order_lines:
            error_msg = f"Order {bc_order_id} has no valid order lines to create. All products in the order were not found in Odoo."
            _logger.error(error_msg)
            self._create_log('error', error_msg, 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                          error_details="No products from BigCommerce order were found in Odoo. Products must exist in Odoo before importing orders.")
            raise ValueError(error_msg)
        
        order_vals['order_line'] = order_lines
        
        # Handle tax
        tax_amount = float(bc_order.get('total_tax', 0))
        if tax_amount > 0:
            tax_line = self._create_tax_line(tax_amount, bc_order)
            if tax_line:
                order_vals['order_line'].append(tax_line)
        
        # Handle shipping: add a line when there is a cost or when BC has a shipping method (e.g. free "Ground Shipping")
        shipping_cost = float(bc_order.get('shipping_cost_ex_tax', 0))
        if not shipping_cost:
            # Fallback if ex-tax amount not available
            shipping_cost = float(bc_order.get('shipping_cost_inc_tax', 0))
            if shipping_cost:
                _logger.warning(f"Using shipping_cost_inc_tax for order {bc_order_id} - shipping_cost_ex_tax not available")
        has_shipping_cost = shipping_cost > 0
        has_shipping_method = False
        if not has_shipping_cost and api and bc_order.get('id'):
            # Free shipping: still add a line if BC has a method (e.g. "Ground Shipping") so it shows in Odoo
            method_name = self._get_bc_shipping_method_name(bc_order)
            if method_name == 'Shipping':
                try:
                    full = api.get_order(bc_order['id'])
                    if full and isinstance(full, dict):
                        method_name = self._get_bc_shipping_method_name(full)
                except Exception:
                    pass
            if method_name == 'Shipping':
                method_name = self._get_bc_shipping_method_name_from_api(api, bc_order['id'])
            has_shipping_method = (method_name or 'Shipping').strip() != 'Shipping'
        if has_shipping_cost or has_shipping_method:
            shipping_line = self._create_shipping_line(shipping_cost, bc_order, api=api)
            if shipping_line:
                order_vals['order_line'].append(shipping_line)
        
        # Extract delivery_block_id from order_vals to set it after creation
        delivery_block_id_to_set = order_vals.pop('delivery_block_id', None)
        
        try:
            order = order_obj.create(order_vals)
            self._create_log('info', f"Created new order {order.name} (BC ID: {bc_order_id})",
                            order_id=bc_order_id, order_name=order.name)
            
            # Set delivery block AFTER order creation if it was specified
            if delivery_block_id_to_set:
                try:
                    order.sudo().write({'delivery_block_id': delivery_block_id_to_set})
                    _logger.info(f"Set delivery block for order {order.name} after creation")
                except Exception as block_err:
                    _logger.warning(f"Could not set delivery block for order {order.name}: {str(block_err)}")
            
            # Verify delivery block was set
            if order.delivery_block_id:
                _logger.info(f"Order {order.name} created with delivery block: {order.delivery_block_id.name}")
            else:
                _logger.warning(f"Order {order.name} created but delivery_block_id is not set!")
            
            # Override order name with prefix if configured (after creation to ensure it's set)
            if order_name:
                order.write({'name': order_name})
            
            # Confirm order if configured or if state mapping requires it
            config = self.config_id
            should_confirm = False
            
            if config.confirm_sale_orders:
                should_confirm = True
            elif odoo_state in ('sale', 'done') and order.state == 'draft':
                should_confirm = True
            
            if should_confirm and order.state == 'draft':
                try:
                    # Use sudo with a proper user context for action_confirm
                    order.with_user(1).sudo().action_confirm()
                except Exception as confirm_err:
                    _logger.warning(f"Could not confirm order {order.name}: {str(confirm_err)}")
            
            # Handle cancel state
            if odoo_state == 'cancel' and order.state != 'cancel':
                if order.state == 'draft':
                    try:
                        order.with_user(1).sudo().action_cancel()
                        _logger.info(f"Cancelled order {order.name} based on BigCommerce status")
                    except Exception as cancel_err:
                        _logger.warning(f"Could not cancel order {order.name}: {str(cancel_err)}")
                else:
                    # Can't cancel confirmed orders directly, try to cancel anyway
                    try:
                        order.with_user(1).sudo()._action_cancel()
                        _logger.info(f"Force cancelled confirmed order {order.name} based on BigCommerce status")
                    except Exception as e:
                        warning_msg = f"Cannot cancel order {order.name} that is already confirmed: {str(e)}"
                        _logger.warning(warning_msg)
                        self._create_log('warning', warning_msg, 
                                      order_id=bc_order.get('id'), order_name=order.name)
            
            # Create invoice if configured
            if config.create_invoices and order.state in ('sale', 'done'):
                try:
                    # Use with_user(1) to ensure proper user context for invoice creation
                    invoices = order.with_user(1).sudo()._create_invoices()
                    if invoices:
                        # Set invoice date and due date before posting (required for receivable accounts)
                        # Also clear fiscal position to prevent Avalara from applying taxes
                        for invoice in invoices:
                            if invoice.state == 'draft':
                                # Set invoice date to order date (or today if not set)
                                invoice_date = order.date_order.date() if order.date_order else fields.Date.today()
                                # Set due date based on payment terms, or same as invoice date if no payment terms
                                if order.payment_term_id:
                                    # Payment terms will calculate the due date automatically
                                    invoice.write({
                                        'invoice_date': invoice_date,
                                        'fiscal_position_id': False,  # Clear fiscal position to prevent Avalara (taxes already from BigCommerce)
                                    })
                                else:
                                    # No payment terms - set due date same as invoice date (immediate payment)
                                    invoice.write({
                                        'invoice_date': invoice_date,
                                        'invoice_date_due': invoice_date,
                                        'fiscal_position_id': False,  # Clear fiscal position to prevent Avalara (taxes already from BigCommerce)
                                    })
                                _logger.info(f"Set invoice date {invoice_date} and cleared fiscal position for invoice {invoice.id}")
                        
                        # Post invoices if created
                        for invoice in invoices:
                            # Post the invoice if it's in draft state
                            if invoice.state == 'draft':
                                try:
                                    invoice.with_user(1).sudo().action_post()
                                    _logger.info(f"Posted invoice {invoice.name} for order {order.name}")
                                except Exception as e:
                                    error_msg = f"Could not post invoice {invoice.name}: {str(e)}"
                                    _logger.error(error_msg, exc_info=True)
                                    self._create_log('error', error_msg, 
                                                  order_id=bc_order.get('id'), order_name=order.name,
                                                  error_details=str(e))
                                    continue
                        
                        # Mark invoices as paid if register_payment_on_import is enabled
                        if config.register_payment_on_import:
                            for invoice in invoices:
                                # Mark invoice as paid by registering payment
                                if invoice.state == 'posted' and invoice.amount_residual > 0:
                                    self._register_payment_for_invoice(invoice, order, bc_order)
                        
                        # Also use the existing payment registration method (for backward compatibility)
                        self._register_payment(order, bc_order)
                except Exception as invoice_err:
                    _logger.warning(f"Could not create invoices for order {order.name}: {str(invoice_err)}")
            
            # Import payment information if configured
            # import_bc_transactions takes precedence for detailed transaction info
            if config.import_bc_transactions or config.import_payments:
                import_transactions = config.import_bc_transactions  # Use detailed import if transactions option is enabled
                self._import_payment_info(api, bc_order.get('id'), order, import_transactions=import_transactions)
            
            # Import shipment details if configured
            if config.import_shipment_details:
                bc_status_id = bc_order.get('status_id')
                if bc_status_id is not None:
                    allowed_status_ids = config.import_shipment_status_ids.mapped('bc_status_id') if config.import_shipment_status_ids else []
                    if not config.import_shipment_status_ids or bc_status_id in allowed_status_ids:
                        self._import_shipment_details(api, bc_order.get('id'), order)
            
            _logger.info(f"Successfully created order {order.id} from BigCommerce order {bc_order.get('id')}")
            return 'created'
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"Error creating order from BigCommerce order {bc_order.get('id')}: {str(e)}"
            _logger.error(error_msg, exc_info=True)
            self._create_log('error', error_msg, 
                          order_id=bc_order.get('id'), order_name=f"Order #{bc_order.get('id')}",
                          error_details=error_trace)
            raise
    
    def _get_or_create_customer(self, api, customer_id, bc_order=None):
        """Get or create customer from BigCommerce. Never falls back to a random contact."""
        config = self.config_id
        
        if not customer_id:
            if config.default_customer_enabled and config.default_customer_id:
                return config.default_customer_id
            # Guest order: create contact from order billing so each order gets the correct contact
            partner = self._create_partner_from_order_billing(0, bc_order, api=api)
            if partner:
                return partner
            raise ValueError("Order has no customer_id and no billing address to create a contact. Set a default customer in Order Import Settings or fix the order in BigCommerce.")
        
        # Check if customer exists in Odoo by BigCommerce ID
        customer = self.env['res.partner'].search([('bigcommerce_id', '=', customer_id)], limit=1)
        if customer:
            return customer

        # Fetch customer from BigCommerce (we need it to match existing company or create new)
        try:
            bc_customer = api.get_customer(customer_id)
            if not bc_customer or not isinstance(bc_customer, dict):
                # API returned no data (e.g. 404, deleted customer): create customer from order billing when possible
                error_msg = f"API returned no data for customer {customer_id}"
                _logger.warning(error_msg)
                self._create_log('warning', error_msg,
                                 order_id=bc_order.get('id') if bc_order else None,
                                 order_name=f"Order #{bc_order.get('id')}" if bc_order else None)
                partner = self._create_partner_from_order_billing(customer_id, bc_order, api=api)
                if partner:
                    return partner
                if config.default_customer_enabled and config.default_customer_id:
                    return config.default_customer_id
                raise ValueError(f"Cannot create or find customer for BC customer_id={customer_id}. Set a default customer in Order Import Settings or ensure the order has a billing address.")

            # Before creating a new partner, check for an existing company (is_company=True) matching
            # by company name or email. If found, link it to this BC customer so the delivery contact
            # is attached to the company instead of creating a new top-level person.
            bc_company = (bc_customer.get('company') or '').strip()
            if bc_company and '@' in bc_company:
                bc_company = ''
            bc_email = (bc_customer.get('email') or '').strip()
            if bc_company or bc_email:
                existing_company = self._find_existing_company_for_bc_customer(
                    bc_company, bc_email, customer_id
                )
                if existing_company:
                    existing_company.write({'bigcommerce_id': customer_id})
                    self._create_log(
                        'info',
                        f"Linked existing company {existing_company.name} to BC customer {customer_id}; delivery will be attached as contact.",
                        order_id=bc_order.get('id') if bc_order else None,
                        order_name=existing_company.name,
                    )
                    return existing_company

            customer_vals = {
                'name': f"{bc_customer.get('first_name', '')} {bc_customer.get('last_name', '')}".strip(),
                'email': bc_customer.get('email', ''),
                'phone': bc_customer.get('phone', ''),
                'bigcommerce_id': customer_id,
            }
            if not customer_vals['name']:
                customer_vals['name'] = customer_vals['email'] or f"BC Customer {customer_id}"
            # Only set company name if it exists in BigCommerce and is not an email (BC sometimes sends email in company)
            bc_company = bc_customer.get('company', '').strip()
            if bc_company and '@' not in bc_company:
                customer_vals['company_name'] = bc_company
            if self._customer_group_is_wholesale_company(bc_customer.get('customer_group_id'), api):
                customer_vals['is_company'] = True
            new_partner = self.env['res.partner'].create(customer_vals)
            self._create_log('info', f"Created new customer {new_partner.name} (BC ID: {customer_id})",
                            order_id=customer_id, order_name=new_partner.name)
            return new_partner
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Error fetching customer {customer_id}: {str(e)}"
            _logger.error(error_msg)
            self._create_log('error', error_msg,
                             order_id=bc_order.get('id') if bc_order else None,
                             order_name=f"Order #{bc_order.get('id')}" if bc_order else None,
                             error_details=str(e))
            # Create customer from order billing when possible so the order has the correct contact
            partner = self._create_partner_from_order_billing(customer_id, bc_order, api=api)
            if partner:
                return partner
            if config.default_customer_enabled and config.default_customer_id:
                return config.default_customer_id
            raise ValueError(f"Cannot create or find customer for BC customer_id={customer_id}. Fix the customer in BigCommerce or set a default customer in Order Import Settings.") from e

    def _find_existing_company_for_bc_customer(self, company_name, email, bc_customer_id):
        """Find an existing Odoo company (is_company=True) that matches by company name or email.
        Used so we attach the order's delivery contact to the company instead of creating a new person.
        Only returns a partner that does not already have a different bigcommerce_id (so we don't steal
        a company that is linked to another BC customer).
        """
        if not company_name and not email:
            return self.env['res.partner']
        # Must be a top-level company not already linked to another BC customer
        base = [
            ('is_company', '=', True),
            ('parent_id', '=', False),
            '|', ('bigcommerce_id', '=', False), ('bigcommerce_id', '=', 0),
        ]
        match_terms = []
        if company_name:
            match_terms.append(('name', 'ilike', company_name))
            if 'company_name' in self.env['res.partner']._fields:
                match_terms.append(('company_name', 'ilike', company_name))
        if email:
            match_terms.append(('email', '=', email))
        if not match_terms:
            return self.env['res.partner']
        if len(match_terms) == 1:
            match_domain = Domain([match_terms[0]])
        else:
            match_domain = Domain([match_terms[0]])
            for t in match_terms[1:]:
                match_domain = match_domain | Domain([t])
        domain = Domain(base) & match_domain
        existing = self.env['res.partner'].search(domain, limit=1)
        if existing and (existing.bigcommerce_id in (False, 0, None)):
            return existing
        return self.env['res.partner']

    def _get_wholesale_company_group_ids(self, api):
        """Return set of BigCommerce customer group IDs whose name is Wholesale Jobber, Silver, or Gold.
        Cached on config for the current request to avoid repeated API calls."""
        config = self.config_id
        cache_attr = '_bc_wholesale_company_group_ids'
        if getattr(config, cache_attr, None) is not None:
            return getattr(config, cache_attr)
        try:
            groups = api.get_customer_groups() or []
            ids = {int(g['id']) for g in groups if g.get('name') in WHOLESALE_COMPANY_GROUP_NAMES}
            setattr(config, cache_attr, ids)
            return ids
        except Exception as e:
            _logger.warning("Could not fetch BigCommerce customer groups for wholesale company check: %s", e)
            setattr(config, cache_attr, set())
            return set()

    def _customer_group_is_wholesale_company(self, customer_group_id, api):
        """Return True if customer_group_id (from BC customer or order) is one of the wholesale company groups."""
        if customer_group_id is None:
            return False
        try:
            gid = int(customer_group_id)
        except (TypeError, ValueError):
            return False
        return gid in self._get_wholesale_company_group_ids(api)
    
    def _create_partner_from_order_billing(self, customer_id, bc_order, api=None):
        """Create a minimal res.partner from order billing when BC customer API fails. Returns partner or False."""
        if not bc_order or not isinstance(bc_order, dict):
            return False
        billing = bc_order.get('billing_address')
        if not billing or not isinstance(billing, dict):
            return False
        addr = self._extract_address(billing)
        bc_company = (addr.get('company') or billing.get('company') or '').strip()
        if bc_company and '@' in bc_company:
            bc_company = ''
        email = (billing.get('email') or bc_order.get('billing_address', {}).get('email') or '').strip()
        if customer_id and (bc_company or email):
            existing_company = self._find_existing_company_for_bc_customer(bc_company, email, customer_id)
            if existing_company:
                existing_company.write({'bigcommerce_id': customer_id})
                self._create_log(
                    'info',
                    f"Linked existing company {existing_company.name} to BC customer {customer_id} (from billing); delivery will be attached as contact.",
                    order_id=bc_order.get('id'), order_name=existing_company.name,
                )
                return existing_company
        name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
        if not name:
            name = f"BC Customer {customer_id}"
        # Email may be on order or billing depending on BC version
        email = (billing.get('email') or bc_order.get('billing_address', {}).get('email') or '').strip()
        country_id = False
        if addr.get('country_iso2'):
            country = self.env['res.country'].search([('code', '=', addr['country_iso2'])], limit=1)
            country_id = country.id if country else False
        state_id = False
        if addr.get('state') and country_id:
            state = self.env['res.country.state'].search([
                ('country_id', '=', country_id),
                ('name', '=', addr['state']),
            ], limit=1)
            state_id = state.id if state else False
        vals = {
            'name': name,
            'email': email or False,
            'phone': addr.get('phone') or False,
            'street': addr.get('street_1') or '',
            'street2': addr.get('street_2') or '',
            'city': addr.get('city') or '',
            'state_id': state_id,
            'zip': addr.get('zip') or '',
            'country_id': country_id,
        }
        # Only set bigcommerce_id for non-guest so we can find this partner on future orders
        if customer_id:
            vals['bigcommerce_id'] = customer_id
        if bc_company and '@' not in bc_company:
            vals['company_name'] = bc_company
        if api and self._customer_group_is_wholesale_company(bc_order.get('customer_group_id'), api):
            vals['is_company'] = True
        try:
            partner = self.env['res.partner'].create(vals)
            self._create_log('info', f"Created customer from order billing: {partner.name} (BC ID: {customer_id})",
                             order_id=bc_order.get('id'), order_name=partner.name)
            return partner
        except Exception:
            return False
    
    def _get_product_by_bc_id(self, bc_product_id):
        """Get Odoo product by BigCommerce product ID and configuration
        Now supports products that exist in multiple stores - finds product by BC ID and config
        """
        if not bc_product_id:
            return False
        
        # Search for mapping by BC ID and config
        mapping = self.env['bigcommerce.product.mapping'].search([
            ('bigcommerce_id', '=', bc_product_id),
            ('config_id', '=', self.config_id.id)
        ], limit=1)
        
        if mapping:
            return mapping.product_tmpl_id
        
        # Fallback: search by legacy fields for backward compatibility
        return self.env['product.template'].search([
            ('bigcommerce_id', '=', bc_product_id),
            ('bigcommerce_config_id', '=', self.config_id.id)
        ], limit=1)
    
    def _extract_address(self, address_data):
        """Extract address data from BigCommerce address object (billing or shipping).
        Handles both street_1/street_2 and address_1/address_2 (API variations)."""
        if not address_data or not isinstance(address_data, dict):
            return {}
        street_1 = address_data.get('street_1') or address_data.get('address_1') or ''
        street_2 = address_data.get('street_2') or address_data.get('address_2') or ''
        return {
            'first_name': address_data.get('first_name', ''),
            'last_name': address_data.get('last_name', ''),
            'company': address_data.get('company', ''),
            'street_1': street_1,
            'street_2': street_2,
            'city': address_data.get('city', ''),
            'state': address_data.get('state', ''),
            'zip': address_data.get('zip', ''),
            'country': address_data.get('country', ''),
            'country_iso2': address_data.get('country_iso2', ''),
            'phone': address_data.get('phone', ''),
            'email': (address_data.get('email') or '').strip(),
        }

    def _address_equals_partner(self, address, partner):
        """Return True if the BC address (from _extract_address) matches the partner's main address.
        Used to avoid creating invoice/delivery sub-contacts when they are the same as the customer address."""
        if not address or not partner:
            return not address and not partner
        # Resolve country_id from address (same logic as _get_or_create_address_partner)
        country_id = False
        if address.get('country_iso2'):
            country = self.env['res.country'].search([('code', '=', address.get('country_iso2'))], limit=1)
            country_id = country.id if country else False
        if not country_id and address.get('country'):
            country_name = (address.get('country') or '').strip()
            if country_name:
                country = self.env['res.country'].search([('name', 'ilike', country_name)], limit=1)
                if not country and len(country_name) == 2:
                    country = self.env['res.country'].search([('code', '=', country_name.upper())], limit=1)
                country_id = country.id if country else False
        state_id = False
        if address.get('state') and country_id:
            state = self.env['res.country.state'].search([
                ('name', '=', address.get('state')),
                ('country_id', '=', country_id)
            ], limit=1)
            state_id = state.id if state else False
        # Normalize for comparison (strip, treat empty consistently)
        def n(s):
            return (s or '').strip()
        addr_street = n(address.get('street_1'))
        addr_street2 = n(address.get('street_2'))
        addr_city = n(address.get('city'))
        addr_zip = n(address.get('zip'))
        p_street = n(partner.street)
        p_street2 = n(partner.street2 or '')
        p_city = n(partner.city)
        p_zip = n(partner.zip or '')
        p_country_id = partner.country_id.id if partner.country_id else False
        p_state_id = partner.state_id.id if partner.state_id else False
        return (
            addr_street == p_street
            and addr_street2 == p_street2
            and addr_city == p_city
            and addr_zip == p_zip
            and country_id == p_country_id
            and state_id == p_state_id
        )

    def _should_use_partner_for_invoice(self, billing_address, partner):
        """Return True if we should use the partner as invoice (do not create a separate invoice contact).
        Used when the company already exists and billing is the same as the company address (including
        format differences), or when the partner is a company or company-like (top-level with company name)."""
        if not partner:
            return False
        # Always use the company record for invoice when it's marked as a company
        if partner.is_company:
            return True
        # Top-level partner with company_name is treated as company (e.g. legacy records without is_company)
        if partner.parent_id is False and getattr(partner, 'company_name', None) and partner.company_name:
            return True
        # If billing address exactly matches partner, we use partner (no need to create)
        if billing_address and self._address_equals_partner(billing_address, partner):
            return True
        # Same location (city, zip, country, state) and billing name would be same as partner -> use partner
        # to avoid creating a duplicate when BC sends "9 Old Windsor Rd" + "Unit 9-B" and Odoo has "9 Old Windsor Rd Ste B"
        if not billing_address:
            return False
        def n(s):
            return (s or '').strip()
        addr_city = n(billing_address.get('city'))
        addr_zip = n(billing_address.get('zip'))
        p_city = n(partner.city)
        p_zip = n(partner.zip or '')
        p_country_id = partner.country_id.id if partner.country_id else False
        p_state_id = partner.state_id.id if partner.state_id else False
        country_id = False
        if billing_address.get('country_iso2'):
            country = self.env['res.country'].search([('code', '=', billing_address.get('country_iso2'))], limit=1)
            country_id = country.id if country else False
        if not country_id and billing_address.get('country'):
            country = self.env['res.country'].search([('name', 'ilike', n(billing_address.get('country')))], limit=1)
            country_id = country.id if country else False
        state_id = False
        if billing_address.get('state') and country_id:
            state = self.env['res.country.state'].search([
                ('name', '=', billing_address.get('state')),
                ('country_id', '=', country_id)
            ], limit=1)
            state_id = state.id if state else False
        if addr_city != p_city or addr_zip != p_zip or country_id != p_country_id or state_id != p_state_id:
            return False
        # Location matches; use partner for invoice when billing name matches partner (same entity)
        billing_name = n(f"{billing_address.get('first_name', '')} {billing_address.get('last_name', '')}".strip())
        bc_company = n(billing_address.get('company', ''))
        if bc_company and '@' in bc_company:
            bc_company = ''
        if not billing_name:
            billing_name = bc_company
        partner_name = n(partner.name or '')
        if billing_name and partner_name and billing_name.lower() == partner_name.lower():
            return True
        if bc_company and partner_name and bc_company.lower() in partner_name.lower():
            return True
        if partner_name and bc_company and partner_name.lower() in bc_company.lower():
            return True
        return False

    def _get_or_create_address_partner(self, parent_partner, address, address_type='invoice'):
        """Create or get a partner address record for invoice or shipping (e.g. dropship delivery address)."""
        if not address:
            return parent_partner
        
        # Get country ID: prefer ISO2, fallback to full country name (BC APIs vary)
        country_id = False
        if address.get('country_iso2'):
            country = self.env['res.country'].search([('code', '=', address.get('country_iso2'))], limit=1)
            country_id = country.id if country else False
        if not country_id and address.get('country'):
            country_name = (address.get('country') or '').strip()
            if country_name:
                country = self.env['res.country'].search([
                    ('name', 'ilike', country_name)
                ], limit=1)
                if not country and len(country_name) == 2:
                    country = self.env['res.country'].search([('code', '=', country_name.upper())], limit=1)
                country_id = country.id if country else False
        
        # Get state ID
        state_id = False
        if address.get('state') and country_id:
            state = self.env['res.country.state'].search([
                ('name', '=', address.get('state')),
                ('country_id', '=', country_id)
            ], limit=1)
            state_id = state.id if state else False
        
        # Create address name
        address_name = f"{address.get('first_name', '')} {address.get('last_name', '')}".strip()
        if not address_name:
            address_name = parent_partner.name
        
        # Prepare partner address values
        partner_vals = {
            'name': address_name,
            'parent_id': parent_partner.id,
            'type': address_type,
            'street': address.get('street_1', ''),
            'street2': address.get('street_2', ''),
            'city': address.get('city', ''),
            'state_id': state_id,
            'zip': address.get('zip', ''),
            'country_id': country_id,
            'phone': address.get('phone', ''),
        }
        if address.get('email'):
            partner_vals['email'] = address.get('email')
        # Only set company name if it exists in BigCommerce address and is not an email (BC sometimes sends email in company)
        bc_company = address.get('company', '').strip()
        if bc_company and '@' not in bc_company:
            partner_vals['company_name'] = bc_company
        
        # Search for existing address partner with same details (include street2 so dropship vs billing don't match)
        domain = [
            ('parent_id', '=', parent_partner.id),
            ('type', '=', address_type),
            ('street', '=', partner_vals['street']),
            ('street2', '=', partner_vals.get('street2') or ''),
            ('city', '=', partner_vals['city']),
            ('zip', '=', partner_vals['zip']),
        ]
        existing = self.env['res.partner'].search(domain, limit=1)
        
        if existing:
            # Update existing address if needed
            existing.write(partner_vals)
            return existing
        else:
            # Create new address partner
            new_partner = self.env['res.partner'].create(partner_vals)
            self._create_log('info', f"Created new address {address_name} for customer",
                            order_name=new_partner.display_name)
            return new_partner
    
    def _get_order_reference(self, bc_order):
        """Get order reference from BigCommerce order"""
        return bc_order.get('id') or bc_order.get('order_id') or ''
    
    def _get_order_notes(self, bc_order):
        """Get order notes from BigCommerce order"""
        notes = []
        if bc_order.get('customer_message'):
            notes.append(f"Customer Message: {bc_order.get('customer_message')}")
        if bc_order.get('staff_notes'):
            notes.append(f"Staff Notes: {bc_order.get('staff_notes')}")
        return '\n'.join(notes) if notes else ''
    
    def _create_tax_line(self, tax_amount, bc_order):
        """Create a tax line for the order"""
        # Use tax product from config if available
        config = self.config_id
        tax_product = config.tax_product_id if config and config.tax_product_id else False
        
        # Find or create a tax product if not configured
        if not tax_product:
            tax_product = self.env['product.product'].search([
                ('name', 'ilike', 'Tax'),
                ('type', '=', 'service')
            ], limit=1)
        
        if not tax_product:
            tax_product = self.env['product.product'].create({
                'name': 'Tax',
                'type': 'service',
                'sale_ok': False,
                'purchase_ok': False,
            })
            self._create_log('info', "Created new Tax product for order sync", order_name='Tax')
        
        # Ensure Tax product has Avalara tax code to prevent validation errors
        if 'tax_code_id' in self.env['product.product']._fields and not tax_product.tax_code_id:
            # Try to find NT (Not Taxable) tax code for Tax product
            nt_code = self.env['product.tax.code'].sudo().search([('name', '=', 'NT')], limit=1)
            if nt_code:
                tax_product.sudo().write({'tax_code_id': nt_code.id})
                _logger.info(f"Set Avalara tax code 'NT' on Tax product {tax_product.id}")
            else:
                # If NT doesn't exist, use any available code to satisfy validation
                any_code = self.env['product.tax.code'].sudo().search([], limit=1)
                if any_code:
                    tax_product.sudo().write({'tax_code_id': any_code.id})
                    _logger.info(f"Set Avalara tax code '{any_code.name}' on Tax product {tax_product.id}")
        
        # Get UOM for tax line
        tax_uom = tax_product.uom_id if tax_product.uom_id else self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
        if not tax_uom:
            tax_uom = self.env['uom.uom'].search([('name', '=', 'Units')], limit=1)
            if not tax_uom:
                tax_uom = self.env['uom.uom'].search([], limit=1)
        
        return (0, 0, {
            'product_id': tax_product.id,
            'name': 'Tax',
            'product_uom_qty': 1,
            'product_uom_id': tax_uom.id if tax_uom else False,
            'price_unit': tax_amount,
            'discount': 0,  # Prevent pricelist discounts on tax line
        })
    
    def _get_bc_shipping_method_name(self, bc_order):
        """Extract BigCommerce shipping method label (e.g. USPS Ground Advantage) from order payload. Returns 'Shipping' if not found."""
        if not bc_order or not isinstance(bc_order, dict):
            return 'Shipping'
        name = (bc_order.get('shipping_method') or '').strip() if isinstance(bc_order.get('shipping_method'), str) else ''
        if name:
            return name
        methods = bc_order.get('shipping_methods') or []
        if methods and isinstance(methods, list):
            first = methods[0] if methods else {}
            if isinstance(first, dict):
                name = (
                    first.get('description')
                    or first.get('name')
                    or first.get('shipping_method')
                    or first.get('method')
                    or first.get('shipping_provider_display_name')
                    or ''
                )
                if name:
                    return name.strip()
        addrs = bc_order.get('shipping_addresses') or []
        if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict):
            sa = addrs[0]
            name = (
                sa.get('shipping_method')
                or sa.get('description')
                or sa.get('shipping_provider_display_name')
                or sa.get('method')
                or ''
            )
            if name:
                return name.strip()
        return 'Shipping'
    
    def _get_bc_shipping_method_name_from_api(self, api, order_id):
        """Fetch order shipping addresses from BC API and return the first address's shipping method name. V2 GET /orders/{id}/shipping_addresses often includes the selected method."""
        if not api or not order_id:
            return 'Shipping'
        try:
            addresses = api.get_order_shipping_addresses(order_id)
            if not addresses or not isinstance(addresses, list):
                return 'Shipping'
            first = addresses[0] if addresses else {}
            if not isinstance(first, dict):
                return 'Shipping'
            name = (
                first.get('shipping_method')
                or first.get('description')
                or first.get('method')
                or first.get('shipping_provider_display_name')
                or ''
            )
            return (name or 'Shipping').strip()
        except Exception:
            return 'Shipping'
    
    def _create_shipping_line(self, shipping_cost, bc_order, api=None):
        """Create a shipping line for the order. Uses config Delivery Method (carrier) product when set; line name = BC shipping method (e.g. USPS Priority Mail) so it shows below the product. When BC method ends with *, uses Delivery Method (Wholesale) if configured."""
        config = self.config_id
        # Resolve BC shipping method name first so we can choose wholesale carrier product when method ends with *
        shipping_name = self._get_bc_shipping_method_name(bc_order)
        if shipping_name == 'Shipping' and api and bc_order.get('id'):
            try:
                full_order = api.get_order(bc_order['id'])
                if full_order and isinstance(full_order, dict):
                    shipping_name = self._get_bc_shipping_method_name(full_order)
            except Exception:
                pass
            if shipping_name == 'Shipping':
                shipping_name = self._get_bc_shipping_method_name_from_api(api, bc_order['id'])
        shipping_name = (shipping_name or 'Shipping').strip()
        is_wholesale_method = shipping_name.endswith('*') or shipping_name.rstrip().endswith('*')
        # Use wholesale carrier product when BC method ends with * (e.g. "USPS Ground*" -> Shippo Wholesale)
        shipping_product = False
        if is_wholesale_method and config and config.delivery_carrier_wholesale_id and getattr(config.delivery_carrier_wholesale_id, 'product_id', None):
            shipping_product = config.delivery_carrier_wholesale_id.product_id
        if not shipping_product and config and config.delivery_carrier_id and getattr(config.delivery_carrier_id, 'product_id', None):
            shipping_product = config.delivery_carrier_id.product_id
        if not shipping_product and config and config.shipping_product_id:
            shipping_product = config.shipping_product_id
        if not shipping_product:
            shipping_product = self.env['product.product'].search([
                ('name', 'ilike', 'Shipping'),
                ('type', '=', 'service')
            ], limit=1)
        if not shipping_product:
            shipping_product = self.env['product.product'].create({
                'name': 'Shipping',
                'type': 'service',
                'sale_ok': False,
                'purchase_ok': False,
            })
            self._create_log('info', "Created new Shipping product for order sync", order_name='Shipping')
        # Ensure Shipping product has Avalara tax code to prevent validation errors
        if 'tax_code_id' in self.env['product.product']._fields and not shipping_product.tax_code_id:
            fr_code = self.env['product.tax.code'].sudo().search([('name', '=', 'FR')], limit=1)
            if fr_code:
                shipping_product.sudo().write({'tax_code_id': fr_code.id})
                _logger.info(f"Set Avalara tax code 'FR' on Shipping product {shipping_product.id}")
            else:
                any_code = self.env['product.tax.code'].sudo().search([], limit=1)
                if any_code:
                    shipping_product.sudo().write({'tax_code_id': any_code.id})
                    _logger.info(f"Set Avalara tax code '{any_code.name}' on Shipping product {shipping_product.id}")
        
        shipping_uom = shipping_product.uom_id or self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
        if not shipping_uom:
            shipping_uom = self.env['uom.uom'].search([('name', '=', 'Units')], limit=1) or self.env['uom.uom'].search([], limit=1)
        
        return (0, 0, {
            'product_id': shipping_product.id,
            'name': shipping_name,
            'product_uom_qty': 1,
            'product_uom_id': shipping_uom.id if shipping_uom else False,
            'price_unit': shipping_cost,
            'discount': 0,  # Prevent pricelist discounts on shipping line
        })
    
    def _import_payment_info(self, api, bc_order_id, order, import_transactions=True):
        """Import payment information from BigCommerce order
        
        Args:
            api: BigCommerce API client
            bc_order_id: BigCommerce order ID
            order: Odoo sale.order record
            import_transactions: If True, import transaction details; if False, import payment method/status
        """
        try:
            payment_events = api.get_order_payments(bc_order_id)
            if payment_events:
                payment_info = []
                for event in payment_events:
                    if import_transactions:
                        # Import detailed transaction information
                        event_id = event.get('id', 'N/A')
                        event_type = event.get('event_type', 'Unknown')
                        amount = event.get('amount', 0)
                        method = event.get('method', 'Unknown')
                        gateway = event.get('gateway', 'Unknown')
                        payment_info.append(
                            f"Transaction ID: {event_id}, Type: {event_type}, "
                            f"Amount: {amount}, Method: {method}, Gateway: {gateway}"
                        )
                    else:
                        # Import basic payment information
                        amount = event.get('amount', 0)
                        method = event.get('method', 'Unknown')
                        payment_info.append(f"Payment: {amount} - {method}")
                
                if payment_info:
                    existing_note = order.note or ''
                    new_info = '\n'.join(payment_info)
                    # Avoid duplicate entries
                    if new_info not in existing_note:
                        order.note = (existing_note + '\n' + new_info).strip()
                        _logger.info(f"Imported payment information for order {order.name} (BC ID: {bc_order_id})")
        except Exception as e:
            warning_msg = f"Could not import payment info for order {bc_order_id}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                          error_details=str(e))
    
    def _register_payment_for_invoice(self, invoice, order, bc_order):
        """Register payment for a specific invoice to mark it as paid"""
        try:
            if invoice.amount_residual <= 0:
                # Invoice is already paid
                return
            
            # Find journal for payments (prefer bank, fallback to cash)
            journal = self.env['account.journal'].search([
                ('type', 'in', ['bank', 'cash']),
                ('company_id', '=', order.company_id.id)
            ], limit=1)
            
            if not journal:
                warning_msg = f"No payment journal found for company {order.company_id.name}"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, 
                              order_id=bc_order.get('id'), order_name=order.name)
                return
            
            # Get payment method
            payment_method = self.env['account.payment.method'].search([
                ('payment_type', '=', 'inbound'),
                ('code', '=', 'manual')
            ], limit=1)
            
            if not payment_method:
                # Try to get any inbound payment method
                payment_method = self.env['account.payment.method'].search([
                    ('payment_type', '=', 'inbound')
                ], limit=1)
            
            # Use the invoice's action_register_payment method for proper reconciliation
            # This is the standard Odoo way to register payments
            try:
                # Create payment register wizard with proper context and user
                payment_register = self.env['account.payment.register'].with_user(1).sudo().with_context(
                    active_model='account.move',
                    active_ids=[invoice.id],
                    default_journal_id=journal.id,
                    default_payment_method_id=payment_method.id if payment_method else False,
                    default_amount=invoice.amount_residual,
                    default_currency_id=order.currency_id.id,
                    default_payment_date=fields.Date.today(),
                ).create({})
                
                # Create and post the payment - this should automatically reconcile
                payments = payment_register.with_user(1).sudo()._create_payments()
                
                if payments:
                    # Refresh invoice to update payment state
                    invoice.invalidate_recordset(['payment_state', 'amount_residual'])
                    invoice._compute_payment_state()
                    _logger.info(f"Marked invoice {invoice.name} as paid using payment register wizard. Payment state: {invoice.payment_state}, Residual: {invoice.amount_residual}")
                else:
                    raise Exception("Payment register wizard did not return any payments")
                    
            except Exception as wizard_error:
                # Fallback to direct payment creation if wizard fails
                _logger.warning(f"Payment register wizard failed, using direct payment creation: {str(wizard_error)}")
                
                # Create payment directly with invoice_ids - this should auto-reconcile when posted
                payment_vals = {
                    'payment_type': 'inbound',
                    'partner_type': 'customer',
                    'partner_id': order.partner_id.id,
                    'amount': invoice.amount_residual,
                    'currency_id': order.currency_id.id,
                    'journal_id': journal.id,
                    'payment_method_id': payment_method.id if payment_method else False,
                    'invoice_ids': [(6, 0, [invoice.id])],
                    'date': fields.Date.today(),
                    'company_id': order.company_id.id,
                }
                payment = self.env['account.payment'].with_user(1).sudo().create(payment_vals)
                
                # Post the payment - this should automatically reconcile with invoice
                payment.with_user(1).sudo().action_post()
                
                # Force refresh invoice to update payment state
                invoice.invalidate_recordset(['payment_state', 'amount_residual'])
                invoice._compute_payment_state()
                
                # If still not paid, manually reconcile
                if invoice.amount_residual > 0:
                    _logger.warning(f"Invoice {invoice.name} still has residual {invoice.amount_residual} after payment. Attempting manual reconciliation.")
                    payment_move = payment.move_id
                    if payment_move:
                        # Get all move lines for reconciliation
                        payment_lines = payment_move.line_ids.filtered(
                            lambda l: l.partner_id == order.partner_id and not l.reconciled
                        )
                        invoice_lines = invoice.line_ids.filtered(
                            lambda l: l.partner_id == order.partner_id and not l.reconciled
                        )
                        
                        # Try to reconcile lines with matching accounts
                        for payment_line in payment_lines:
                            matching_invoice_lines = invoice_lines.filtered(
                                lambda l: l.account_id == payment_line.account_id
                            )
                            if matching_invoice_lines:
                                (payment_line + matching_invoice_lines[0]).reconcile()
                                break
                        
                        # Refresh again after reconciliation
                        invoice.invalidate_recordset(['payment_state', 'amount_residual'])
                        invoice._compute_payment_state()
                
                _logger.info(f"Marked invoice {invoice.name} as paid with direct payment {payment.name}. Payment state: {invoice.payment_state}, Residual: {invoice.amount_residual}")
                
        except Exception as e:
            warning_msg = f"Could not register payment for invoice {invoice.name}: {str(e)}"
            _logger.warning(warning_msg)
            import traceback
            error_details = traceback.format_exc()
            self._create_log('warning', warning_msg, 
                          order_id=bc_order.get('id'), order_name=order.name,
                          error_details=error_details)
    
    def _register_payment(self, order, bc_order):
        """Register payment for the order"""
        try:
            # Get payment amount from BigCommerce order
            payment_amount = float(bc_order.get('total_inc_tax', 0))
            if payment_amount <= 0:
                return
            
            # Find journal for payments (prefer bank, fallback to cash)
            journal = self.env['account.journal'].search([
                ('type', 'in', ['bank', 'cash']),
                ('company_id', '=', order.company_id.id)
            ], limit=1)
            
            if not journal:
                warning_msg = f"No payment journal found for company {order.company_id.name}"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, 
                              order_id=bc_order.get('id'), order_name=order.name)
                return
            
            # Get payment method
            payment_method = self.env['account.payment.method'].search([
                ('payment_type', '=', 'inbound'),
                ('code', '=', 'manual')
            ], limit=1)
            
            if not payment_method:
                # Try to get any inbound payment method
                payment_method = self.env['account.payment.method'].search([
                    ('payment_type', '=', 'inbound')
                ], limit=1)
            
            if order.invoice_ids:
                for invoice in order.invoice_ids:
                    if invoice.state == 'posted' and invoice.amount_residual > 0:
                        payment_vals = {
                            'payment_type': 'inbound',
                            'partner_type': 'customer',
                            'partner_id': order.partner_id.id,
                            'amount': min(payment_amount, invoice.amount_residual),
                            'currency_id': order.currency_id.id,
                            'journal_id': journal.id,
                            'payment_method_id': payment_method.id if payment_method else False,
                            'invoice_ids': [(6, 0, [invoice.id])],
                            'company_id': order.company_id.id,
                        }
                        payment = self.env['account.payment'].with_user(1).sudo().create(payment_vals)
                        # Post/validate the payment
                        try:
                            payment.with_user(1).sudo().action_post()
                        except Exception as e:
                            warning_msg = f"Could not post payment {payment.id}: {str(e)}"
                            _logger.warning(warning_msg)
                            self._create_log('warning', warning_msg, 
                                          order_id=bc_order.get('id'), order_name=order.name,
                                          error_details=str(e))
                        payment_amount -= min(payment_amount, invoice.amount_residual)
                        if payment_amount <= 0:
                            break
        except Exception as e:
            warning_msg = f"Could not register payment for order {bc_order.get('id')}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, 
                          order_id=bc_order.get('id'), order_name=f"Order #{bc_order.get('id')}",
                          error_details=str(e))
    
    def _import_shipment_details(self, api, bc_order_id, order):
        """Import shipment details from BigCommerce order"""
        try:
            # This would typically fetch shipment data from BigCommerce
            # For now, we'll just log that it's being called
            # The actual implementation would depend on BigCommerce API endpoints for shipments
            _logger.debug(f"Importing shipment details for order {bc_order_id}")
            # TODO: Implement shipment details import when API endpoint is available
        except Exception as e:
            warning_msg = f"Could not import shipment details for order {bc_order_id}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, 
                          order_id=bc_order_id, order_name=f"Order #{bc_order_id}",
                          error_details=str(e))

