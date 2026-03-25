# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class BigCommerceFulfillmentSync(models.Model):
    _name = 'bigcommerce.fulfillment.sync'
    _description = 'BigCommerce Fulfillment Sync'
    _order = 'sync_date desc'

    name = fields.Char(string='Sync Name', required=True, default=lambda self: f"Fulfillment Sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='State', default='draft')
    
    fulfillments_exported = fields.Integer(string='Fulfillments Exported', default=0)
    fulfillments_failed = fields.Integer(string='Fulfillments Failed', default=0)
    error_message = fields.Text(string='Error Message')
    
    # Progress tracking
    total_items = fields.Integer(string='Total Items', default=0, help='Total number of items to process')
    processed_items = fields.Integer(string='Processed Items', default=0, help='Number of items processed so far')
    progress_percentage = fields.Float(string='Progress', compute='_compute_progress', store=False, help='Progress percentage')
    current_item = fields.Char(string='Current Item', help='Currently processing item')
    
    # Link to sync operation for dashboard tracking
    sync_operation_id = fields.Many2one('bigcommerce.sync.operation', string='Sync Operation', ondelete='set null')
    
    # Filters
    date_from = fields.Datetime(string='Date From', help='Export fulfillments from this date')
    date_to = fields.Datetime(string='Date To', help='Export fulfillments until this date')
    min_date_modified = fields.Datetime(string='Min Date Modified', help='Export fulfillments for orders modified after this date')
    
    # Options
    update_order_status = fields.Boolean(
        string='Update Order Status',
        default=True,
        help='When selected, BigCommerce orders will be updated after products have been shipped. '
             'Order status will be set to Shipped or Partially Shipped based on fulfillment status.'
    )
    
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
                    'items_synced': self.fulfillments_exported,
                    'items_updated': self.fulfillments_exported,
                    'items_failed': self.fulfillments_failed,
                })
                # Also update the sync record itself to ensure UI sees progress
                self.write({
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                })
                self.env.cr.commit()
            except Exception as e:
                _logger.warning(f"Failed to update sync operation: {str(e)}")
    
    def _create_log(self, log_level, message, order_id=None, order_name=None, error_details=None):
        """Create a sync log entry for fulfillment sync"""
        try:
            log_vals = {
                'sync_type': 'fulfillment',
                'sync_record_id': self.id,
                'sync_operation_id': self.sync_operation_id.id if self.sync_operation_id else False,
                'config_id': self.config_id.id,
                'log_level': log_level,  # 'error', 'warning', 'info', 'debug'
                'message': message,
                'product_id': order_id,  # Using product_id field to store order ID
                'product_name': order_name,  # Using product_name field to store order name
                'error_details': error_details,
            }
            return self.env['bigcommerce.sync.log'].sudo().create(log_vals)
        except Exception as e:
            _logger.warning(f"Failed to create sync log entry: {str(e)}")
            return False
    
    def _mark_invoice_as_paid(self, order, bc_order_id):
        """Mark invoices for an order as paid"""
        try:
            if not order.invoice_ids:
                return
            
            # Find journal for payments (prefer bank, fallback to cash)
            journal = self.env['account.journal'].search([
                ('type', 'in', ['bank', 'cash']),
                ('company_id', '=', order.company_id.id)
            ], limit=1)
            
            if not journal:
                warning_msg = f"No payment journal found for company {order.company_id.name}"
                _logger.warning(warning_msg)
                self._create_log('warning', warning_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
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
            
            # Process each invoice
            for invoice in order.invoice_ids:
                # Post the invoice if it's in draft state
                if invoice.state == 'draft':
                    try:
                        invoice.action_post()
                        _logger.info(f"Posted invoice {invoice.name} for order {order.name}")
                    except Exception as e:
                        error_msg = f"Could not post invoice {invoice.name}: {str(e)}"
                        _logger.error(error_msg, exc_info=True)
                        self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}", error_details=str(e))
                        continue
                
                # Mark invoice as paid if it's posted and has residual amount
                if invoice.state == 'posted' and invoice.amount_residual > 0:
                    try:
                        # Use payment register wizard approach for proper reconciliation
                        # This ensures the payment is properly linked and reconciled
                        payment_register_wizard = self.env['account.payment.register'].with_context(
                            active_model='account.move',
                            active_ids=[invoice.id]
                        ).create({
                            'journal_id': journal.id,
                            'payment_method_id': payment_method.id if payment_method else False,
                            'amount': invoice.amount_residual,
                            'currency_id': order.currency_id.id,
                            'payment_date': fields.Date.today(),
                        })
                        
                        # Create and post the payment using the wizard
                        payment = payment_register_wizard._create_payments()
                        
                        if payment:
                            _logger.info(f"Marked invoice {invoice.name} as paid with payment for order {order.name}")
                        else:
                            # Fallback to direct payment creation if wizard fails
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
                            }
                            payment = self.env['account.payment'].create(payment_vals)
                            payment.action_post()
                            _logger.info(f"Marked invoice {invoice.name} as paid with payment {payment.name} for order {order.name} (fallback method)")
                    except Exception as e:
                        # If wizard approach fails, try direct payment creation
                        try:
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
                            }
                            payment = self.env['account.payment'].create(payment_vals)
                            payment.action_post()
                            _logger.info(f"Marked invoice {invoice.name} as paid with payment {payment.name} for order {order.name} (direct method)")
                        except Exception as e2:
                            warning_msg = f"Could not mark invoice {invoice.name} as paid: {str(e2)}"
                            _logger.warning(warning_msg)
                            import traceback
                            error_details = traceback.format_exc()
                            self._create_log('warning', warning_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}", error_details=error_details)
        except Exception as e:
            warning_msg = f"Error marking invoices as paid for order {order.name}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}", error_details=str(e))
    
    def action_sync_fulfillments(self):
        """Export fulfillment data from Odoo to BigCommerce"""
        self.ensure_one()
        
        # Create sync operation record for dashboard tracking
        sync_operation = self.env['bigcommerce.sync.operation'].create({
            'sync_type': 'fulfillment',
            'config_id': self.config_id.id,
            'sync_direction': 'odoo_to_bc',
            'state': 'running',
            'start_date': fields.Datetime.now(),
            'current_item': 'Initializing...',
        })
        self.sync_operation_id = sync_operation.id
        
        # Update state and commit immediately so UI shows "Running"
        self.write({
            'state': 'running',
            'total_items': 0,
            'processed_items': 0,
            'current_item': 'Initializing...',
        })
        self.env.cr.commit()
        
        try:
            api = self.config_id.get_api_client()
            self._export_fulfillments(api)
            
            self.state = 'done'
            total_items = self.fulfillments_exported + self.fulfillments_failed
            warnings_count = self.env['bigcommerce.sync.log'].search_count([
                ('config_id', '=', self.config_id.id),
                ('sync_type', '=', 'fulfillment'),
                ('log_level', '=', 'WARNING'),
                ('log_date', '>=', self.config_id.last_fulfillment_sync or fields.Datetime.now() - timedelta(days=1))
            ])
            self.config_id.write({
                'last_fulfillment_sync_total': total_items,
                'last_fulfillment_sync_updated': self.fulfillments_exported,
                'last_fulfillment_sync_failed': self.fulfillments_failed,
                'last_fulfillment_sync_warnings': warnings_count,
            })
            
            # Update sync operation record
            if self.sync_operation_id:
                if self.fulfillments_failed > 0:
                    state = 'completed_with_errors'
                elif warnings_count > 0:
                    state = 'completed_with_warnings'
                else:
                    state = 'completed'
                self.sync_operation_id.write({
                    'state': state,
                    'end_date': fields.Datetime.now(),
                    'total_items': total_items,
                    'processed_items': total_items,
                    'items_synced': self.fulfillments_exported,
                    'items_updated': self.fulfillments_exported,
                    'items_failed': self.fulfillments_failed,
                    'error_count': self.fulfillments_failed,
                    'warning_count': warnings_count,
                })
            
            if self.fulfillments_failed > 0 and self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Fulfillment',
                        error_message=f"Sync completed with {self.fulfillments_failed} failed item(s).",
                        updated=self.fulfillments_exported,
                        failed=self.fulfillments_failed,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send fulfillment sync failure notification: %s", mail_e)
            
        except UserError as e:
            # Check if this is a cancellation
            if 'cancelled' in str(e).lower():
                if self.sync_operation_id and self.sync_operation_id.state == 'running':
                    self.sync_operation_id.action_cancel()
                self.state = 'error'
                self.error_message = str(e)
                _logger.info(f"Fulfillment sync cancelled: {str(e)}")
            else:
                self.state = 'error'
                self.error_message = str(e)
                if self.config_id:
                    try:
                        self.config_id._send_sync_failure_email('Fulfillment', error_message=str(e), sync_name=self.name)
                    except Exception as mail_e:
                        _logger.warning("Could not send fulfillment sync failure notification: %s", mail_e)
                raise
        except Exception as e:
            self.state = 'error'
            self.error_message = str(e)
            if self.config_id:
                try:
                    import traceback
                    self.config_id._send_sync_failure_email(
                        'Fulfillment',
                        error_message=str(e),
                        details=traceback.format_exc(),
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send fulfillment sync failure notification: %s", mail_e)
            
            # Update sync operation record on error (only if not cancelled)
            if self.sync_operation_id and self.sync_operation_id.state != 'cancelled':
                self.sync_operation_id.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_count': self.fulfillments_failed + 1,
                })
            
            _logger.error(f"Fulfillment sync error: {str(e)}", exc_info=True)
            raise UserError(f"Fulfillment sync failed: {str(e)}")
    
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
    
    def _export_fulfillments(self, api):
        """Export shipment tracking and fulfillment status to BigCommerce"""
        # Find all delivered/picked sale orders that have BigCommerce IDs
        domain = [
            ('bigcommerce_id', '!=', False),
            ('bigcommerce_config_id', '=', self.config_id.id),
            ('state', 'in', ['sale', 'done']),
        ]
        
        if self.date_from:
            domain.append(('date_order', '>=', self.date_from))
        if self.date_to:
            domain.append(('date_order', '<=', self.date_to))
        if self.min_date_modified:
            domain.append(('write_date', '>=', self.min_date_modified))
            _logger.info(f"Filtering orders by write_date >= {self.min_date_modified} for fulfillment sync")
        
        orders = self.env['sale.order'].search(domain)
        _logger.info(f"Found {len(orders)} orders to check for fulfillment export")
        
        # Set total items
        self.total_items = len(orders)
        self.current_item = f"Found {len(orders)} orders to check. Starting..."
        self.env.cr.commit()
        
        for idx, order in enumerate(orders, 1):
            # Check if sync has been cancelled
            if self._check_cancelled():
                _logger.info("Fulfillment sync cancelled by user")
                raise UserError("Sync operation was cancelled by user")
            
            # Update progress
            self.current_item = f"Processing Order #{order.name or order.id}... (Item {idx}/{self.total_items})"
            self.processed_items = idx
            self._update_sync_operation()
            self.env.cr.commit()
            
            try:
                result = self._export_order_fulfillment(api, order)
                # result is a dict with 'exported' and 'failed' counts
                if result:
                    self.fulfillments_exported += result.get('exported', 0)
                    self.fulfillments_failed += result.get('failed', 0)
            except Exception as e:
                error_msg = f"Error exporting fulfillment for order {order.id} (BC ID: {order.bigcommerce_id}): {str(e)}"
                _logger.error(error_msg, exc_info=True)
                import traceback
                error_details = traceback.format_exc()
                self._create_log('error', error_msg, order_id=order.bigcommerce_id, order_name=f"Order #{order.name or order.id}", error_details=error_details)
                self.fulfillments_failed += 1
    
    def _export_order_fulfillment(self, api, order):
        """Export fulfillment for a single order
        
        Returns:
            dict with 'exported' and 'failed' counts, or None if order should be skipped entirely
        """
        bc_order_id = order.bigcommerce_id
        if not bc_order_id:
            _logger.warning(f"Order {order.id} has no BigCommerce ID, skipping")
            return None
        
        # Get existing shipments from BigCommerce
        existing_shipment = None
        try:
            existing_shipments = api.get_order_shipments(bc_order_id)
            _logger.debug(f"Order {bc_order_id} has {len(existing_shipments) if existing_shipments else 0} existing shipments")
            # Get the first existing shipment if any
            if existing_shipments:
                existing_shipment = existing_shipments[0]
                _logger.info(f"Order {bc_order_id}: Found existing shipment {existing_shipment.get('id')} with tracking number '{existing_shipment.get('tracking_number', 'N/A')}'")
        except Exception as e:
            warning_msg = f"Could not fetch existing shipments for order {bc_order_id}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
            existing_shipments = []
        
        # Calculate already-shipped quantities per order product ID
        # This is needed when shipping kit items in multiple packages
        shipped_quantities = {}  # {order_product_id: total_shipped_qty}
        for shipment in existing_shipments:
            shipment_items = shipment.get('items', [])
            for item in shipment_items:
                op_id = item.get('order_product_id')
                qty = item.get('quantity', 0)
                if op_id:
                    shipped_quantities[op_id] = shipped_quantities.get(op_id, 0) + qty
        
        # Get order products to know total ordered quantities
        try:
            order_products = api.get_order_products(bc_order_id)
            order_product_quantities = {}  # {order_product_id: total_ordered_qty}
            for op in order_products:
                op_id = op.get('id')
                op_qty = op.get('quantity', 0)
                if op_id:
                    order_product_quantities[op_id] = op_qty
        except Exception as e:
            warning_msg = f"Could not fetch order products for order {bc_order_id}: {str(e)}"
            _logger.warning(warning_msg)
            self._create_log('warning', warning_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
            order_product_quantities = {}
        
        # Get pickings (deliveries) for this order
        # Include both regular outgoing pickings and dropship pickings
        # Dropship pickings may have purchase_id set or be linked via purchase orders
        pickings = order.picking_ids.filtered(
            lambda p: p.state == 'done' and (
                p.picking_type_id.code == 'outgoing' or  # Regular deliveries
                p.purchase_id or  # Dropship pickings (linked to purchase orders)
                (hasattr(p.picking_type_id, 'code') and p.picking_type_id.code == 'dropship')  # Explicit dropship type if exists
            )
        )
        
        _logger.debug(f"Order {order.id}: Found {len(pickings)} pickings directly linked to sale order")
        
        # Also check for dropship pickings linked via purchase orders from this sale order
        # Dropship pickings might be linked through purchase.order -> stock.picking
        dropship_count = 0
        if hasattr(order, 'order_line'):
            for line in order.order_line:
                if hasattr(line, 'purchase_line_ids') and line.purchase_line_ids:
                    for purchase_line in line.purchase_line_ids:
                        purchase_order = purchase_line.order_id
                        if purchase_order and hasattr(purchase_order, 'picking_ids') and purchase_order.picking_ids:
                            dropship_pickings = purchase_order.picking_ids.filtered(
                                lambda p: p.state == 'done' and p not in pickings
                            )
                            if dropship_pickings:
                                pickings |= dropship_pickings
                                dropship_count += len(dropship_pickings)
                                _logger.info(f"Found {len(dropship_pickings)} dropship pickings from purchase order {purchase_order.id}: {[p.name for p in dropship_pickings]}")
        
        if not pickings:
            _logger.debug(f"Order {order.id} has no completed deliveries (including dropship), skipping fulfillment export")
            return {'exported': 0, 'failed': 0}
        
        _logger.info(f"Order {order.id}: Found {len(pickings)} total pickings to sync ({dropship_count} dropship): {[p.name for p in pickings]}")
        
        # Get carrier mapping from config
        carrier_mapping = self._get_carrier_mapping()
        
        # Get shipping addresses for the order (required for creating shipments)
        try:
            shipping_addresses = api.get_order_shipping_addresses(bc_order_id)
            if not shipping_addresses:
                error_msg = f"Order {bc_order_id} has no shipping addresses, cannot create shipment"
                _logger.error(error_msg)
                self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
                # Count as failure if there are pickings that need to be synced
                return {'exported': 0, 'failed': len(pickings)}
            # Use the first shipping address ID
            order_address_id = shipping_addresses[0].get('id') if shipping_addresses else None
            if not order_address_id:
                error_msg = f"Order {bc_order_id} shipping address has no ID, cannot create shipment"
                _logger.error(error_msg)
                self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
                # Count as failure if there are pickings that need to be synced
                return {'exported': 0, 'failed': len(pickings)}
        except Exception as e:
            error_msg = f"Error fetching shipping addresses for order {bc_order_id}: {str(e)}"
            _logger.error(error_msg, exc_info=True)
            import traceback
            error_details = traceback.format_exc()
            self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}", error_details=error_details)
            # Count as failure if there are pickings that need to be synced
            return {'exported': 0, 'failed': len(pickings)}
        
        exported_count = 0
        failed_count = 0
        
        # Filter out pickings that have already been exported
        pickings_to_process = [p for p in pickings if not p.bigcommerce_shipment_id]
        
        if not pickings_to_process:
            _logger.debug(f"Order {bc_order_id}: All pickings already exported")
            return {'exported': 0, 'failed': 0}
        
        # Collect tracking numbers from all pickings (including those already exported)
        # This allows us to update existing shipments with combined tracking numbers
        all_tracking_numbers = []
        for picking in pickings:
            tracking_number = picking.carrier_tracking_ref or picking.name
            if tracking_number:
                # Clean and validate tracking number
                tracking_number = str(tracking_number).strip()
                # Remove any invalid characters (keep alphanumeric, spaces, hyphens, and common tracking number chars)
                tracking_number = ''.join(c for c in tracking_number if c.isalnum() or c in (' ', '-', '/', '.'))
                if tracking_number and tracking_number not in all_tracking_numbers:
                    all_tracking_numbers.append(tracking_number)
                    _logger.debug(f"Picking {picking.id} ({picking.name}): Found tracking number '{tracking_number}'")
        
        _logger.info(f"Order {bc_order_id}: Collected {len(all_tracking_numbers)} unique tracking numbers from {len(pickings)} pickings: {all_tracking_numbers}")
        
        # Process each picking separately - one shipment per picking
        # Each shipment will have one tracking number for the products in that picking
        _logger.info(f"Order {bc_order_id}: Processing {len(pickings_to_process)} pickings separately (one shipment per picking)")
        
        for picking in pickings_to_process:
            result = self._create_single_picking_shipment(
                api, bc_order_id, order, picking, order_address_id, 
                carrier_mapping, shipped_quantities, order_product_quantities
            )
            if result:
                exported_count += result.get('exported', 0)
                failed_count += result.get('failed', 0)
        
        # Mark invoices as paid after successful shipment creation
        if exported_count > 0:
            self._mark_invoice_as_paid(order, bc_order_id)
        
        return {'exported': exported_count, 'failed': failed_count}
    
    def _get_carrier_mapping(self):
        """Get carrier mapping from configuration"""
        # This will be enhanced with a proper mapping table
        # For now, return a basic mapping
        return {
            'usps': 'usps',
            'fedex': 'fedex',
            'ups': 'ups',
            'dhl': 'dhl',
        }
    
    def _map_carrier(self, carrier, mapping):
        """Map Odoo carrier to BigCommerce carrier code"""
        if not carrier:
            return 'custom'
        
        carrier_name = carrier.name.lower()
        for key, value in mapping.items():
            if key in carrier_name:
                return value
        
        return 'custom'
    
    def _create_single_picking_shipment(self, api, bc_order_id, order, picking, order_address_id, 
                                        carrier_mapping, shipped_quantities, order_product_quantities):
        """Create a shipment for a single picking (normal case, not kit items)
        
        Returns:
            dict with 'exported' and 'failed' counts
        """
        # Get tracking number - use picking name as fallback if no tracking ref
        tracking_number = picking.carrier_tracking_ref or picking.name or f"PICKING-{picking.id}"
        if not tracking_number:
            # Last resort: generate a tracking number from picking ID
            tracking_number = f"ODOO-{picking.id}"
            _logger.warning(f"Picking {picking.id} has no tracking number, using generated: {tracking_number}")
        
        # Clean and validate tracking number
        if tracking_number:
            tracking_number = str(tracking_number).strip()
            # Remove any invalid characters (keep alphanumeric, spaces, hyphens, and common tracking number chars)
            tracking_number = ''.join(c for c in tracking_number if c.isalnum() or c in (' ', '-', '/', '.'))
        
        # Map carrier
        carrier_code = self._map_carrier(picking.carrier_id, carrier_mapping)
        
        # Prepare shipment data
        shipment_data = {
            'order_address_id': order_address_id,  # Required field
            'tracking_number': tracking_number,
            'comments': f"Shipped via {picking.carrier_id.name if picking.carrier_id else 'Standard Shipping'}",
        }
        
        # Only include tracking_carrier if we have a valid carrier code (not 'custom')
        if carrier_code and carrier_code != 'custom':
            shipment_data['tracking_carrier'] = carrier_code
        
        # Get order items to include in shipment
        shipment_items = []
        for move in picking.move_ids.filtered(lambda m: m.sale_line_id):
            # Find corresponding BigCommerce order product
            product = move.sale_line_id.product_id
            bc_variant_id = product.bigcommerce_id  # Variant ID if exists
            bc_product_id = product.product_tmpl_id.bigcommerce_id  # Template ID
            product_sku = product.default_code or product.barcode  # SKU for fallback matching
            
            if bc_variant_id or bc_product_id:
                # In Odoo 19, qty_done is on stock.move.line, not stock.move
                quantity_done = sum(move.move_line_ids.mapped('qty_done')) if move.move_line_ids else 0
                quantity = int(quantity_done) if quantity_done > 0 else int(move.product_uom_qty)
                
                # Get BigCommerce order product ID
                order_product_id = self._get_bc_order_product_id(
                    api, bc_order_id, 
                    bc_variant_id=bc_variant_id,
                    bc_product_id=bc_product_id,
                    product_sku=product_sku
                )
                
                if order_product_id:
                    # Calculate how much has already been shipped
                    already_shipped = shipped_quantities.get(order_product_id, 0)
                    # Get total ordered quantity from order products
                    total_ordered = order_product_quantities.get(order_product_id)
                    if total_ordered is None:
                        # If we don't have the order product quantity, fetch it
                        try:
                            order_products = api.get_order_products(bc_order_id)
                            for op in order_products:
                                if op.get('id') == order_product_id:
                                    total_ordered = op.get('quantity', 0)
                                    # Update the dictionary for future use
                                    order_product_quantities[order_product_id] = total_ordered
                                    break
                            if total_ordered is None:
                                _logger.warning(
                                    f"Picking {picking.id}: Could not find order product {order_product_id} "
                                    f"in order {bc_order_id} to get ordered quantity. Using quantity from picking."
                                )
                                total_ordered = quantity
                        except Exception as e:
                            _logger.warning(f"Could not fetch order products to get quantity: {str(e)}")
                            total_ordered = quantity
                    
                    # Ensure we don't exceed what's available
                    remaining_available = max(0, total_ordered - already_shipped)
                    quantity_to_ship = min(quantity, remaining_available)
                    
                    # Log if we're reducing the quantity
                    if quantity_to_ship < quantity:
                        _logger.info(
                            f"Picking {picking.id}: Order product {order_product_id} - "
                            f"Requested: {quantity}, Available: {remaining_available}, "
                            f"Already shipped: {already_shipped}, Total ordered: {total_ordered}, "
                            f"Will ship: {quantity_to_ship}"
                        )
                    
                    if quantity_to_ship > 0:
                        shipment_items.append({
                            'order_product_id': order_product_id,
                            'quantity': quantity_to_ship,
                        })
                        
                        if quantity_to_ship < quantity:
                            _logger.warning(
                                f"Picking {picking.id}: Reducing quantity for order product {order_product_id} "
                                f"from {quantity} to {quantity_to_ship} (already shipped: {already_shipped}, "
                                f"total ordered: {total_ordered})"
                            )
                    else:
                        _logger.warning(
                            f"Picking {picking.id}: Skipping order product {order_product_id} - "
                            f"already fully shipped ({already_shipped}/{total_ordered})"
                        )
        
        # Fallback to all order products if no items matched
        if not shipment_items:
            try:
                all_order_products = api.get_order_products(bc_order_id)
                for op in all_order_products:
                    op_id = op.get('id')
                    op_qty = op.get('quantity', 1)
                    if op_id:
                        # Check against already shipped quantities
                        already_shipped = shipped_quantities.get(op_id, 0)
                        remaining_available = op_qty - already_shipped
                        quantity_to_ship = max(0, int(remaining_available))
                        
                        if quantity_to_ship > 0:
                            shipment_items.append({
                                'order_product_id': op_id,
                                'quantity': quantity_to_ship,
                            })
                        else:
                            _logger.debug(
                                f"Picking {picking.id}: Skipping order product {op_id} in fallback - "
                                f"already fully shipped ({already_shipped}/{op_qty})"
                            )
                _logger.info(f"Added {len(shipment_items)} order products as fallback for picking {picking.id}")
            except Exception as e:
                _logger.error(f"Error getting order products for fallback: {str(e)}", exc_info=True)
        
        if not shipment_items:
            error_msg = (
                f"Picking {picking.id} (Order {order.name}, BC ID: {bc_order_id}) has no items to include in shipment."
            )
            _logger.error(error_msg)
            return {'exported': 0, 'failed': 1}
        
        # Final validation: double-check all quantities before creating shipment
        # Fetch current order products to ensure we have accurate quantities
        try:
            current_order_products = api.get_order_products(bc_order_id)
            current_order_qty_map = {op.get('id'): op.get('quantity', 0) for op in current_order_products if op.get('id')}
            
            # Re-validate each item quantity
            validated_items = []
            for item in shipment_items:
                op_id = item['order_product_id']
                qty_to_ship = item['quantity']
                
                # Get current totals
                total_ordered = current_order_qty_map.get(op_id, 0)
                already_shipped = shipped_quantities.get(op_id, 0)
                remaining = max(0, total_ordered - already_shipped)
                
                # Ensure we don't exceed remaining
                final_qty = min(qty_to_ship, remaining)
                
                if final_qty > 0:
                    validated_items.append({
                        'order_product_id': op_id,
                        'quantity': final_qty,
                    })
                    if final_qty < qty_to_ship:
                        _logger.warning(
                            f"Picking {picking.id}: Final validation reduced quantity for order product {op_id} "
                            f"from {qty_to_ship} to {final_qty} (ordered: {total_ordered}, "
                            f"already shipped: {already_shipped}, remaining: {remaining})"
                        )
                else:
                    _logger.warning(
                        f"Picking {picking.id}: Final validation removed order product {op_id} - "
                        f"no quantity available (ordered: {total_ordered}, already shipped: {already_shipped})"
                    )
            
            if not validated_items:
                error_msg = (
                    f"Picking {picking.id}: After final validation, no items remain to ship "
                    f"(all quantities already shipped or invalid)"
                )
                _logger.error(error_msg)
                return {'exported': 0, 'failed': 1}
            
            shipment_items = validated_items
        except Exception as e:
            _logger.warning(f"Could not perform final quantity validation: {str(e)}. Proceeding with original quantities.")
        
        shipment_data['items'] = shipment_items
        
        # Log what we're about to ship
        _logger.info(
            f"Creating shipment for order {bc_order_id} with tracking {tracking_number}. "
            f"Items: {[(item['order_product_id'], item['quantity']) for item in shipment_items]}"
        )
        
        try:
            # Create shipment in BigCommerce
            shipment = api.create_order_shipment(bc_order_id, shipment_data)
            
            if shipment and shipment.get('id'):
                picking.write({
                    'bigcommerce_shipment_id': shipment.get('id'),
                    'bigcommerce_synced': True,
                    'bigcommerce_last_sync': fields.Datetime.now(),
                })
                _logger.info(f"Successfully created shipment {shipment.get('id')} for order {bc_order_id}")
                
                # Update order status if enabled
                if self.update_order_status:
                    self._update_order_status_after_shipment(api, bc_order_id, order)
                
                return {'exported': 1, 'failed': 0}
            else:
                error_msg = f"Shipment created but no ID returned for order {bc_order_id} (Picking {picking.id})"
                _logger.error(error_msg)
                self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}")
                return {'exported': 0, 'failed': 1}
                
        except Exception as e:
            error_msg = f"Error creating shipment for order {bc_order_id} (Picking {picking.id}): {str(e)}"
            _logger.error(error_msg, exc_info=True)
            import traceback
            error_details = traceback.format_exc()
            self._create_log('error', error_msg, order_id=bc_order_id, order_name=f"Order #{order.name or order.id}", error_details=error_details)
            return {'exported': 0, 'failed': 1}
    
    def _update_order_status_after_shipment(self, api, bc_order_id, order):
        """Update BigCommerce order status after shipment creation
        
        Matches Fishbowl behavior: Updates order status to Shipped or Partially Shipped
        based on whether all items have been shipped.
        
        Args:
            api: BigCommerce API client
            bc_order_id: BigCommerce order ID
            order: Odoo sale.order record
        """
        try:
            # Get current order from BigCommerce to check quantities
            bc_order = api.get_order(bc_order_id)
            if not bc_order:
                _logger.warning(f"Could not fetch order {bc_order_id} to update status")
                return
            
            # Get all order products and their quantities
            order_products = api.get_order_products(bc_order_id)
            if not order_products:
                _logger.warning(f"Could not fetch order products for order {bc_order_id}")
                return
            
            # Get all shipments for this order
            shipments = api.get_order_shipments(bc_order_id)
            
            # Calculate total ordered and total shipped quantities
            total_ordered = {}
            total_shipped = {}
            
            for op in order_products:
                op_id = op.get('id')
                op_qty = op.get('quantity', 0)
                total_ordered[op_id] = op_qty
                total_shipped[op_id] = 0
            
            # Sum up shipped quantities from all shipments
            for shipment in shipments:
                shipment_items = shipment.get('items', [])
                for item in shipment_items:
                    op_id = item.get('order_product_id')
                    qty = item.get('quantity', 0)
                    if op_id in total_shipped:
                        total_shipped[op_id] += qty
            
            # Determine if order is fully shipped or partially shipped
            all_shipped = True
            some_shipped = False
            
            for op_id, ordered_qty in total_ordered.items():
                shipped_qty = total_shipped.get(op_id, 0)
                if shipped_qty < ordered_qty:
                    all_shipped = False
                if shipped_qty > 0:
                    some_shipped = True
            
            # Determine new status
            # BigCommerce status IDs: 2 = Shipped, 3 = Partially Shipped
            current_status_id = bc_order.get('status_id')
            new_status_id = None
            
            if all_shipped and some_shipped:
                new_status_id = 2  # Shipped
                _logger.info(f"All items shipped for order {bc_order_id}, updating status to Shipped")
            elif some_shipped:
                new_status_id = 3  # Partially Shipped
                _logger.info(f"Some items shipped for order {bc_order_id}, updating status to Partially Shipped")
            
            # Update order status if it needs to change
            if new_status_id and new_status_id != current_status_id:
                try:
                    api.update_order(bc_order_id, {'status_id': new_status_id})
                    _logger.info(f"Updated order {bc_order_id} status from {current_status_id} to {new_status_id}")
                except Exception as e:
                    _logger.error(f"Error updating order status for {bc_order_id}: {str(e)}", exc_info=True)
            else:
                _logger.debug(f"Order {bc_order_id} status does not need updating (current: {current_status_id})")
                
        except Exception as e:
            _logger.error(f"Error in _update_order_status_after_shipment for order {bc_order_id}: {str(e)}", exc_info=True)
            # Don't raise - status update failure shouldn't fail the shipment creation
    
    def _get_bc_order_product_id(self, api, bc_order_id, bc_variant_id=None, bc_product_id=None, product_sku=None):
        """Get BigCommerce order product ID for a product
        
        Tries multiple matching strategies:
        1. Match by variant_id (if product has variants)
        2. Match by product_id (template)
        3. Match by SKU (fallback)
        
        Args:
            api: BigCommerce API client
            bc_order_id: BigCommerce order ID
            bc_variant_id: BigCommerce variant ID (optional)
            bc_product_id: BigCommerce product ID/template ID (optional)
            product_sku: Product SKU for fallback matching (optional)
        
        Returns:
            BigCommerce order product ID, or None if not found
        """
        try:
            order_products = api.get_order_products(bc_order_id)
            if not order_products:
                _logger.debug(f"No order products found for order {bc_order_id}")
                return None
            
            # Strategy 1: Try to match by variant_id first (most specific)
            if bc_variant_id:
                for op in order_products:
                    op_variant_id = op.get('variant_id')
                    if op_variant_id and op_variant_id == bc_variant_id:
                        order_product_id = op.get('id')
                        if order_product_id:
                            _logger.debug(
                                f"Found order product ID {order_product_id} by variant_id {bc_variant_id} "
                                f"in order {bc_order_id}"
                            )
                            return order_product_id
            
            # Strategy 2: Try to match by product_id (template)
            if bc_product_id:
                for op in order_products:
                    op_product_id = op.get('product_id')
                    if op_product_id == bc_product_id:
                        order_product_id = op.get('id')
                        if order_product_id:
                            _logger.debug(
                                f"Found order product ID {order_product_id} by product_id {bc_product_id} "
                                f"in order {bc_order_id}"
                            )
                            return order_product_id
            
            # Strategy 3: Try to match by SKU (fallback)
            if product_sku:
                for op in order_products:
                    op_sku = op.get('sku', '').strip()
                    if op_sku and product_sku and op_sku.lower() == product_sku.lower():
                        order_product_id = op.get('id')
                        if order_product_id:
                            _logger.debug(
                                f"Found order product ID {order_product_id} by SKU {product_sku} "
                                f"in order {bc_order_id}"
                            )
                            return order_product_id
            
            # If no match found, log available products for debugging
            available_info = []
            for op in order_products:
                info = f"id={op.get('id')}, product_id={op.get('product_id')}, variant_id={op.get('variant_id')}, sku={op.get('sku')}"
                available_info.append(info)
            
            _logger.warning(
                f"Could not find order product for variant_id={bc_variant_id}, product_id={bc_product_id}, "
                f"sku={product_sku} in order {bc_order_id}. Available order products: {available_info}"
            )
        except Exception as e:
            _logger.warning(f"Error getting order products for order {bc_order_id}: {str(e)}", exc_info=True)
        return None


class StockPicking(models.Model):
    _inherit = 'stock.picking'
    
    bigcommerce_shipment_id = fields.Integer(string='BigCommerce Shipment ID', copy=False, index=True)
    bigcommerce_synced = fields.Boolean(string='Synced with BigCommerce', default=False)
    bigcommerce_last_sync = fields.Datetime(string='Last Sync with BigCommerce')

