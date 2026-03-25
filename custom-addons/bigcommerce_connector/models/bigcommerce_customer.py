# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    bigcommerce_id = fields.Integer(string='BigCommerce Customer ID', copy=False, index=True)
    bigcommerce_synced = fields.Boolean(string='Synced with BigCommerce', default=False)
    bigcommerce_last_sync = fields.Datetime(string='Last Sync with BigCommerce')
    bigcommerce_config_id = fields.Many2one('bigcommerce.config', string='BigCommerce Config')


class BigCommerceCustomerSync(models.Model):
    _name = 'bigcommerce.customer.sync'
    _description = 'BigCommerce Customer Sync'
    _order = 'sync_date desc'

    name = fields.Char(string='Sync Name', required=True, default=lambda self: f"Customer Sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, required=True)
    sync_direction = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
        ('bc_to_odoo', 'BigCommerce to Odoo'),
        ('bidirectional', 'Bidirectional'),
    ], string='Sync Direction', required=True)
    
    # Filters
    date_from = fields.Datetime(string='Date From', help='Sync customers created from this date')
    date_to = fields.Datetime(string='Date To', help='Sync customers created until this date')
    min_date_modified = fields.Datetime(string='Min Date Modified', help='Sync customers modified after this date')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='State', default='draft')
    
    customers_created = fields.Integer(string='Customers Created', default=0)
    customers_updated = fields.Integer(string='Customers Updated', default=0)
    customers_failed = fields.Integer(string='Customers Failed', default=0)
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
                    'items_created': self.customers_created,
                    'items_updated': self.customers_updated,
                    'items_failed': self.customers_failed,
                })
                # Also update the sync record itself to ensure UI sees progress
                self.write({
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                })
                self.env.cr.commit()
            except Exception as e:
                _logger.warning(f"Failed to update sync operation: {str(e)}")
    
    def _create_log(self, log_level, message, customer_id=None, customer_name=None, error_details=None):
        """Create a sync log entry"""
        try:
            log_vals = {
                'sync_type': 'customer',
                'sync_record_id': self.id,
                'config_id': self.config_id.id,
                'log_level': log_level,
                'message': message,
                'error_details': error_details,
            }
            # Link to sync operation if available
            if self.sync_operation_id:
                log_vals['sync_operation_id'] = self.sync_operation_id.id
            
            # Use sudo() to bypass permission checks - logs are system-level records
            return self.env['bigcommerce.sync.log'].sudo().create(log_vals)
        except Exception as e:
            # If log creation fails, at least log to the standard logger
            _logger.warning(f"Failed to create sync log entry: {str(e)}")
            return False
    
    def action_sync_customers(self):
        """Sync customers between Odoo and BigCommerce"""
        self.ensure_one()
        
        # Create sync operation record for dashboard tracking
        sync_operation = self.env['bigcommerce.sync.operation'].create({
            'sync_type': 'customer',
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
            
            if self.sync_direction in ('bc_to_odoo', 'bidirectional'):
                self._sync_from_bigcommerce(api)
            
            if self.sync_direction in ('odoo_to_bc', 'bidirectional'):
                self._sync_to_bigcommerce(api)
            
            self.state = 'done'
            total_items = self.customers_created + self.customers_updated + self.customers_failed
            warnings_count = self.env['bigcommerce.sync.log'].search_count([
                ('config_id', '=', self.config_id.id),
                ('sync_type', '=', 'customer'),
                ('log_level', '=', 'WARNING'),
                ('log_date', '>=', self.config_id.last_customer_sync or fields.Datetime.now() - timedelta(days=1))
            ])
            self.config_id.write({
                'last_customer_sync_total': total_items,
                'last_customer_sync_updated': self.customers_updated,
                'last_customer_sync_failed': self.customers_failed,
                'last_customer_sync_warnings': warnings_count,
            })
            
            # Update sync operation record
            if self.sync_operation_id:
                if self.customers_failed > 0:
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
                    'items_synced': self.customers_created + self.customers_updated,
                    'items_created': self.customers_created,
                    'items_updated': self.customers_updated,
                    'items_failed': self.customers_failed,
                    'error_count': self.customers_failed,
                    'warning_count': warnings_count,
                })
            
            if self.customers_failed > 0 and self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Customer',
                        error_message=f"Sync completed with {self.customers_failed} failed item(s).",
                        created=self.customers_created,
                        updated=self.customers_updated,
                        failed=self.customers_failed,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send customer sync failure notification: %s", mail_e)
            
        except UserError as e:
            # Check if this is a cancellation
            if 'cancelled' in str(e).lower():
                if self.sync_operation_id and self.sync_operation_id.state == 'running':
                    self.sync_operation_id.action_cancel()
                self.state = 'error'
                self.error_message = str(e)
                _logger.info(f"Customer sync cancelled: {str(e)}")
            else:
                self.state = 'error'
                self.error_message = str(e)
                if self.config_id:
                    try:
                        self.config_id._send_sync_failure_email('Customer', error_message=str(e), sync_name=self.name)
                    except Exception as mail_e:
                        _logger.warning("Could not send customer sync failure notification: %s", mail_e)
                raise
        except Exception as e:
            self.state = 'error'
            self.error_message = str(e)
            if self.config_id:
                try:
                    import traceback
                    self.config_id._send_sync_failure_email(
                        'Customer',
                        error_message=str(e),
                        details=traceback.format_exc(),
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send customer sync failure notification: %s", mail_e)
            
            # Update sync operation record on error (only if not cancelled)
            if self.sync_operation_id and self.sync_operation_id.state != 'cancelled':
                self.sync_operation_id.write({
                    'state': 'failed',
                    'end_date': fields.Datetime.now(),
                    'error_count': self.customers_failed + 1,
                })
            
            _logger.error(f"Customer sync error: {str(e)}")
            raise UserError(f"Customer sync failed: {str(e)}")
    
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
    
    def _sync_from_bigcommerce(self, api):
        """Sync customers from BigCommerce to Odoo using parallel processing"""
        page = 1
        limit = 250
        total_processed = 0
        total_count = None
        all_customers = []
        
        # Build filters for BigCommerce API
        filters = {}
        if self.min_date_modified:
            # BigCommerce API uses date_modified:min for filtering by date_modified
            filters['date_modified:min'] = self.min_date_modified.strftime('%Y-%m-%dT%H:%M:%S')
            _logger.info(f"Filtering customers by date_modified >= {self.min_date_modified}")
        if self.date_from:
            # BigCommerce API uses date_created:min for filtering by creation date
            filters['date_created:min'] = self.date_from.strftime('%Y-%m-%dT%H:%M:%S')
            _logger.info(f"Filtering customers by date_created >= {self.date_from}")
        if self.date_to:
            # BigCommerce API uses date_created:max for filtering by creation date
            filters['date_created:max'] = self.date_to.strftime('%Y-%m-%dT%H:%M:%S')
            _logger.info(f"Filtering customers by date_created <= {self.date_to}")
        
        # Collect all customers first
        while True:
            try:
                customers_response = api.get_customers(page=page, limit=limit, **filters)
                if not customers_response:
                    break
                
                # Handle V3 API response format (dict with data) or V2 (list)
                if isinstance(customers_response, dict):
                    if 'data' in customers_response:
                        customers = customers_response['data']
                        # Update total count if available and we didn't have it before
                        if '_total_count' in customers_response and total_count is None:
                            total_count = customers_response['_total_count']
                        elif 'meta' in customers_response and 'pagination' in customers_response['meta']:
                            pagination = customers_response['meta']['pagination']
                            if 'total' in pagination and total_count is None:
                                total_count = pagination['total']
                    else:
                        customers = []
                else:
                    customers = customers_response if isinstance(customers_response, list) else []
                
                if not customers:
                    break
                
                all_customers.extend(customers)
                
                # Update total if we didn't have it before (for V2 API or when total is unknown)
                if total_count is None:
                    # If we got a full page, estimate there's at least one more page
                    if len(customers) == limit:
                        # Estimate: current collected + at least one more page
                        self.total_items = len(all_customers) + limit
                    else:
                        # Partial page means this is the last page
                        self.total_items = len(all_customers)
                    self.env.cr.commit()
                else:
                    self.total_items = total_count
                    self.env.cr.commit()
                
                # If we have the total count and we've collected all customers, break
                if total_count and len(all_customers) >= total_count:
                    break
                
                # If we got less than a full page, we're done
                if len(customers) < limit:
                    self.total_items = len(all_customers)
                    self.env.cr.commit()
                    break
                
                page += 1
                
            except Exception as e:
                _logger.error(f"Error fetching customers page {page}: {str(e)}")
                break
        
        # Final update to ensure total_items matches what we actually collected
        if not total_count:
            self.total_items = len(all_customers)
            self.env.cr.commit()
        
        if not all_customers:
            _logger.warning("No customers found to sync")
            return
        
        _logger.info(f"Collected {len(all_customers)} customers. Starting parallel processing...")
        self.current_item = f"Processing {len(all_customers)} customers in parallel..."
        self._update_sync_operation()
        self.env.cr.commit()
        
        # Process customers in batches with parallel API calls for addresses
        batch_size = 50  # Process 50 customers before committing
        max_workers = 10  # Number of parallel API calls for addresses
        
        for batch_start in range(0, len(all_customers), batch_size):
            batch_customers = all_customers[batch_start:batch_start + batch_size]
            
            # First, try to extract addresses from customer objects (if included in response)
            address_data_map = {}
            customers_needing_address_fetch = []
            
            for bc_customer in batch_customers:
                # Try to extract address from customer object first
                address_data = self._extract_address_from_customer(bc_customer)
                if address_data:
                    address_data_map[bc_customer.get('id')] = address_data
                    _logger.debug(f"Found address in customer object for customer {bc_customer.get('id')}")
                else:
                    # Need to fetch address separately
                    customers_needing_address_fetch.append(bc_customer)
            
            # Fetch addresses in parallel for customers that don't have addresses in the response
            if customers_needing_address_fetch:
                _logger.debug(f"Fetching addresses for {len(customers_needing_address_fetch)} customers using {max_workers} parallel workers...")
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all address API calls for this batch
                    future_to_customer = {
                        executor.submit(self._fetch_customer_address, api, bc_customer.get('id')): bc_customer
                        for bc_customer in customers_needing_address_fetch
                    }
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_customer):
                        bc_customer = future_to_customer[future]
                        try:
                            address_data = future.result()
                            if address_data:
                                address_data_map[bc_customer.get('id')] = address_data
                                _logger.debug(f"Fetched address for customer {bc_customer.get('id')}")
                        except Exception as e:
                            warning_msg = f"Error fetching address for customer {bc_customer.get('id')}: {str(e)}"
                            _logger.warning(warning_msg)
                            self._create_log(
                                'warning',
                                warning_msg,
                                customer_id=bc_customer.get('id'),
                                customer_name=f"{bc_customer.get('first_name', '')} {bc_customer.get('last_name', '')}".strip() or bc_customer.get('email', 'Customer'),
                                error_details=str(e)
                            )
            
            # Now process customers sequentially (ORM operations)
            for idx, bc_customer in enumerate(batch_customers, 1):
                # Check if sync has been cancelled
                if self._check_cancelled():
                    _logger.info("Customer sync cancelled by user")
                    raise UserError("Sync operation was cancelled by user")
                
                customer_id = bc_customer.get('id', 'Unknown')
                customer_name = f"{bc_customer.get('first_name', '')} {bc_customer.get('last_name', '')}".strip() or bc_customer.get('email', 'Customer')
                
                # Update progress
                current_item_number = batch_start + idx
                self.current_item = f"Processing: {customer_name[:50]}... (Item {current_item_number}/{self.total_items})"
                self.processed_items = current_item_number
                self._update_sync_operation()
                self.env.cr.commit()
                
                try:
                    # Get address data from the map
                    address_data = address_data_map.get(bc_customer.get('id'), {})
                    self._create_or_update_customer_from_bc(bc_customer, api, address_data)
                    self.customers_created += 1
                except Exception as e:
                    _logger.error(f"Error syncing customer {bc_customer.get('id')}: {str(e)}", exc_info=True)
                    self.customers_failed += 1
            
            total_processed += len(batch_customers)
        
        # Final update
        self.total_items = total_processed
        self.env.cr.commit()
    
    def _extract_address_from_customer(self, bc_customer):
        """Extract address data from customer object (addresses may be included in customer response)"""
        addresses = bc_customer.get('addresses', [])
        if addresses and isinstance(addresses, list) and len(addresses) > 0:
            # Use the first address (or find default/billing address)
            address = addresses[0]
            # Look for default or billing address
            for addr in addresses:
                # Prefer residential or commercial addresses
                if addr.get('address_type') in ('residential', 'commercial'):
                    address = addr
                    break
                # Or look for default address
                if addr.get('address_type') == 'residential' or (not address.get('address_type') and addr.get('address_type')):
                    address = addr
            
            # Extract address fields - V3 API uses address1, address2, state_or_province, postal_code, country_code
            street_1 = address.get('address1', '') or address.get('street_1', '') or address.get('address_1', '')
            street_2 = address.get('address2', '') or address.get('street_2', '') or address.get('address_2', '')
            city = address.get('city', '')
            zip_code = address.get('postal_code', '') or address.get('zip', '')
            country_code = address.get('country_code', '') or address.get('country_iso2', '') or address.get('country', '')
            state_name = address.get('state_or_province', '') or address.get('state', '')
            
            if street_1 or city:  # Only return address if we have at least street or city
                return {
                    'street': street_1,
                    'street2': street_2,
                    'city': city,
                    'zip': zip_code,
                    'country_code': country_code,
                    'state_name': state_name,
                    'phone': address.get('phone', ''),
                }
        return {}
    
    def _fetch_customer_address(self, api, customer_id):
        """Helper method to fetch customer address from BigCommerce (can be called in parallel)"""
        try:
            bc_addresses = api.get_customer_addresses(customer_id)
            if bc_addresses and isinstance(bc_addresses, list) and len(bc_addresses) > 0:
                # Use the first address (or find default/billing address)
                address = bc_addresses[0]
                # Look for default or billing address
                for addr in bc_addresses:
                    # Prefer residential or commercial addresses
                    if addr.get('address_type') in ('residential', 'commercial'):
                        address = addr
                        break
                    # Or look for default address
                    if addr.get('address_type') == 'residential' or (not address.get('address_type') and addr.get('address_type')):
                        address = addr
                
                # Extract address fields - V3 API uses address1, address2, state_or_province, postal_code, country_code
                street_1 = address.get('address1', '') or address.get('street_1', '') or address.get('address_1', '')
                street_2 = address.get('address2', '') or address.get('street_2', '') or address.get('address_2', '')
                city = address.get('city', '')
                zip_code = address.get('postal_code', '') or address.get('zip', '')
                country_code = address.get('country_code', '') or address.get('country_iso2', '') or address.get('country', '')
                state_name = address.get('state_or_province', '') or address.get('state', '')
                
                if street_1 or city:  # Only return address if we have at least street or city
                    return {
                        'street': street_1,
                        'street2': street_2,
                        'city': city,
                        'zip': zip_code,
                        'country_code': country_code,
                        'state_name': state_name,
                        'phone': address.get('phone', ''),
                    }
        except Exception as e:
            _logger.debug(f"Could not fetch addresses for customer {customer_id}: {str(e)}")
        return {}
    
    def _create_or_update_customer_from_bc(self, bc_customer, api, address_data=None):
        """Create or update Odoo customer from BigCommerce customer"""
        partner_obj = self.env['res.partner']
        
        # Search for existing customer by BigCommerce ID
        existing = partner_obj.search([('bigcommerce_id', '=', bc_customer.get('id'))], limit=1)
        
        # Prepare customer data
        first_name = bc_customer.get('first_name', '')
        last_name = bc_customer.get('last_name', '')
        name = f"{first_name} {last_name}".strip() or bc_customer.get('email', 'Customer')
        
        # Process address data if provided
        processed_address_data = {}
        if address_data:
            processed_address_data = {
                'street': address_data.get('street', ''),
                'street2': address_data.get('street2', ''),
                'city': address_data.get('city', ''),
                'zip': address_data.get('zip', ''),
                'country_id': self._get_country_id(address_data.get('country_code', '')),
                'state_id': self._get_state_id(address_data.get('state_name', ''), address_data.get('country_code', '')),
                'phone': address_data.get('phone', '') or bc_customer.get('phone', ''),
            }
            _logger.debug(f"Customer {bc_customer.get('id')} address data: street={processed_address_data.get('street')}, city={processed_address_data.get('city')}, country_id={processed_address_data.get('country_id')}")
        else:
            _logger.debug(f"No address data provided for customer {bc_customer.get('id')}")
        
        customer_vals = {
            'name': name,
            'email': bc_customer.get('email', ''),
            'phone': processed_address_data.get('phone') or bc_customer.get('phone', ''),
            'street': processed_address_data.get('street', ''),
            'street2': processed_address_data.get('street2', ''),
            'city': processed_address_data.get('city', ''),
            'zip': processed_address_data.get('zip', ''),
            'country_id': processed_address_data.get('country_id', False),
            'state_id': processed_address_data.get('state_id', False),
            'bigcommerce_id': bc_customer.get('id'),
            'bigcommerce_synced': True,
            'bigcommerce_last_sync': fields.Datetime.now(),
            'bigcommerce_config_id': self.config_id.id,
            'is_company': False,
        }
        
        # Log address data being set
        if processed_address_data.get('street') or processed_address_data.get('city'):
            _logger.info(f"Setting address for customer {bc_customer.get('id')} ({name}): {processed_address_data.get('street')}, {processed_address_data.get('city')}, {processed_address_data.get('zip')}")
        else:
            _logger.debug(f"No address to set for customer {bc_customer.get('id')} ({name})")
        
        # Only set company name if it exists in BigCommerce
        bc_company = bc_customer.get('company', '').strip()
        if bc_company:
            customer_vals['company_name'] = bc_company
        
        if existing:
            existing.write(customer_vals)
            self.customers_updated += 1
        else:
            new_partner = partner_obj.create(customer_vals)
            self.customers_created += 1
            self._create_log('info', f"Created new customer {customer_vals.get('name', '')} (BC ID: {bc_customer.get('id')})",
                            customer_id=bc_customer.get('id'), customer_name=customer_vals.get('name', ''))
    
    def _sync_to_bigcommerce(self, api):
        """Sync customers from Odoo to BigCommerce"""
        domain = [
            ('bigcommerce_config_id', '=', self.config_id.id),
            ('is_company', '=', False),
            '|', ('bigcommerce_id', '=', False), ('bigcommerce_synced', '=', False)
        ]
        # Apply date filters
        if self.min_date_modified:
            domain.append(('write_date', '>=', self.min_date_modified))
            _logger.info(f"Filtering Odoo customers by write_date >= {self.min_date_modified}")
        if self.date_from:
            domain.append(('create_date', '>=', self.date_from))
            _logger.info(f"Filtering Odoo customers by create_date >= {self.date_from}")
        if self.date_to:
            domain.append(('create_date', '<=', self.date_to))
            _logger.info(f"Filtering Odoo customers by create_date <= {self.date_to}")
        customers = self.env['res.partner'].search(domain)
        
        # Update total items if not already set
        if self.total_items == 0:
            self.total_items = len(customers)
            self.env.cr.commit()
        
        for idx, customer in enumerate(customers, 1):
            # Update progress
            self.current_item = f"Processing: {customer.name[:50]}... (Item {idx}/{self.total_items})"
            self.processed_items = idx
            self.env.cr.commit()
            
            try:
                # Split name into first and last name
                name_parts = customer.name.split(' ', 1)
                first_name = name_parts[0] if name_parts else ''
                last_name = name_parts[1] if len(name_parts) > 1 else ''
                
                customer_data = {
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': customer.email or '',
                    'phone': customer.phone or '',
                }
                
                if customer.bigcommerce_id:
                    # Update existing customer
                    api.update_customer(customer.bigcommerce_id, customer_data)
                    self.customers_updated += 1
                else:
                    # Create new customer
                    bc_customer = api.create_customer(customer_data)
                    customer.write({
                        'bigcommerce_id': bc_customer.get('id'),
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    })
                    self.customers_created += 1
                    
            except Exception as e:
                _logger.error(f"Error syncing customer {customer.id} to BigCommerce: {str(e)}")
                self.customers_failed += 1
    
    def _get_country_id(self, country_code):
        """Get Odoo country ID from ISO code"""
        if not country_code:
            return False
        country = self.env['res.country'].search([('code', '=', country_code)], limit=1)
        return country.id if country else False
    
    def _get_state_id(self, state_name, country_code):
        """Get Odoo state ID from state name and country code"""
        if not state_name or not country_code:
            return False
        country_id = self._get_country_id(country_code)
        if not country_id:
            return False
        state = self.env['res.country.state'].search([
            ('name', '=', state_name),
            ('country_id', '=', country_id)
        ], limit=1)
        return state.id if state else False

