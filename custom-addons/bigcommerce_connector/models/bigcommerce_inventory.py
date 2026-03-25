# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

_logger = logging.getLogger(__name__)


class BigCommerceInventorySync(models.Model):
    _name = 'bigcommerce.inventory.sync'
    _description = 'BigCommerce Inventory Sync'
    _order = 'sync_date desc'

    name = fields.Char(string='Sync Name', required=True, default=lambda self: f"Inventory Sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, required=True)
    sync_direction = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
    ], string='Sync Direction', required=True, default='odoo_to_bc')
    
    # Filters
    date_from = fields.Datetime(string='Date From', help='Sync inventory for products created from this date')
    date_to = fields.Datetime(string='Date To', help='Sync inventory for products created until this date')
    min_date_modified = fields.Datetime(string='Min Date Modified', help='Sync inventory for products modified after this date')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='State', default='draft')
    
    products_synced = fields.Integer(string='Products Synced', default=0)
    products_failed = fields.Integer(string='Products Failed', default=0)
    error_message = fields.Text(string='Error Message')
    
    # Progress tracking
    total_items = fields.Integer(string='Total Items', default=0, help='Total number of items to process')
    processed_items = fields.Integer(string='Processed Items', default=0, help='Number of items processed so far')
    progress_percentage = fields.Float(string='Progress', compute='_compute_progress', store=False, help='Progress percentage')
    current_item = fields.Char(string='Current Item', help='Currently processing item')
    
    # Link to sync operation for dashboard tracking
    sync_operation_id = fields.Many2one('bigcommerce.sync.operation', string='Sync Operation', ondelete='set null')
    
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
                    'items_synced': self.products_synced,
                    'items_updated': self.products_synced,
                    'items_failed': self.products_failed,
                })
                # Also update the sync record itself to ensure UI sees progress
                self.write({
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                })
                self.env.cr.commit()
            except Exception as e:
                _logger.warning(f"Failed to update sync operation: {str(e)}")
    
    def _create_log(self, log_level, message, product_id=None, product_name=None, error_details=None, 
                    request_url=None, request_method=None, response_status=None, response_data=None):
        """Create a sync log entry"""
        try:
            log_vals = {
                'sync_type': 'inventory',
                'sync_record_id': self.id,
                'sync_operation_id': self.sync_operation_id.id if self.sync_operation_id else False,
                'config_id': self.config_id.id,
                'log_level': log_level,
                'message': message,
                'product_id': product_id,
                'product_name': product_name,
                'error_details': error_details,
                'request_url': request_url,
                'request_method': request_method,
                'response_status': response_status,
                'response_data': response_data,
            }
            # Use sudo() to bypass permission checks - logs are system-level records
            return self.env['bigcommerce.sync.log'].sudo().create(log_vals)
        except Exception as e:
            # If log creation fails, at least log to the standard logger
            _logger.warning(f"Failed to create sync log entry: {str(e)}")
            return False
    
    def action_sync_inventory(self):
        """Sync inventory between Odoo and BigCommerce"""
        self.ensure_one()
        
        # Auto-set min_date_modified to last successful inventory sync if not set
        # This ensures we only sync inventory that has changed since the last successful sync
        if not self.min_date_modified:
            if self.config_id.last_inventory_sync:
                self.min_date_modified = self.config_id.last_inventory_sync
                _logger.info(f"Auto-set min_date_modified to last successful inventory sync: {self.min_date_modified}")
            else:
                # If no last sync, check the last successful sync operation
                last_sync_op = self.env['bigcommerce.sync.operation'].search([
                    ('sync_type', '=', 'inventory'),
                    ('config_id', '=', self.config_id.id),
                    ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                    ('end_date', '!=', False),
                ], order='end_date desc', limit=1)
                if last_sync_op:
                    self.min_date_modified = last_sync_op.end_date
                    _logger.info(f"Auto-set min_date_modified to last successful inventory sync operation end date: {self.min_date_modified}")
        
        # Create sync operation record for dashboard tracking
        sync_operation = self.env['bigcommerce.sync.operation'].create({
            'sync_type': 'inventory',
            'config_id': self.config_id.id,
            'sync_direction': self.sync_direction,
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
            
            # Only sync from Odoo to BigCommerce (inventory sync is one-way only)
            if self.sync_direction == 'odoo_to_bc':
                self._sync_to_bigcommerce(api)
            
            self.state = 'done'
            total_items = self.products_synced + self.products_failed
            warnings_count = self.env['bigcommerce.sync.log'].search_count([
                ('config_id', '=', self.config_id.id),
                ('sync_type', '=', 'inventory'),
                ('log_level', '=', 'WARNING'),
                ('log_date', '>=', self.config_id.last_inventory_sync or fields.Datetime.now() - timedelta(days=1))
            ])
            self.config_id.write({
                'last_inventory_sync_total': total_items,
                'last_inventory_sync_updated': self.products_synced,
                'last_inventory_sync_failed': self.products_failed,
                'last_inventory_sync_warnings': warnings_count,
            })
            
            # Update sync operation record
            if self.sync_operation_id:
                if self.products_failed > 0:
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
                    'items_synced': self.products_synced,
                    'items_updated': self.products_synced,
                    'items_failed': self.products_failed,
                    'error_count': self.products_failed,
                    'warning_count': warnings_count,
                })
            
            if self.products_failed > 0 and self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Inventory',
                        error_message=f"Sync completed with {self.products_failed} failed item(s).",
                        updated=self.products_synced,
                        failed=self.products_failed,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send inventory sync failure notification: %s", mail_e)
            
        except UserError as e:
            # Check if this is a cancellation
            if 'cancelled' in str(e).lower():
                if self.sync_operation_id and self.sync_operation_id.state == 'running':
                    self.sync_operation_id.action_cancel()
                self.state = 'error'
                self.error_message = str(e)
                _logger.info(f"Inventory sync cancelled: {str(e)}")
            else:
                self.state = 'error'
                self.error_message = str(e)
                if self.config_id:
                    try:
                        self.config_id._send_sync_failure_email('Inventory', error_message=str(e), sync_name=self.name)
                    except Exception as mail_e:
                        _logger.warning("Could not send inventory sync failure notification: %s", mail_e)
                raise
        except Exception as e:
            self.state = 'error'
            self.error_message = str(e)
            if self.config_id:
                try:
                    import traceback
                    self.config_id._send_sync_failure_email(
                        'Inventory',
                        error_message=str(e),
                        details=traceback.format_exc(),
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send inventory sync failure notification: %s", mail_e)
            
            # Update sync operation record on error (only if not cancelled)
            if self.sync_operation_id and self.sync_operation_id.state != 'cancelled':
                self.sync_operation_id.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_count': self.products_failed + 1,
                })
            
            _logger.error(f"Inventory sync error: {str(e)}")
            raise UserError(f"Inventory sync failed: {str(e)}")
    
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
    
    def _fetch_inventory_data(self, api, product_id, product_name):
        """Helper method to fetch inventory data from BigCommerce (can be called in parallel)"""
        try:
            bc_inventory = api.get_product_inventory(product_id)
            return {
                'product_id': product_id,
                'product_name': product_name,
                'inventory': bc_inventory,
                'error': None
            }
        except Exception as e:
            import traceback
            return {
                'product_id': product_id,
                'product_name': product_name,
                'inventory': None,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
    
    def _sync_from_bigcommerce(self, api):
        """Sync inventory from BigCommerce to Odoo using parallel processing"""
        # Get all products that are synced with BigCommerce
        domain = [
            ('bigcommerce_id', '!=', False),
            ('bigcommerce_config_id', '=', self.config_id.id)
        ]
        # Filter by write_date if min_date_modified is set (for auto sync)
        if self.min_date_modified:
            domain.append(('write_date', '>=', self.min_date_modified))
            _logger.info(f"Filtering Odoo products by write_date >= {self.min_date_modified} for inventory sync")
        products = self.env['product.template'].search(domain)
        
        # Set total items
        self.total_items = len(products)
        self.current_item = f"Found {len(products)} products to sync. Starting parallel processing..."
        self._update_sync_operation()
        self.env.cr.commit()
        
        # Get location mappings (preferred) or warehouse mappings (fallback)
        location_mappings = self.config_id.location_mapping_ids.filtered(lambda l: l.active)
        default_location = location_mappings.filtered(lambda l: l.is_default) if location_mappings else False
        
        # Fallback to warehouse mappings if no location mappings are configured
        warehouse_mappings = self.config_id.warehouse_mapping_ids.filtered(lambda w: w.active) if not location_mappings else False
        default_warehouse = warehouse_mappings.filtered(lambda w: w.is_default) if warehouse_mappings else False
        
        # Process products in batches with parallel API calls
        batch_size = 50  # Process 50 products before committing
        max_workers = 10  # Number of parallel API calls (adjust based on BigCommerce rate limits - typically 10-20 requests/second)
        progress_update_interval = 10  # Only update progress every 10 products
        
        for batch_start in range(0, len(products), batch_size):
            batch_products = products[batch_start:batch_start + batch_size]
            
            # Fetch inventory data in parallel for this batch
            _logger.info(f"Fetching inventory data for batch {batch_start // batch_size + 1} ({len(batch_products)} products) using {max_workers} parallel workers...")
            inventory_results = {}
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all API calls for this batch
                future_to_product = {
                    executor.submit(self._fetch_inventory_data, api, product.bigcommerce_id, product.name): product
                    for product in batch_products
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_product):
                    product = future_to_product[future]
                    try:
                        result = future.result()
                        inventory_results[product.id] = result
                    except Exception as e:
                        _logger.error(f"Error fetching inventory for product {product.name}: {str(e)}")
                        inventory_results[product.id] = {
                            'product_id': product.bigcommerce_id,
                            'product_name': product.name,
                            'inventory': None,
                            'error': str(e)
                        }
            
            # Now process results sequentially (Odoo ORM is not thread-safe)
            _logger.info(f"Processing {len(inventory_results)} inventory results...")
            for idx, product in enumerate(batch_products, 1):
                # Check if sync has been cancelled
                if self._check_cancelled():
                    _logger.info("Inventory sync cancelled by user")
                    raise UserError("Sync operation was cancelled by user")
                
                actual_idx = batch_start + idx
                # Update progress less frequently to reduce overhead (every 10 products)
                if actual_idx % progress_update_interval == 0 or idx == 1:
                    self.current_item = f"Processing: {product.name[:50]}... (Item {actual_idx}/{self.total_items})"
                    self.processed_items = actual_idx
                    self._update_sync_operation()
                    # Use write() without commit for better performance
                    self.write({'current_item': self.current_item, 'processed_items': self.processed_items})
                
                # Check if product tracks inventory in both systems before processing
                odoo_tracks, bc_tracks, bc_inventory_tracking = self._check_inventory_tracking(product, api)
                
                # Skip products that don't track inventory in both systems
                if not odoo_tracks and not bc_tracks:
                    _logger.debug(f"Skipping product {product.name} (BC ID: {product.bigcommerce_id}) - inventory tracking disabled in both Odoo (is_storable=False) and BigCommerce (inventory_tracking='{bc_inventory_tracking}')")
                    continue
                
                # Get the inventory result for this product
                result = inventory_results.get(product.id)
                if not result:
                    warning_msg = f"No inventory result for product {product.name}"
                    _logger.warning(f"{warning_msg} (ID: {product.id})")
                    self._create_log(
                        'warning',
                        warning_msg,
                        product_id=product.bigcommerce_id,
                        product_name=product.name,
                        error_details="Inventory data fetch returned no result for this product"
                    )
                    self.products_failed += 1
                    continue
                
                try:
                    # Handle errors from API call
                    if result.get('error'):
                        error_msg = f"Error fetching inventory: {result['error']}"
                        _logger.warning(f"✗ {error_msg} Product: {product.name} (BC ID: {product.bigcommerce_id})")
                        self._create_log(
                            'error',
                            error_msg,
                            product_id=product.bigcommerce_id,
                            product_name=product.name,
                            error_details=result.get('traceback', result['error'])
                        )
                        self.products_failed += 1
                        continue
                    
                    bc_inventory = result.get('inventory')
                    _logger.debug(f"Inventory API response for {product.name}: {bc_inventory}")
                    
                    # Handle 404 - inventory endpoint not available or product doesn't exist
                    if bc_inventory is None:
                        _logger.debug(f"Inventory API returned None for {product.name} (BC ID: {product.bigcommerce_id}), checking if product exists...")
                        # Skip the extra API call - if inventory is None, just log and continue
                        # This avoids a second API call per product when inventory endpoint returns 404
                        error_msg = f"Inventory endpoint not available for this product. Inventory may be managed at variant level or through inventory locations."
                        _logger.debug(f"Product {product.name} (BC ID: {product.bigcommerce_id}) - inventory endpoint returned 404. Skipping.")
                        self._create_log(
                            'warning',
                            error_msg,
                            product_id=product.bigcommerce_id,
                            product_name=product.name,
                            error_details=f"Inventory endpoint not available. May need variant-level inventory sync.",
                            response_status=404
                        )
                        self.products_failed += 1
                        continue
                    
                    # Process inventory if we got data (even if empty dict)
                    if bc_inventory is not None:
                        # Check if BigCommerce product has variants by fetching variants from BigCommerce
                        # This determines whether to sync product-level or variant-level inventory
                        bc_has_variants = False
                        bc_variants = []
                        
                        try:
                            bc_variants = api.get_product_variants(product.bigcommerce_id)
                            # BigCommerce product has variants if there are more than 1 variant
                            # (1 variant is the base variant, which is still product-level inventory)
                            bc_has_variants = bc_variants and len(bc_variants) > 1
                        except Exception as variant_check_error:
                            _logger.debug(f"Could not check variants for product {product.name} (BC ID: {product.bigcommerce_id}): {str(variant_check_error)}")
                            # If we can't check variants, assume no variants and use product-level inventory
                            bc_has_variants = False
                        
                        if bc_has_variants:
                            # BigCommerce product has variants - sync each variant individually
                            _logger.info(f"BigCommerce product {product.name} has {len(bc_variants)} variants, syncing variant-level inventory...")
                            
                            if not bc_variants:
                                warning_msg = f"No variants found in BigCommerce for product {product.name}"
                                _logger.warning(f"{warning_msg} (BC ID: {product.bigcommerce_id})")
                                self._create_log(
                                    'warning',
                                    warning_msg,
                                    product_id=product.bigcommerce_id,
                                    product_name=product.name,
                                    error_details="Product has variants in Odoo but no variants found in BigCommerce"
                                )
                                self.products_failed += 1
                                continue
                            
                            try:
                                
                                # Create a mapping of BigCommerce variant IDs to Odoo variants
                                bc_variant_map = {}
                                odoo_variant_ids_seen = set()  # Track to detect duplicate mappings
                                for odoo_variant in product.product_variant_ids:
                                    if odoo_variant.bigcommerce_variant_id:
                                        bc_variant_id = odoo_variant.bigcommerce_variant_id
                                        if bc_variant_id in bc_variant_map:
                                            warning_msg = f"Duplicate BigCommerce variant ID {bc_variant_id} found! Multiple Odoo variants map to the same BC variant."
                                            _logger.warning(warning_msg)
                                            self._create_log(
                                                'warning',
                                                warning_msg,
                                                product_id=product.bigcommerce_id,
                                                product_name=product.name,
                                                error_details=f"Variant ID {bc_variant_id} is mapped to multiple Odoo variants"
                                            )
                                        if odoo_variant.id in odoo_variant_ids_seen:
                                            warning_msg = f"Odoo variant {odoo_variant.name} (ID: {odoo_variant.id}) is being mapped multiple times!"
                                            _logger.warning(warning_msg)
                                            self._create_log(
                                                'warning',
                                                warning_msg,
                                                product_id=product.bigcommerce_id,
                                                product_name=product.name,
                                                error_details=f"Variant {odoo_variant.name} is mapped to multiple BigCommerce variants"
                                            )
                                        bc_variant_map[bc_variant_id] = odoo_variant
                                        odoo_variant_ids_seen.add(odoo_variant.id)
                                        _logger.debug(f"Mapped BC Variant ID {bc_variant_id} to Odoo variant {odoo_variant.name} (ID: {odoo_variant.id})")
                                
                                _logger.debug(f"Created variant mapping: {len(bc_variant_map)} Odoo variants mapped to BigCommerce variants")
                                _logger.debug(f"BigCommerce has {len(bc_variants)} variants for product {product.name}")
                                
                                # Sync inventory for each variant
                                variants_synced = 0
                                variants_failed = 0
                                
                                for bc_variant in bc_variants:
                                    bc_variant_id = bc_variant.get('id')
                                    if not bc_variant_id:
                                        continue
                                    
                                    # Find matching Odoo variant
                                    odoo_variant = bc_variant_map.get(bc_variant_id)
                                    if not odoo_variant:
                                        _logger.debug(f"No matching Odoo variant found for BigCommerce variant ID {bc_variant_id} in product {product.name}")
                                        variants_failed += 1
                                        continue
                                    
                                    # Get variant inventory from BigCommerce
                                    # First check if inventory_level is in the variant data itself (common in BigCommerce API)
                                    variant_qty = None
                                    if 'inventory_level' in bc_variant:
                                        variant_qty = float(bc_variant.get('inventory_level', 0))
                                        _logger.debug(f"Found inventory_level in variant data for variant {bc_variant_id}: {variant_qty}")
                                    else:
                                        # Fall back to separate inventory endpoint if not in variant data
                                        try:
                                            variant_inventory = api.get_variant_inventory(product.bigcommerce_id, bc_variant_id)
                                            if variant_inventory is None:
                                                _logger.debug(f"Variant inventory not available for variant {bc_variant_id} in product {product.name}")
                                                variants_failed += 1
                                                continue
                                            
                                            variant_qty = float(variant_inventory.get('inventory_level', 0) if variant_inventory else 0)
                                        except Exception as inventory_fetch_error:
                                            _logger.warning(f"Could not fetch inventory for variant {bc_variant_id} in product {product.name}: {str(inventory_fetch_error)}")
                                            variants_failed += 1
                                            continue
                                    
                                    if variant_qty is None:
                                        _logger.debug(f"Could not determine inventory for variant {bc_variant_id} in product {product.name}")
                                        variants_failed += 1
                                        continue
                                    
                                    _logger.info(f"Got inventory for variant {odoo_variant.name} (BC Variant ID: {bc_variant_id}): qty={variant_qty}")
                                    
                                    # Update this variant's inventory in Odoo
                                    try:
                                        update_result = self._update_variant_inventory_in_odoo(odoo_variant, variant_qty, warehouse_mappings, default_warehouse)
                                        if update_result is True:
                                            variants_synced += 1
                                        elif update_result == 'SKIPPED':
                                            # Silently skip variants without inventory tracking - don't count as failure
                                            _logger.debug(f"Skipping variant {odoo_variant.name} - inventory tracking disabled")
                                            continue
                                        else:
                                            error_reason = update_result if isinstance(update_result, str) else "Unknown error"
                                            _logger.warning(f"Failed to update inventory for variant {odoo_variant.name}: {error_reason}")
                                            variants_failed += 1
                                    except Exception as variant_error:
                                        _logger.error(f"Error syncing inventory for variant {odoo_variant.name}: {str(variant_error)}")
                                        variants_failed += 1
                                
                                # Count product as synced if at least one variant was synced
                                if variants_synced > 0:
                                    self.products_synced += 1
                                    _logger.info(f"✓ Synced inventory for {variants_synced} variant(s) of product {product.name} (BC ID: {product.bigcommerce_id})")
                                else:
                                    error_msg = f"Failed to sync inventory for all variants of product {product.name}"
                                    _logger.warning(f"✗ {error_msg} - Products Failed: {self.products_failed + 1}")
                                    self._create_log(
                                        'warning',
                                        error_msg,
                                        product_id=product.bigcommerce_id,
                                        product_name=product.name,
                                        error_details=f"Failed to sync {variants_failed} variant(s)"
                                    )
                                    self.products_failed += 1
                            except Exception as variant_sync_error:
                                error_msg = f"Error syncing variant inventory from BigCommerce: {str(variant_sync_error)}"
                                _logger.error(f"✗ {error_msg} Product: {product.name} (BC ID: {product.bigcommerce_id}) - Products Failed: {self.products_failed + 1}")
                                self._create_log(
                                    'error',
                                    error_msg,
                                    product_id=product.bigcommerce_id,
                                    product_name=product.name,
                                    error_details=str(variant_sync_error)
                                )
                                self.products_failed += 1
                        else:
                            # BigCommerce product without variants - sync product-level inventory to Odoo product
                            qty_available = float(bc_inventory.get('inventory_level', 0) if bc_inventory else 0)
                            _logger.info(f"BigCommerce product {product.name} has no variants, syncing product-level inventory: qty={qty_available}")
                            
                            # Get the product variant (single variant for non-variant products)
                            product_variant = product.product_variant_id
                            if not product_variant and product.product_variant_ids:
                                product_variant = product.product_variant_ids[0]
                            
                            if not product_variant:
                                error_msg = f"Product has no variant to sync inventory for."
                                _logger.warning(f"✗ {error_msg} Product: {product.name} (BC ID: {product.bigcommerce_id}) - Products Failed: {self.products_failed + 1}")
                                self._create_log(
                                    'warning',
                                    error_msg,
                                    product_id=product.bigcommerce_id,
                                    product_name=product.name,
                                    error_details="Product template has no product variants"
                                )
                                self.products_failed += 1
                                continue
                            
                            # Update this variant's inventory in Odoo
                            update_result = self._update_variant_inventory_in_odoo(product_variant, qty_available, warehouse_mappings, default_warehouse)
                            if update_result is True:
                                self.products_synced += 1
                                _logger.info(f"✓ Successfully synced inventory for {product.name} (BC ID: {product.bigcommerce_id}) - Products Synced: {self.products_synced}")
                            elif update_result == 'SKIPPED':
                                # Silently skip products without inventory tracking - don't count as failure
                                _logger.debug(f"Skipping product {product.name} - inventory tracking disabled")
                                continue
                            else:
                                # Get more specific error message
                                error_reason = update_result if isinstance(update_result, str) else "Unknown error"
                                error_msg = f"Could not update inventory for product {product.name}: {error_reason}"
                                _logger.warning(f"✗ {error_msg} - Products Failed: {self.products_failed + 1}")
                                self._create_log(
                                    'warning',
                                    error_msg,
                                    product_id=product.bigcommerce_id,
                                    product_name=product.name,
                                    error_details=f"Inventory update failed: {error_reason}"
                                )
                                self.products_failed += 1
                    
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    
                    # Check if it's a UserError from API (permission denied, etc.)
                    from odoo.exceptions import UserError
                    if isinstance(e, UserError) and '403' in str(e):
                        error_msg = f"Permission denied (403) when syncing inventory. API token may not have inventory read permissions."
                        _logger.error(f"✗ {error_msg} Product: {product.name} (BC ID: {product.bigcommerce_id}) - Products Failed: {self.products_failed + 1}")
                        self._create_log(
                            'error',
                            error_msg,
                            product_id=product.bigcommerce_id,
                            product_name=product.name,
                            error_details=error_trace,
                            response_status=403
                        )
                        self.products_failed += 1
                        # Continue with other products instead of failing entire sync
                        continue
                    
                    error_msg = f"Error syncing inventory: {str(e)}"
                    _logger.error(f"✗ {error_msg} for product {product.name} (BC ID: {product.bigcommerce_id}, Odoo ID: {product.id}) - Products Failed: {self.products_failed + 1}\n{error_trace}")
                    self._create_log(
                        'error',
                        error_msg,
                        product_id=product.bigcommerce_id,
                        product_name=product.name,
                        error_details=error_trace
                    )
                self.products_failed += 1
            
            # Commit after each batch and update progress
            self.processed_items = batch_start + len(batch_products)
            self.current_item = f"Completed batch: {self.processed_items}/{self.total_items} products processed"
            self.env.cr.commit()
    
    def _check_inventory_tracking(self, product, api):
        """Check if product tracks inventory in both Odoo and BigCommerce
        
        Returns:
            tuple: (odoo_tracks, bc_tracks, bc_inventory_tracking)
            - odoo_tracks: True if Odoo product tracks inventory (is_storable=True)
            - bc_tracks: True if BigCommerce product tracks inventory (inventory_tracking != 'none')
            - bc_inventory_tracking: The BigCommerce inventory_tracking value
        """
        # Check Odoo inventory tracking
        odoo_tracks = product.is_storable if hasattr(product, 'is_storable') else False
        
        # Check BigCommerce inventory tracking
        bc_tracks = False
        bc_inventory_tracking = 'none'
        
        if product.bigcommerce_id:
            try:
                bc_product = api.get_product(product.bigcommerce_id)
                if bc_product:
                    # Handle V3 API response format
                    if isinstance(bc_product, dict) and 'data' in bc_product:
                        bc_product = bc_product['data']
                    
                    bc_inventory_tracking = bc_product.get('inventory_tracking', 'none')
                    bc_tracks = bc_inventory_tracking and bc_inventory_tracking != 'none'
            except Exception as e:
                _logger.debug(f"Could not fetch BigCommerce product data for {product.name} (BC ID: {product.bigcommerce_id}): {str(e)}")
                # If we can't fetch, assume it tracks inventory to avoid skipping valid products
                bc_tracks = True
        
        return odoo_tracks, bc_tracks, bc_inventory_tracking
    
    def _fetch_product_data(self, api, product_id, product_name):
        """Helper method to fetch product data from BigCommerce (can be called in parallel)"""
        try:
            bc_product = api.get_product(product_id)
            if bc_product:
                # Handle V3 API response format
                if isinstance(bc_product, dict) and 'data' in bc_product:
                    bc_product = bc_product['data']
                return {
                    'product_id': product_id,
                    'product_name': product_name,
                    'product_data': bc_product,
                    'error': None
                }
            return {
                'product_id': product_id,
                'product_name': product_name,
                'product_data': None,
                'error': 'Product not found'
            }
        except Exception as e:
            import traceback
            return {
                'product_id': product_id,
                'product_name': product_name,
                'product_data': None,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
    
    def _update_inventory_to_bigcommerce(self, api, product_id, variant_id, inventory_data, product_name, variant_name=None):
        """Helper method to update inventory in BigCommerce (can be called in parallel)"""
        try:
            if variant_id:
                # Update variant-level inventory
                result = api.update_variant_inventory(product_id, variant_id, inventory_data)
            else:
                # Update product-level inventory
                result = api.update_product_inventory(product_id, inventory_data)
            
            return {
                'product_id': product_id,
                'variant_id': variant_id,
                'product_name': product_name,
                'variant_name': variant_name,
                'success': result is not None,
                'error': None if result is not None else 'API returned None (404 or permission error)'
            }
        except Exception as e:
            import traceback
            return {
                'product_id': product_id,
                'variant_id': variant_id,
                'product_name': product_name,
                'variant_name': variant_name,
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
    
    def _sync_to_bigcommerce(self, api):
        """Sync inventory from Odoo to BigCommerce using parallel processing
        Only syncs inventory that has been modified in Odoo since the last successful sync
        """
        # Get all products that are synced with BigCommerce
        domain = [
            ('bigcommerce_id', '!=', False),
            ('bigcommerce_config_id', '=', self.config_id.id)
        ]
        
        # Filter by Odoo inventory changes (stock.quant write_date) if min_date_modified is set
        # This ensures we only sync inventory that has actually changed in Odoo since the last sync
        if self.min_date_modified:
            _logger.info(f"Filtering by Odoo inventory changes since {self.min_date_modified}")
            
            # Find product variants where inventory (stock.quant) has been modified since last sync
            # Use direct SQL for better performance
            self.env.cr.execute("""
                SELECT DISTINCT pt.id
                FROM product_template pt
                INNER JOIN product_product pp ON pp.product_tmpl_id = pt.id
                INNER JOIN stock_quant sq ON sq.product_id = pp.id
                WHERE pt.bigcommerce_id IS NOT NULL
                  AND pt.bigcommerce_config_id = %s
                  AND sq.write_date >= %s
            """, (self.config_id.id, self.min_date_modified))
            
            product_ids_with_inventory_changes = [row[0] for row in self.env.cr.fetchall()]
            
            if product_ids_with_inventory_changes:
                domain.append(('id', 'in', product_ids_with_inventory_changes))
                _logger.info(f"Found {len(product_ids_with_inventory_changes)} products with inventory changes since last sync")
            else:
                # No products have inventory changes - nothing to sync
                _logger.info("No products found with inventory changes since last sync - no inventory to sync")
                products = self.env['product.template'].browse([])
                self.total_items = 0
                self.current_item = "No products found with inventory changes since last sync"
                self.env.cr.commit()
                return
        
        # Apply Odoo date filters (date_from and date_to) if no BigCommerce filter was applied
        if not self.min_date_modified:
            if self.date_from:
                domain.append(('create_date', '>=', self.date_from))
                _logger.info(f"Filtering Odoo products by create_date >= {self.date_from} for inventory sync")
            if self.date_to:
                domain.append(('create_date', '<=', self.date_to))
                _logger.info(f"Filtering Odoo products by create_date <= {self.date_to} for inventory sync")
        
        products = self.env['product.template'].search(domain)
        
        # Update total items if not already set
        if self.total_items == 0:
            self.total_items = len(products)
            self.current_item = f"Found {len(products)} products to sync. Starting parallel processing..."
            self.env.cr.commit()
        
        # Get location mappings (preferred) or warehouse mappings (fallback)
        location_mappings = self.config_id.location_mapping_ids.filtered(lambda l: l.active)
        default_location = location_mappings.filtered(lambda l: l.is_default) if location_mappings else False
        
        # Fallback to warehouse mappings if no location mappings are configured
        warehouse_mappings = self.config_id.warehouse_mapping_ids.filtered(lambda w: w.active) if not location_mappings else False
        default_warehouse = warehouse_mappings.filtered(lambda w: w.is_default) if warehouse_mappings else False
        
        # Process products in batches with parallel API calls
        batch_size = 100  # Process 100 products before committing (increased for better throughput)
        max_workers = 20  # Number of parallel API calls (increased - BigCommerce typically allows 20+ requests/second)
        progress_update_interval = 10  # Only update progress every 10 products
        
        for batch_start in range(0, len(products), batch_size):
            batch_products = products[batch_start:batch_start + batch_size]
            
            # Optimize: Only fetch BigCommerce product data for products where Odoo doesn't track inventory
            # If Odoo tracks inventory, we'll sync regardless of BigCommerce setting
            products_needing_bc_check = [p for p in batch_products if not (p.is_storable if hasattr(p, 'is_storable') else False)]
            
            product_data_results = {}
            if products_needing_bc_check:
                _logger.info(f"Fetching BigCommerce product data for {len(products_needing_bc_check)} products (Odoo doesn't track inventory) using {max_workers} parallel workers...")
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit product data fetches only for products that need it
                    future_to_product = {
                        executor.submit(self._fetch_product_data, api, product.bigcommerce_id, product.name): product
                        for product in products_needing_bc_check
                    }
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_product):
                        product = future_to_product[future]
                        try:
                            result = future.result()
                            product_data_results[product.id] = result
                        except Exception as e:
                            _logger.error(f"Error fetching product data for {product.name}: {str(e)}")
                            product_data_results[product.id] = {
                                'product_id': product.bigcommerce_id,
                                'product_name': product.name,
                                'product_data': None,
                                'error': str(e)
                            }
            
            # Now prepare inventory updates for this batch (sequential - uses ORM)
            _logger.info(f"Preparing inventory data for batch {batch_start // batch_size + 1} ({len(batch_products)} products)...")
            inventory_updates = []  # List of (product, variant, inventory_data) tuples
            
            for product in batch_products:
                # Check if sync has been cancelled
                if self._check_cancelled():
                    _logger.info("Inventory sync cancelled by user")
                    raise UserError("Sync operation was cancelled by user")
                
                # Check Odoo inventory tracking (no API call needed)
                odoo_tracks = product.is_storable if hasattr(product, 'is_storable') else False
                
                # Only check BigCommerce if Odoo doesn't track inventory
                if not odoo_tracks:
                    # Get BigCommerce inventory tracking from cached result
                    bc_tracks = False
                    bc_inventory_tracking = 'none'
                    product_data_result = product_data_results.get(product.id)
                    if product_data_result and product_data_result.get('product_data'):
                        bc_product = product_data_result['product_data']
                        bc_inventory_tracking = bc_product.get('inventory_tracking', 'none')
                        bc_tracks = bc_inventory_tracking and bc_inventory_tracking != 'none'
                    
                    # Skip products that don't track inventory in both systems
                    if not bc_tracks:
                        _logger.debug(f"Skipping product {product.name} (BC ID: {product.bigcommerce_id}) - inventory tracking disabled in both Odoo (is_storable=False) and BigCommerce (inventory_tracking='{bc_inventory_tracking}')")
                        continue
                # If Odoo tracks inventory, proceed with sync regardless of BigCommerce setting
                
                try:
                    # Check if product has variants
                    has_variants = product.product_variant_ids and len(product.product_variant_ids) > 1
                    
                    if has_variants:
                        # Product has variants - prepare updates for each variant
                        for variant in product.product_variant_ids:
                            if not variant.bigcommerce_variant_id:
                                _logger.debug(f"Variant {variant.name} is not linked to BigCommerce. Skipping inventory sync.")
                                continue
                            
                            # Check if this variant's inventory has changed since last sync
                            # Only sync variants where inventory (stock.quant) has been modified
                            if self.min_date_modified:
                                variant_has_inventory_changes = False
                                try:
                                    # Check if any stock.quant for this variant was modified since last sync
                                    self.env.cr.execute("""
                                        SELECT COUNT(*) 
                                        FROM stock_quant sq
                                        WHERE sq.product_id = %s
                                          AND sq.write_date >= %s
                                    """, (variant.id, self.min_date_modified))
                                    count = self.env.cr.fetchone()[0]
                                    variant_has_inventory_changes = count > 0
                                except Exception as e:
                                    _logger.warning(f"Error checking inventory changes for variant {variant.name}: {str(e)}. Will sync to be safe.")
                                    variant_has_inventory_changes = True  # Sync to be safe if check fails
                                
                                if not variant_has_inventory_changes:
                                    _logger.debug(f"Skipping variant {variant.name} - inventory has not changed since last sync")
                                    continue
                            
                            # Calculate inventory for this variant
                            qty_available = self._calculate_variant_inventory_for_sync(variant, location_mappings, default_location, warehouse_mappings, default_warehouse)
                            
                            inventory_data = {
                                'inventory_level': int(qty_available),
                                'inventory_warning_level': 0,
                            }
                            
                            inventory_updates.append({
                                'product': product,
                                'variant': variant,
                                'inventory_data': inventory_data,
                                'qty_available': qty_available
                            })
                    else:
                        # Product without variants - prepare product-level update
                        product_variant = product.product_variant_id
                        if not product_variant and product.product_variant_ids:
                            product_variant = product.product_variant_ids[0]
                        
                        if not product_variant:
                            _logger.warning(f"Product {product.name} (BC ID: {product.bigcommerce_id}) has no variant. Skipping inventory sync.")
                            continue
                        
                        # Check if this variant's inventory has changed since last sync
                        # Only sync variants where inventory (stock.quant) has been modified
                        if self.min_date_modified:
                            variant_has_inventory_changes = False
                            try:
                                # Check if any stock.quant for this variant was modified since last sync
                                self.env.cr.execute("""
                                    SELECT COUNT(*) 
                                    FROM stock_quant sq
                                    WHERE sq.product_id = %s
                                      AND sq.write_date >= %s
                                """, (product_variant.id, self.min_date_modified))
                                count = self.env.cr.fetchone()[0]
                                variant_has_inventory_changes = count > 0
                            except Exception as e:
                                _logger.warning(f"Error checking inventory changes for variant {product_variant.name}: {str(e)}. Will sync to be safe.")
                                variant_has_inventory_changes = True  # Sync to be safe if check fails
                            
                            if not variant_has_inventory_changes:
                                _logger.debug(f"Skipping product {product.name} - inventory has not changed since last sync")
                                continue
                        
                        # Calculate inventory for this variant
                        qty_available = self._calculate_variant_inventory_for_sync(product_variant, location_mappings, default_location, warehouse_mappings, default_warehouse)
                        
                        inventory_data = {
                            'inventory_level': int(qty_available),
                            'inventory_warning_level': 0,
                        }
                        
                        inventory_updates.append({
                            'product': product,
                            'variant': None,
                            'inventory_data': inventory_data,
                            'qty_available': qty_available
                        })
                
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    _logger.error(f"Error preparing inventory for product {product.name}: {str(e)}", exc_info=True)
                    # Will be counted as failed when processing results
                    continue
            
            # Now update inventory in parallel for this batch
            if inventory_updates:
                _logger.info(f"Updating inventory for {len(inventory_updates)} product/variant(s) using {max_workers} parallel workers...")
                update_results = {}
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all API calls for this batch
                    future_to_update = {
                        executor.submit(
                            self._update_inventory_to_bigcommerce,
                            api,
                            update['product'].bigcommerce_id,
                            update['variant'].bigcommerce_variant_id if update['variant'] else None,
                            update['inventory_data'],
                            update['product'].name,
                            update['variant'].name if update['variant'] else None
                        ): update
                        for update in inventory_updates
                    }
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_update):
                        update = future_to_update[future]
                        product = update['product']
                        # Use product.id as key for more reliable matching
                        product_key = product.id
                        try:
                            result = future.result()
                            if product_key not in update_results:
                                update_results[product_key] = []
                            update_results[product_key].append(result)
                        except Exception as e:
                            _logger.error(f"Error updating inventory for product {product.name}: {str(e)}")
                            if product_key not in update_results:
                                update_results[product_key] = []
                            update_results[product_key].append({
                                'success': False,
                                'error': str(e)
                            })
                
                # Process results sequentially (Odoo ORM is not thread-safe)
                _logger.info(f"Processing {len(update_results)} inventory update results...")
                
                # Track which products have been processed to ensure we count all products
                processed_product_ids = set()
                
                for idx, product in enumerate(batch_products, 1):
                    # Check if sync has been cancelled
                    if self._check_cancelled():
                        _logger.info("Inventory sync cancelled by user")
                        raise UserError("Sync operation was cancelled by user")
                    
                    actual_idx = batch_start + idx
                    # Update progress less frequently to reduce overhead
                    if actual_idx % progress_update_interval == 0 or idx == 1:
                        self.current_item = f"Processing: {product.name[:50]}... (Item {actual_idx}/{self.total_items})"
                        self.processed_items = actual_idx
                        self.write({'current_item': self.current_item, 'processed_items': self.processed_items})
                        self.env.cr.commit()
                    
                    # Get results for this product (use product.id for reliable matching)
                    product_results = update_results.get(product.id, [])
                    if not product_results:
                        # Product was skipped (no inventory tracking or other reason) - don't count it
                        continue
                    
                    processed_product_ids.add(id(product))
                    
                    # Process results for this product
                    variants_synced = 0
                    variants_failed = 0
                    
                    for result in product_results:
                        if result.get('success'):
                            variants_synced += 1
                            variant_name = result.get('variant_name', 'product')
                            _logger.debug(f"✓ Successfully synced inventory for {variant_name} of product {product.name}")
                        else:
                            variants_failed += 1
                            error_msg = result.get('error', 'Unknown error')
                            variant_name = result.get('variant_name', 'product')
                            _logger.warning(f"✗ Failed to sync inventory for {variant_name} of product {product.name}: {error_msg}")
                            
                            # Create log entry for failure
                            error_details = result.get('traceback', error_msg)
                            self._create_log(
                                'warning',
                                f"Failed to sync inventory for {variant_name}",
                                product_id=product.bigcommerce_id,
                                product_name=product.name,
                                error_details=error_details
                            )
                    
                    # Count product as synced if at least one variant was synced
                    if variants_synced > 0:
                        self.products_synced += 1
                        _logger.info(f"✓ Synced inventory for {variants_synced} variant(s) of product {product.name} (BC ID: {product.bigcommerce_id}) - Products Synced: {self.products_synced}")
                        self.write({'products_synced': self.products_synced})
                        self.env.cr.commit()
                    elif variants_failed > 0:
                        error_msg = f"Failed to sync inventory for all variants of product {product.name}"
                        error_details = (
                            f"Failed to sync {variants_failed} variant(s). "
                            f"Possible causes:\n"
                            f"- Variants may not exist in BigCommerce (check variant IDs)\n"
                            f"- API token may lack required permissions for inventory updates\n"
                            f"- Variants may not be properly linked (missing bigcommerce_variant_id)\n"
                            f"Check the logs above for specific error details for each variant."
                        )
                        _logger.warning(f"✗ {error_msg} - Products Failed: {self.products_failed + 1}")
                        self._create_log(
                            'warning',
                            error_msg,
                            product_id=product.bigcommerce_id,
                            product_name=product.name,
                            error_details=error_details
                        )
                        self.products_failed += 1
                        self.write({'products_failed': self.products_failed})
                        self.env.cr.commit()
                
                # Log summary for this batch
                _logger.info(f"Batch {batch_start // batch_size + 1} complete: {len(processed_product_ids)} products processed, {self.products_synced} synced, {self.products_failed} failed")
            
            # Commit after each batch and update progress
            self.processed_items = batch_start + len(batch_products)
            self.current_item = f"Completed batch: {self.processed_items}/{self.total_items} products processed"
            self.env.cr.commit()
    
    def _calculate_variant_inventory_for_sync(self, variant, location_mappings, default_location, warehouse_mappings, default_warehouse):
        """Helper method to calculate inventory quantity for a variant when syncing TO BigCommerce.
        Uses stock.quant only (never qty_available) to avoid triggering recomputation on product variant lists."""
        qty_available = 0
        
        # Priority 1: Use location mappings if configured
        if location_mappings:
            for mapping in location_mappings:
                location = mapping.odoo_location_id
                if location:
                    child_locations = self.env['stock.location'].search([('id', 'child_of', location.id)])
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', variant.id),
                        ('location_id', 'in', child_locations.ids)
                    ])
                    location_qty = sum(quants.mapped('quantity'))
                    if mapping.min_threshold > 0 and location_qty < mapping.min_threshold:
                        continue
                    qty_available += location_qty
        elif default_location:
            location = default_location.odoo_location_id
            if location:
                child_locations = self.env['stock.location'].search([('id', 'child_of', location.id)])
                quants = self.env['stock.quant'].search([
                    ('product_id', '=', variant.id),
                    ('location_id', 'in', child_locations.ids)
                ])
                qty_available = sum(quants.mapped('quantity'))
        elif warehouse_mappings:
            for mapping in warehouse_mappings:
                warehouse = mapping.odoo_warehouse_id
                if warehouse and warehouse.lot_stock_id:
                    quant = self.env['stock.quant'].search([
                        ('product_id', '=', variant.id),
                        ('location_id', '=', warehouse.lot_stock_id.id)
                    ], limit=1)
                    warehouse_qty = quant.quantity if quant else 0
                    if mapping.min_threshold > 0 and warehouse_qty < mapping.min_threshold:
                        continue
                    qty_available += warehouse_qty
        elif default_warehouse:
            warehouse = default_warehouse.odoo_warehouse_id
            if warehouse and warehouse.lot_stock_id:
                quant = self.env['stock.quant'].search([
                    ('product_id', '=', variant.id),
                    ('location_id', '=', warehouse.lot_stock_id.id)
                ], limit=1)
                qty_available = quant.quantity if quant else 0
        else:
            location = self.env['stock.location'].search([('usage', '=', 'internal')], limit=1)
            if location:
                quant = self.env['stock.quant'].search([
                    ('product_id', '=', variant.id),
                    ('location_id', '=', location.id)
                ], limit=1)
                qty_available = quant.quantity if quant else 0
        return qty_available
    
    def _update_variant_inventory_in_odoo(self, product_variant, qty_available, warehouse_mappings, default_warehouse):
        """Helper method to update a variant's inventory in Odoo
        
        Returns:
            bool: True if inventory was updated successfully
            'SKIPPED': If inventory tracking is disabled (should be handled silently)
            str: Error message if update failed
        """
        # Check if variant can track inventory
        variant_is_storable = getattr(product_variant, 'is_storable', None)
        if variant_is_storable is None:
            # Variant doesn't have is_storable, check template
            variant_is_storable = getattr(product_variant.product_tmpl_id, 'is_storable', False)
        
        if not variant_is_storable:
            # Silently skip - don't log warnings or count as failures
            return 'SKIPPED'
        
        # Determine which warehouse(s) to update
        warehouses_to_update = []
        if warehouse_mappings:
            # Use mapped warehouses
            for mapping in warehouse_mappings:
                warehouses_to_update.append(mapping.odoo_warehouse_id)
        elif default_warehouse:
            warehouses_to_update = [default_warehouse.odoo_warehouse_id]
        else:
            # Fallback to default internal location
            location = self.env['stock.location'].search([
                ('usage', '=', 'internal')
            ], limit=1)
            if location:
                # Get warehouse from location
                if location.warehouse_id:
                    warehouses_to_update = [location.warehouse_id]
                else:
                    # Search for warehouse that uses this location
                    warehouse = self.env['stock.warehouse'].search([
                        ('lot_stock_id', '=', location.id)
                    ], limit=1)
                    if warehouse:
                        warehouses_to_update = [warehouse]
        
        if not warehouses_to_update:
            error_msg = f"No warehouses configured for variant '{product_variant.name}'. Please configure warehouse mappings in BigCommerce configuration."
            _logger.warning(error_msg)
            return error_msg
        
        # Track if we actually updated any warehouse
        inventory_updated = False
        
        for warehouse in warehouses_to_update:
            if warehouse:
                location = warehouse.lot_stock_id
                if location:
                    # Calculate difference for this warehouse
                    quant = self.env['stock.quant'].search([
                        ('product_id', '=', product_variant.id),
                        ('location_id', '=', location.id)
                    ], limit=1)
                    current_qty = quant.quantity if quant else 0
                    diff = qty_available - current_qty
                    
                    # Check minimum threshold before updating
                    mapping = warehouse_mappings.filtered(lambda w: w.odoo_warehouse_id == warehouse) if warehouse_mappings else False
                    if mapping and mapping.min_threshold > 0 and qty_available < mapping.min_threshold:
                        _logger.debug(f"Skipping sync for variant {product_variant.name} - below threshold ({qty_available} < {mapping.min_threshold})")
                        # Still count as synced even if below threshold (inventory was checked)
                        inventory_updated = True
                        continue
                    
                    # Only update if there's a difference
                    if diff != 0:
                        # Directly update quant to set "On Hand" quantity (not "Counted")
                        try:
                            # Get or create quant
                            quant = self.env['stock.quant'].search([
                                ('product_id', '=', product_variant.id),
                                ('location_id', '=', location.id)
                            ], limit=1)
                            
                            if quant:
                                # Update existing quant directly
                                quant.inventory_quantity = qty_available
                                quant.action_apply_inventory()
                                inventory_updated = True
                            else:
                                # Create new quant if it doesn't exist
                                try:
                                    quant = self.env['stock.quant'].create({
                                        'product_id': product_variant.id,
                                        'location_id': location.id,
                                        'inventory_quantity': qty_available,
                                    })
                                    quant.action_apply_inventory()
                                    inventory_updated = True
                                except Exception as quant_create_error:
                                    # If quant creation fails, try stock move as fallback
                                    error_msg = str(quant_create_error)
                                    if 'consumables' in error_msg.lower() or 'services' in error_msg.lower():
                                        _logger.debug(f"Variant {product_variant.name} cannot have inventory - may be consumable/service. Skipping.")
                                        continue
                                    else:
                                        _logger.warning(f"Could not create quant for variant {product_variant.name}, using stock move fallback: {error_msg}")
                                        self._update_inventory_with_move(product_variant, location, diff)
                                        inventory_updated = True
                        except Exception as quant_error:
                            # If direct quant update fails, use stock move as fallback
                            _logger.warning(f"Could not update quant directly for variant {product_variant.name}, using stock move fallback: {str(quant_error)}")
                            self._update_inventory_with_move(product_variant, location, diff)
                            inventory_updated = True
                    else:
                        # No difference - inventory already matches, but still count as synced
                        _logger.debug(f"Variant {product_variant.name} inventory already matches ({current_qty}), no update needed.")
                        inventory_updated = True
        
        if inventory_updated:
            return True
        else:
            error_msg = f"Failed to update inventory for variant '{product_variant.name}' - no warehouses were processed"
            _logger.warning(error_msg)
            return error_msg
    
    def _update_inventory_with_move(self, product, location, qty_diff):
        """Update inventory using stock move"""
        if qty_diff == 0:
            return
        
        # Check if product has "Track Inventory" enabled (is_storable field)
        is_storable = getattr(product, 'is_storable', False)
        if not is_storable:
            _logger.warning(f"Product {product.name} (ID: {product.id}) does not have 'Track Inventory' enabled (is_storable=False), cannot create inventory. Skipping.")
            return
        
        # Determine move type based on quantity difference
        if qty_diff > 0:
            # Increase inventory - use internal transfer from a virtual location
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('default_location_src_id.usage', '=', 'inventory'),
            ], limit=1)
            
            if picking_type:
                try:
                    move = self.env['stock.move'].create({
                        'name': f'BigCommerce Sync - {product.name}',
                        'product_id': product.id,
                        'product_uom': product.uom_id.id,
                        'location_id': picking_type.default_location_src_id.id,
                        'location_dest_id': location.id,
                        'product_uom_qty': abs(qty_diff),
                    })
                    move._action_confirm()
                    move._action_assign()
                    for move_line in move.move_line_ids:
                        move_line.qty_done = move_line.product_uom_qty
                    move._action_done()
                except Exception as move_error:
                    _logger.warning(f"Could not create stock move for product {product.name} (ID: {product.id}): {str(move_error)}")
                    # Try fallback to quant update
                    self._update_quant_fallback(product, location, qty_diff)
            else:
                # Fallback: directly update quant using search
                self._update_quant_fallback(product, location, qty_diff)
        else:
            # Decrease inventory - use internal transfer to a virtual location
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('default_location_dest_id.usage', '=', 'inventory'),
            ], limit=1)
            
            if picking_type:
                try:
                    move = self.env['stock.move'].create({
                        'name': f'BigCommerce Sync - {product.name}',
                        'product_id': product.id,
                        'product_uom': product.uom_id.id,
                        'location_id': location.id,
                        'location_dest_id': picking_type.default_location_dest_id.id,
                        'product_uom_qty': abs(qty_diff),
                    })
                    move._action_confirm()
                    move._action_assign()
                    for move_line in move.move_line_ids:
                        move_line.qty_done = move_line.product_uom_qty
                    move._action_done()
                except Exception as move_error:
                    _logger.warning(f"Could not create stock move for product {product.name} (ID: {product.id}): {str(move_error)}")
                    # Try fallback to quant update
                    self._update_quant_fallback(product, location, qty_diff)
            else:
                # Fallback: directly update quant using search
                self._update_quant_fallback(product, location, qty_diff)
    
    def _update_quant_fallback(self, product, location, qty_diff):
        """Fallback method to update inventory using quant directly"""
        try:
            quant = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', location.id)
            ], limit=1)
            
            if quant:
                # Update existing quant
                if qty_diff > 0:
                    quant.inventory_quantity = quant.quantity + abs(qty_diff)
                else:
                    if quant.quantity >= abs(qty_diff):
                        quant.inventory_quantity = quant.quantity - abs(qty_diff)
                    else:
                        _logger.warning(f"Cannot decrease inventory for {product.name} - current quantity ({quant.quantity}) is less than decrease amount ({abs(qty_diff)})")
                        return
                quant.action_apply_inventory()
            else:
                # Try to create new quant only if qty_diff is positive
                if qty_diff > 0:
                    try:
                        self.env['stock.quant'].create({
                            'product_id': product.id,
                            'location_id': location.id,
                            'inventory_quantity': abs(qty_diff),
                        }).action_apply_inventory()
                    except Exception as quant_error:
                        # If quant creation fails (e.g., consumable/service), log and skip
                        error_msg = str(quant_error)
                        if 'consumables' in error_msg.lower() or 'services' in error_msg.lower():
                            _logger.debug(f"Product {product.name} (ID: {product.id}) cannot have inventory created - may be consumable/service or inventory tracking disabled. Error: {error_msg}")
                        else:
                            _logger.warning(f"Could not create quant for product {product.name} (ID: {product.id}): {error_msg}")
                else:
                    _logger.warning(f"Cannot decrease inventory for {product.name} - no quant exists and cannot create negative quant")
        except Exception as e:
            _logger.warning(f"Error in quant fallback for product {product.name} (ID: {product.id}): {str(e)}")

