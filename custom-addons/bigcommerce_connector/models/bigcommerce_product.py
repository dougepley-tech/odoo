# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta, timezone
import logging
import requests
import base64
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz

_logger = logging.getLogger(__name__)


# =============================================================================
# PERFORMANCE OPTIMIZATION UTILITIES
# =============================================================================

def parse_bigcommerce_datetime(date_string):
    """
    Parse BigCommerce datetime string to UTC datetime.
    
    BigCommerce returns dates in ISO 8601/RFC 3339 format:
    - "2024-01-09T14:30:00Z" (UTC with Z indicator)
    - "2024-01-09T14:30:00+00:00" (UTC with offset)
    - "2024-01-09T14:30:00-05:00" (EST with offset)
    
    Returns:
        datetime: Timezone-aware datetime in UTC, or None if parsing fails
    """
    if not date_string:
        return None
    
    if isinstance(date_string, datetime):
        if date_string.tzinfo is None:
            return pytz.UTC.localize(date_string)
        return date_string.astimezone(pytz.UTC)
    
    if not isinstance(date_string, str):
        return None
    
    try:
        # Remove microseconds if present for easier parsing
        date_str = date_string
        has_microseconds = '.' in date_str and ('Z' in date_str or '+' in date_str or date_str.count('-') > 2)
        if has_microseconds:
            if 'Z' in date_str:
                date_str = date_str.split('.')[0] + 'Z'
            elif '+' in date_str:
                parts = date_str.split('+')
                date_str = parts[0].split('.')[0] + '+' + parts[1]
            elif date_str.count('-') > 2:
                last_dash_idx = date_str.rfind('-')
                if ':' in date_str[last_dash_idx:]:
                    date_part = date_str[:last_dash_idx]
                    tz_part = date_str[last_dash_idx:]
                    date_str = date_part.split('.')[0] + tz_part
        
        # Parse based on format
        if date_str.endswith('Z'):
            date_part = date_str[:-1]
            parsed_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
            return pytz.UTC.localize(parsed_date)
        elif '+' in date_str or (date_str.count('-') > 2 and ':' in date_str[-6:]):
            if '+' in date_str:
                date_part, tz_part = date_str.split('+', 1)
                tz_sign = 1
            else:
                last_dash = date_str.rfind('-')
                if last_dash > 10 and ':' in date_str[last_dash:]:
                    date_part = date_str[:last_dash]
                    tz_part = date_str[last_dash+1:]
                    tz_sign = -1
                else:
                    # Fallback - assume UTC
                    parsed_date = datetime.strptime(date_str[:19], '%Y-%m-%dT%H:%M:%S')
                    return pytz.UTC.localize(parsed_date)
            
            parsed_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
            tz_hours, tz_mins = map(int, tz_part.split(':'))
            tz_offset = timezone(timedelta(hours=tz_sign * tz_hours, minutes=tz_sign * tz_mins))
            parsed_date = parsed_date.replace(tzinfo=tz_offset)
            return parsed_date.astimezone(pytz.UTC)
        else:
            # No timezone info - assume UTC
            parsed_date = datetime.strptime(date_str[:19], '%Y-%m-%dT%H:%M:%S')
            return pytz.UTC.localize(parsed_date)
    except Exception as e:
        _logger.debug(f"Could not parse BigCommerce datetime '{date_string}': {str(e)}")
        return None


def download_image_async(image_url, timeout=30):
    """
    Download an image from URL. Designed for use with ThreadPoolExecutor.
    
    Returns:
        tuple: (image_url, base64_data, error_message)
        - On success: (url, base64_string, None)
        - On failure: (url, None, error_string)
    """
    try:
        response = requests.get(image_url, timeout=timeout, stream=True)
        response.raise_for_status()
        image_data = response.content
        if not image_data:
            return (image_url, None, "Downloaded image is empty")
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        return (image_url, image_base64, None)
    except Exception as e:
        return (image_url, None, str(e))


class ConcurrentImageDownloader:
    """
    Utility class for downloading multiple images concurrently.
    
    Usage:
        downloader = ConcurrentImageDownloader(max_workers=5)
        results = downloader.download_batch(image_urls)
        for url, data, error in results:
            if data:
                # Use base64 data
            else:
                # Handle error
    """
    
    def __init__(self, max_workers=5, timeout=30):
        self.max_workers = max_workers
        self.timeout = timeout
    
    def download_batch(self, image_urls):
        """
        Download multiple images concurrently.
        
        Args:
            image_urls: List of image URLs to download
            
        Returns:
            List of tuples: (url, base64_data_or_None, error_or_None)
        """
        if not image_urls:
            return []
        
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(download_image_async, url, self.timeout): url 
                for url in image_urls
            }
            for future in as_completed(future_to_url):
                results.append(future.result())
        
        return results


class ProductDataCache:
    """
    Cache for pre-fetched product data to avoid repeated database queries.
    
    This cache dramatically improves performance by loading all relevant data
    in bulk before processing products, eliminating N+1 query patterns.
    """
    
    def __init__(self, env, config_id):
        self.env = env
        self.config_id = config_id
        self._products_by_sku = {}
        self._products_by_bc_id = {}
        self._mappings_by_product = {}
        self._mappings_by_bc_id = {}
        self._dropship_route = None
        self._turn14_partner = None
        self._loaded = False
    
    def load(self):
        """Load all product data into cache."""
        if self._loaded:
            return
        
        _logger.info("OPTIMIZATION: Pre-fetching existing products and mappings...")
        start_time = datetime.now()
        
        # Load all products with SKUs
        products = self.env['product.template'].search([('default_code', '!=', False)])
        for product in products:
            if product.default_code:
                self._products_by_sku[product.default_code] = product
        
        _logger.info(f"  Loaded {len(self._products_by_sku)} products by SKU")
        
        # Load all mappings for this config
        mappings = self.env['bigcommerce.product.mapping'].search([
            ('config_id', '=', self.config_id)
        ])
        for mapping in mappings:
            if mapping.product_tmpl_id:
                if mapping.product_tmpl_id.id not in self._mappings_by_product:
                    self._mappings_by_product[mapping.product_tmpl_id.id] = []
                self._mappings_by_product[mapping.product_tmpl_id.id].append(mapping)
            if mapping.bigcommerce_id:
                self._mappings_by_bc_id[mapping.bigcommerce_id] = mapping
        
        _logger.info(f"  Loaded {len(mappings)} product mappings")
        
        # Pre-load dropship route
        self._dropship_route = self.env['stock.route'].search([
            ('name', 'ilike', 'dropship')
        ], limit=1)
        if not self._dropship_route:
            self._dropship_route = self.env['stock.route'].search([
                ('name', '=', 'Dropship')
            ], limit=1)
        
        # Pre-load TURN14 partner
        self._turn14_partner = self.env['res.partner'].search([
            ('name', '=', 'TURN14'),
            ('supplier_rank', '>', 0)
        ], limit=1)
        if not self._turn14_partner:
            self._turn14_partner = self.env['res.partner'].search([
                ('name', '=', 'TURN14')
            ], limit=1)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        _logger.info(f"OPTIMIZATION: Pre-fetch completed in {elapsed:.2f}s")
        self._loaded = True
    
    def get_product_by_sku(self, sku):
        """Get product by SKU from cache."""
        return self._products_by_sku.get(sku)
    
    def get_mapping_by_bc_id(self, bc_id):
        """Get mapping by BigCommerce ID from cache."""
        return self._mappings_by_bc_id.get(bc_id)
    
    def get_mappings_for_product(self, product_id):
        """Get all mappings for a product from cache."""
        return self._mappings_by_product.get(product_id, [])
    
    def get_dropship_route(self):
        """Get pre-loaded dropship route."""
        return self._dropship_route
    
    def get_turn14_partner(self):
        """Get pre-loaded TURN14 partner."""
        return self._turn14_partner
    
    def add_product(self, product):
        """Add a newly created product to the cache."""
        if product.default_code:
            self._products_by_sku[product.default_code] = product
    
    def add_mapping(self, mapping):
        """Add a newly created mapping to the cache."""
        if mapping.product_tmpl_id:
            if mapping.product_tmpl_id.id not in self._mappings_by_product:
                self._mappings_by_product[mapping.product_tmpl_id.id] = []
            self._mappings_by_product[mapping.product_tmpl_id.id].append(mapping)
        if mapping.bigcommerce_id:
            self._mappings_by_bc_id[mapping.bigcommerce_id] = mapping


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Legacy fields - kept for backward compatibility only (DO NOT USE FOR MULTI-STORE)
    # These will be populated from the first mapping if mappings exist
    # For multi-store products, use bigcommerce_mapping_ids instead
    # Legacy fields use group_bigcommerce_manager so BC managers can use sync buttons without developer mode
    bigcommerce_id = fields.Integer(copy=False, index=True, compute='_compute_legacy_bc_fields', store=True, groups='bigcommerce_connector.group_bigcommerce_manager')
    bigcommerce_id_display = fields.Char(compute='_compute_bigcommerce_id_display', store=False, groups='bigcommerce_connector.group_bigcommerce_manager')
    bigcommerce_variant_id = fields.Integer(copy=False, groups='bigcommerce_connector.group_bigcommerce_manager')
    bigcommerce_synced = fields.Boolean(string='Synced with BigCommerce', default=False, compute='_compute_legacy_bc_fields', store=True)
    bigcommerce_last_sync = fields.Datetime(string='Last Sync with BigCommerce', compute='_compute_legacy_bc_fields', store=True)
    bigcommerce_config_id = fields.Many2one('bigcommerce.config', compute='_compute_legacy_bc_fields', store=True, groups='bigcommerce_connector.group_bigcommerce_manager')
    
    # New multi-store support
    bigcommerce_mapping_ids = fields.One2many('bigcommerce.product.mapping', 'product_tmpl_id', string='BigCommerce Mappings')
    bigcommerce_mappings_display = fields.Html(string='BigCommerce Configurations', compute='_compute_bigcommerce_mappings_display', sanitize=False)
    
    # Product dimensions (synced from BigCommerce)
    product_length = fields.Float(string='Length', digits='Stock Weight', help='Product length (from BigCommerce)')
    product_width = fields.Float(string='Width', digits='Stock Weight', help='Product width (from BigCommerce)')
    product_height = fields.Float(string='Height', digits='Stock Weight', help='Product height (from BigCommerce)')

    # BigCommerce product description (separate from Odoo Internal notes)
    product_description = fields.Html(string='Product Description', sanitize=False,
                                     help='Product description synced from BigCommerce. Shown in Product Description section. Odoo Internal notes are kept separate and left blank on sync.')
    
    # Searchable field for custom filters (appears as top-level item)
    bc_product_id_search = fields.Char(string='BigCommerce Product ID', search='_search_bc_product_id', store=False)
    
    @api.depends('bigcommerce_mapping_ids', 'bigcommerce_mapping_ids.config_id', 'bigcommerce_mapping_ids.bigcommerce_id')
    def _compute_bigcommerce_mappings_display(self):
        """Compute HTML display of all BigCommerce mappings for this product"""
        for record in self:
            if not record.bigcommerce_mapping_ids:
                record.bigcommerce_mappings_display = False
                continue
            
            html_parts = []
            
            for mapping in record.bigcommerce_mapping_ids:
                config_name = mapping.config_id.name if mapping.config_id else 'Unknown'
                bc_id = mapping.bigcommerce_id or 'N/A'
                
                html = f'''
                <div style="padding: 8px 0; font-size: 13px; opacity: 0.8;">
                    <strong>BigCommerce Configuration:</strong> {config_name} &nbsp;&nbsp;|&nbsp;&nbsp; 
                    <strong>BigCommerce Product ID:</strong> {bc_id}
                </div>
                '''
                html_parts.append(html)
            
            record.bigcommerce_mappings_display = ''.join(html_parts)
    
    @api.depends('bigcommerce_mapping_ids', 'bigcommerce_mapping_ids.bigcommerce_id')
    def _compute_legacy_bc_fields(self):
        """Compute legacy fields from mappings for backward compatibility"""
        for record in self:
            if record.bigcommerce_mapping_ids:
                # Use the first mapping for legacy fields
                first_mapping = record.bigcommerce_mapping_ids[0]
                record.bigcommerce_id = first_mapping.bigcommerce_id
                record.bigcommerce_config_id = first_mapping.config_id
                record.bigcommerce_synced = first_mapping.bigcommerce_synced
                record.bigcommerce_last_sync = first_mapping.bigcommerce_last_sync
            else:
                # If no mappings, keep existing values (for backward compatibility)
                # Don't clear them automatically
                pass
    
    @api.depends('bigcommerce_id')
    def _compute_bigcommerce_id_display(self):
        """Compute display value for BigCommerce ID without thousands separator"""
        for record in self:
            record.bigcommerce_id_display = str(record.bigcommerce_id) if record.bigcommerce_id else ''
    
    def _search_bc_product_id(self, operator, value):
        """Custom search method for BigCommerce Product ID across all mappings"""
        return [('bigcommerce_mapping_ids.bigcommerce_id', operator, value)]
    
    @api.model
    def _name_search(self, name='', args=None, operator='ilike', limit=100, order=None):
        """Override name_search to include internal reference (default_code), name, and barcode in search.
        Does not include product_variant_ids.default_code to avoid slow JOINs on large catalogs."""
        args = args or []
        domain = []
        if name:
            domain = ['|', '|', ('default_code', operator, name), ('name', operator, name), ('barcode', operator, name)]
        return super(ProductTemplate, self)._name_search(name, args + domain, operator=operator, limit=limit, order=order)
    
    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """Override _search to include internal reference (default_code), name, and barcode in search bar searches.
        Does not include product_variant_ids.default_code to avoid slow JOINs. Also rewrite custom filter
        'BigCommerce Configuration is not set' so it returns products with no mappings.
        """
        # Only process if domain is a list (simple domain structure)
        # If domain is a Domain object or DomainCondition, let Odoo handle it natively
        if domain and isinstance(domain, list):
            new_domain = []
            i = 0
            while i < len(domain):
                item = domain[i]
                # Check if this is a name search condition
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    field, op, value = item
                    # Custom filter "BigCommerce Configuration is not set" generates bigcommerce_mapping_ids.config_id = False,
                    # which matches no products. Rewrite to "no mappings" so it returns products that did not sync.
                    if field == 'bigcommerce_mapping_ids.config_id' and op == '=' and value is False:
                        new_domain.append(('bigcommerce_mapping_ids', '=', False))
                        i += 1
                        continue
                    # Check if this is a name search (either direct name field or from search bar)
                    if field == 'name' and op in ('ilike', 'like', '=', '!=', '=like', 'not ilike') and value:
                        # Replace with OR on template-level fields only (no variant JOIN for performance)
                        new_domain.append('|')
                        new_domain.append('|')
                        new_domain.append(('default_code', op, value))
                        new_domain.append(('name', op, value))
                        new_domain.append(('barcode', op, value))
                        i += 1
                        continue
                new_domain.append(item)
                i += 1
            domain = new_domain
        
        return super(ProductTemplate, self)._search(domain, offset=offset, limit=limit, order=order, **kwargs)
    
    def action_sync_to_bigcommerce(self):
        """Sync this product to BigCommerce"""
        self.ensure_one()
        if not self.bigcommerce_config_id:
            raise UserError("No BigCommerce configuration set for this product. Please set a configuration first.")
        
        # Validate that if product already has a bigcommerce_id, it matches the current config
        # This prevents syncing a product from one store to another store
        if self.bigcommerce_id:
            # Check if there's another product with the same bigcommerce_id but different config
            conflicting_product = self.env['product.template'].search([
                ('bigcommerce_id', '=', self.bigcommerce_id),
                ('bigcommerce_config_id', '!=', self.bigcommerce_config_id.id),
                ('id', '!=', self.id)
            ], limit=1)
            if conflicting_product:
                raise UserError(
                    f"Product with BigCommerce ID {self.bigcommerce_id} already exists "
                    f"with a different configuration ({conflicting_product.bigcommerce_config_id.name}). "
                    f"Cannot sync to a different store configuration."
                )
        
        # Check if product has required tags (if tags are configured)
        if self.bigcommerce_config_id.product_tag_ids:
            product_tags = self.product_tag_ids
            config_tags = self.bigcommerce_config_id.product_tag_ids
            if not (product_tags & config_tags):
                raise UserError(
                    f"Product '{self.name}' does not have the required tags to sync to BigCommerce. "
                    f"Required tags: {', '.join(config_tags.mapped('name'))}. "
                    f"Product tags: {', '.join(product_tags.mapped('name')) if product_tags else 'None'}."
                )
        
        try:
            api = self.bigcommerce_config_id.get_api_client()
            self._sync_single_product_to_bc(api)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f'Product {self.name} synced to BigCommerce successfully!',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.error(f"Error syncing product {self.id} to BigCommerce: {str(e)}", exc_info=True)
            raise UserError(f"Error syncing product to BigCommerce: {str(e)}")
    
    def action_sync_from_bigcommerce(self):
        """Sync this product from BigCommerce - syncs from all configurations that have mappings"""
        self.ensure_one()
        
        # Get all mappings for this product
        mappings = self.bigcommerce_mapping_ids
        if not mappings:
            # Fallback to legacy field for backward compatibility
            if not self.bigcommerce_id or not self.bigcommerce_config_id:
                raise UserError("This product is not linked to any BigCommerce store. Cannot sync from BigCommerce.")
            # Use legacy fields - create a temporary mapping reference
            config = self.bigcommerce_config_id
            bc_id = self.bigcommerce_id
        else:
            # Use mappings - sync from all configurations
            config = None
            bc_id = None
        
        synced_configs = []
        failed_configs = []
        
        # If we have mappings, sync from each configuration
        if mappings:
            for mapping in mappings:
                config = mapping.config_id
                bc_id = mapping.bigcommerce_id
                
                if not config or not bc_id:
                    continue
                
                try:
                    api = config.get_api_client()
                    # Always fetch with images if sync_product_images is enabled in config
                    sync_images_enabled = config.sync_product_images if config else False
                    bc_product = api.get_product(bc_id, include_images=sync_images_enabled)
                    
                    # Validate response - product removed from this store, remove the mapping
                    if not bc_product:
                        _logger.info(f"Product {bc_id} not found in {config.name} - removing mapping (product was removed from this store)")
                        mapping.unlink()
                        failed_configs.append(f"{config.name} (Product not found - mapping removed)")
                        continue
                    
                    # Ensure bc_product is a dictionary
                    if not isinstance(bc_product, dict):
                        _logger.error(f"Invalid product data type from API: {type(bc_product)}, value: {bc_product}")
                        failed_configs.append(f"{config.name} (Invalid data)")
                        continue
                    
                    # Validate that product has an ID
                    if 'id' not in bc_product or not bc_product.get('id'):
                        _logger.error(f"Product data missing ID field. Product data: {bc_product}")
                        failed_configs.append(f"{config.name} (Missing ID)")
                        continue
                    
                    # Validate that the BigCommerce product ID matches
                    bc_product_id = bc_product.get('id')
                    if bc_product_id != bc_id:
                        _logger.warning(f"Product BigCommerce ID mismatch for config {config.name}. Expected {bc_id}, got {bc_product_id}")
                        # Continue anyway - the mapping might be outdated
                    
                    # Create a temporary sync object to use the sync method
                    # This will sync product images and variant images if sync_product_images is enabled
                    sync_obj = self.env['bigcommerce.product.sync'].create({
                        'name': f'Sync Product {self.name} from {config.name}',
                        'config_id': config.id,
                        'sync_direction': 'bc_to_odoo',
                        'sync_images': sync_images_enabled,
                        'state': 'draft',
                    })
                    # This method will sync both product template images and variant images if sync_images is True
                    sync_obj._create_or_update_product_from_bc(api, bc_product)
                    # Clean up the temporary sync record
                    sync_obj.unlink()
                    
                    # Update mapping sync status
                    mapping.write({
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    })
                    
                    synced_configs.append(config.name)
                    
                except Exception as e:
                    error_str = str(e).lower()
                    if '404' in error_str or 'not found' in error_str:
                        _logger.info(f"Product {bc_id} not found in {config.name} (API error) - removing mapping (product was removed from this store)")
                        mapping.unlink()
                        failed_configs.append(f"{config.name} (Product not found - mapping removed)")
                    else:
                        _logger.error(f"Error syncing product {self.id} from BigCommerce config {config.name}: {str(e)}", exc_info=True)
                        failed_configs.append(f"{config.name} ({str(e)})")
        else:
            # Legacy path - sync from single configuration
            try:
                api = config.get_api_client()
                # Always fetch with images if sync_product_images is enabled in config
                sync_images_enabled = config.sync_product_images if config else False
                bc_product = api.get_product(bc_id, include_images=sync_images_enabled)
                
                # Validate response
                if not bc_product:
                    raise UserError(f"Product not found in BigCommerce (ID: {bc_id})")
                
                # Ensure bc_product is a dictionary
                if not isinstance(bc_product, dict):
                    _logger.error(f"Invalid product data type from API: {type(bc_product)}, value: {bc_product}")
                    raise UserError(f"Invalid product data received from BigCommerce. Expected dictionary, got {type(bc_product).__name__}.")
                
                # Validate that product has an ID
                if 'id' not in bc_product or not bc_product.get('id'):
                    _logger.error(f"Product data missing ID field. Product data: {bc_product}")
                    raise UserError(f"Product data from BigCommerce is missing the product ID.")
                
                # Validate that the BigCommerce product ID matches
                bc_product_id = bc_product.get('id')
                if bc_product_id != bc_id:
                    raise UserError(
                        f"Product BigCommerce ID mismatch. Product has BC ID {bc_id}, "
                        f"but fetched product has BC ID {bc_product_id}. This may indicate a configuration mismatch."
                    )
                
                # Create a temporary sync object to use the sync method
                sync_obj = self.env['bigcommerce.product.sync'].create({
                    'name': f'Sync Product {self.name}',
                    'config_id': config.id,
                    'sync_direction': 'bc_to_odoo',
                    'sync_images': sync_images_enabled,
                    'state': 'draft',
                })
                # This method will sync both product template images and variant images if sync_images is True
                sync_obj._create_or_update_product_from_bc(api, bc_product)
                # Clean up the temporary sync record
                sync_obj.unlink()
                
                synced_configs.append(config.name)
                
            except Exception as e:
                _logger.error(f"Error syncing product {self.id} from BigCommerce: {str(e)}", exc_info=True)
                raise UserError(f"Error syncing product from BigCommerce: {str(e)}")
        
        # Reload the product record to show updated data
        self.invalidate_recordset()
        
        # Build success message
        if synced_configs:
            message = f'Product {self.name} synced from {len(synced_configs)} store(s): {", ".join(synced_configs)}'
            if failed_configs:
                message += f'\nFailed: {", ".join(failed_configs)}'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success' if not failed_configs else 'Partial Success',
                    'message': message,
                    'type': 'success' if not failed_configs else 'warning',
                    'sticky': False,
                }
            }
        else:
            raise UserError(f"Failed to sync product from BigCommerce. Errors: {', '.join(failed_configs)}")
    
    def action_bulk_sync_from_bigcommerce(self):
        """Bulk sync selected products from BigCommerce"""
        if not self:
            raise UserError("Please select at least one product to update.")
        
        # Track results
        total_products = len(self)
        synced_count = 0
        failed_products = []
        
        # Process each product
        for product in self:
            try:
                # Check if product has BigCommerce mapping
                if not product.bigcommerce_mapping_ids and not product.bigcommerce_id:
                    failed_products.append(f"{product.name} (No BigCommerce mapping)")
                    continue
                
                # Sync this product
                product.action_sync_from_bigcommerce()
                synced_count += 1
                
            except Exception as e:
                _logger.error(f"Error syncing product {product.id} from BigCommerce: {str(e)}", exc_info=True)
                failed_products.append(f"{product.name} ({str(e)})")
        
        # Build notification message
        message = f"Updated {synced_count} of {total_products} product(s) from BigCommerce."
        if failed_products:
            message += f"\n\nFailed ({len(failed_products)}):\n" + "\n".join(failed_products[:5])
            if len(failed_products) > 5:
                message += f"\n...and {len(failed_products) - 5} more"
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Bulk Update Complete' if synced_count == total_products else 'Partial Success',
                'message': message,
                'type': 'success' if not failed_products else 'warning',
                'sticky': True,
            }
        }
    
    def _sync_single_product_to_bc(self, api):
        """Sync a single product to BigCommerce
        
        Note: This method uses the product's bigcommerce_config_id to ensure
        products are synced to the correct BigCommerce store.
        """
        # Validate that we're using the correct API client for this product's config
        # The API client should have been created from self.bigcommerce_config_id
        # Determine inventory_tracking value based on product configuration
        # BigCommerce accepts: "none", "product", or "variant"
        if self.product_variant_ids and len(self.product_variant_ids) > 1:
            # Product has variants - track inventory at variant level
            inventory_tracking = 'variant'
        elif hasattr(self, 'is_storable') and self.is_storable and self.tracking != 'none':
            # Product has "Track Inventory" enabled and tracking is set - track at product level
            inventory_tracking = 'product'
        elif hasattr(self, 'is_storable') and self.is_storable:
            # Product has "Track Inventory" enabled but no tracking method set - track at product level
            inventory_tracking = 'product'
        else:
            # No inventory tracking
            inventory_tracking = 'none'
        
        product_data = {
            'name': self.name,
            'type': 'physical',
            'price': str(self.list_price),
            'weight': self.weight or 0,
            'depth': self.product_length or 0,  # BigCommerce uses 'depth' for length
            'width': self.product_width or 0,
            'height': self.product_height or 0,
        }
        
        # Only include inventory_tracking when creating a new product
        # For existing products, preserve the current inventory tracking settings in BigCommerce
        if not self.bigcommerce_id:
            product_data['inventory_tracking'] = inventory_tracking
            _logger.debug(f"Creating new product - including inventory_tracking: {inventory_tracking}")
        else:
            _logger.debug(f"Updating existing product - skipping inventory_tracking update (preserving current BigCommerce settings)")
        
        if self.default_code:
            product_data['sku'] = self.default_code
        
        if self.product_description or self.description:
            product_data['description'] = self.product_description or self.description
        
        # Always include option_values for both create and update to prevent 422 errors
        # BigCommerce requires this field, even if empty
        product_data['option_values'] = []
        
        # Store BigCommerce product ID for inventory sync
        bc_product_id = None
        
        if self.bigcommerce_id:
            # Update existing product
            # Validate that the product's bigcommerce_config_id matches before updating
            # This ensures we're updating the product in the correct store
            if not self.bigcommerce_config_id:
                raise UserError(
                    f"Cannot update product {self.name}: No BigCommerce configuration set. "
                    f"Product has BigCommerce ID {self.bigcommerce_id} but no configuration."
                )
            
            # Include option_values to prevent 422 errors (empty array won't clear existing options)
            _logger.debug(f"Updating product with data: {product_data} (Config: {self.bigcommerce_config_id.name})")
            api.update_product(self.bigcommerce_id, product_data)
            _logger.info(f"Updated product {self.id} in BigCommerce (BC ID: {self.bigcommerce_id}, Config: {self.bigcommerce_config_id.name})")
            bc_product_id = self.bigcommerce_id
            
            # For existing products, don't sync variants automatically
            # Variants should be synced individually when the user clicks "Sync to BigCommerce" on a specific variant
        else:
            # Create new product
            # BigCommerce API requires option_values field when creating products
            # According to BigCommerce API docs, this field is required for products with variants
            # We'll include it as an empty array - variants/options will be created separately after product creation
            # This prevents 422 "Missing Required Fields" errors
            _logger.debug(f"Creating product with data: {product_data}")
            bc_product = api.create_product(product_data)
            bc_product_id = bc_product.get('id')
            self.write({
                'bigcommerce_id': bc_product_id,
                'bigcommerce_synced': True,
                'bigcommerce_last_sync': fields.Datetime.now(),
                'bigcommerce_config_id': self.bigcommerce_config_id.id,
            })
            _logger.info(f"Created product {self.id} in BigCommerce (BC ID: {bc_product_id})")
            
            # For new products, sync all variants automatically
            if self.product_variant_ids:
                _logger.info(f"Syncing {len(self.product_variant_ids)} variant(s) for newly created product")
                for variant in self.product_variant_ids:
                    try:
                        variant._sync_variant_to_bc(api, bc_product_id)
                    except Exception as variant_error:
                        _logger.error(f"Error syncing variant {variant.id} during product creation: {str(variant_error)}")
                        # Continue with other variants even if one fails
                        continue
        
        self.write({
            'bigcommerce_synced': True,
            'bigcommerce_last_sync': fields.Datetime.now(),
        })
    
    def action_view_in_bigcommerce(self):
        """Open the product in BigCommerce admin panel"""
        self.ensure_one()
        if not self.bigcommerce_config_id:
            raise UserError("No BigCommerce configuration set for this product. Please set a configuration first.")
        
        if not self.bigcommerce_id:
            raise UserError("This product is not synced with BigCommerce. Please sync it first.")
        
        if not self.bigcommerce_config_id.store_hash:
            raise UserError("BigCommerce store hash is not configured.")
        
        # Construct BigCommerce admin URL using the correct format
        # Format: https://store-{store_hash}.mybigcommerce.com/manage/products/edit/{product_id}
        store_hash = self.bigcommerce_config_id.store_hash
        product_id = self.bigcommerce_id
        bc_url = f"https://store-{store_hash}.mybigcommerce.com/manage/products/edit/{product_id}"
        
        return {
            'type': 'ir.actions.act_url',
            'url': bc_url,
            'target': 'new',
        }
    
    def action_sync_inventory_to_bigcommerce(self):
        """Sync inventory quantity for this product to BigCommerce
        
        For products with variants, syncs inventory for each variant individually.
        For products without variants, syncs product-level inventory.
        """
        self.ensure_one()
        if not self.bigcommerce_config_id:
            raise UserError("No BigCommerce configuration set for this product. Please set a configuration first.")
        
        if not self.bigcommerce_id:
            raise UserError("This product is not linked to a BigCommerce product. Please sync the product to BigCommerce first.")
        
        try:
            api = self.bigcommerce_config_id.get_api_client()
            
            # Check if product has "Track Inventory" enabled
            is_storable = hasattr(self, 'is_storable') and self.is_storable
            if not is_storable:
                raise UserError("This product does not have 'Track Inventory' enabled. Enable 'Track Inventory' checkbox to sync inventory.")
            
            # Get location mappings (preferred) or warehouse mappings (fallback)
            location_mappings = self.bigcommerce_config_id.location_mapping_ids.filtered(lambda l: l.active)
            default_location = location_mappings.filtered(lambda l: l.is_default) if location_mappings else False
            
            # Fallback to warehouse mappings if no location mappings are configured
            warehouse_mappings = self.bigcommerce_config_id.warehouse_mapping_ids.filtered(lambda w: w.active) if not location_mappings else False
            default_warehouse = warehouse_mappings.filtered(lambda w: w.is_default) if warehouse_mappings else False
            
            # Determine if product has variants
            has_variants = self.product_variant_ids and len(self.product_variant_ids) > 1
            
            if has_variants:
                # Product has variants - sync each variant individually
                synced_count = 0
                failed_count = 0
                
                for variant in self.product_variant_ids:
                    if not variant.bigcommerce_variant_id:
                        _logger.warning(f"Variant {variant.name} is not linked to BigCommerce. Skipping inventory sync.")
                        failed_count += 1
                        continue
                    
                    try:
                        # Calculate inventory for this variant (using location mappings if available)
                        qty_available = self._calculate_variant_inventory(variant, location_mappings, default_location, warehouse_mappings, default_warehouse)
                        
                        # Update variant-level inventory
                        inventory_data = {
                            'inventory_level': int(qty_available),
                            'inventory_warning_level': 0,
                        }
                        
                        _logger.info(f"Syncing inventory for variant {variant.name} (BC Variant ID: {variant.bigcommerce_variant_id}): {qty_available} units")
                        result = api.update_variant_inventory(
                            self.bigcommerce_id,
                            variant.bigcommerce_variant_id,
                            inventory_data
                        )
                        
                        if result is None:
                            _logger.warning(f"Could not update inventory for variant {variant.name} (BC Variant ID: {variant.bigcommerce_variant_id})")
                            failed_count += 1
                        else:
                            _logger.info(f"✓ Successfully synced inventory for variant {variant.name}: {qty_available} units")
                            synced_count += 1
                    except Exception as variant_error:
                        _logger.error(f"Error syncing inventory for variant {variant.name}: {str(variant_error)}")
                        failed_count += 1
                
                # Return summary notification
                if synced_count > 0:
                    message = f'Inventory synced for {synced_count} variant(s)'
                    if failed_count > 0:
                        message += f'. {failed_count} variant(s) failed.'
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Success' if failed_count == 0 else 'Partial Success',
                            'message': message,
                            'type': 'success' if failed_count == 0 else 'warning',
                            'sticky': False,
                        }
                    }
                else:
                    raise UserError(f"Failed to sync inventory for all variants. Check logs for details.")
            else:
                # Product without variants - sync product-level inventory
                product_variant = self.product_variant_id or (self.product_variant_ids[0] if self.product_variant_ids else None)
                
                if not product_variant:
                    raise UserError("Product has no variant to sync inventory for.")
                
                # Calculate inventory (using location mappings if available)
                qty_available = self._calculate_variant_inventory(product_variant, location_mappings, default_location, warehouse_mappings, default_warehouse)
                
                # Update BigCommerce inventory
                # Note: Do not include inventory_tracking - preserve existing BigCommerce settings
                inventory_data = {
                    'inventory_level': int(qty_available),
                    'inventory_warning_level': 0,
                }
                
                _logger.info(f"Syncing inventory for product {self.name} (BC ID: {self.bigcommerce_id}): {qty_available} units")
                result = api.update_product_inventory(self.bigcommerce_id, inventory_data)
                
                if result is None:
                    raise UserError(f"Could not update inventory for product {self.name}. The product may not exist in BigCommerce or the inventory endpoint is unavailable.")
                
                _logger.info(f"✓ Successfully synced inventory for product {self.name} (BC ID: {self.bigcommerce_id}): {qty_available} units")
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Success',
                        'message': f'Inventory synced to BigCommerce successfully! Quantity: {qty_available} units',
                        'type': 'success',
                        'sticky': False,
                    }
                }
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Error syncing inventory for product {self.id} to BigCommerce: {str(e)}", exc_info=True)
            raise UserError(f"Error syncing inventory to BigCommerce: {str(e)}")
    
    def _calculate_variant_inventory(self, variant, location_mappings, default_location, warehouse_mappings, default_warehouse):
        """Helper method to calculate inventory quantity for a variant
        Prioritizes location mappings over warehouse mappings"""
        qty_available = 0
        
        # Priority 1: Use location mappings if configured
        if location_mappings:
            # Sum inventory from all mapped locations (including sub-locations)
            for mapping in location_mappings:
                location = mapping.odoo_location_id
                if location:
                    # Get all child locations (sub-locations) of this location
                    child_locations = self.env['stock.location'].search([
                        ('id', 'child_of', location.id)
                    ])
                    
                    # Sum inventory from the location and all its sub-locations
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', variant.id),
                        ('location_id', 'in', child_locations.ids)
                    ])
                    location_qty = sum(quants.mapped('quantity'))
                    
                    # Check minimum threshold
                    if mapping.min_threshold > 0 and location_qty < mapping.min_threshold:
                        _logger.debug(f"Skipping location {location.complete_name} (and sub-locations) for variant {variant.name} - below threshold")
                        continue
                    
                    qty_available += location_qty
        elif default_location:
            # Use default location only (including sub-locations)
            location = default_location.odoo_location_id
            if location:
                # Get all child locations (sub-locations) of this location
                child_locations = self.env['stock.location'].search([
                    ('id', 'child_of', location.id)
                ])
                
                # Sum inventory from the location and all its sub-locations
                quants = self.env['stock.quant'].search([
                    ('product_id', '=', variant.id),
                    ('location_id', 'in', child_locations.ids)
                ])
                qty_available = sum(quants.mapped('quantity'))
        # Priority 2: Fallback to warehouse mappings if no location mappings
        elif warehouse_mappings:
            # Sum inventory from all mapped warehouses
            for mapping in warehouse_mappings:
                warehouse = mapping.odoo_warehouse_id
                if warehouse:
                    location = warehouse.lot_stock_id
                    if location:
                        quant = self.env['stock.quant'].search([
                            ('product_id', '=', variant.id),
                            ('location_id', '=', location.id)
                        ], limit=1)
                        warehouse_qty = quant.quantity if quant else 0
                        
                        # Check minimum threshold
                        if mapping.min_threshold > 0 and warehouse_qty < mapping.min_threshold:
                            _logger.debug(f"Skipping warehouse {warehouse.name} for variant {variant.name} - below threshold")
                            continue
                        
                        qty_available += warehouse_qty
        elif default_warehouse:
            # Use default warehouse only
            warehouse = default_warehouse.odoo_warehouse_id
            if warehouse:
                location = warehouse.lot_stock_id
                if location:
                    quant = self.env['stock.quant'].search([
                        ('product_id', '=', variant.id),
                        ('location_id', '=', location.id)
                    ], limit=1)
                    qty_available = quant.quantity if quant else 0
        else:
            # Fallback: try to get quantity from variant's quants only (do not use qty_available to avoid triggering recomputation)
            location = self.env['stock.location'].search([
                ('usage', '=', 'internal')
            ], limit=1)
            if location:
                quant = self.env['stock.quant'].search([
                    ('product_id', '=', variant.id),
                    ('location_id', '=', location.id)
                ], limit=1)
                qty_available = quant.quantity if quant else 0
            # else: qty_available stays 0
        
        return qty_available


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _get_description(self, picking_type_id):
        """Never use internal notes on pickings or barcode app; use product name only.
        Standard Odoo uses product.description (internal notes) for non-outgoing pickings,
        which can show long BC descriptions on barcode picking. Override to use display_name only.
        """
        return self.display_name

    # Add related field for product tags from template (for list view display)
    product_tag_ids = fields.Many2many('product.tag', related='product_tmpl_id.product_tag_ids', string='Product Tags', readonly=False)

    # Legacy field - kept for backward compatibility
    # Will be computed from variant mappings
    bigcommerce_variant_id = fields.Integer(string='BigCommerce Variant ID', copy=False, index=True, compute='_compute_legacy_variant_bc_id', store=True)
    
    # New multi-store support
    variant_mapping_ids = fields.One2many('bigcommerce.variant.mapping', 'product_variant_id', string='BigCommerce Variant Mappings')
    
    @api.depends('variant_mapping_ids', 'variant_mapping_ids.bigcommerce_variant_id')
    def _compute_legacy_variant_bc_id(self):
        """Compute legacy bigcommerce_variant_id from mappings for backward compatibility"""
        for record in self:
            if record.variant_mapping_ids:
                # Use the first mapping's BC variant ID
                record.bigcommerce_variant_id = record.variant_mapping_ids[0].bigcommerce_variant_id
            # If no mappings, keep existing value (for backward compatibility)
    
    def action_sync_to_bigcommerce(self):
        """Sync this product variant to BigCommerce"""
        self.ensure_one()
        
        if not self.product_tmpl_id.bigcommerce_config_id:
            raise UserError("No BigCommerce configuration set for this product. Please set a configuration first.")
        
        if not self.product_tmpl_id.bigcommerce_id:
            raise UserError("The parent product must be synced to BigCommerce first. Please sync the product template first.")
        
        # Get the config from the product template's mapping (or legacy field)
        config_id = None
        if self.product_tmpl_id.bigcommerce_mapping_ids:
            # Use the first mapping's config (or find by current context if available)
            config_id = self.product_tmpl_id.bigcommerce_mapping_ids[0].config_id
        elif self.product_tmpl_id.bigcommerce_config_id:
            config_id = self.product_tmpl_id.bigcommerce_config_id
        
        if not config_id:
            raise UserError("No BigCommerce configuration found for this product. Please set a configuration first.")
        
        # Check if parent product has required tags (if tags are configured)
        if self.product_tmpl_id.bigcommerce_config_id.product_tag_ids:
            product_tags = self.product_tmpl_id.product_tag_ids
            config_tags = self.product_tmpl_id.bigcommerce_config_id.product_tag_ids
            if not (product_tags & config_tags):
                raise UserError(
                    f"Product '{self.product_tmpl_id.name}' does not have the required tags to sync to BigCommerce. "
                    f"Required tags: {', '.join(config_tags.mapped('name'))}. "
                    f"Product tags: {', '.join(product_tags.mapped('name')) if product_tags else 'None'}."
                )
        
        try:
            api = self.product_tmpl_id.bigcommerce_config_id.get_api_client()
            self._sync_variant_to_bc(api, self.product_tmpl_id.bigcommerce_id)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f'Variant {self.name} synced to BigCommerce successfully!',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.error(f"Error syncing variant {self.id} to BigCommerce: {str(e)}", exc_info=True)
            raise UserError(f"Error syncing variant to BigCommerce: {str(e)}")
    
    def action_sync_inventory_to_bigcommerce(self):
        """Sync inventory quantity for this variant to BigCommerce"""
        self.ensure_one()
        
        if not self.product_tmpl_id.bigcommerce_config_id:
            raise UserError("No BigCommerce configuration set for this product. Please set a configuration first.")
        
        if not self.product_tmpl_id.bigcommerce_id:
            raise UserError("The parent product must be synced to BigCommerce first. Please sync the product template first.")
        
        # Get config from product template
        config_id = None
        if self.product_tmpl_id.bigcommerce_mapping_ids:
            config_id = self.product_tmpl_id.bigcommerce_mapping_ids[0].config_id
        elif self.product_tmpl_id.bigcommerce_config_id:
            config_id = self.product_tmpl_id.bigcommerce_config_id
        
        if not config_id:
            raise UserError("No BigCommerce configuration found for this product.")
        
        # Get BigCommerce variant ID from mapping
        variant_mapping = self.env['bigcommerce.variant.mapping'].search([
            ('product_variant_id', '=', self.id),
            ('config_id', '=', config_id.id)
        ], limit=1)
        
        bc_variant_id = variant_mapping.bigcommerce_variant_id if variant_mapping else None
        
        if not bc_variant_id:
            raise UserError("This variant is not linked to a BigCommerce variant for this configuration. Please sync the variant to BigCommerce first.")
        
        try:
            api = config_id.get_api_client()
            
            # Check if variant has "Track Inventory" enabled
            variant_is_storable = getattr(self, 'is_storable', None)
            if variant_is_storable is None:
                variant_is_storable = getattr(self.product_tmpl_id, 'is_storable', False)
            
            if not variant_is_storable:
                raise UserError("This variant does not have 'Track Inventory' enabled. Enable 'Track Inventory' checkbox to sync inventory.")
            
            # Get location mappings (preferred) or warehouse mappings (fallback)
            location_mappings = self.product_tmpl_id.bigcommerce_config_id.location_mapping_ids.filtered(lambda l: l.active)
            default_location = location_mappings.filtered(lambda l: l.is_default) if location_mappings else False
            
            # Fallback to warehouse mappings if no location mappings are configured
            warehouse_mappings = self.product_tmpl_id.bigcommerce_config_id.warehouse_mapping_ids.filtered(lambda w: w.active) if not location_mappings else False
            default_warehouse = warehouse_mappings.filtered(lambda w: w.is_default) if warehouse_mappings else False
            
            qty_available = 0
            
            # Priority 1: Use location mappings if configured
            if location_mappings:
                # Sum inventory from all mapped locations (including sub-locations)
                for mapping in location_mappings:
                    location = mapping.odoo_location_id
                    if location:
                        # Get all child locations (sub-locations) of this location
                        child_locations = self.env['stock.location'].search([
                            ('id', 'child_of', location.id)
                        ])
                        
                        # Sum inventory from the location and all its sub-locations
                        quants = self.env['stock.quant'].search([
                            ('product_id', '=', self.id),
                            ('location_id', 'in', child_locations.ids)
                        ])
                        location_qty = sum(quants.mapped('quantity'))
                        
                        # Check minimum threshold
                        if mapping.min_threshold > 0 and location_qty < mapping.min_threshold:
                            _logger.debug(f"Skipping location {location.complete_name} (and sub-locations) for variant {self.name} - below threshold")
                            continue
                        
                        qty_available += location_qty
            elif default_location:
                # Use default location only (including sub-locations)
                location = default_location.odoo_location_id
                if location:
                    # Get all child locations (sub-locations) of this location
                    child_locations = self.env['stock.location'].search([
                        ('id', 'child_of', location.id)
                    ])
                    
                    # Sum inventory from the location and all its sub-locations
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', self.id),
                        ('location_id', 'in', child_locations.ids)
                    ])
                    qty_available = sum(quants.mapped('quantity'))
            # Priority 2: Fallback to warehouse mappings if no location mappings
            elif warehouse_mappings:
                # Sum inventory from all mapped warehouses
                for mapping in warehouse_mappings:
                    warehouse = mapping.odoo_warehouse_id
                    if warehouse:
                        location = warehouse.lot_stock_id
                        if location:
                            quant = self.env['stock.quant'].search([
                                ('product_id', '=', self.id),
                                ('location_id', '=', location.id)
                            ], limit=1)
                            warehouse_qty = quant.quantity if quant else 0
                            
                            # Check minimum threshold
                            if mapping.min_threshold > 0 and warehouse_qty < mapping.min_threshold:
                                _logger.debug(f"Skipping warehouse {warehouse.name} for variant {self.name} - below threshold")
                                continue
                            
                            qty_available += warehouse_qty
            elif default_warehouse:
                # Use default warehouse only
                warehouse = default_warehouse.odoo_warehouse_id
                if warehouse:
                    location = warehouse.lot_stock_id
                    if location:
                        quant = self.env['stock.quant'].search([
                            ('product_id', '=', self.id),
                            ('location_id', '=', location.id)
                        ], limit=1)
                        qty_available = quant.quantity if quant else 0
            else:
                # Fallback: try to get quantity from quants only (do not use qty_available to avoid triggering recomputation)
                location = self.env['stock.location'].search([
                    ('usage', '=', 'internal')
                ], limit=1)
                if location:
                    quant = self.env['stock.quant'].search([
                        ('product_id', '=', self.id),
                        ('location_id', '=', location.id)
                    ], limit=1)
                    qty_available = quant.quantity if quant else 0
                # else: qty_available stays 0
            
            # For variants, use variant-level inventory update
            inventory_data = {
                'inventory_level': int(qty_available),
                'inventory_warning_level': 0,
            }
            
            # Get product BC ID from mapping
            product_mapping = self.env['bigcommerce.product.mapping'].search([
                ('product_tmpl_id', '=', self.product_tmpl_id.id),
                ('config_id', '=', config_id.id)
            ], limit=1)
            
            bc_product_id = product_mapping.bigcommerce_id if product_mapping else (self.product_tmpl_id.bigcommerce_id or None)
            
            if not bc_product_id:
                raise UserError(f"Product {self.product_tmpl_id.name} is not linked to a BigCommerce product for this configuration.")
            
            _logger.info(f"Syncing inventory for variant {self.name} (BC Variant ID: {bc_variant_id}, Product BC ID: {bc_product_id}): {qty_available} units")
            
            # Use variant-specific inventory endpoint
            result = api.update_variant_inventory(
                bc_product_id,
                bc_variant_id,
                inventory_data
            )
            
            if result is None:
                raise UserError(f"Could not update inventory for variant {self.name}. The variant may not exist in BigCommerce or the inventory endpoint is unavailable.")
            
            _logger.info(f"✓ Successfully synced inventory for variant {self.name} (BC Variant ID: {bc_variant_id}): {qty_available} units")
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f'Inventory synced to BigCommerce successfully! Quantity: {qty_available} units',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Error syncing inventory for variant {self.id} to BigCommerce: {str(e)}", exc_info=True)
            raise UserError(f"Error syncing inventory to BigCommerce: {str(e)}")
    
    def action_sync_from_bigcommerce(self):
        """Sync this product variant's template from BigCommerce"""
        self.ensure_one()
        # Delegate to the product template's sync method
        return self.product_tmpl_id.action_sync_from_bigcommerce()
    
    def action_view_in_bigcommerce(self):
        """Open the product in BigCommerce admin panel (delegates to product template)"""
        self.ensure_one()
        # Variants don't have their own BigCommerce product ID - they're part of the parent product
        # Delegate to the product template's method
        return self.product_tmpl_id.action_view_in_bigcommerce()
    
    def _sync_variant_to_bc(self, api, bc_product_id):
        """Sync a single product variant to BigCommerce"""
        # Use direct variant price when product_variant_pricing sets list_price_override
        effective_price = self.variant_list_price if self.list_price_override else self.list_price
        variant_data = {
            'price': str(effective_price) if effective_price else '0',
            'weight': self.weight or 0,
        }
        
        if self.default_code:
            variant_data['sku'] = self.default_code
        
        if self.barcode:
            variant_data['upc'] = self.barcode
        
        # Set cost price if available
        if self.standard_price:
            variant_data['cost_price'] = str(self.standard_price)
        
        try:
            if self.bigcommerce_variant_id:
                # Update existing variant
                api.update_product_variant(bc_product_id, self.bigcommerce_variant_id, variant_data)
                _logger.info(f"Updated variant {self.id} in BigCommerce (BC Variant ID: {self.bigcommerce_variant_id})")
            else:
                # Create new variant
                # Note: For variants with attributes, we need to map Odoo attribute values to BigCommerce option values
                # This is a simplified version - full implementation would need to map attribute values
                bc_variant = api.create_product_variant(bc_product_id, variant_data)
                bc_variant_id = bc_variant.get('id') if isinstance(bc_variant, dict) else None
                if bc_variant_id:
                    self.write({
                        'bigcommerce_variant_id': bc_variant_id,
                    })
                    _logger.info(f"Created variant {self.id} in BigCommerce (BC Variant ID: {bc_variant_id})")
                else:
                    _logger.warning(f"Failed to create variant {self.id} in BigCommerce - no ID returned")
                    raise UserError("Failed to create variant in BigCommerce - no ID returned from API")
        except Exception as e:
            _logger.error(f"Error syncing variant {self.id} to BigCommerce: {str(e)}", exc_info=True)
            raise


class BigCommerceProductSync(models.Model):
    _name = 'bigcommerce.product.sync'
    _description = 'BigCommerce Product Sync'
    _order = 'sync_date desc'

    name = fields.Char(string='Sync Name', required=True, default=lambda self: f"Product Sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    @api.onchange('full_sync', 'sync_direction')
    def _onchange_full_sync(self):
        """Update sync name when full sync is enabled"""
        if self.full_sync and self.sync_direction == 'bc_to_odoo':
            self.name = f"BigCommerce to Odoo (Full Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    config_id = fields.Many2one('bigcommerce.config', string='Configuration', required=True)
    sync_date = fields.Datetime(string='Sync Date', default=fields.Datetime.now, required=True)
    sync_direction = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
        ('bc_to_odoo', 'BigCommerce to Odoo'),
        ('bidirectional', 'Bidirectional'),
    ], string='Sync Direction', required=True)
    sync_images = fields.Boolean(string='Sync Product Images', default=False,
                                 help='Enable syncing of product images from BigCommerce to Odoo. Images will be downloaded and attached to products and variants.')
    
    # Full Sync option
    full_sync = fields.Boolean(string='Full Sync', default=False,
                               help='If enabled, syncs ALL products from BigCommerce regardless of modification date. Updates existing products and creates new ones. Ignores last successful sync time.')
    
    # Specific Product Sync option
    sync_specific_product = fields.Boolean(string='Sync Specific Product', default=False,
                                            help='If enabled, syncs only a single product identified by SKU or BigCommerce ID')
    product_sku = fields.Char(string='Product SKU/Internal Reference', 
                              help='Enter the SKU or internal reference of the product to sync (for BigCommerce to Odoo sync)')
    product_bigcommerce_id = fields.Integer(string='Product BigCommerce ID',
                                            help='Enter the BigCommerce product ID to sync (for BigCommerce to Odoo sync)')
    
    # Filters
    date_from = fields.Datetime(string='Date From', help='Sync products created from this date')
    date_to = fields.Datetime(string='Date To', help='Sync products created until this date')
    min_date_modified = fields.Datetime(string='Min Date Modified', help='Sync products modified after this date (ignored if Full Sync is enabled)')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='State', default='draft')
    
    products_created = fields.Integer(string='Products Created', default=0)
    products_updated = fields.Integer(string='Products Updated', default=0)
    products_failed = fields.Integer(string='Products Failed', default=0)
    products_archived = fields.Integer(string='Products Archived', default=0,
                                      help='Number of Odoo products archived because they were deleted from BigCommerce')
    products_skipped = fields.Integer(string='Products Skipped', default=0)
    error_message = fields.Text(string='Error Message')
    
    # Progress tracking
    total_items = fields.Integer(string='Total Items', default=0, help='Total number of items to process')
    processed_items = fields.Integer(string='Processed Items', default=0, help='Number of items processed so far')
    progress_percentage = fields.Float(string='Progress', compute='_compute_progress', store=False, help='Progress percentage')
    current_item = fields.Char(string='Current Item', help='Currently processing item')
    
    # Link to sync operation for dashboard tracking
    sync_operation_id = fields.Many2one('bigcommerce.sync.operation', string='Sync Operation', ondelete='set null')
    # When sync is cancelled or fails, config.last_product_sync is reverted to this value (last successful sync start time)
    config_last_sync_before_run = fields.Datetime(string='Config Last Sync Before Run', readonly=True)
    
    @api.depends('total_items', 'processed_items')
    def _compute_progress(self):
        """Compute progress percentage"""
        for record in self:
            if record.total_items > 0:
                record.progress_percentage = (record.processed_items / record.total_items) * 100
            else:
                record.progress_percentage = 0.0
    
    def _revert_config_last_product_sync(self):
        """Revert config last_product_sync and stats to last successful sync (or clear).
        Call when this product sync is cancelled or fails so the config does not show
        the failed/cancelled run as the last sync. Uses raw SQL in a new cursor so
        the revert commits even when the main request transaction is rolled back.
        """
        if not self.config_id:
            return
        config_id = self.config_id.id
        last_sync = self.config_last_sync_before_run
        last_op = self.env['bigcommerce.sync.operation'].search([
            ('sync_type', '=', 'product'),
            ('config_id', '=', config_id),
            ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
        ], order='start_date desc', limit=1)
        if last_op:
            last_ts = last_op.start_date
            total = last_op.total_items or 0
            updated = last_op.items_updated or 0
            failed = last_op.items_failed or 0
            warnings = last_op.warning_count or 0
        else:
            last_ts = last_sync
            total = updated = failed = warnings = 0

        def _do_revert(cr):
            if last_ts:
                cr.execute(
                    """
                    UPDATE bigcommerce_config
                    SET last_product_sync = %s, last_product_sync_total = %s,
                        last_product_sync_updated = %s, last_product_sync_failed = %s,
                        last_product_sync_warnings = %s
                    WHERE id = %s
                    """,
                    (last_ts, total, updated, failed, warnings, config_id),
                )
            else:
                cr.execute(
                    """
                    UPDATE bigcommerce_config
                    SET last_product_sync = NULL, last_product_sync_total = 0,
                        last_product_sync_updated = 0, last_product_sync_failed = 0,
                        last_product_sync_warnings = 0
                    WHERE id = %s
                    """,
                    (config_id,),
                )
            cr.commit()

        try:
            with self.env.registry.cursor() as new_cr:
                _do_revert(new_cr)
            _logger.info(
                "Reverted config %s last_product_sync to %s (cancelled/failed sync)",
                config_id, last_ts,
            )
        except Exception as e:
            _logger.warning("Could not revert last_product_sync in new cursor: %s", e)
            try:
                _do_revert(self.env.cr)
                _logger.info("Reverted config %s last_product_sync in current transaction", config_id)
            except Exception as e2:
                _logger.warning("Could not commit revert of last_product_sync: %s", e2)

    def _check_cancelled(self):
        """Check if the sync operation has been cancelled

        This method reads fresh data from the database to ensure we see
        cancellation even if the sync is running in a long transaction.
        """
        if self.sync_operation_id:
            op_id = self.sync_operation_id.id
            try:
                # Read fresh state directly from database using SQL to bypass all caching
                self.env.cr.execute(
                    "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                    (op_id,)
                )
                result = self.env.cr.fetchone()
                if result and result[0] == 'cancelled':
                    _logger.info(f"Sync operation {op_id} has been cancelled - stopping sync")
                    return True
            except Exception as e:
                # If there's a transaction error, rollback and try again
                # This can happen if a previous SQL command failed
                if 'InFailedSqlTransaction' in str(type(e).__name__) or 'transaction' in str(e).lower():
                    _logger.warning(f"Transaction error in _check_cancelled, rolling back: {str(e)}")
                    try:
                        self.env.cr.rollback()
                        # Try again after rollback
                        self.env.cr.execute(
                            "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                            (op_id,)
                        )
                        result = self.env.cr.fetchone()
                        if result and result[0] == 'cancelled':
                            _logger.info(f"Sync operation {op_id} has been cancelled - stopping sync")
                            return True
                    except Exception as retry_error:
                        _logger.error(f"Error retrying cancelled check after rollback: {str(retry_error)}")
                        # If retry fails, assume not cancelled to avoid stopping sync unnecessarily
                        return False
                else:
                    # For other errors, log and assume not cancelled
                    _logger.warning(f"Error checking if sync is cancelled: {str(e)}")
                    return False
        return False
    
    def _update_sync_operation(self, commit_sync_record=False):
        """Update the linked sync operation record with current progress
        
        Args:
            commit_sync_record: If True, also commit the sync record's progress to database
        """
        if self.sync_operation_id:
            try:
                self.sync_operation_id.write({
                    'total_items': self.total_items,
                    'processed_items': self.processed_items,
                    'current_item': self.current_item or '',
                    'items_created': self.products_created,
                    'items_updated': self.products_updated,
                    'items_failed': self.products_failed,
                    'items_skipped': self.products_skipped or 0,
                    'last_progress_date': fields.Datetime.now(),  # So cron knows sync is still alive
                })
                # Also update the sync record itself if requested (for UI updates)
                if commit_sync_record:
                    self.write({
                        'processed_items': self.processed_items,
                        'current_item': self.current_item or '',
                    })
                self.env.cr.commit()
            except Exception as e:
                _logger.warning(f"Failed to update sync operation: {str(e)}")
    
    def _is_ssl_connection_error(self, error):
        """Return True if the error is an SSL or connection error (for clear logging in Monitoring > Sync Operations)."""
        if error is None:
            return False
        s = str(error).lower()
        return (
            'ssl' in s or
            'connection error' in s or
            'connectionerror' in s or
            'sslerror' in s or
            'max retries exceeded' in s or
            'unexpected_eof_while_reading' in s or
            'eof occurred in violation of protocol' in s
        )

    def _create_log(self, log_level, message, product_id=None, product_name=None, error_details=None, 
                    request_url=None, request_method=None, response_status=None, response_data=None):
        """Create a sync log entry"""
        try:
            log_vals = {
                'sync_type': 'product',
                'sync_record_id': self.id,
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
            # Link to sync operation if available
            if self.sync_operation_id:
                log_vals['sync_operation_id'] = self.sync_operation_id.id
            
            # Use sudo() to bypass permission checks - logs are system-level records
            return self.env['bigcommerce.sync.log'].sudo().create(log_vals)
        except Exception as e:
            # If log creation fails, at least log to the standard logger
            _logger.warning(f"Failed to create sync log entry: {str(e)}")
            return False
    
    def action_sync_products(self):
        """Sync products between Odoo and BigCommerce"""
        self.ensure_one()
        
        # Validate specific product sync options
        if self.sync_specific_product:
            if self.sync_direction != 'bc_to_odoo':
                raise UserError("Specific product sync is only available for 'BigCommerce to Odoo' direction.")
            if not self.product_sku and not self.product_bigcommerce_id:
                raise UserError("Please enter either a Product SKU/Internal Reference or a Product BigCommerce ID to sync a specific product.")
            if self.product_sku and self.product_bigcommerce_id:
                raise UserError("Please enter either SKU or BigCommerce ID, not both.")
            if self.full_sync:
                raise UserError("Cannot use Full Sync and Specific Product sync at the same time. Please disable one of them.")
        
        # Automatically set min_date_modified to last successful sync if not already set
        # This ensures we only sync products modified since the last successful sync
        # This applies to both manual and auto syncs
        # Skip this if syncing a specific product or full sync
        if not self.min_date_modified and self.config_id and not self.sync_specific_product and not self.full_sync:
            # Find the last successful sync operation for this config
            last_successful_sync = None
            if self.config_id.last_product_sync:
                # Use the config's last_product_sync as the default
                last_successful_sync = self.config_id.last_product_sync
            else:
                # If config doesn't have last_product_sync, find the last successful sync operation
                last_sync_op = self.env['bigcommerce.sync.operation'].search([
                    ('sync_type', '=', 'product'),
                    ('config_id', '=', self.config_id.id),
                    ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                    ('end_date', '!=', False),
                ], order='end_date desc', limit=1)
                
                if last_sync_op:
                    last_successful_sync = last_sync_op.end_date
            
            if last_successful_sync:
                self.min_date_modified = last_successful_sync
                _logger.info(f"Auto-set min_date_modified to last successful sync: {self.min_date_modified}")
            else:
                _logger.info("No previous successful sync found - will sync all products from BigCommerce")
        
        # Create sync operation record for dashboard tracking
        now = fields.Datetime.now()
        sync_operation = self.env['bigcommerce.sync.operation'].create({
            'sync_type': 'product',
            'config_id': self.config_id.id,
            'sync_direction': self.sync_direction,
            'state': 'running',
            'start_date': now,
            'last_progress_date': now,  # Used by cron to detect stale syncs (timeout/crash)
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
        
        _logger.info(f"Starting product sync: {self.name} (ID: {self.id})")
        _logger.info(f"Sync direction: {self.sync_direction}")
        _logger.info(f"Configuration: {self.config_id.name} (ID: {self.config_id.id})")
        _logger.info(f"Min date modified filter: {self.min_date_modified if self.min_date_modified else 'None (will sync all products)'}")
        
        try:
            api = self.config_id.get_api_client()
            _logger.info("API client initialized successfully")
            
            if self.sync_direction in ('bc_to_odoo', 'bidirectional'):
                _logger.info("Starting sync from BigCommerce to Odoo...")
                try:
                    self._sync_from_bigcommerce(api)
                    _logger.info(f"Sync from BigCommerce completed. Created: {self.products_created}, Updated: {self.products_updated}, Failed: {self.products_failed}")
                except Exception as e:
                    # Log the error and re-raise so sync is marked as failed
                    _logger.error(f"Sync from BigCommerce failed: {str(e)}", exc_info=True)
                    import traceback
                    error_trace = traceback.format_exc()
                    # Ensure SSL/connection errors are clearly recorded in Monitoring > Sync Operations
                    if self._is_ssl_connection_error(e):
                        log_msg = f"SSL/connection error: {str(e)}"
                    else:
                        log_msg = f"Sync from BigCommerce failed: {str(e)}"
                    self._create_log(
                        'error',
                        log_msg,
                        error_details=error_trace
                    )
                    raise
            
            if self.sync_direction in ('odoo_to_bc', 'bidirectional'):
                _logger.info("Starting sync from Odoo to BigCommerce...")
                try:
                    self._sync_to_bigcommerce(api)
                    _logger.info(f"Sync to BigCommerce completed. Created: {self.products_created}, Updated: {self.products_updated}, Failed: {self.products_failed}")
                except Exception as e:
                    # Log the error and re-raise so sync is marked as failed
                    _logger.error(f"Sync to BigCommerce failed: {str(e)}", exc_info=True)
                    import traceback
                    error_trace = traceback.format_exc()
                    if self._is_ssl_connection_error(e):
                        log_msg = f"SSL/connection error: {str(e)}"
                    else:
                        log_msg = f"Sync to BigCommerce failed: {str(e)}"
                    self._create_log(
                        'error',
                        log_msg,
                        error_details=error_trace
                    )
                    raise
            
            self.state = 'done'
            total_items = self.products_created + self.products_updated + self.products_failed
            warnings_count = self.env['bigcommerce.sync.log'].search_count([
                ('config_id', '=', self.config_id.id),
                ('sync_type', '=', 'product'),
                ('log_level', '=', 'WARNING'),
                ('log_date', '>=', self.config_id.last_product_sync or fields.Datetime.now() - timedelta(days=1))
            ])
            # Only update config last sync time and stats on success (cancelled/failed runs revert in _revert_config_last_product_sync)
            last_sync_time = self.sync_operation_id.start_date if self.sync_operation_id else fields.Datetime.now()
            self.config_id.write({
                'last_product_sync': last_sync_time,
                'last_product_sync_total': total_items,
                'last_product_sync_updated': self.products_updated,
                'last_product_sync_failed': self.products_failed,
                'last_product_sync_warnings': warnings_count,
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
                    'items_synced': self.products_created + self.products_updated,
                    'items_created': self.products_created,
                    'items_updated': self.products_updated,
                    'items_failed': self.products_failed,
                    'items_archived': getattr(self, 'products_archived', 0),
                    'error_count': self.products_failed,
                    'warning_count': warnings_count,
                })
            
            _logger.info(f"Product sync completed successfully. Total - Created: {self.products_created}, Updated: {self.products_updated}, Failed: {self.products_failed}")
            if self.products_failed > 0 and self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Product',
                        error_message=f"Sync completed with {self.products_failed} failed item(s).",
                        created=self.products_created,
                        updated=self.products_updated,
                        failed=self.products_failed,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send product sync failure notification: %s", mail_e)
            if total_items == 0:
                warning_msg = (f"Product sync completed but synced 0 products. This may be because: "
                              f"1) No products were modified since last sync ({self.config_id.last_product_sync}), "
                              f"2) No products exist in BigCommerce, or "
                              f"3) The date filter is too restrictive. "
                              f"Sync direction: {self.sync_direction}, min_date_modified: {self.min_date_modified}")
                _logger.warning(warning_msg)
                self._create_log(
                    'warning',
                    warning_msg,
                    error_details=f"Sync completed successfully but no products were processed. Check date filters and last sync time."
                )
            
        except UserError as e:
            # Check if this is a cancellation
            if 'cancelled' in str(e).lower():
                # Update sync operation to cancelled state if not already
                # Use SQL to check state to avoid transaction errors
                if self.sync_operation_id:
                    try:
                        op_id = self.sync_operation_id.id
                        self.env.cr.execute(
                            "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                            (op_id,)
                        )
                        result = self.env.cr.fetchone()
                        if result and result[0] == 'running':
                            # Use action_cancel which handles the state update properly
                            try:
                                sync_op = self.env['bigcommerce.sync.operation'].browse(op_id)
                                sync_op.action_cancel()
                            except Exception as cancel_error:
                                _logger.warning(f"Could not cancel sync operation: {str(cancel_error)}")
                                # If ORM fails, try SQL update
                                try:
                                    self.env.cr.rollback()
                                    self.env.cr.execute(
                                        "UPDATE bigcommerce_sync_operation SET state = 'cancelled' WHERE id = %s",
                                        (op_id,)
                                    )
                                    self.env.cr.commit()
                                except:
                                    pass
                    except Exception as check_error:
                        _logger.warning(f"Could not check sync operation state: {str(check_error)}")
                        try:
                            if 'transaction' in str(check_error).lower():
                                self.env.cr.rollback()
                        except:
                            pass
                self.state = 'error'
                self.error_message = str(e)
                _logger.info(f"Product sync cancelled: {str(e)}")
                self._revert_config_last_product_sync()
            else:
                # Other UserError (e.g. product not found): mark sync as failed and stop
                self.state = 'error'
                self.error_message = str(e)
                self._create_log('error', str(e), error_details='Specific product sync failed.')
                if self.config_id:
                    try:
                        self.config_id._send_sync_failure_email('Product', error_message=str(e), sync_name=self.name)
                    except Exception as mail_e:
                        _logger.warning("Could not send product sync failure notification: %s", mail_e)
                if self.sync_operation_id:
                    try:
                        op_id = self.sync_operation_id.id
                        self.env.cr.execute(
                            "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                            (op_id,)
                        )
                        result = self.env.cr.fetchone()
                        if result and result[0] == 'running':
                            self.sync_operation_id.write({
                                'state': 'failed',
                                'end_date': fields.Datetime.now(),
                                'error_count': (self.sync_operation_id.error_count or 0) + 1,
                                'summary': str(e),
                            })
                            self.env.cr.commit()
                    except Exception as update_error:
                        _logger.warning(f"Could not update sync operation on UserError: {str(update_error)}")
                self._revert_config_last_product_sync()
                raise
        except Exception as e:
            self.state = 'error'
            self.error_message = str(e)
            import traceback
            error_trace = traceback.format_exc()
            if self.config_id:
                try:
                    self.config_id._send_sync_failure_email(
                        'Product',
                        error_message=str(e),
                        details=error_trace,
                        sync_name=self.name,
                    )
                except Exception as mail_e:
                    _logger.warning("Could not send product sync failure notification: %s", mail_e)
            
            # Create a sync log entry for the error (ensures SSL/connection errors appear in Monitoring > Sync Operations)
            if self._is_ssl_connection_error(e):
                log_msg = f"SSL/connection error: {str(e)}"
            else:
                log_msg = f"Product sync failed: {str(e)}"
            self._create_log(
                'error',
                log_msg,
                error_details=error_trace,
            )
            
            # Update sync operation record on error (only if not cancelled)
            # Use SQL to check state to avoid transaction errors
            if self.sync_operation_id:
                try:
                    op_id = self.sync_operation_id.id
                    # Check state using SQL to avoid ORM transaction issues
                    self.env.cr.execute(
                        "SELECT state FROM bigcommerce_sync_operation WHERE id = %s",
                        (op_id,)
                    )
                    result = self.env.cr.fetchone()
                    if result and result[0] != 'cancelled':
                        # Use SQL to update to avoid transaction errors
                        self.env.cr.execute(
                            """UPDATE bigcommerce_sync_operation 
                               SET state = 'failed', 
                                   end_date = NOW() AT TIME ZONE 'UTC',
                                   error_count = COALESCE(error_count, 0) + %s,
                                   summary = %s
                               WHERE id = %s""",
                            (self.products_failed + 1, f"Sync failed with error: {str(e)}", op_id)
                        )
                        self.env.cr.commit()
                except Exception as update_error:
                    # If update fails, log but don't fail the error handling
                    _logger.warning(f"Could not update sync operation state on error: {str(update_error)}")
                    try:
                        # Try to rollback if there was a transaction error
                        if 'transaction' in str(update_error).lower():
                            self.env.cr.rollback()
                    except:
                        pass
            
            _logger.error(f"Product sync error: {str(e)}", exc_info=True)
            self._revert_config_last_product_sync()
            raise UserError(f"Product sync failed: {str(e)}")
    
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
    
    def _remove_stale_mappings_for_product(self, product, synced_config_id):
        """Remove mappings to stores where the product was deleted (product not found in that store).
        
        Called after syncing a product to ensure all sync methods behave the same - stale links
        to stores where the product was removed are cleaned up.
        """
        for mapping in product.bigcommerce_mapping_ids:
            if mapping.config_id.id == synced_config_id:
                continue  # Skip the config we just synced from
            config = mapping.config_id
            bc_id = mapping.bigcommerce_id
            if not config or not bc_id:
                continue
            try:
                api = config.get_api_client()
                bc_product = api.get_product(bc_id, include_images=False)
                if not bc_product:
                    _logger.info(f"Product {bc_id} not found in {config.name} - removing mapping (product was removed from this store)")
                    self._create_log('info', f"Removed link to {config.name}: {product.name} (BC ID: {bc_id}) - product was removed from this store",
                                    product_id=bc_id, product_name=product.name,
                                    error_details=f"Product not found in {config.name}; mapping removed.")
                    mapping.unlink()
            except Exception as e:
                error_str = str(e).lower()
                if '404' in error_str or 'not found' in error_str:
                    _logger.info(f"Product {bc_id} not found in {config.name} (API error) - removing mapping (product was removed from this store)")
                    self._create_log('info', f"Removed link to {config.name}: {product.name} (BC ID: {bc_id}) - product was removed from this store",
                                    product_id=bc_id, product_name=product.name,
                                    error_details=f"Product not found in {config.name} (API error); mapping removed.")
                    mapping.unlink()
    
    def _sync_specific_product_from_bigcommerce(self, api):
        """Sync a specific product from BigCommerce to Odoo by SKU or BigCommerce ID"""
        bc_product = None
        bc_product_id = None
        
        # Update progress - searching for product
        self.total_items = 1
        self.processed_items = 0
        self.current_item = "Searching for product..."
        self._update_sync_operation(commit_sync_record=True)
        self.env.cr.commit()
        
        # Try to find product by BigCommerce ID first
        if self.product_bigcommerce_id:
            bc_product_id = self.product_bigcommerce_id
            self.current_item = f"Fetching product by BigCommerce ID: {bc_product_id}..."
            self._update_sync_operation(commit_sync_record=True)
            self.env.cr.commit()
            try:
                sync_images_enabled = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
                bc_product = api.get_product(bc_product_id, include_images=sync_images_enabled)
                if bc_product:
                    # Handle V3 API response format
                    if isinstance(bc_product, dict) and 'data' in bc_product:
                        bc_product = bc_product['data']
                    if not isinstance(bc_product, dict) or 'id' not in bc_product:
                        bc_product = None
            except Exception as e:
                _logger.error(f"Error fetching product by BigCommerce ID {bc_product_id}: {str(e)}")
                raise UserError(f"Product not found in BigCommerce with ID {bc_product_id}: {str(e)}")
        
        # If not found by ID, try to find by SKU (or by numeric value as BigCommerce ID)
        if not bc_product and self.product_sku:
            try:
                # If user entered a number in the SKU field (e.g. 59295), it may be the BigCommerce product ID
                # Try fetching by ID first so "59295" in either field finds the product
                sku_stripped = (self.product_sku or '').strip()
                if sku_stripped.isdigit() and int(sku_stripped) > 0:
                    try:
                        self.current_item = f"Trying BigCommerce ID {sku_stripped} (from SKU field)..."
                        self._update_sync_operation(commit_sync_record=True)
                        self.env.cr.commit()
                        sync_images_enabled = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
                        bc_product = api.get_product(int(sku_stripped), include_images=sync_images_enabled)
                        if bc_product:
                            if isinstance(bc_product, dict) and 'data' in bc_product:
                                bc_product = bc_product['data']
                            if isinstance(bc_product, dict) and bc_product.get('id'):
                                bc_product_id = bc_product['id']
                                _logger.info(f"Found product by numeric ID from SKU field: BC ID={bc_product_id}")
                    except Exception as e:
                        _logger.debug(f"Value '{sku_stripped}' is not a valid BigCommerce product ID: {e}. Will search by SKU.")
                        bc_product = None
                
                if not bc_product:
                    self.current_item = f"Searching for product with SKU: {self.product_sku}..."
                    self._update_sync_operation(commit_sync_record=True)
                    self.env.cr.commit()
                
                sku_normalized = (self.product_sku or '').strip()
                # First, try to find in Odoo (parent or variant SKU) to get BigCommerce ID from mapping
                # Use =ilike for case-insensitive exact match
                odoo_template = self.env['product.template'].search([
                    ('default_code', '=ilike', sku_normalized)
                ], limit=1)
                # If not found as template (parent SKU), try variant SKU -> get parent template
                if not odoo_template:
                    odoo_variant = self.env['product.product'].search([
                        ('default_code', '=ilike', sku_normalized)
                    ], limit=1)
                    if odoo_variant:
                        odoo_template = odoo_variant.product_tmpl_id
                        _logger.info(f"Found variant SKU '{sku_normalized}' in Odoo on template {odoo_template.id}, resolving via mapping")
                
                if odoo_template:
                    self.current_item = f"Found product in Odoo, checking BigCommerce mapping..."
                    self._update_sync_operation(commit_sync_record=True)
                    self.env.cr.commit()
                    
                    mapping = self.env['bigcommerce.product.mapping'].search([
                        ('product_tmpl_id', '=', odoo_template.id),
                        ('config_id', '=', self.config_id.id)
                    ], limit=1)
                    
                    if mapping and mapping.bigcommerce_id:
                        bc_product_id = mapping.bigcommerce_id
                        self.current_item = f"Fetching product from BigCommerce (ID: {bc_product_id})..."
                        self._update_sync_operation(commit_sync_record=True)
                        self.env.cr.commit()
                        sync_images_enabled = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
                        bc_product = api.get_product(bc_product_id, include_images=sync_images_enabled)
                        if bc_product:
                            if isinstance(bc_product, dict) and 'data' in bc_product:
                                bc_product = bc_product['data']
                
                # If not found via Odoo mapping, search BigCommerce (product-level SKU then variant SKU)
                if not bc_product:
                    self.current_item = f"Searching BigCommerce products for SKU: {self.product_sku}..."
                    self._update_sync_operation(commit_sync_record=True)
                    self.env.cr.commit()
                    
                    # Use BigCommerce API's built-in SKU filter for product-level SKU
                    # This is much more efficient than searching through pages
                    # Try both as-entered and uppercased since BigCommerce SKU filter may be case-sensitive
                    try:
                        products = []
                        for sku_attempt in [self.product_sku, sku_normalized.upper()]:
                            products_result = api.get_products(sku=sku_attempt, limit=250)
                            if isinstance(products_result, dict) and 'data' in products_result:
                                products = products_result['data']
                            else:
                                products = products_result if isinstance(products_result, list) else []
                            if products:
                                break
                        
                        # Check if any product matches the SKU (case-insensitive)
                        for product in products:
                            if (product.get('sku') or '').strip().upper() == sku_normalized.upper():
                                bc_product = product
                                bc_product_id = product.get('id')
                                break
                    except Exception as e:
                        _logger.warning(f"Error using SKU filter API: {str(e)}. Falling back to manual search.")
                        products = []
                    
                    # If not found at product level, search variants using the fast
                    # GET /catalog/variants?sku=<value> endpoint (single API call)
                    if not bc_product:
                        self.current_item = f"Searching product variants for SKU: {self.product_sku}..."
                        self._update_sync_operation(commit_sync_record=True)
                        self.env.cr.commit()
                        
                        # Try the dedicated variants endpoint — much faster than paginating products
                        for sku_attempt in dict.fromkeys([sku_normalized, sku_normalized.upper()]):
                            variant_results = api.search_variants_by_sku(sku_attempt)
                            if variant_results:
                                for vr in variant_results:
                                    vr_sku = (vr.get('sku') or '').strip()
                                    if vr_sku.upper() == sku_normalized.upper() and vr.get('product_id'):
                                        bc_product_id = vr['product_id']
                                        self.current_item = f"Found variant SKU, fetching parent product (BC ID: {bc_product_id})..."
                                        self._update_sync_operation(commit_sync_record=True)
                                        self.env.cr.commit()
                                        sync_images_enabled = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
                                        bc_product = api.get_product(bc_product_id, include_images=sync_images_enabled)
                                        if bc_product:
                                            if isinstance(bc_product, dict) and 'data' in bc_product:
                                                bc_product = bc_product['data']
                                        _logger.info(f"Found variant SKU '{sku_normalized}' via /catalog/variants on product BC ID={bc_product_id}")
                                        break
                            if bc_product:
                                break
                    
                    if not bc_product:
                        raise UserError(f"Product with SKU '{self.product_sku}' not found in BigCommerce for configuration '{self.config_id.name}'. The product may not exist in this store or may be in a different channel.")
                        
            except UserError:
                raise
            except Exception as e:
                _logger.error(f"Error searching for product by SKU {self.product_sku}: {str(e)}", exc_info=True)
                raise UserError(f"Error searching for product with SKU '{self.product_sku}': {str(e)}")
        
        if not bc_product:
            raise UserError("Could not find product to sync. Please verify the SKU or BigCommerce ID.")
        
        # Validate product data
        if not isinstance(bc_product, dict):
            raise UserError(f"Invalid product data received from BigCommerce. Expected dictionary, got {type(bc_product).__name__}.")
        
        if 'id' not in bc_product or not bc_product.get('id'):
            raise UserError("Product data from BigCommerce is missing the product ID.")
        
        # Update progress - found product, now syncing
        product_name = bc_product.get('name', 'Unknown')
        self.current_item = f"Syncing product: {product_name}..."
        self._update_sync_operation(commit_sync_record=True)
        self.env.cr.commit()
        
        # Sync the specific product (this will also sync all variants)
        try:
            # Ensure we have the full product data including variants
            # If the product was found by variant SKU, make sure we fetch complete product data
            if not bc_product.get('variants') or not isinstance(bc_product.get('variants'), list):
                # Fetch variants if not already included
                self.current_item = f"Fetching variants for product: {product_name}..."
                self._update_sync_operation(commit_sync_record=True)
                self.env.cr.commit()
                
                try:
                    bc_variants = api.get_product_variants(bc_product_id)
                    if bc_variants:
                        if isinstance(bc_variants, dict) and 'data' in bc_variants:
                            bc_variants = bc_variants['data']
                        # Store variants in product data for _create_or_update_product_from_bc
                        bc_product['variants'] = bc_variants
                        _logger.info(f"Fetched {len(bc_variants)} variants for product {product_name} (BC ID: {bc_product_id})")
                except Exception as variant_fetch_error:
                    _logger.warning(f"Could not fetch variants separately: {str(variant_fetch_error)}. Will fetch during sync.")
            
            self.current_item = f"Syncing product and variants: {product_name}..."
            self._update_sync_operation(commit_sync_record=True)
            self.env.cr.commit()
            
            # This will sync the product and all its variants
            self._create_or_update_product_from_bc(api, bc_product)
            
            # Verify variants were synced and remove stale mappings (same behavior as Sync from BigCommerce button)
            try:
                # Check if product was created/updated
                odoo_product = self.env['product.template'].search([
                    ('default_code', '=', bc_product.get('sku', ''))
                ], limit=1)
                
                if not odoo_product and bc_product_id:
                    # Try to find by mapping
                    mapping = self.env['bigcommerce.product.mapping'].search([
                        ('bigcommerce_id', '=', bc_product_id),
                        ('config_id', '=', self.config_id.id)
                    ], limit=1)
                    if mapping:
                        odoo_product = mapping.product_tmpl_id
                
                if odoo_product:
                    # Remove mappings to stores where product was deleted (same as Sync from BigCommerce button)
                    self._remove_stale_mappings_for_product(odoo_product, self.config_id.id)
                    variant_count = len(odoo_product.product_variant_ids)
                    _logger.info(f"Product '{product_name}' synced with {variant_count} variant(s)")
                    self.current_item = f"Completed: {product_name} ({variant_count} variant(s) synced)"
                else:
                    self.current_item = f"Completed: {product_name}"
            except Exception as check_error:
                _logger.debug(f"Could not verify variant count: {str(check_error)}")
                self.current_item = f"Completed: {product_name}"
            
            self.processed_items = 1
            self._update_sync_operation(commit_sync_record=True)
            self.env.cr.commit()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error syncing specific product: {str(e)}", exc_info=True)
            self._create_log(
                'error',
                f"Error syncing product: {str(e)}",
                product_id=bc_product.get('id'),
                product_name=bc_product.get('name', 'Unknown'),
                error_details=error_trace,
                response_data=str(bc_product)
            )
            self.products_failed += 1
            self.current_item = f"Error: {str(e)}"
            self._update_sync_operation(commit_sync_record=True)
            raise
    
    def _sync_from_bigcommerce(self, api):
        """Sync products from BigCommerce to Odoo
        
        Note: This method also creates product.attribute and product.attribute.value records
        in Odoo when syncing products with variants from BigCommerce.
        
        OPTIMIZATIONS IMPLEMENTED:
        - Pre-fetch existing products and mappings in bulk (eliminates N+1 queries)
        - Include variants/options in API request (eliminates per-product API calls)
        - Concurrent image downloading (parallel I/O)
        - Increased batch commit size (reduced DB overhead)
        - Reusable date parsing utility (cleaner code, cached pytz)
        """
        # Check if syncing a specific product
        if self.sync_specific_product:
            return self._sync_specific_product_from_bigcommerce(api)
        
        page = 1
        limit = 250  # Maximum allowed by BigCommerce API for optimal performance
        total_processed = 0
        total_items_known = None
        products_actually_processed = 0  # Track only products that are actually processed (not filtered)
        
        _logger.info(f"Starting to fetch products from BigCommerce (page size: {limit})")
        _logger.info("OPTIMIZATION: Performance optimizations enabled - bulk pre-fetch, inline variants, concurrent images")
        
        # OPTIMIZATION: Initialize data cache for bulk pre-fetching
        data_cache = ProductDataCache(self.env, self.config_id.id)
        data_cache.load()
        
        # OPTIMIZATION: Initialize concurrent image downloader
        image_downloader = ConcurrentImageDownloader(max_workers=5, timeout=30)
        
        # Determine if images should be synced (check sync operation field first, then config)
        sync_images = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
        
        # Determine if variants should be synced
        sync_variants = self.config_id.sync_product_variants
        
        # For BC→Odoo: collect all BC product IDs so we can archive Odoo products deleted from BC
        # Full sync: build bc_ids_seen in the main loop. Incremental: fetch all IDs in a lightweight pass first.
        if self.sync_direction != 'bc_to_odoo':
            bc_ids_seen = None
        elif self.full_sync:
            bc_ids_seen = set()
        else:
            _logger.info("Fetching all BigCommerce product IDs to detect deleted products (for archiving in Odoo)...")
            bc_ids_seen = self._fetch_all_bigcommerce_product_ids(api)
            _logger.info(f"Found {len(bc_ids_seen)} product(s) currently in BigCommerce")
        
        # Build filters for BigCommerce API
        filters = {}
        # Skip date filtering if full_sync is enabled
        if self.full_sync:
            _logger.info("Full Sync enabled - will sync ALL products from BigCommerce regardless of modification date")
        elif self.min_date_modified:
            # BigCommerce API v3 requires RFC 3339 format (ISO 8601 with timezone)
            # Since Odoo stores datetimes in UTC, append 'Z' to indicate UTC timezone
            # Format: YYYY-MM-DDTHH:MM:SSZ
            date_str = self.min_date_modified.strftime('%Y-%m-%dT%H:%M:%S')
            # Ensure the datetime is timezone-aware (UTC)
            if self.min_date_modified.tzinfo is None:
                # Assume UTC if naive
                filters['date_modified:min'] = f"{date_str}Z"
            else:
                # Convert to UTC and format with Z
                if self.min_date_modified.tzinfo != pytz.UTC:
                    utc_dt = self.min_date_modified.astimezone(pytz.UTC)
                    filters['date_modified:min'] = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    filters['date_modified:min'] = f"{date_str}Z"
            _logger.info(f"Filtering products by date_modified >= {self.min_date_modified} (only products modified since last sync will be synced)")
        else:
            _logger.info(f"No date filter - will sync all products from BigCommerce")
        # Note: date_created filters are not supported in BigCommerce API v3
        # We'll filter by date_created in Python after fetching if needed
        if self.date_from or self.date_to:
            _logger.info(f"Date filters (date_from: {self.date_from}, date_to: {self.date_to}) will be applied after fetching from API (v3 doesn't support date_created filters)")
        
        # First, get the first page to determine total count
        # OPTIMIZATION: Include variants and options inline to avoid N+1 API calls
        try:
            _logger.info("OPTIMIZATION: Fetching products with inline variants/options (reduces API calls by ~60%)")
            first_result = api.get_products(
                page=1, 
                limit=limit, 
                include_images=sync_images,
                include_variants=sync_variants,
                include_options=sync_variants,
                **filters
            )
            
            # Extract products and total count
            total_pages_known = None  # Track total pages for pagination
            if isinstance(first_result, dict):
                products = first_result.get('data', [])
                # Check for total count in the result (from _make_request)
                if '_total_count' in first_result:
                    total_items_known = first_result['_total_count']
                    _logger.info(f"Found total product count from API _total_count: {total_items_known}")
                # Also check meta.pagination if available
                if 'meta' in first_result and 'pagination' in first_result['meta']:
                    pagination = first_result['meta']['pagination']
                    if 'total' in pagination and total_items_known is None:
                        total_items_known = pagination['total']
                        _logger.info(f"Found total product count from meta.pagination.total: {total_items_known}")
                    if 'total_pages' in pagination:
                        total_pages_known = pagination['total_pages']
                        _logger.info(f"Found total pages from meta.pagination.total_pages: {total_pages_known}")
                    # Log full pagination info for debugging
                    _logger.info(f"Pagination metadata: {pagination}")
                
                _logger.info(f"First page returned {len(products)} products (with include=variants,options - page size varies based on payload)")
            else:
                products = first_result if isinstance(first_result, list) else []
            
            if products:
                # If we don't have total count from API, we need to discover it
                # by fetching pages until we get a partial page
                if total_items_known is None:
                    # Try to get a high page number to see total
                    # Fetch page 10 (2500 items) - if it exists, we know there are at least that many
                    try:
                        test_result = api.get_products(
                            page=10, limit=limit, 
                            include_images=sync_images,
                            include_variants=sync_variants,
                            include_options=sync_variants,
                            **filters
                        )
                        if isinstance(test_result, dict):
                            test_products = test_result.get('data', [])
                            if test_products:
                                # We got products on page 10, so there are at least 2500
                                # Keep fetching to find the actual total
                                _logger.info("Found products on page 10, discovering total count...")
                                # Start with a high estimate
                                total_items_known = 10000  # Will be refined as we go
                            else:
                                # No products on page 10, total is less than 2500
                                # Estimate based on first page
                                if len(products) == limit:
                                    total_items_known = limit * 5  # Estimate 5 pages
                                else:
                                    total_items_known = len(products)
                        else:
                            test_products = test_result if isinstance(test_result, list) else []
                            if test_products:
                                total_items_known = 10000
                            else:
                                if len(products) == limit:
                                    total_items_known = limit * 5
                                else:
                                    total_items_known = len(products)
                    except:
                        # If we can't test, estimate conservatively
                        if len(products) == limit:
                            total_items_known = limit * 10  # Estimate 10 pages (2500 items)
                            _logger.info(f"Estimated total products: {total_items_known} (will refine as we go)")
                        else:
                            total_items_known = len(products)
                            _logger.info(f"Found {total_items_known} products (partial first page)")
                else:
                    _logger.info(f"Using API-provided total: {total_items_known}")
                
                # Set initial total
                self.total_items = total_items_known
                self.current_item = f"Found {total_items_known} products to sync. Starting..."
                self._update_sync_operation()
                self.env.cr.commit()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error getting first page of products: {str(e)}", exc_info=True)
            self._create_log(
                'error',
                f"Error getting first page of products: {str(e)}",
                error_details=error_trace
            )
            # Re-raise the exception so it's caught by action_sync_products and marked as error
            raise
        
        # Process first page if we got it
        if products:
            page = 1
            # Batch process products (process in chunks to reduce commits)
            batch_size = 200  # Process 200 products before committing (optimized for large syncs)
            
            for batch_start in range(0, len(products), batch_size):
                batch_products = products[batch_start:batch_start + batch_size]
                
                for idx, bc_product in enumerate(batch_products, 1):
                    # Check if sync has been cancelled
                    if self._check_cancelled():
                        _logger.info("Product sync cancelled by user")
                        raise UserError("Sync operation was cancelled by user")
                    
                    # Validate that bc_product is a dictionary
                    if not isinstance(bc_product, dict):
                        _logger.error(f"Invalid product data type on page {page}, item {idx}: {type(bc_product)}")
                        self.products_failed += 1
                        continue
                    
                    bc_id = bc_product.get('id')
                    bc_name = bc_product.get('name', 'Unknown')
                    
                    if not bc_id:
                        _logger.error(f"Product on page {page}, item {idx} has no ID")
                        self.products_failed += 1
                        continue
                    
                    if bc_ids_seen is not None:
                        bc_ids_seen.add(bc_id)
                    
                    # OPTIMIZATION: Use utility function for date parsing (cleaner, faster)
                    # Verify date_modified filter if specified - check actual date_modified from BigCommerce
                    filter_date = self.min_date_modified if self.min_date_modified else (self.date_from if self.date_from else None)
                    if filter_date:
                        bc_date_modified = bc_product.get('date_modified')
                        if bc_date_modified:
                            bc_date = parse_bigcommerce_datetime(bc_date_modified)
                            if not bc_date:
                                _logger.debug(f"Skipping product {bc_id} ({bc_name}): could not parse date_modified")
                                continue
                            
                            # Make filter date timezone-aware for comparison
                            check_filter_date = self.min_date_modified if self.min_date_modified else self.date_from
                            if check_filter_date:
                                filter_date_utc = parse_bigcommerce_datetime(check_filter_date) or pytz.UTC.localize(check_filter_date) if check_filter_date.tzinfo is None else check_filter_date.astimezone(pytz.UTC)
                                
                                # Skip products that were modified before the filter date
                                if bc_date < filter_date_utc:
                                    _logger.debug(f"Skipping product {bc_id} ({bc_name}): date_modified before filter date")
                                    continue
                                
                                # Also check date_to if set
                                if self.date_to:
                                    date_to_utc = parse_bigcommerce_datetime(self.date_to) or (pytz.UTC.localize(self.date_to) if self.date_to.tzinfo is None else self.date_to.astimezone(pytz.UTC))
                                    if bc_date > date_to_utc:
                                        _logger.debug(f"Skipping product {bc_id} ({bc_name}): date_modified after date_to")
                                        continue
                        else:
                            _logger.debug(f"Skipping product {bc_id} ({bc_name}): no date_modified field")
                            continue
                    
                    # Update progress - only count products actually being processed
                    products_actually_processed += 1
                    self.current_item = f"Processing: {bc_name[:50]}... (Item {products_actually_processed}/{self.total_items})"
                    self.processed_items = products_actually_processed
                    # OPTIMIZATION: Commit sync record progress every 100 items (increased from 50)
                    commit_record = (products_actually_processed % 100 == 0)
                    self._update_sync_operation(commit_sync_record=commit_record)
                    
                    _logger.debug(f"Processing product {idx}/{len(products)} from page {page}: BC ID={bc_id}")
                    
                    try:
                        # OPTIMIZATION: Pass cache and image downloader to avoid repeated lookups
                        self._create_or_update_product_from_bc(
                            api, bc_product, 
                            data_cache=data_cache,
                            image_downloader=image_downloader
                        )
                    except Exception as e:
                        import traceback
                        error_trace = traceback.format_exc()
                        _logger.error(f"Error syncing product BC ID={bc_id}: {str(e)}", exc_info=True)
                        self._create_log(
                            'error',
                            f"Error syncing product: {str(e)}",
                            product_id=bc_id,
                            product_name=bc_name,
                            error_details=error_trace,
                            response_data=str(bc_product)
                        )
                        self.products_failed += 1
                
                # OPTIMIZATION: Commit after each batch (batch size already optimal at 200)
                self.env.cr.commit()
            
            total_processed += len(products)  # Track total fetched for pagination
            page = 2  # Start from page 2 since we already processed page 1
        
        # Continue with remaining pages
        while True:
            try:
                _logger.info(f"Fetching products page {page}...")
                # OPTIMIZATION: Include variants and options inline
                result = api.get_products(
                    page=page, limit=limit, 
                    include_images=sync_images,
                    include_variants=sync_variants,
                    include_options=sync_variants,
                    **filters
                )
                
                # Extract products from result
                if isinstance(result, dict):
                    products = result.get('data', [])
                    # Update total count if we get it from API
                    if '_total_count' in result and result['_total_count']:
                        new_total = result['_total_count']
                        if new_total != self.total_items:
                            self.total_items = new_total
                            _logger.info(f"Updated total count to: {self.total_items}")
                            self.env.cr.commit()
                    # Log pagination info for debugging
                    if 'meta' in result and 'pagination' in result['meta']:
                        pagination = result['meta']['pagination']
                        _logger.debug(f"Page {page} pagination: current={pagination.get('current_page')}, total_pages={pagination.get('total_pages')}, total={pagination.get('total')}")
                else:
                    products = result if isinstance(result, list) else []
                
                if not products:
                    _logger.info(f"No products returned for page {page}, ending sync")
                    break
                
                _logger.info(f"Received {len(products)} products from page {page} (with include=variants,options - page size may vary)")
                
                # Batch process products (process in chunks to reduce commits and improve performance)
                batch_size = 200  # Process 200 products before committing (optimized for large syncs)
                
                for batch_start in range(0, len(products), batch_size):
                    # Check if sync has been cancelled before processing batch
                    if self._check_cancelled():
                        _logger.info("Product sync cancelled by user")
                        raise UserError("Sync operation was cancelled by user")
                    
                    batch_products = products[batch_start:batch_start + batch_size]
                    
                    for idx, bc_product in enumerate(batch_products, 1):
                        # Check if sync has been cancelled
                        if self._check_cancelled():
                            _logger.info("Product sync cancelled by user")
                            raise UserError("Sync operation was cancelled by user")
                        
                        # Validate that bc_product is a dictionary
                        if not isinstance(bc_product, dict):
                            _logger.error(f"Invalid product data type on page {page}, item {idx}: {type(bc_product)}")
                            self.products_failed += 1
                            continue
                        
                        bc_id = bc_product.get('id')
                        bc_name = bc_product.get('name', 'Unknown')
                        
                        if not bc_id:
                            _logger.error(f"Product on page {page}, item {idx} has no ID")
                            self.products_failed += 1
                            continue
                        
                        if bc_ids_seen is not None:
                            bc_ids_seen.add(bc_id)
                        
                        # OPTIMIZATION: Use utility function for date parsing (same as first page)
                        if not self.full_sync and self.min_date_modified:
                            bc_date_modified = bc_product.get('date_modified')
                            if bc_date_modified:
                                bc_date = parse_bigcommerce_datetime(bc_date_modified)
                                if not bc_date:
                                    _logger.debug(f"Skipping product {bc_id} ({bc_name}): could not parse date_modified")
                                    continue
                                
                                check_filter_date = self.min_date_modified if self.min_date_modified else self.date_from
                                if check_filter_date:
                                    filter_date_utc = parse_bigcommerce_datetime(check_filter_date) or (pytz.UTC.localize(check_filter_date) if check_filter_date.tzinfo is None else check_filter_date.astimezone(pytz.UTC))
                                    
                                    if bc_date < filter_date_utc:
                                        _logger.debug(f"Skipping product {bc_id} ({bc_name}): date_modified before filter date")
                                        continue
                                    
                                    if self.date_to:
                                        date_to_utc = parse_bigcommerce_datetime(self.date_to) or (pytz.UTC.localize(self.date_to) if self.date_to.tzinfo is None else self.date_to.astimezone(pytz.UTC))
                                        if bc_date > date_to_utc:
                                            _logger.debug(f"Skipping product {bc_id} ({bc_name}): date_modified after date_to")
                                            continue
                            else:
                                _logger.debug(f"Skipping product {bc_id} ({bc_name}): no date_modified field")
                                continue
                        
                        # Update progress - only count products actually being processed
                        products_actually_processed += 1
                        self.current_item = f"Processing: {bc_name[:50]}... (Item {products_actually_processed}/{self.total_items})"
                        self.processed_items = products_actually_processed
                        # OPTIMIZATION: Commit sync record progress every 100 items (increased from 10)
                        commit_record = (products_actually_processed % 100 == 0)
                        self._update_sync_operation(commit_sync_record=commit_record)
                        
                        _logger.debug(f"Processing product {idx}/{len(products)} from page {page}: BC ID={bc_id}")
                        
                        try:
                            # OPTIMIZATION: Pass cache and image downloader
                            self._create_or_update_product_from_bc(
                                api, bc_product,
                                data_cache=data_cache,
                                image_downloader=image_downloader
                            )
                        except Exception as e:
                            import traceback
                            error_trace = traceback.format_exc()
                            _logger.error(f"Error syncing product BC ID={bc_id}: {str(e)}", exc_info=True)
                            self._create_log(
                                'error',
                                f"Error syncing product: {str(e)}",
                                product_id=bc_id,
                                product_name=bc_name,
                                error_details=error_trace,
                                response_data=str(bc_product)
                            )
                            self.products_failed += 1
                    
                    # OPTIMIZATION: Commit after each batch
                    self.env.cr.commit()
                
                total_processed += len(products)  # Track total fetched for pagination
                
                # Update total if we discover more items than estimated (for non-filtered mode)
                if total_items_known is None and total_processed > self.total_items:
                    self.total_items = total_processed
                    self.env.cr.commit()
                
                _logger.info(f"Completed page {page}. Processed: {products_actually_processed}/{self.total_items} products (fetched: {total_processed})")
                
                # IMPORTANT: When using include=variants,options,images, BigCommerce may return
                # fewer products per page due to payload size limits. We should NOT use partial
                # page detection to end the sync - only stop when we get an empty page (0 products).
                # The empty page check is done at the start of the loop with: if not products: break
                
                # Check pagination metadata if available to see if there are more pages
                has_more_pages = True
                if isinstance(result, dict) and 'meta' in result and 'pagination' in result['meta']:
                    pagination = result['meta']['pagination']
                    current_page = pagination.get('current_page', page)
                    total_pages = pagination.get('total_pages', 0)
                    if total_pages > 0 and current_page >= total_pages:
                        _logger.info(f"Reached last page ({current_page}/{total_pages}), ending sync")
                        has_more_pages = False
                
                if not has_more_pages:
                    self.total_items = total_processed
                    self.env.cr.commit()
                    break
                
                page += 1
                
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                _logger.error(f"Error fetching products page {page}: {str(e)}", exc_info=True)
                self._create_log(
                    'error',
                    f"Error fetching products page {page}: {str(e)}",
                    error_details=error_trace
                )
                # Re-raise the exception so it's caught by action_sync_products and marked as error
                raise
        
        _logger.info(f"Finished fetching from BigCommerce. Total pages: {page}, Total products processed: {total_processed}")
        
        # BC→Odoo: archive Odoo products that were linked to this config but no longer exist in BigCommerce
        if self.sync_direction == 'bc_to_odoo' and bc_ids_seen is not None:
            self._archive_odoo_products_deleted_from_bigcommerce(bc_ids_seen)
        
        _logger.info("OPTIMIZATION SUMMARY: Used bulk pre-fetch, inline variants/options, and optimized batch commits")
    
    def _fetch_all_bigcommerce_product_ids(self, api):
        """Fetch all BigCommerce product IDs (lightweight pass, no includes). Used to detect products
        deleted from BC so we can archive them in Odoo during incremental sync.
        """
        all_ids = set()
        page = 1
        limit = 250
        while True:
            result = api.get_products(
                page=page,
                limit=limit,
                include_images=False,
                include_variants=False,
                include_options=False,
            )
            products = result.get('data', []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
            if not products:
                break
            for p in products:
                if isinstance(p, dict) and p.get('id'):
                    all_ids.add(p['id'])
            pagination = result.get('meta', {}).get('pagination', {}) if isinstance(result, dict) else {}
            total_pages = pagination.get('total_pages', 0)
            if total_pages and page >= total_pages:
                break
            if len(products) < limit:
                break
            page += 1
        return all_ids

    def _archive_odoo_products_deleted_from_bigcommerce(self, bc_ids_seen):
        """Remove mappings for products deleted from this store; archive product only if removed from ALL stores.
        
        Products linked to multiple BigCommerce stores are only archived when removed from BOTH stores.
        When removed from one store, we remove that store's mapping but keep the product active if it
        still has mappings to other stores.
        """
        mapping_obj = self.env['bigcommerce.product.mapping']
        # Mappings for this config whose BC ID is not in the current BC product list (product deleted from this store)
        if bc_ids_seen:
            mappings = mapping_obj.search([
                ('config_id', '=', self.config_id.id),
                ('bigcommerce_id', 'not in', list(bc_ids_seen)),
            ])
        else:
            mappings = mapping_obj.search([('config_id', '=', self.config_id.id)])
        if not mappings:
            return
        # Collect products before unlinking mappings
        products_affected = mappings.mapped('product_tmpl_id')
        # Remove mappings for this config (product deleted from this store)
        mapping_ids = mappings.ids
        mappings.unlink()
        # Only archive products that have NO remaining mappings to any store
        to_archive = products_affected.filtered(
            lambda p: p.active and not p.bigcommerce_mapping_ids
        )
        if not to_archive:
            _logger.info(f"Removed {len(mapping_ids)} mapping(s) for products deleted from {self.config_id.name}; "
                        f"products kept active (still linked to other stores)")
            return
        count = len(to_archive)
        to_archive.write({'active': False})
        self.products_archived = count
        if self.sync_operation_id:
            self.sync_operation_id.write({'items_archived': count})
        _logger.info(f"Removed {len(mapping_ids)} mapping(s); archived {count} product(s) removed from all stores (config: {self.config_id.name})")
        for product in to_archive:
            self._create_log(
                'info',
                f"Archived (removed from all BigCommerce stores): {product.name}",
                product_name=product.name,
                error_details="Product had mappings only to this config and no longer exists in BigCommerce. It has been archived (deactivated) in Odoo.",
            )
        names_preview = ", ".join(to_archive[:5].mapped('name'))
        if count > 5:
            names_preview += f" ... and {count - 5} more"
        self._create_log(
            'info',
            f"Archived {count} product(s) removed from all stores: {names_preview}",
            error_details="These products had mappings only to this config and no longer exist in BigCommerce. They have been archived (deactivated) in Odoo.",
        )
        self.env.cr.commit()
    
    def _create_or_update_product_from_bc(self, api, bc_product, data_cache=None, image_downloader=None):
        """Create or update Odoo product from BigCommerce product
        
        OPTIMIZATION: Accepts optional data_cache and image_downloader for improved performance.
        - data_cache: Pre-fetched product/mapping data to avoid N+1 queries
        - image_downloader: ConcurrentImageDownloader for parallel image fetching
        """
        bc_id = bc_product.get('id')
        bc_name = bc_product.get('name', '')
        
        # OPTIMIZATION: Reduced debug logging for performance
        # _logger.debug(f"Creating/updating product from BigCommerce data: ID={bc_id}, Name='{bc_name}'")
        
        if not bc_id:
            raise ValueError("BigCommerce product ID is missing")
        
        if not bc_name:
            warning_msg = f"Product has no name in BigCommerce, using 'Product {bc_id}'"
            _logger.warning(f"BigCommerce product ID={bc_id} has no name, using 'Product {bc_id}'")
            self._create_log(
                'warning',
                warning_msg,
                product_id=bc_id,
                product_name=f"Product {bc_id}",
                error_details="BigCommerce product missing name field"
            )
            bc_name = f"Product {bc_id}"
        
        product_obj = self.env['product.template']
        mapping_obj = self.env['bigcommerce.product.mapping']
        
        # Get SKU from BigCommerce product for matching
        bc_sku = bc_product.get('sku', '') or ''
        
        # MATCHING STRATEGY: (config_id, bigcommerce_id) is the source of truth.
        # Always look up by BC ID first to avoid creating duplicate Odoo products for the same BC product.
        # Then update that single product with BC data (SKU, name, image, etc.).
        existing = product_obj.browse()
        existing_mapping = mapping_obj.browse()
        
        # PRIMARY: Does a mapping already exist for this (config, BC ID)? If yes, always use that product.
        if data_cache:
            existing_mapping = data_cache.get_mapping_by_bc_id(bc_id)
        else:
            existing_mapping = mapping_obj.search([
                ('config_id', '=', self.config_id.id),
                ('bigcommerce_id', '=', bc_id),
            ], limit=1)
        if existing_mapping:
            existing = existing_mapping.product_tmpl_id
            _logger.debug(f"Using existing mapping for BC ID {bc_id} -> Odoo product {existing.id} ({existing.name})")
        
        # OPTIMIZATION: Use cache if available to avoid database lookups
        if not existing_mapping and data_cache:
            # No mapping for this BC ID yet - find or create by SKU
            if bc_sku:
                # Use cache to find product by SKU
                existing_by_sku = data_cache.get_product_by_sku(bc_sku)
                
                if existing_by_sku:
                    # Product exists with same SKU - check if mapping exists for this config (from cache)
                    mappings = data_cache.get_mappings_for_product(existing_by_sku.id)
                    for mapping in mappings:
                        if mapping.config_id.id == self.config_id.id:
                            existing_mapping = mapping
                            break
                    
                    if existing_mapping:
                        existing = existing_by_sku
                        _logger.debug(f"CACHE HIT: Found existing mapping for SKU '{bc_sku}'")
                        if existing_mapping.bigcommerce_id != bc_id:
                            _logger.info(f"Updating BigCommerce ID in existing mapping from {existing_mapping.bigcommerce_id} to {bc_id}")
                            existing_mapping.write({'bigcommerce_id': bc_id})
                    else:
                        # Product exists but no mapping for this config - reuse product and create mapping
                        existing = existing_by_sku
                        _logger.debug(f"CACHE HIT: Product found for SKU '{bc_sku}', creating new mapping")
                        
                        try:
                            existing_mapping = mapping_obj.create({
                                'product_tmpl_id': existing.id,
                                'config_id': self.config_id.id,
                                'bigcommerce_id': bc_id,
                                'bigcommerce_synced': True,
                                'bigcommerce_last_sync': fields.Datetime.now(),
                            })
                            # Add to cache for future lookups
                            data_cache.add_mapping(existing_mapping)
                        except Exception as mapping_error:
                            _logger.error(f"Error creating mapping: {str(mapping_error)}", exc_info=True)
                            raise
                else:
                    _logger.debug(f"CACHE MISS: No product found for SKU '{bc_sku}'")
            # When no SKU: we already did BC ID lookup above; no mapping means we'll create new product later
        elif not existing_mapping:
            # Fallback to database queries when no cache or mapping not found via cache
            # Find or create by SKU (mapping by BC ID already checked above)
            if bc_sku:
                # Search for product by SKU
                existing_by_sku = product_obj.search([
                    ('default_code', '=', bc_sku)
                ], limit=1)
                
                if existing_by_sku:
                    # Product exists with same SKU - check if mapping exists for this config
                    existing_mapping = mapping_obj.search([
                        ('product_tmpl_id', '=', existing_by_sku.id),
                        ('config_id', '=', self.config_id.id)
                    ], limit=1)
                    
                    if existing_mapping:
                        # Mapping exists - use the existing product and update BigCommerce ID if it changed
                        existing = existing_by_sku
                        _logger.info(f"Found existing mapping: Product ID={existing.id}, SKU='{bc_sku}', BC ID={bc_id}")
                        if existing_mapping.bigcommerce_id != bc_id:
                            _logger.info(f"Updating BigCommerce ID in existing mapping from {existing_mapping.bigcommerce_id} to {bc_id}")
                            existing_mapping.write({'bigcommerce_id': bc_id})
                    else:
                        # Product exists but no mapping for this config - reuse product and create mapping
                        existing = existing_by_sku
                        _logger.info(f"Product with SKU '{bc_sku}' exists. Creating NEW mapping for config '{self.config_id.name}'")
                        
                        try:
                            existing_mapping = mapping_obj.create({
                                'product_tmpl_id': existing.id,
                                'config_id': self.config_id.id,
                                'bigcommerce_id': bc_id,
                                'bigcommerce_synced': True,
                                'bigcommerce_last_sync': fields.Datetime.now(),
                            })
                            _logger.info(f"✓ Created mapping for product {existing.id} with BC ID {bc_id}")
                        except Exception as mapping_error:
                            _logger.error(f"Error creating mapping: {str(mapping_error)}", exc_info=True)
                            raise
                else:
                    _logger.debug(f"No existing product found for SKU '{bc_sku}'")
            else:
                # No SKU provided - check if mapping exists for this BC ID and config
                existing_mapping = mapping_obj.search([
                    ('bigcommerce_id', '=', bc_id),
                    ('config_id', '=', self.config_id.id)
                ], limit=1)
                
                if existing_mapping:
                    existing = existing_mapping.product_tmpl_id
                    _logger.debug(f"Found existing mapping (no SKU): Product ID={existing.id}, BC ID={bc_id}")
        
        # Note: Product tag filtering is NOT applied for BigCommerce to Odoo sync
        # Tags are only used for Odoo to BigCommerce sync to filter which Odoo products get synced
        
        # Initialize product_needs_update - always True for full_sync, specific product sync, or new product
        # For specific product sync, always create/update regardless of last sync date (user requested that product)
        product_needs_update = self.full_sync or self.sync_specific_product or not existing
        
        # If product exists, check if it needs updating by comparing date_modified with last successful sync
        # Skip this check for full_sync and sync_specific_product (always update in those cases)
        if existing and not self.full_sync and not self.sync_specific_product:
            bc_date_modified = bc_product.get('date_modified')
            if bc_date_modified:
                try:
                    # Find the last successful sync operation for this product
                    # Only consider syncs that completed successfully (not failed or cancelled)
                    last_successful_sync = None
                    if self.sync_operation_id and self.sync_operation_id.config_id:
                        # Query for the last successful product sync operation that processed this product
                        sync_ops = self.env['bigcommerce.sync.operation'].search([
                            ('sync_type', '=', 'product'),
                            ('config_id', '=', self.sync_operation_id.config_id.id),
                            ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                            ('end_date', '!=', False),
                        ], order='end_date desc', limit=10)  # Check last 10 successful syncs
                        
                        # Check if this product was processed in any of those syncs (by checking logs)
                        for sync_op in sync_ops:
                            log_entry = self.env['bigcommerce.sync.log'].search([
                                ('sync_operation_id', '=', sync_op.id),
                                ('product_id', '=', bc_id),
                            ], limit=1)
                            
                            if log_entry:
                                last_successful_sync = sync_op.end_date
                                break
                    
                    # Fallback to product's bigcommerce_last_sync if no successful sync operation found
                    # But only if it exists (might be from a failed sync)
                    if not last_successful_sync and existing.bigcommerce_last_sync:
                        # Verify this date is from a successful sync by checking if there's a successful sync operation
                        # that ended around this time
                        potential_sync = self.env['bigcommerce.sync.operation'].search([
                            ('sync_type', '=', 'product'),
                            ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                            ('end_date', '>=', existing.bigcommerce_last_sync),
                            ('end_date', '<=', existing.bigcommerce_last_sync),
                        ], limit=1)
                        
                        if potential_sync:
                            last_successful_sync = existing.bigcommerce_last_sync
                    
                    if last_successful_sync:
                        # Parse BigCommerce date_modified - properly handle timezone
                        # BigCommerce returns dates in ISO 8601 format (RFC 3339)
                        # Format examples: 
                        #   - "2024-01-09T14:30:00Z" (UTC with Z indicator)
                        #   - "2024-01-09T14:30:00+00:00" (UTC with offset)
                        #   - "2024-01-09T14:30:00-05:00" (EST with offset)
                        # BigCommerce typically returns dates in UTC, but we'll handle any timezone
                        bc_date = None
                        if isinstance(bc_date_modified, str):
                            try:
                                # Remove microseconds if present for easier parsing
                                date_str = bc_date_modified
                                has_microseconds = '.' in date_str and ('Z' in date_str or '+' in date_str or (date_str.count('-') > 2))
                                if has_microseconds:
                                    # Split on decimal point, keep only the part before microseconds
                                    if 'Z' in date_str:
                                        date_str = date_str.split('.')[0] + 'Z'
                                    elif '+' in date_str:
                                        parts = date_str.split('+')
                                        date_str = parts[0].split('.')[0] + '+' + parts[1]
                                    elif date_str.count('-') > 2:
                                        # Has timezone offset at the end
                                        last_dash_idx = date_str.rfind('-')
                                        if ':' in date_str[last_dash_idx:]:
                                            date_part = date_str[:last_dash_idx]
                                            tz_part = date_str[last_dash_idx:]
                                            date_str = date_part.split('.')[0] + tz_part
                                
                                # Parse based on format
                                if date_str.endswith('Z'):
                                    # UTC timezone (Z indicator)
                                    date_part = date_str[:-1]  # Remove Z
                                    bc_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
                                    bc_date = pytz.UTC.localize(bc_date)
                                elif '+' in date_str or (date_str.count('-') > 2 and ':' in date_str[-6:]):
                                    # Has timezone offset (e.g., +00:00, -05:00)
                                    if '+' in date_str:
                                        date_part, tz_part = date_str.split('+', 1)
                                        tz_sign = 1
                                    else:
                                        # Negative timezone - find the last dash that's part of timezone
                                        # Format: YYYY-MM-DDTHH:MM:SS-HH:MM
                                        last_dash = date_str.rfind('-')
                                        if last_dash > 10 and ':' in date_str[last_dash:]:  # Timezone offset
                                            date_part = date_str[:last_dash]
                                            tz_part = date_str[last_dash+1:]  # Remove the dash, we'll add it back
                                            tz_sign = -1
                                        else:
                                            raise ValueError("Cannot parse timezone")
                                    
                                    # Parse datetime part
                                    bc_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
                                    
                                    # Parse timezone offset (HH:MM format)
                                    tz_hours, tz_mins = map(int, tz_part.split(':'))
                                    # Create timezone offset
                                    tz_offset_hours = tz_sign * tz_hours
                                    tz_offset_mins = tz_sign * tz_mins
                                    
                                    # Create a fixed offset timezone and convert to UTC
                                    tz_offset = timezone(timedelta(hours=tz_offset_hours, minutes=tz_offset_mins))
                                    bc_date = bc_date.replace(tzinfo=tz_offset)
                                    bc_date = bc_date.astimezone(pytz.UTC)
                                else:
                                    # No timezone info - assume UTC (BigCommerce default)
                                    bc_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
                                    bc_date = pytz.UTC.localize(bc_date)
                            except (ValueError, AttributeError):
                                # If full datetime parse fails, try date only
                                try:
                                    date_str = bc_date_modified.split('T')[0] if 'T' in bc_date_modified else bc_date_modified.split()[0]
                                    bc_date = pytz.UTC.localize(datetime.strptime(date_str, '%Y-%m-%d'))
                                except ValueError:
                                    # If all parsing fails, proceed with update to be safe
                                    pass
                        elif isinstance(bc_date_modified, datetime):
                            bc_date = bc_date_modified
                            if bc_date.tzinfo is None:
                                bc_date = pytz.UTC.localize(bc_date)
                        
                        if bc_date:
                            # Compare with last successful sync date (convert to UTC for comparison)
                            if last_successful_sync.tzinfo is None:
                                last_sync_utc = pytz.UTC.localize(last_successful_sync)
                            else:
                                last_sync_utc = last_successful_sync.astimezone(pytz.UTC)
                            
                            # Convert BigCommerce date to UTC for comparison
                            bc_date_utc = bc_date.astimezone(pytz.UTC)
                            
                            # If BigCommerce date_modified is not newer than last successful sync, check variants
                            # Variants may have been updated even if parent product wasn't
                            # BUT: Skip date comparison if full_sync is enabled (already set to True above)
                            if not self.full_sync:
                                product_needs_update = bc_date_utc > last_sync_utc
                            
                            if not product_needs_update:
                                # Product itself hasn't changed - check if any variants have been modified
                                try:
                                    bc_variants = api.get_product_variants(bc_id)
                                    if bc_variants:
                                        if isinstance(bc_variants, dict) and 'data' in bc_variants:
                                            bc_variants = bc_variants['data']
                                        
                                        # Check each variant's date_modified
                                        variant_modified = False
                                        for variant in bc_variants:
                                            variant_date_modified = variant.get('date_modified')
                                            if variant_date_modified:
                                                try:
                                                    # Parse variant date using same logic as product date
                                                    variant_date = None
                                                    if isinstance(variant_date_modified, str):
                                                        date_str = variant_date_modified
                                                        has_microseconds = '.' in date_str and ('Z' in date_str or '+' in date_str or (date_str.count('-') > 2))
                                                        if has_microseconds:
                                                            if 'Z' in date_str:
                                                                date_str = date_str.split('.')[0] + 'Z'
                                                            elif '+' in date_str:
                                                                parts = date_str.split('+')
                                                                date_str = parts[0].split('.')[0] + '+' + parts[1]
                                                            elif date_str.count('-') > 2:
                                                                last_dash_idx = date_str.rfind('-')
                                                                if ':' in date_str[last_dash_idx:]:
                                                                    date_part = date_str[:last_dash_idx]
                                                                    tz_part = date_str[last_dash_idx:]
                                                                    date_str = date_part.split('.')[0] + tz_part
                                                        
                                                        if date_str.endswith('Z'):
                                                            date_part = date_str[:-1]
                                                            variant_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
                                                            variant_date = pytz.UTC.localize(variant_date)
                                                        elif '+' in date_str or (date_str.count('-') > 2 and ':' in date_str[-6:]):
                                                            if '+' in date_str:
                                                                date_part, tz_part = date_str.split('+', 1)
                                                                tz_sign = 1
                                                            else:
                                                                last_dash = date_str.rfind('-')
                                                                if last_dash > 10 and ':' in date_str[last_dash:]:
                                                                    date_part = date_str[:last_dash]
                                                                    tz_part = date_str[last_dash+1:]
                                                                    tz_sign = -1
                                                                else:
                                                                    continue
                                                            
                                                            variant_date = datetime.strptime(date_part, '%Y-%m-%dT%H:%M:%S')
                                                            tz_hours, tz_mins = map(int, tz_part.split(':'))
                                                            tz_offset_hours = tz_sign * tz_hours
                                                            tz_offset_mins = tz_sign * tz_mins
                                                            tz_offset = timezone(timedelta(hours=tz_offset_hours, minutes=tz_offset_mins))
                                                            variant_date = variant_date.replace(tzinfo=tz_offset)
                                                            variant_date = variant_date.astimezone(pytz.UTC)
                                                        else:
                                                            variant_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
                                                            variant_date = pytz.UTC.localize(variant_date)
                                                    
                                                    if variant_date:
                                                        variant_date_utc = variant_date.astimezone(pytz.UTC)
                                                        if variant_date_utc > last_sync_utc:
                                                            variant_modified = True
                                                            _logger.debug(f"Variant {variant.get('id')} of product {bc_name} (BC ID: {bc_id}) was modified after last sync. Variant date: {variant_date_utc}, Last sync: {last_sync_utc}")
                                                            break
                                                except Exception as variant_date_error:
                                                    # If variant date parsing fails, continue checking other variants
                                                    _logger.debug(f"Error parsing variant date_modified for variant {variant.get('id')}: {str(variant_date_error)}")
                                                    continue
                                        
                                        if variant_modified:
                                            # At least one variant was modified - proceed with sync
                                            _logger.debug(f"Product {bc_name} (BC ID: {bc_id}) has variants modified since last sync - will sync")
                                            product_needs_update = True
                                        else:
                                            # Neither product nor variants were modified - skip (unless full_sync is enabled)
                                            if not self.full_sync:
                                                _logger.debug(f"Skipping product {bc_name} (BC ID: {bc_id}) - product and all variants not modified since last successful sync. BC date: {bc_date_utc}, Last successful sync: {last_sync_utc}")
                                except Exception as variant_check_error:
                                    # If variant check fails, proceed with sync to be safe
                                    _logger.warning(f"Error checking variant dates for product {bc_id}: {str(variant_check_error)}. Proceeding with sync to be safe.")
                                    product_needs_update = True
                            
                            # Skip variant checking if full_sync is enabled (we'll process all products anyway)
                            if not product_needs_update and not self.full_sync:
                                # Check if product has variants - if so, we should still sync variants
                                # because variant changes (price, SKU, etc.) may not update product's date_modified
                                if self.sync_direction == 'bc_to_odoo' and existing:
                                    # For existing products in bc_to_odoo mode, check if product has variants
                                    # If it does, we should sync variants even if product hasn't changed
                                    try:
                                        # Quick check: fetch variants to see if product has actual variants
                                        if bc_variants is None:
                                            bc_variants_check = api.get_product_variants(bc_id)
                                            if isinstance(bc_variants_check, dict) and 'data' in bc_variants_check:
                                                bc_variants_check = bc_variants_check['data']
                                            has_actual_variants = len(bc_variants_check) > 1 if bc_variants_check else False
                                        else:
                                            has_actual_variants = len(bc_variants) > 1
                                        
                                        if has_actual_variants:
                                            # Product has variants - sync them even if product hasn't changed
                                            # Variant changes (price, SKU) may not update product's date_modified
                                            _logger.info(f"Product {bc_name} (BC ID: {bc_id}) has variants - will sync variants even though product hasn't changed (variant changes may not update product date_modified)")
                                            product_needs_update = True
                                        else:
                                            # No actual variants - skip if product hasn't changed
                                            _logger.debug(f"Skipping product {bc_name} (BC ID: {bc_id}) - product not modified and has no actual variants. BC date: {bc_date_utc}, Last successful sync: {last_sync_utc}")
                                    except Exception as variant_check_error:
                                        # If variant check fails, proceed with sync to be safe
                                        _logger.warning(f"Error checking if product has variants for {bc_id}: {str(variant_check_error)}. Proceeding with sync to be safe.")
                                        product_needs_update = True
                                
                                if not product_needs_update and not self.full_sync:
                                    # Neither product nor variants were modified, and product has no variants - skip
                                    # BUT: Always process if full_sync is enabled
                                    self.products_skipped = (self.products_skipped or 0) + 1
                                    # Update sync operation with skipped count
                                    if self.sync_operation_id:
                                        self.sync_operation_id.items_skipped = (self.sync_operation_id.items_skipped or 0) + 1
                                    return  # Skip this product
                except Exception as e:
                    # If date comparison fails, proceed with update to be safe
                    _logger.warning(f"Error comparing dates for product {bc_id}: {str(e)}. Proceeding with update.")
        
        # Prepare product data
        try:
            price = float(bc_product.get('price', 0)) if bc_product.get('price') else 0.0
            cost_price = float(bc_product.get('cost_price', 0)) if bc_product.get('cost_price') else 0.0
        except (ValueError, TypeError) as e:
            warning_msg = f"Error parsing price: {str(e)}. Using 0.0"
            _logger.warning(f"Error parsing price for product BC ID={bc_id}: {str(e)}. Using 0.0")
            self._create_log(
                'warning',
                warning_msg,
                product_id=bc_id,
                product_name=bc_name,
                error_details=f"Price value from BigCommerce: {bc_product.get('price')}, Cost price: {bc_product.get('cost_price')}"
            )
            price = 0.0
            cost_price = 0.0
        
        # Get parent product SKU - this should be set on the product template even if variants exist
        parent_sku = bc_product.get('sku', '') or ''
        
        # Map BigCommerce inventory_tracking to Odoo fields
        # BigCommerce: 'none', 'product', 'variant'
        # Odoo: 
        #   - is_storable (boolean): Controls "Track Inventory" checkbox
        #   - tracking: 'none' (displays as "By Quantity" when is_storable=True), 'lot' (By Lots), 'serial' (By Serial Number)
        bc_inventory_tracking = bc_product.get('inventory_tracking')
        _logger.info(f"BigCommerce product {bc_name} (ID: {bc_id}) - inventory_tracking value from API: {bc_inventory_tracking} (type: {type(bc_inventory_tracking).__name__})")
        
        # Handle None, empty string, or missing values
        if not bc_inventory_tracking or bc_inventory_tracking == 'none':
            # BigCommerce doesn't track inventory (or field is missing/None/empty)
            is_storable = False
            odoo_tracking = 'none'
            _logger.info(f"BigCommerce product {bc_name} (ID: {bc_id}) - inventory_tracking is '{bc_inventory_tracking}' - setting is_storable=False, tracking='none'")
        elif bc_inventory_tracking in ('product', 'variant'):
            # BigCommerce tracks inventory - enable Track Inventory checkbox and set to "By Quantity"
            is_storable = True
            # 'none' is the stored value that displays as "By Quantity" in the UI when is_storable=True
            odoo_tracking = 'none'
            _logger.info(f"BigCommerce product {bc_name} (ID: {bc_id}) - inventory_tracking='{bc_inventory_tracking}' - setting is_storable=True, tracking='none' (displays as 'By Quantity')")
        else:
            # Unexpected value - log warning but default to tracking inventory
            warning_msg = f"Unexpected inventory_tracking value: '{bc_inventory_tracking}'. Defaulting to track inventory."
            _logger.warning(f"BigCommerce product {bc_name} (ID: {bc_id}) - {warning_msg}")
            self._create_log(
                'warning',
                warning_msg,
                product_id=bc_id,
                product_name=bc_name,
                error_details=f"Expected 'none', 'product', or 'variant', but got '{bc_inventory_tracking}'"
            )
            is_storable = True
            odoo_tracking = 'none'
        
        # Prepare product values
        # IMPORTANT: We do NOT sync inventory quantity here - that is handled by the separate inventory sync
        # We only sync product information (name, price, description, etc.) and inventory tracking settings (is_storable, tracking)
        # Do NOT read inventory_level, inventory_warning_level, or any quantity fields from bc_product
        # Weight and dimensions from BigCommerce (always synced)
        bc_weight = 0
        if bc_product.get('weight'):
            try:
                bc_weight = float(bc_product.get('weight', 0))
            except (ValueError, TypeError):
                bc_weight = 0
        bc_length = 0
        if bc_product.get('depth'):  # BigCommerce uses 'depth' for length
            try:
                bc_length = float(bc_product.get('depth', 0))
            except (ValueError, TypeError):
                bc_length = 0
        bc_width = 0
        if bc_product.get('width'):
            try:
                bc_width = float(bc_product.get('width', 0))
            except (ValueError, TypeError):
                bc_width = 0
        bc_height = 0
        if bc_product.get('height'):
            try:
                bc_height = float(bc_product.get('height', 0))
            except (ValueError, TypeError):
                bc_height = 0
        
        # Calculate volume from dimensions (L × W × H in inches → cubic feet)
        # BigCommerce dimensions are in inches; Odoo volume is in cubic feet (1 ft³ = 1728 in³)
        bc_volume = (bc_length * bc_width * bc_height) / 1728.0
        
        # BigCommerce → Odoo: clear Internal notes and put BC description only in Product Description section.
        bc_desc = bc_product.get('description') or ''
        if not isinstance(bc_desc, str):
            bc_desc = str(bc_desc) if bc_desc is not None else ''
        product_vals = {
            'name': bc_name,
            'default_code': parent_sku,  # Set parent product SKU as internal reference
            'list_price': price,
            'standard_price': cost_price,
            'product_description': bc_desc,  # Only populate Product Description section
            'description': False,             # Always clear Internal notes on sync (False clears the field)
            'weight': bc_weight,
            'product_length': bc_length,
            'product_width': bc_width,
            'product_height': bc_height,
            'volume': bc_volume,
            # Note: bigcommerce_id, bigcommerce_config_id, etc. are now stored in mappings, not directly on product
        }
        # Note: We intentionally do NOT include any inventory quantity fields here
        # Inventory quantities are synced separately using the inventory sync feature
        
        # Only set inventory tracking fields when creating a new product
        # For existing products, preserve the current inventory tracking settings
        # Create new product if it doesn't exist
        if not existing:
            product_vals['is_storable'] = is_storable  # Enable/disable "Track Inventory" checkbox
            product_vals['tracking'] = odoo_tracking  # Set tracking method: 'none' for "By Quantity" when is_storable=True
            _logger.info(f"Product values prepared for NEW product {bc_name} (BC ID: {bc_id}): is_storable={is_storable}, tracking='{odoo_tracking}'")
        else:
            _logger.info(f"Product values prepared for EXISTING product {bc_name} (BC ID: {bc_id}): skipping inventory tracking update (preserving current settings)")
        
        _logger.debug(f"Full product values: {product_vals}")
        
        # Set category only for NEW products - filter-based rules first (SKU / Internal Reference), then default
        # Existing products keep their current category; rules are not applied on update
        category_id = None
        if not existing:
            sku_for_rules = bc_product.get('sku') or ''
            if self.config_id.category_rule_ids:
                category_id = self.env['bigcommerce.category.rule'].get_category_id_for_sku(self.config_id, sku_for_rules)
                if category_id:
                    _logger.debug(f"Category from rule (SKU '{sku_for_rules[:30]}...'): category_id={category_id}")
            if not category_id and self.config_id.product_default_category:
                category_id = self.config_id.product_default_category.id
                _logger.debug(f"Using default category: {self.config_id.product_default_category.name}")
            if category_id:
                product_vals['categ_id'] = category_id
        
        # Set default UOM if configured
        if self.config_id.product_default_uom:
            product_vals['uom_id'] = self.config_id.product_default_uom.id
            # Note: uom_po_id is not available in Odoo 19.0 - purchase UOM is handled automatically
            _logger.debug(f"Using default UOM: {self.config_id.product_default_uom.name}")
        
        try:
            if existing:
                _logger.debug(f"Updating existing product Odoo ID={existing.id} with BigCommerce ID={bc_id}")
                # Ensure inventory tracking fields are NOT in product_vals for existing products
                product_vals.pop('is_storable', None)
                product_vals.pop('tracking', None)
                # Reactivate if product was archived - it exists in BigCommerce again so it should be active
                if not existing.active:
                    product_vals['active'] = True
                    _logger.info(f"Reactivating archived product {existing.name} (BC ID: {bc_id}) - product exists in BigCommerce")
                    self._create_log('info', f"Unarchived product: {existing.name} (BC ID: {bc_id}) - product exists in BigCommerce again",
                                    product_id=bc_id, product_name=existing.name,
                                    error_details="Product was archived but exists in BigCommerce; reactivated.")
                # Keep description and product_description in product_vals: clear Internal notes, update Product Description only
                # Category rules apply only to new products; existing products keep their category (categ_id not in product_vals)
                
                try:
                    # OPTIMIZATION: Reduced logging for performance
                    existing.write(product_vals)
                    # Force-clear Internal notes so BC description never appears there (only in Product Description section)
                    if existing.description:
                        existing.write({'description': False})
                except ValueError as write_error:
                    # Log the error but don't try to fix tracking for existing products
                    # We should never modify inventory tracking for existing products
                    _logger.error(f"Error writing to existing product {existing.name}: {str(write_error)}")
                    raise
                product_template = existing
                
                # Create or update mapping for this config
                if not existing_mapping:
                    existing_mapping = mapping_obj.create({
                        'product_tmpl_id': existing.id,
                        'config_id': self.config_id.id,
                        'bigcommerce_id': bc_id,
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    })
                    _logger.info(f"Created mapping for existing product {existing.id} with config {self.config_id.id}")
                else:
                    # Update existing mapping - ensure BigCommerce ID is current
                    update_vals = {
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    }
                    # Update BigCommerce ID if it has changed (shouldn't normally happen, but handle it)
                    if existing_mapping.bigcommerce_id != bc_id:
                        _logger.info(f"Updating BigCommerce ID in existing mapping from {existing_mapping.bigcommerce_id} to {bc_id}")
                        update_vals['bigcommerce_id'] = bc_id
                    existing_mapping.write(update_vals)
                
                self.products_updated += 1
            else:
                _logger.debug(f"Creating new product for BigCommerce ID={bc_id}")
                try:
                    new_product = product_obj.create(product_vals)
                except ValueError as tracking_error:
                    # If tracking value is invalid, check what valid values are and adjust
                    if 'tracking' in str(tracking_error):
                        _logger.warning(f"Invalid tracking value '{odoo_tracking}' for new product. Checking valid values...")
                        # Get field definition from product template model
                        tracking_field = product_obj._fields.get('tracking')
                        if tracking_field and hasattr(tracking_field, 'selection'):
                            valid_values = [val[0] for val in tracking_field.selection]
                            _logger.info(f"Valid tracking values: {valid_values}")
                            # Try 'none' if available, otherwise use first valid value
                            # Note: If product creation fails, we won't create the mapping
                            if 'none' in valid_values:
                                product_vals['tracking'] = 'none'
                            elif valid_values:
                                product_vals['tracking'] = valid_values[0]
                                _logger.warning(f"Using '{valid_values[0]}' as tracking value for new product")
                            new_product = product_obj.create(product_vals)
                            
                            # Create mapping for the new product
                            new_mapping = mapping_obj.create({
                                'product_tmpl_id': new_product.id,
                                'config_id': self.config_id.id,
                                'bigcommerce_id': bc_id,
                                'bigcommerce_synced': True,
                                'bigcommerce_last_sync': fields.Datetime.now(),
                            })
                            _logger.info(f"Created new product {new_product.id} with mapping for config {self.config_id.id}")
                            self._create_log('info', f"Created new product {new_product.name} (BC ID: {bc_id})",
                                            product_id=bc_id, product_name=new_product.name)
                        else:
                            raise
                    else:
                        raise
                else:
                    # Force-clear Internal notes on new product (product_vals has description=''; ensure it stuck)
                    if new_product.description:
                        new_product.write({'description': False})
                    # Create mapping for the new product (normal path)
                    new_mapping = mapping_obj.create({
                        'product_tmpl_id': new_product.id,
                        'config_id': self.config_id.id,
                        'bigcommerce_id': bc_id,
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    })
                    _logger.info(f"Created new product {new_product.id} with mapping for config {self.config_id.id}")
                    self._create_log('info', f"Created new product {new_product.name} (BC ID: {bc_id})",
                                    product_id=bc_id, product_name=new_product.name)
                    # OPTIMIZATION: Add to cache for future lookups
                    if data_cache:
                        data_cache.add_product(new_product)
                        data_cache.add_mapping(new_mapping)
                
                product_template = new_product
                self.products_created += 1
            
            # Ensure product template is saved and refreshed before adding attributes
            product_template.invalidate_recordset()
            
            # Don't commit here - let batch commit handle it for better performance
            # self.env.cr.commit()  # Removed for performance - batch commits will handle this
            
            # Final verification - only for NEW products, ensure is_storable and tracking are set correctly
            # For existing products, skip this verification to preserve their current settings
            # OPTIMIZATION: Skip this verification for performance - values should already be set correctly
            # if not existing:
            #     product_template.invalidate_recordset()
            #     # Access fields to reload from database
            #     current_is_storable = product_template.is_storable
            #     current_tracking = product_template.tracking
            #     if current_is_storable != is_storable:
            #         _logger.warning(f"Product {product_template.name} (BC ID: {bc_id}) - is_storable mismatch! Expected {is_storable}, got {current_is_storable}. Fixing...")
            #         product_template.write({'is_storable': is_storable})
            #         product_template.invalidate_recordset()
            #     if current_tracking != odoo_tracking:
            #         _logger.warning(f"Product {product_template.name} (BC ID: {bc_id}) - tracking mismatch! Expected '{odoo_tracking}', got '{current_tracking}'. Fixing...")
            #         product_template.write({'tracking': odoo_tracking})
            #         product_template.invalidate_recordset()
            # else:
            #     _logger.debug(f"Skipping inventory tracking verification for existing product {product_template.name} (BC ID: {bc_id}) - preserving current settings")
            
            _logger.info(f"Product template {product_template.id} created/updated and committed for BC ID={bc_id}. Final values: is_storable={product_template.is_storable}, tracking='{product_template.tracking}'")
            
            # Configure dropship route and vendor for products starting with "ds_" (case-insensitive)
            # Check BigCommerce name, Odoo product name, and SKU/internal reference (default_code)
            # OPTIMIZATION: Skip invalidate_recordset for performance - access fields directly
            
            product_name_lower = (bc_name or '').lower()
            odoo_product_name_lower = (product_template.name or '').lower()
            parent_sku_lower = (parent_sku or '').lower()
            odoo_sku_lower = (product_template.default_code or '').lower()
            
            # OPTIMIZATION: Reduced logging for performance
            # _logger.debug(f"Checking dropship condition for product BC ID={bc_id}: bc_name='{bc_name}', odoo_name='{product_template.name}', parent_sku='{parent_sku}', odoo_sku='{product_template.default_code}'")
            
            is_dropship_product = (
                product_name_lower.startswith('ds_') or 
                odoo_product_name_lower.startswith('ds_') or
                parent_sku_lower.startswith('ds_') or
                odoo_sku_lower.startswith('ds_')
            )
            
            if is_dropship_product:
                _logger.debug(f"DROPSHIP PRODUCT DETECTED: BC ID={bc_id}, SKU='{product_template.default_code or parent_sku}'")
                try:
                    # OPTIMIZATION: Use cache if available for dropship route and TURN14 partner
                    dropship_route = None
                    turn14_partner = None
                    
                    if data_cache:
                        dropship_route = data_cache.get_dropship_route()
                        turn14_partner = data_cache.get_turn14_partner()
                    
                    # Fallback to database lookup if not in cache
                    if not dropship_route:
                        dropship_route = self.env['stock.route'].search([
                            ('name', 'ilike', 'dropship')
                        ], limit=1)
                        if not dropship_route:
                            dropship_route = self.env['stock.route'].search([
                                ('name', '=', 'Dropship')
                            ], limit=1)
                    
                    if dropship_route:
                        current_route_ids = product_template.route_ids.ids
                        if dropship_route.id not in current_route_ids:
                            try:
                                product_template.sudo().write({'route_ids': [(4, dropship_route.id)]})
                            except Exception as route_error:
                                _logger.error(f"Error adding dropship route: {str(route_error)}")
                    else:
                        _logger.warning(f"Could not find dropship route in Odoo.")
                    
                    # Find or create TURN14 vendor
                    if not turn14_partner:
                        turn14_partner = self.env['res.partner'].search([
                            ('name', '=', 'TURN14'),
                            ('supplier_rank', '>', 0)
                        ], limit=1)
                        if not turn14_partner:
                            turn14_partner = self.env['res.partner'].search([
                                ('name', '=', 'TURN14')
                            ], limit=1)
                    
                    if not turn14_partner:
                        turn14_partner = self.env['res.partner'].create({
                            'name': 'TURN14',
                            'supplier_rank': 1,
                            'is_company': True,
                        })
                    else:
                        if turn14_partner.supplier_rank == 0:
                            turn14_partner.write({'supplier_rank': 1})
                            # _logger.info(f"Updated TURN14 partner (ID: {turn14_partner.id}) to be a vendor")
                    
                    # OPTIMIZATION: Skip invalidate_recordset for performance
                    # product_template.invalidate_recordset()
                    
                    # Add or update vendor info for this product
                    # Use sudo() to ensure we have permissions to create supplier info
                    supplier_info = self.env['product.supplierinfo'].sudo().search([
                        ('product_tmpl_id', '=', product_template.id),
                        ('partner_id', '=', turn14_partner.id)
                    ], limit=1)
                    
                    if not supplier_info:
                        # Create new supplier info with sudo() to ensure permissions
                        try:
                            supplier_info = self.env['product.supplierinfo'].sudo().create({
                                'product_tmpl_id': product_template.id,
                                'partner_id': turn14_partner.id,
                                'price': cost_price,  # Set vendor cost to product cost
                            })
                            # OPTIMIZATION: Reduced logging and invalidate_recordset for performance
                            # _logger.info(f"Created supplier info: TURN14 (Partner ID: {turn14_partner.id}, Price: {cost_price}) for product {product_template.name} (BC ID: {bc_id}, Supplier Info ID: {supplier_info.id})")
                            # OPTIMIZATION: Skip verification for performance
                            # product_template.invalidate_recordset()
                            # seller_ids = product_template.seller_ids.ids
                        except Exception as supplier_error:
                            _logger.error(f"✗ ERROR creating supplier info for product {product_template.name}: {str(supplier_error)}", exc_info=True)
                            raise
                        else:
                            # Update existing supplier info with current cost price
                            if supplier_info.price != cost_price:
                                supplier_info.write({'price': cost_price})
                                # OPTIMIZATION: Reduced logging for performance
                                # _logger.info(f"Updated TURN14 vendor price to {cost_price} for product {product_template.name} (Supplier Info ID: {supplier_info.id})")
                        # OPTIMIZATION: Reduced logging and invalidate_recordset for performance
                        # _logger.info(f"Product {product_template.name} already has TURN14 as vendor (Supplier Info ID: {supplier_info.id}, Price: {supplier_info.price})")
                        # product_template.invalidate_recordset()
                    
                    # Commit changes to ensure they're persisted
                    self.env.cr.commit()
                    
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    _logger.error(f"Error configuring dropship route/vendor for product {bc_name} (BC ID: {bc_id}): {str(e)}", exc_info=True)
                    _logger.error(f"Full traceback: {error_trace}")
                    # Don't fail the entire sync if this fails
            
            # Sync product images if enabled (check sync operation field first, then config)
            sync_images = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
            if sync_images:
                try:
                    # OPTIMIZATION: Pass image_downloader for potential batch downloading
                    self._sync_product_images(product_template, bc_product, api, image_downloader=image_downloader)
                except Exception as e:
                    _logger.warning(f"Error syncing images for product {bc_name} (BC ID: {bc_id}): {str(e)}", exc_info=True)
            
            # Import attributes/options and variants if the product has them in BigCommerce
            # Only sync variants if configured to do so (can be disabled for faster imports)
            # OPTIMIZATION: Skip variant sync entirely if product data indicates no variants
            # This avoids unnecessary API calls
            if not self.config_id.sync_product_variants:
                warning_msg = "Product variant sync is DISABLED in configuration. Attributes and variants will NOT be created"
                _logger.warning(f"{warning_msg} for product BC ID={bc_id}")
                self._create_log(
                    'warning',
                    warning_msg,
                    product_id=bc_id,
                    product_name=bc_name,
                    error_details="Variant sync is disabled in BigCommerce configuration. Enable 'Sync Product Variants' to sync product variants."
                )
            else:
                _logger.info(f"Product variant sync is ENABLED. Proceeding with attribute/variant sync for product BC ID={bc_id}")
            
            if self.config_id.sync_product_variants:
                # Always try to fetch variants - don't rely on variant_count field which may be missing
                try:
                    bc_variants = []
                    has_variants = False
                    try:
                        # OPTIMIZATION: Use pre-fetched variants from API response if available
                        # When using include=variants in API call, variants are embedded in product data
                        if 'variants' in bc_product:
                            bc_variants = bc_product['variants']
                            _logger.debug(f"OPTIMIZATION: Using pre-fetched variants for product BC ID={bc_id}")
                        else:
                            # Fallback to separate API call if variants not included
                            _logger.debug(f"Fetching variants separately for product BC ID={bc_id}")
                            bc_variants = api.get_product_variants(bc_id)
                        
                        if bc_variants:
                            if isinstance(bc_variants, list):
                                # BigCommerce always returns at least 1 variant (the base variant) even for products without variants
                                # Products with actual variants have 2+ variants
                                has_variants = len(bc_variants) > 1
                            elif isinstance(bc_variants, dict) and 'data' in bc_variants:
                                # Handle V3 API response format
                                bc_variants = bc_variants['data']
                                # BigCommerce always returns at least 1 variant (the base variant) even for products without variants
                                # Products with actual variants have 2+ variants
                                has_variants = len(bc_variants) > 1
                    except Exception as variant_fetch_error:
                        _logger.error(f"Error fetching variants for product BC ID={bc_id}: {str(variant_fetch_error)}")
                        has_variants = False
                    
                    if has_variants:
                        # Try to get attributes using the options endpoint first (more reliable)
                        # Fall back to extracting from variants if options endpoint fails
                        attribute_mapping = {}
                        try:
                            # OPTIMIZATION: Pass bc_product to use pre-fetched options if available
                            attribute_mapping = self._sync_product_attributes(api, bc_id, product_template, bc_product=bc_product)
                            
                            # If options endpoint didn't return any attributes, try extracting from variants
                            if not attribute_mapping:
                                _logger.debug(f"No attributes from options endpoint, trying variant extraction for product BC ID={bc_id}")
                                attribute_mapping = self._extract_attributes_from_variants(api, bc_id, product_template, bc_variants)
                                    
                        except Exception as extract_error:
                            import traceback
                            error_trace = traceback.format_exc()
                            _logger.warning(f"Error fetching attributes for product BC ID={bc_id}: {str(extract_error)}. Trying variant extraction...")
                            
                            # Fall back to variant extraction if options endpoint fails
                            try:
                                attribute_mapping = self._extract_attributes_from_variants(api, bc_id, product_template, bc_variants)
                            except Exception as variant_extract_error:
                                _logger.error(f"Error extracting attributes from variants for product BC ID={bc_id}: {str(variant_extract_error)}")
                                self._create_log(
                                    'error',
                                    f"Error extracting attributes from variants: {str(variant_extract_error)}",
                                    product_id=bc_id,
                                    product_name=bc_name,
                                    error_details=traceback.format_exc()
                                )
                                attribute_mapping = {}
                        
                        # Sync variants with the extracted attribute mapping
                        # For existing products (especially in Update Existing mode or Create/Update mode), always sync variants if they exist
                        # (attributes may already exist in Odoo, so attribute_mapping might be empty)
                        # For new products, also try to sync variants even without attribute mapping - the variant sync method can handle it
                        should_sync_variants = False
                        if self.sync_direction == 'bc_to_odoo' and existing and has_variants:
                            # In BigCommerce to Odoo mode for existing products, always sync variants
                            should_sync_variants = True
                            _logger.debug(f"BigCommerce to Odoo mode (existing product): Will sync {len(bc_variants)} variants for product BC ID={bc_id}")
                        elif attribute_mapping:
                            # If we have attribute mapping, sync variants
                            should_sync_variants = True
                            _logger.debug(f"Attribute mapping found: Will sync {len(bc_variants)} variants for product BC ID={bc_id}")
                        elif has_variants and existing:
                            # For other modes, sync variants if product exists and has variants
                            should_sync_variants = True
                            _logger.debug(f"Existing product with variants: Will sync {len(bc_variants)} variants for product BC ID={bc_id}")
                        elif has_variants and not existing and self.sync_direction == 'bc_to_odoo':
                            # For new products in bc_to_odoo mode, try to sync variants even without attribute mapping
                            # The variant sync method can extract attributes from variant data or create simple variants
                            should_sync_variants = True
                            _logger.info(f"New product with variants (no attribute mapping): Will attempt to sync {len(bc_variants)} variants for product BC ID={bc_id} (variant sync will try to extract attributes)")
                        
                        if should_sync_variants:
                            # Use a savepoint to isolate variant sync errors
                            savepoint = self.env.cr.savepoint()
                            try:
                                # Pass empty dict if attribute_mapping is None/empty - variant matching will use other methods
                                _logger.debug(f"Syncing {len(bc_variants)} variants for product BC ID={bc_id} (attribute_mapping: {'present' if attribute_mapping else 'empty'})")
                                self._sync_product_variants(api, bc_id, product_template, attribute_mapping or {}, bc_variants, bc_product=bc_product)
                            except Exception as variant_sync_error:
                                import traceback
                                error_trace = traceback.format_exc()
                                _logger.error(f"Error syncing variants for product BC ID={bc_id}: {str(variant_sync_error)}")
                                
                                # Check if it's a transaction error
                                if 'transaction' in str(variant_sync_error).lower() or 'aborted' in str(variant_sync_error).lower():
                                    _logger.warning(f"Transaction error in variant sync - rolling back to savepoint")
                                    try:
                                        savepoint.rollback()
                                    except Exception as rollback_error:
                                        _logger.error(f"Error rolling back savepoint: {str(rollback_error)}")
                                        # Try to rollback the entire transaction
                                        try:
                                            self.env.cr.rollback()
                                        except:
                                            pass
                                
                                # Try to create log entry (may fail if transaction is aborted)
                                try:
                                    self._create_log(
                                        'error',
                                        f"Error syncing variants: {str(variant_sync_error)}",
                                        product_id=bc_id,
                                        product_name=bc_name,
                                        error_details=error_trace
                                    )
                                except Exception as log_error:
                                    _logger.warning(f"Could not create log entry due to transaction error: {str(log_error)}")
                        elif has_variants:
                            # This should only happen if len(bc_variants) > 1 (actual variants, not just base variant)
                            warning_msg = f"Product has {len(bc_variants)} variants but will not be synced (new product without attribute mapping)"
                            _logger.warning(f"Product BC ID={bc_id} {warning_msg}")
                            self._create_log(
                                'warning',
                                warning_msg,
                                product_id=bc_id,
                                product_name=bc_name,
                                error_details=f"Could not extract attributes from variants. Variant sync requires attribute mapping to create variants in Odoo."
                            )
                        elif len(bc_variants) == 1:
                            # Product has only the base variant (no actual variants) - this is normal, no warning needed
                            _logger.debug(f"Product BC ID={bc_id} has only base variant (no actual variants) - skipping variant sync")
                        
                        # Ensure parent product template's default_code is always set to parent SKU
                        # This is important for products with variants - the parent SKU should be on the template, not the variants
                        if parent_sku:
                            try:
                                current_default_code = product_template.default_code
                                if current_default_code != parent_sku:
                                    product_template.write({'default_code': parent_sku})
                                    _logger.debug(f"Set parent product template default_code to '{parent_sku}' for BC ID={bc_id}")
                            except Exception as default_code_error:
                                if 'transaction' in str(default_code_error).lower() or 'aborted' in str(default_code_error).lower():
                                    _logger.warning(f"Transaction error accessing product_template.default_code - skipping update. Error: {str(default_code_error)}")
                                    try:
                                        self.env.cr.rollback()
                                    except:
                                        pass
                                else:
                                    _logger.warning(f"Error setting parent product template default_code: {str(default_code_error)}")
                        else:
                            _logger.debug(f"Parent product BC ID={bc_id} has no SKU - default_code will remain empty")
                except Exception as variant_error:
                    is_transaction_error = 'transaction' in str(variant_error).lower() or 'aborted' in str(variant_error).lower()
                    _logger.error(f"✗ Error in variant processing for product BC ID={bc_id}: {str(variant_error)}", exc_info=True)
                    
                    if is_transaction_error:
                        _logger.error(f"Transaction error in variant processing - rolling back")
                        try:
                            self.env.cr.rollback()
                        except:
                            pass
                        # Don't try to create log if transaction is aborted
                    else:
                        # Don't fail the whole product sync if variants fail
                        try:
                            self._create_log(
                                'warning',
                                f"Error syncing variants: {str(variant_error)}",
                                product_id=bc_id,
                                product_name=bc_name,
                                error_details=str(variant_error)
                            )
                        except Exception as log_error:
                            _logger.warning(f"Could not create log entry due to error: {str(log_error)}")
            
            # Final check for dropship configuration AFTER variant processing
            # (default_code might have been set during variant processing)
            # Handle potential transaction errors when accessing product_template fields
            try:
                product_template.invalidate_recordset()
                final_sku = (product_template.default_code or '').lower()
                final_name = (product_template.name or '').lower()
            except Exception as field_access_error:
                # If transaction is aborted, rollback and use safe defaults
                if 'transaction' in str(field_access_error).lower() or 'aborted' in str(field_access_error).lower():
                    _logger.warning(f"Transaction error accessing product_template fields - rolling back and using safe defaults")
                    try:
                        self.env.cr.rollback()
                        # Use values from bc_product instead
                        final_sku = (bc_product.get('sku', '') or '').lower()
                        final_name = (bc_name or '').lower()
                    except Exception as rollback_error:
                        _logger.error(f"Error rolling back transaction: {str(rollback_error)}")
                        # Use safe defaults
                        final_sku = ''
                        final_name = (bc_name or '').lower()
                else:
                    raise
            
            # Only check again if we didn't already process this product as dropship
            if not is_dropship_product and (final_sku.startswith('ds_') or final_name.startswith('ds_')):
                _logger.info(f"✓ DROPSHIP PRODUCT DETECTED (late detection after variant processing): Product BC ID={bc_id}, Name='{product_template.name}', SKU='{product_template.default_code}'")
                # Re-run the dropship configuration
                try:
                    # Find dropship route (search by name - no 'code' field in Odoo 19)
                    dropship_route = self.env['stock.route'].search([('name', 'ilike', 'dropship')], limit=1)
                    if not dropship_route:
                        dropship_route = self.env['stock.route'].search([('name', '=', 'Dropship')], limit=1)
                    
                    if dropship_route:
                        current_route_ids = product_template.route_ids.ids
                        if dropship_route.id not in current_route_ids:
                            product_template.sudo().write({'route_ids': [(4, dropship_route.id)]})
                            product_template.invalidate_recordset()
                            _logger.info(f"✓ Added dropship route (late) to product {product_template.name}")
                    
                    # Find or create TURN14 vendor
                    turn14_partner = self.env['res.partner'].search([('name', '=', 'TURN14')], limit=1)
                    if not turn14_partner:
                        turn14_partner = self.env['res.partner'].create({
                            'name': 'TURN14',
                            'supplier_rank': 1,
                            'is_company': True,
                        })
                    
                    # Add vendor if not present
                    supplier_info = self.env['product.supplierinfo'].sudo().search([
                        ('product_tmpl_id', '=', product_template.id),
                        ('partner_id', '=', turn14_partner.id)
                    ], limit=1)
                    
                    if not supplier_info:
                        # Get cost price from product template
                        product_cost = product_template.standard_price or 0.0
                        self.env['product.supplierinfo'].sudo().create({
                            'product_tmpl_id': product_template.id,
                            'partner_id': turn14_partner.id,
                            'price': product_cost,  # Set vendor cost to product cost
                        })
                        _logger.info(f"✓ Added TURN14 vendor (late) to product {product_template.name} with price {product_cost}")
                    else:
                        # Update price if it's different
                        product_cost = product_template.standard_price or 0.0
                        if supplier_info.price != product_cost:
                            supplier_info.write({'price': product_cost})
                            _logger.info(f"✓ Updated TURN14 vendor price to {product_cost} for product {product_template.name}")
                    
                    self.env.cr.commit()
                except Exception as late_error:
                    _logger.error(f"Error in late dropship configuration: {str(late_error)}", exc_info=True)
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error saving product for BigCommerce ID={bc_id}: {str(e)}", exc_info=True)
            _logger.error(f"Product values that failed: {product_vals}")
            self._create_log(
                'error',
                f"Error saving product: {str(e)}",
                product_id=bc_id,
                product_name=bc_name,
                error_details=error_trace,
                response_data=str(product_vals)
            )
            raise
    
    def _safe_attribute_or_value_name(self, name, fallback_prefix='Option', fallback_id=None):
        """Never persist 'False' or 'True' as attribute/value name (can come from BC boolean in API)."""
        if name is None:
            name = ''
        if not isinstance(name, str):
            name = str(name)
        name = (name or '').strip()
        if name in ('False', 'True'):
            return f'{fallback_prefix} {fallback_id}' if fallback_id else fallback_prefix
        return name or (f'{fallback_prefix} {fallback_id}' if fallback_id else fallback_prefix)

    @staticmethod
    def _normalize_value_for_match(label):
        """Strip erroneous 'False: ' / 'True: ' prefix for matching (existing DB may have it)."""
        if not label or not isinstance(label, str):
            return (label or '').strip()
        s = (label or '').strip()
        if s.startswith('False: '):
            return s[7:].strip()
        if s.startswith('True: '):
            return s[6:].strip()
        return s

    def _sync_product_attributes(self, api_client, bc_product_id, product_template, bc_product=None):
        """Sync product attributes/options from BigCommerce to Odoo
        
        NOTE: This method is deprecated in favor of _extract_attributes_from_variants
        which avoids additional API calls by extracting attributes directly from variant data.
        
        This method creates product.attribute and product.attribute.value records in Odoo
        based on BigCommerce product options and option values.
        
        Args:
            api_client: BigCommerce API client
            bc_product_id: BigCommerce product ID
            product_template: Odoo product template
            bc_product: Optional pre-fetched product data with options included
        
        Returns a mapping dictionary: {bc_option_id: {'attribute': odoo_attribute, 'values': {bc_value_id: odoo_value}}}
        """
        _logger.debug(f"Starting attribute sync for product BC ID={bc_product_id}")
        
        attribute_mapping = {}
        
        try:
            # OPTIMIZATION: Use pre-fetched options if available
            if bc_product and 'options' in bc_product:
                bc_options = bc_product['options']
                _logger.debug(f"OPTIMIZATION: Using pre-fetched options for product BC ID={bc_product_id}")
            else:
                # Fallback to separate API call if options not included
                bc_options = api_client.get_product_options(bc_product_id)
            
            if not bc_options or not isinstance(bc_options, list):
                return attribute_mapping
            
            attribute_obj = self.env['product.attribute']
            attribute_value_obj = self.env['product.attribute.value']
            attribute_line_obj = self.env['product.template.attribute.line']
            for bc_option in bc_options:
                # Validate that bc_option is a dictionary
                if not isinstance(bc_option, dict):
                    _logger.warning(f"Option is not a dictionary: {type(bc_option)}, value: {bc_option}")
                    continue
                
                bc_option_id = bc_option.get('id')
                bc_option_name = bc_option.get('display_name') or bc_option.get('name') or ''
                if not isinstance(bc_option_name, str):
                    bc_option_name = ''
                bc_option_name = (bc_option_name or '').strip()
                bc_option_name = self._safe_attribute_or_value_name(bc_option_name, 'Option', bc_option_id)
                
                if not bc_option_id or not bc_option_name:
                    _logger.debug(f"Skipping option - missing ID or name. ID: {bc_option_id}, Name: {bc_option_name}")
                    continue
                
                _logger.debug(f"Processing option BC ID={bc_option_id}, Name='{bc_option_name}'")
                
                # Find or create product attribute
                odoo_attribute = attribute_obj.search([
                    ('name', '=', bc_option_name)
                ], limit=1)
                
                if not odoo_attribute:
                    odoo_attribute = attribute_obj.create({
                        'name': bc_option_name,
                        'create_variant': 'always',  # Always create variants for this attribute
                    })
                    _logger.debug(f"Created new attribute: {bc_option_name} (ID: {odoo_attribute.id})")
                else:
                    _logger.debug(f"Using existing attribute: {bc_option_name} (ID: {odoo_attribute.id})")
                
                # Get option values - prefer embedded values from pre-fetched options
                bc_option_values = bc_option.get('option_values') or []
                if not bc_option_values:
                    # Fall back to separate API call if values not embedded
                    try:
                        bc_option_values = api_client.get_product_option_values(bc_product_id, bc_option_id)
                        if not bc_option_values:
                            bc_option_values = []
                    except Exception as e:
                        _logger.warning(f"Could not fetch option values for option BC ID={bc_option_id}: {str(e)}")
                        bc_option_values = []
                
                value_mapping = {}
                
                # Process each option value
                for bc_value in bc_option_values:
                    # Validate that bc_value is a dictionary
                    if not isinstance(bc_value, dict):
                        _logger.warning(f"Option value is not a dictionary: {type(bc_value)}, value: {bc_value}")
                        continue
                    
                    bc_value_id = bc_value.get('id')
                    # Use only string fields for label; 'option_value' can be boolean in BC API
                    bc_value_label = bc_value.get('label') or bc_value.get('option_value') or ''
                    if not isinstance(bc_value_label, str):
                        bc_value_label = bc_value.get('label') or ''
                    bc_value_label = (bc_value_label or '').strip() if isinstance(bc_value_label, str) else ''
                    bc_value_label = self._safe_attribute_or_value_name(bc_value_label, 'Value', bc_value_id)
                    
                    if not bc_value_id or not bc_value_label:
                        _logger.debug(f"Skipping option value - missing ID or label. ID: {bc_value_id}, Label: {bc_value_label}")
                        continue
                    
                    # Use the exact label from BigCommerce - do not clean or modify it
                    _logger.debug(f"Processing option value BC ID={bc_value_id}, Label='{bc_value_label}'")
                    
                    # Find or create attribute value using the exact BigCommerce label
                    odoo_value = attribute_value_obj.search([
                        ('attribute_id', '=', odoo_attribute.id),
                        ('name', '=', bc_value_label)
                    ], limit=1)

                    if not odoo_value:
                        # Check for old erroneous "False: " / "True: " prefixed version from a past sync
                        # and rename it instead of creating a duplicate value
                        for _prefix in ('False: ', 'True: '):
                            bad_value = attribute_value_obj.search([
                                ('attribute_id', '=', odoo_attribute.id),
                                ('name', '=', f'{_prefix}{bc_value_label}')
                            ], limit=1)
                            if bad_value:
                                bad_value.write({'name': bc_value_label})
                                odoo_value = bad_value
                                _logger.info(f"Renamed erroneous attribute value '{_prefix}{bc_value_label}' → '{bc_value_label}' on attribute '{odoo_attribute.name}'")
                                break

                    if not odoo_value:
                        odoo_value = attribute_value_obj.create({
                            'name': bc_value_label,
                            'attribute_id': odoo_attribute.id,
                        })
                    
                    value_mapping[bc_value_id] = odoo_value
                
                attribute_mapping[bc_option_id] = {
                    'attribute': odoo_attribute,
                    'values': value_mapping
                }
            
            # Step 2: Create all attribute lines after all attributes/values are created
            for bc_option_id, mapping_data in attribute_mapping.items():
                odoo_attribute = mapping_data['attribute']
                value_mapping = mapping_data['values']
                
                # Check if attribute line already exists
                attribute_line = attribute_line_obj.search([
                    ('product_tmpl_id', '=', product_template.id),
                    ('attribute_id', '=', odoo_attribute.id)
                ], limit=1)
                
                # Get all value IDs for this attribute
                value_ids = [v.id for v in value_mapping.values() if v]
                
                if not value_ids:
                    continue
                
                if not attribute_line:
                    # Create new attribute line with all values
                    try:
                        attribute_line = attribute_line_obj.create({
                            'product_tmpl_id': product_template.id,
                            'attribute_id': odoo_attribute.id,
                            'value_ids': [(6, 0, value_ids)],
                        })
                    except Exception as create_error:
                        _logger.error(f"Failed to create attribute line for '{odoo_attribute.name}': {str(create_error)}")
                        raise
                else:
                    # Update existing attribute line to include all values
                    existing_value_ids = attribute_line.value_ids.ids
                    all_value_ids = list(set(existing_value_ids + value_ids))
                    try:
                        attribute_line.write({
                            'value_ids': [(6, 0, all_value_ids)],
                        })
                    except Exception as update_error:
                        _logger.error(f"Failed to update attribute line for '{odoo_attribute.name}': {str(update_error)}")
                        raise

            # Commit all attribute lines before triggering variant creation
            self.env.cr.commit()

            # Trigger variant creation by accessing product_variant_ids
            try:
                product_template.invalidate_recordset(['attribute_line_ids', 'product_variant_ids'])
                product_template.refresh()
                variant_ids = product_template.product_variant_ids
                _logger.debug(f"Created {len(variant_ids)} variants for product template {product_template.id}")
            except Exception as variant_error:
                _logger.error(f"Error triggering variant creation: {str(variant_error)}")
            return attribute_mapping
            
        except Exception as e:
            _logger.error(f"Error fetching attributes for product BC ID={bc_product_id}: {str(e)}", exc_info=True)
            self._create_log(
                'error',
                f"Error fetching attributes: {str(e)}",
                product_id=bc_product_id,
                product_name=product_template.name,
                error_details=str(e)
            )
            return attribute_mapping
    
    def _extract_attributes_from_variants(self, api_client, bc_product_id, product_template, bc_variants=None):
        """Extract attributes from variant data if options endpoint is not available
        
        Args:
            api_client: BigCommerce API client
            bc_product_id: BigCommerce product ID
            product_template: Odoo product template
            bc_variants: Optional pre-fetched variants list (to avoid duplicate API calls)
        """
        _logger.debug(f"Starting variant extraction for product BC ID: {bc_product_id}")
        
        attribute_mapping = {}
        
        try:
            # Use provided variants if available, otherwise fetch them
            if bc_variants is None:
                _logger.debug(f"Fetching variants for product {bc_product_id}")
                bc_variants = api_client.get_product_variants(bc_product_id)
            
            if not bc_variants:
                _logger.debug(f"No variants returned for product BC ID={bc_product_id}")
                return attribute_mapping
            
            if not isinstance(bc_variants, list):
                _logger.warning(f"Variants data is not a list: {type(bc_variants)}, Value: {bc_variants}")
                return attribute_mapping
            
            # Collect all unique option-value pairs from variants.
            # Use only purchasable variants so we never pick up option_values that may contain
            # raw booleans (e.g. value: false) which could end up as "False"/"True" in names.
            options_data = {}  # {option_id: {'name': '', 'values': {value_id: 'label'}}}
            
            for bc_variant in bc_variants:
                if not isinstance(bc_variant, dict):
                    _logger.warning(f"Skipping invalid variant (not a dictionary): {bc_variant}")
                    continue
                if bc_variant.get('purchasing_disabled', False):
                    _logger.debug(f"Skipping non-purchasable variant BC ID={bc_variant.get('id')} when building attribute names")
                    continue
                
                variant_id = bc_variant.get('id', 'Unknown')
                _logger.debug(f"Processing variant BC ID={variant_id}")
                
                # Try multiple possible field names for option values
                bc_option_values = (bc_variant.get('option_values') or 
                                  bc_variant.get('optionValues') or 
                                  bc_variant.get('options') or
                                  bc_variant.get('option_values_data') or
                                  [])
                
                if not bc_option_values:
                    _logger.debug(f"Variant {variant_id} has no option_values field")
                    if _logger.isEnabledFor(logging.DEBUG):
                        # Only log all fields in debug mode
                        for key, value in bc_variant.items():
                            if 'option' in key.lower() or 'variant' in key.lower():
                                _logger.debug(f"Found potentially relevant field '{key}': {value}")
                    continue
                
                if not isinstance(bc_option_values, list):
                    _logger.warning(f"Variant {variant_id} option_values is not a list: {type(bc_option_values)}")
                    continue
                
                _logger.debug(f"Variant {variant_id} has {len(bc_option_values)} option values")
                
                for bc_option_value in bc_option_values:
                    if not isinstance(bc_option_value, dict):
                        _logger.warning(f"Skipping invalid variant option_value (not a dictionary): {bc_option_value}")
                        continue
                    
                    # V3 API format: option_values contains objects with:
                    # - 'option_id' (the option/attribute ID)
                    # - 'id' (the option value ID)
                    # - 'option_display_name' (the option/attribute name)
                    # - 'label' (the option value label)
                    bc_option_id = (bc_option_value.get('option_id') or 
                                  bc_option_value.get('optionId'))
                    bc_value_id = (bc_option_value.get('id') or 
                                 bc_option_value.get('value_id') or 
                                 bc_option_value.get('valueId'))
                    # Option/attribute name: use only string fields (BC can return booleans for some keys)
                    bc_option_name = (bc_option_value.get('option_display_name') or
                                    bc_option_value.get('option_name') or
                                    bc_option_value.get('optionName') or
                                    bc_option_value.get('name') or
                                    bc_option_value.get('display_name') or '')
                    if not isinstance(bc_option_name, str):
                        bc_option_name = bc_option_value.get('option_display_name') or bc_option_value.get('option_name') or ''
                    bc_option_name = (bc_option_name or '').strip() if isinstance(bc_option_name, str) else ''
                    bc_option_name = self._safe_attribute_or_value_name(bc_option_name, 'Option', bc_option_id)
                    # Value label: use only string fields; 'value' can be boolean (e.g. checkbox) and must not be used as name
                    bc_value_label = (bc_option_value.get('label') or
                                    bc_option_value.get('option_value') or
                                    bc_option_value.get('optionValue') or
                                    bc_option_value.get('label_text') or '')
                    if not isinstance(bc_value_label, str):
                        bc_value_label = bc_option_value.get('label') or ''
                    bc_value_label = (bc_value_label or '').strip() if isinstance(bc_value_label, str) else ''
                    bc_value_label = self._safe_attribute_or_value_name(bc_value_label, 'Value', bc_value_id)
                    
                    _logger.debug(f"Extracted from variant option_value: option_id={bc_option_id}, value_id={bc_value_id}, option_name={bc_option_name}, value_label={bc_value_label}")
                    
                    if not bc_option_id or not bc_value_id:
                        _logger.debug(f"Skipping option value - missing option_id or value_id")
                        continue
                    
                    if bc_option_id not in options_data:
                        options_data[bc_option_id] = {
                            'name': bc_option_name,
                            'values': {}
                        }
                        _logger.debug(f"Found new option: {options_data[bc_option_id]['name']} (BC ID: {bc_option_id})")
                    
                    if bc_value_id not in options_data[bc_option_id]['values']:
                        options_data[bc_option_id]['values'][bc_value_id] = bc_value_label or f'Value {bc_value_id}'
                        _logger.info(f"Found new option value: {bc_value_label} (BC ID: {bc_value_id})")
            
            if not options_data:
                return attribute_mapping
            
            # If any option name is a fallback ("Option 123"), try to get real name from options API
            try:
                bc_options = api_client.get_product_options(bc_product_id)
                if isinstance(bc_options, list):
                    options_by_id = {opt.get('id'): opt for opt in bc_options if isinstance(opt, dict) and opt.get('id')}
                    for bc_option_id, option_data in options_data.items():
                        if (option_data['name'] or '').strip().startswith('Option '):
                            opt = options_by_id.get(bc_option_id)
                            if opt:
                                real_name = (opt.get('display_name') or opt.get('name') or '').strip()
                                if isinstance(real_name, str) and real_name and real_name not in ('False', 'True'):
                                    option_data['name'] = real_name
                                    _logger.debug(f"Using options API name for option {bc_option_id}: '{real_name}'")
            except Exception as e:
                _logger.debug(f"Could not fetch options API for real names: {e}")
            
            # Step 1: Create all attributes and values first
            attribute_obj = self.env['product.attribute']
            attribute_value_obj = self.env['product.attribute.value']
            attribute_line_obj = self.env['product.template.attribute.line']
            replaced_bad_line_ids = set()  # Don't match the same "False"/"True" line to two options
            
            for bc_option_id, option_data in options_data.items():
                option_name = self._safe_attribute_or_value_name(option_data['name'], 'Option', bc_option_id)
                
                # Find or create product attribute
                odoo_attribute = attribute_obj.search([
                    ('name', '=', option_name)
                ], limit=1)
                
                if not odoo_attribute:
                    # Product may have a line with attribute wrongly named "False"/"True" from an old sync.
                    # Find that line by matching value set (normalized); strip "False: "/"True: " from line values for match.
                    value_labels_for_option = set(
                        self._normalize_value_for_match(v) for v in option_data['values'].values()
                    )
                    for bad_line in attribute_line_obj.search([
                        ('product_tmpl_id', '=', product_template.id),
                        ('attribute_id.name', 'in', ['False', 'True'])
                    ]):
                        if bad_line.id in replaced_bad_line_ids:
                            continue
                        line_value_names = set(
                            self._normalize_value_for_match(n) for n in bad_line.value_ids.mapped('name')
                        )
                        if line_value_names == value_labels_for_option:
                            odoo_attribute = attribute_obj.create({
                                'name': option_name,
                                'create_variant': 'always',
                            })
                            bad_line.unlink()
                            replaced_bad_line_ids.add(bad_line.id)
                            _logger.info(f"Replaced 'False'/'True' attribute line with '{option_name}' (product BC ID={bc_product_id})")
                            break
                
                if not odoo_attribute:
                    odoo_attribute = attribute_obj.search([('name', '=', option_name)], limit=1)
                if not odoo_attribute:
                    odoo_attribute = attribute_obj.create({
                        'name': option_name,
                        'create_variant': 'always',
                    })
                
                value_mapping = {}
                
                # Create all attribute values for this attribute
                for bc_value_id, value_label in option_data['values'].items():
                    value_label = self._safe_attribute_or_value_name(value_label, 'Value', bc_value_id)
                    odoo_value = attribute_value_obj.search([
                        ('attribute_id', '=', odoo_attribute.id),
                        ('name', '=', value_label)
                    ], limit=1)

                    if not odoo_value:
                        # Check for old erroneous "False: " / "True: " prefixed version from a past sync
                        # and rename it instead of creating a duplicate value
                        for _prefix in ('False: ', 'True: '):
                            bad_value = attribute_value_obj.search([
                                ('attribute_id', '=', odoo_attribute.id),
                                ('name', '=', f'{_prefix}{value_label}')
                            ], limit=1)
                            if bad_value:
                                bad_value.write({'name': value_label})
                                odoo_value = bad_value
                                _logger.info(f"Renamed erroneous attribute value '{_prefix}{value_label}' → '{value_label}' on attribute '{odoo_attribute.name}'")
                                break

                    if not odoo_value:
                        odoo_value = attribute_value_obj.create({
                            'name': value_label,
                            'attribute_id': odoo_attribute.id,
                        })
                    
                    value_mapping[bc_value_id] = odoo_value
                
                attribute_mapping[bc_option_id] = {
                    'attribute': odoo_attribute,
                    'values': value_mapping
                }
            
            # Step 2: Create all attribute lines after all attributes/values are created
            for bc_option_id, mapping_data in attribute_mapping.items():
                odoo_attribute = mapping_data['attribute']
                value_mapping = mapping_data['values']
                
                # Check if attribute line already exists
                attribute_line = attribute_line_obj.search([
                    ('product_tmpl_id', '=', product_template.id),
                    ('attribute_id', '=', odoo_attribute.id)
                ], limit=1)
                
                # Get all value IDs for this attribute
                value_ids = [v.id for v in value_mapping.values() if v]
                
                if not value_ids:
                    continue
                
                if not attribute_line:
                    # Create new attribute line with all values
                    try:
                        attribute_line = attribute_line_obj.create({
                            'product_tmpl_id': product_template.id,
                            'attribute_id': odoo_attribute.id,
                            'value_ids': [(6, 0, value_ids)],
                        })
                    except Exception as create_error:
                        _logger.error(f"Failed to create attribute line for '{odoo_attribute.name}': {str(create_error)}")
                        raise
                else:
                    # Update existing attribute line to include all values
                    existing_value_ids = attribute_line.value_ids.ids
                    all_value_ids = list(set(existing_value_ids + value_ids))
                    try:
                        attribute_line.write({
                            'value_ids': [(6, 0, all_value_ids)],
                        })
                    except Exception as update_error:
                        _logger.error(f"Failed to update attribute line for '{odoo_attribute.name}': {str(update_error)}")
                        raise
            
            # Commit all attribute lines before triggering variant creation
            self.env.cr.commit()
            
            # Trigger variant creation
            try:
                product_template.invalidate_recordset(['attribute_line_ids', 'product_variant_ids'])
                product_template.refresh()
                variant_ids = product_template.product_variant_ids
                _logger.debug(f"Created {len(variant_ids)} variants for product template {product_template.id}")
            except Exception as variant_error:
                _logger.error(f"Error triggering variant creation: {str(variant_error)}")
            return attribute_mapping
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            _logger.error(f"Error extracting attributes from variants for product BC ID={bc_product_id}: {str(e)}", exc_info=True)
            _logger.error(f"Error traceback: {error_trace}")
            return attribute_mapping
    
    def _sync_product_variants(self, api_client, bc_product_id, product_template, attribute_mapping=None, bc_variants=None, bc_product=None):
        """Sync product variants from BigCommerce to Odoo
        
        This method updates existing variants (created automatically by Odoo when attribute lines are added)
        with BigCommerce-specific data like SKU, price, and BigCommerce variant ID.
        
        Note: Variants are automatically created by Odoo when attribute lines with value_ids are added
        to the product template. This method just updates those auto-created variants.
        
        Args:
            api_client: BigCommerce API client
            bc_product_id: BigCommerce product ID
            product_template: Odoo product template
            attribute_mapping: Optional attribute mapping dictionary
            bc_variants: Optional pre-fetched variants list (to avoid duplicate API calls)
            bc_product: Optional parent BigCommerce product dict (used for fallback weight)
        """
        _logger.debug(f"Syncing variants for product BC ID={bc_product_id}")
        
        # Skip if variant sync is disabled
        if not self.config_id.sync_product_variants:
            _logger.debug(f"Variant sync disabled, skipping variants for product BC ID={bc_product_id}")
            return
        
        if attribute_mapping is None:
            attribute_mapping = {}
        
        try:
            # Use provided variants if available, otherwise fetch them
            if bc_variants is None:
                bc_variants = api_client.get_product_variants(bc_product_id)
            
            if not bc_variants:
                _logger.debug(f"No variants found for product BC ID={bc_product_id}")
                return
            
            if not isinstance(bc_variants, list):
                _logger.warning(f"Variants data is not a list: {type(bc_variants)}")
                return
            
            _logger.debug(f"Syncing {len(bc_variants)} variants for product BC ID={bc_product_id}")
            
            # Fetch product images once if image syncing is enabled (to avoid multiple API calls)
            # Variants use image_id to reference images in the product's images array
            product_images = []
            sync_images = self.sync_images if hasattr(self, 'sync_images') else self.config_id.sync_product_images
            if sync_images:
                try:
                    bc_product = api_client.get_product(bc_product_id, include_images=True)
                    if bc_product:
                        # Handle V3 API response format
                        if isinstance(bc_product, dict) and 'data' in bc_product:
                            bc_product = bc_product['data']
                        
                        # Get images from product
                        if 'images' in bc_product and bc_product['images']:
                            product_images = bc_product['images']
                            if isinstance(product_images, dict) and 'data' in product_images:
                                product_images = product_images['data']
                        _logger.debug(f"Fetched {len(product_images)} product images for variant image sync")
                except Exception as img_fetch_error:
                    _logger.warning(f"Could not fetch product images for variant image sync: {str(img_fetch_error)}")
                    product_images = []
            
            # Get all existing variants for this template (Odoo automatically creates these when attribute lines are added)
            existing_variants = self.env['product.product'].search([
                ('product_tmpl_id', '=', product_template.id)
            ])
            
            _logger.debug(f"Found {len(existing_variants)} existing variants in Odoo for template {product_template.id}")
            
            # If no variants exist yet, Odoo should have created them when attribute lines were added
            # If they don't exist, trigger variant creation by accessing product_variant_ids
            if not existing_variants and product_template.attribute_line_ids:
                _logger.debug(f"No variants found, but attribute lines exist. Triggering variant creation...")
                # Access product_variant_ids to trigger automatic variant creation
                _ = product_template.product_variant_ids
                existing_variants = self.env['product.product'].search([
                    ('product_tmpl_id', '=', product_template.id)
                ])
                _logger.debug(f"After triggering creation, found {len(existing_variants)} variants")
            
            # Pre-fetch all PTAV records for this template to avoid repeated searches
            all_ptav_records = self.env['product.template.attribute.value'].search([
                ('product_tmpl_id', '=', product_template.id)
            ])
            ptav_by_value_id = {ptav.product_attribute_value_id.id: ptav for ptav in all_ptav_records}
            
            # Build index of variants by BC ID for faster lookup
            # Build index of variants by BigCommerce variant ID using mappings
            variant_mapping_obj = self.env['bigcommerce.variant.mapping']
            variants_by_bc_id = {}
            for v in existing_variants:
                # Check for variant mappings for this config
                variant_mapping = variant_mapping_obj.search([
                    ('product_variant_id', '=', v.id),
                    ('config_id', '=', self.config_id.id)
                ], limit=1)
                if variant_mapping:
                    variants_by_bc_id[variant_mapping.bigcommerce_variant_id] = v
                # Also check legacy field for backward compatibility
                elif v.bigcommerce_variant_id:
                    variants_by_bc_id[v.bigcommerce_variant_id] = v
            
            # Build index of variants by SKU for fallback matching (case-insensitive, trimmed)
            variants_by_sku = {}
            for v in existing_variants:
                if v.default_code:
                    # Store both original and normalized (lowercase, trimmed) versions
                    sku_normalized = v.default_code.strip().upper()
                    variants_by_sku[sku_normalized] = v
                    # Also store original for exact match
                    if v.default_code != sku_normalized:
                        variants_by_sku[v.default_code] = v
            
            # Build index of variants by PTAV combination for faster lookup
            variants_by_ptavs = {}
            for variant in existing_variants:
                ptav_ids = tuple(sorted(variant.product_template_attribute_value_ids.ids))
                if ptav_ids not in variants_by_ptavs:
                    variants_by_ptavs[ptav_ids] = []
                variants_by_ptavs[ptav_ids].append(variant)
            
            # Collect all variant updates to batch them
            variant_updates = []
            
            # Track which Odoo variants have been matched to prevent duplicate matches
            matched_odoo_variant_ids = set()
            
            for bc_variant in bc_variants:
                bc_variant_id = bc_variant.get('id')
                if not bc_variant_id:
                    _logger.warning(f"Variant missing ID, skipping: {bc_variant}")
                    continue
                
                _logger.debug(f"Processing variant BC ID={bc_variant_id}")
                
                # Initialize matching_variant to None for this iteration
                matching_variant = None
                
                # First, try to find by BigCommerce variant ID (most reliable match) - use index
                matching_variant = variants_by_bc_id.get(bc_variant_id)
                if matching_variant:
                    # Check if this variant has already been matched to another BC variant
                    if matching_variant.id in matched_odoo_variant_ids:
                        _logger.warning(f"Odoo variant ID={matching_variant.id} already matched to another BC variant. Skipping BC variant ID={bc_variant_id}")
                        matching_variant = None
                    else:
                        _logger.debug(f"Found variant by BC ID: Odoo ID={matching_variant.id} for BC variant ID={bc_variant_id}")
                else:
                    # Initialize variant_attribute_value_ids for logging
                    variant_attribute_value_ids = []
                    
                    # Try matching by SKU as a quick fallback (before attribute matching)
                    bc_sku = bc_variant.get('sku', '').strip()
                    if bc_sku:
                        # Try exact match first
                        if bc_sku in variants_by_sku:
                            candidate = variants_by_sku[bc_sku]
                            # Only use if not already matched
                            if candidate.id not in matched_odoo_variant_ids:
                                matching_variant = candidate
                                _logger.debug(f"Found variant by SKU (exact): Odoo ID={matching_variant.id} for BC variant ID={bc_variant_id}")
                        else:
                            # Try normalized (uppercase) match
                            bc_sku_normalized = bc_sku.upper()
                            if bc_sku_normalized in variants_by_sku:
                                candidate = variants_by_sku[bc_sku_normalized]
                                # Only use if not already matched
                                if candidate.id not in matched_odoo_variant_ids:
                                    matching_variant = candidate
                                    _logger.debug(f"Found variant by SKU (normalized): Odoo ID={matching_variant.id} for BC variant ID={bc_variant_id}")
                    
                    if not matching_variant:
                        # Build list of Odoo attribute value IDs for this variant based on option values
                        bc_option_values = bc_variant.get('option_values', [])
                        
                        if not bc_option_values:
                            # Try alternative field names
                            bc_option_values = (bc_variant.get('optionValues') or 
                                              bc_variant.get('options') or
                                              bc_variant.get('option_values_data') or
                                              [])
                        
                        _logger.debug(f"Variant BC ID={bc_variant_id} has {len(bc_option_values)} option values")
                        
                        # Only try attribute mapping if attribute_mapping is provided
                        if attribute_mapping:
                            for bc_option_value in bc_option_values:
                                if not isinstance(bc_option_value, dict):
                                    continue
                                
                                bc_option_id = bc_option_value.get('option_id')
                                bc_value_id = bc_option_value.get('id')
                                
                                if not bc_option_id or not bc_value_id:
                                    continue
                                
                                # Map to Odoo attribute value
                                if bc_option_id in attribute_mapping:
                                    if bc_value_id in attribute_mapping[bc_option_id]['values']:
                                        odoo_value = attribute_mapping[bc_option_id]['values'][bc_value_id]
                                        variant_attribute_value_ids.append(odoo_value.id)
                                        _logger.debug(f"Mapped BC option {bc_option_id} value {bc_value_id} to Odoo value {odoo_value.id} ({odoo_value.name})")
                        
                        # Find the matching Odoo variant by attribute value combination
                        # Use pre-fetched PTAV records
                        if variant_attribute_value_ids:
                            # Get PTAV records from cache
                            ptav_records = [ptav_by_value_id.get(avid) for avid in variant_attribute_value_ids if avid in ptav_by_value_id]
                            ptav_records = [ptav for ptav in ptav_records if ptav]  # Remove None values
                            
                            if ptav_records and len(ptav_records) == len(variant_attribute_value_ids):
                                ptav_ids = tuple(sorted([ptav.id for ptav in ptav_records]))
                                # Use index for faster lookup
                                matching_variants = variants_by_ptavs.get(ptav_ids, [])
                                if matching_variants:
                                    # Prefer variant without BC ID, and not already matched
                                    for v in matching_variants:
                                        if v.id not in matched_odoo_variant_ids:
                                            if not v.bigcommerce_variant_id:
                                                matching_variant = v
                                                break
                                    # If no unmatched variant without BC ID, use first unmatched variant
                                    if not matching_variant:
                                        for v in matching_variants:
                                            if v.id not in matched_odoo_variant_ids:
                                                matching_variant = v
                                                break
                                    if matching_variant:
                                        _logger.debug(f"Found matching variant Odoo ID={matching_variant.id} for BC variant ID={bc_variant_id} by attribute values")
                
                # Fallback: If no match found and there's exactly one variant in each, match them
                if not matching_variant and len(existing_variants) == 1 and len(bc_variants) == 1:
                    candidate = existing_variants[0]
                    if candidate.id not in matched_odoo_variant_ids:
                        matching_variant = candidate
                        _logger.debug(f"Fallback match: Using single Odoo variant ID={matching_variant.id} for single BC variant ID={bc_variant_id}")
                
                # Fallback: When attribute matching fails (e.g. after merging products / deleting duplicate attribute values),
                # if BC and Odoo variant counts match, assign by position (first unmatched Odoo variant to this BC variant)
                # so SKU and mapping can be set; next sync will match by SKU.
                if not matching_variant and len(bc_variants) == len(existing_variants):
                    unmatched = sorted([v for v in existing_variants if v.id not in matched_odoo_variant_ids], key=lambda v: v.id)
                    if unmatched:
                        matching_variant = unmatched[0]
                        _logger.info(
                            f"Fallback match by position: BC variant ID={bc_variant_id} → Odoo variant ID={matching_variant.id} "
                            f"(attribute match failed; counts match, using first unmatched variant)"
                        )
                
                # Skip non-purchasable BC variants: do not sync them; archive Odoo variant if it exists
                # BigCommerce API uses purchasing_disabled (true = not purchasable). See:
                # https://developer.bigcommerce.com/docs/rest-catalog/product-variants
                bc_purchasing_disabled = bc_variant.get('purchasing_disabled', False)
                if bc_purchasing_disabled:
                    if matching_variant:
                        matched_odoo_variant_ids.add(matching_variant.id)
                        if matching_variant.active:
                            matching_variant.write({'active': False})
                            _logger.info(
                                f"Archived Odoo variant ID={matching_variant.id} (SKU={matching_variant.default_code}), "
                                f"BC variant ID={bc_variant_id} has purchasing_disabled=True"
                            )
                    else:
                        _logger.debug(f"Skipping BC variant ID={bc_variant_id} (purchasing_disabled, no matching Odoo variant)")
                    continue
                
                # Prepare variant update values - create a fresh dictionary for each variant
                variant_vals = {}
                # Ensure purchasable variants are active (reactivate if previously archived)
                variant_vals['active'] = True
                
                # Create/update variant mapping only when we have a matching Odoo variant (avoids NoneType error)
                if matching_variant:
                    try:
                        mapping_obj = self.env['bigcommerce.product.mapping']
                        variant_mapping_obj = self.env['bigcommerce.variant.mapping']
                        
                        product_mapping = mapping_obj.search([
                            ('product_tmpl_id', '=', product_template.id),
                            ('config_id', '=', self.config_id.id)
                        ], limit=1)
                        
                        if not product_mapping:
                            try:
                                product_mapping = mapping_obj.create({
                                    'product_tmpl_id': product_template.id,
                                    'config_id': self.config_id.id,
                                    'bigcommerce_id': bc_product_id,
                                })
                            except Exception as mapping_error:
                                _logger.warning(f"Error creating product mapping for product {product_template.id}, config {self.config_id.id}: {str(mapping_error)}")
                                product_mapping = mapping_obj.search([
                                    ('product_tmpl_id', '=', product_template.id),
                                    ('config_id', '=', self.config_id.id)
                                ], limit=1)
                                if not product_mapping:
                                    _logger.error(f"Could not create or find product mapping - skipping variant mapping for variant {bc_variant_id}")
                        
                        if product_mapping:
                            variant_savepoint = self.env.cr.savepoint()
                            try:
                                variant_mapping = variant_mapping_obj.search([
                                    ('product_variant_id', '=', matching_variant.id),
                                    ('config_id', '=', self.config_id.id)
                                ], limit=1)
                                
                                if variant_mapping:
                                    variant_mapping.write({'bigcommerce_variant_id': bc_variant_id})
                                    _logger.debug(f"Updated variant mapping for variant {matching_variant.id}")
                                else:
                                    variant_mapping_obj.create({
                                        'config_id': self.config_id.id,
                                        'product_mapping_id': product_mapping.id,
                                        'product_variant_id': matching_variant.id,
                                        'bigcommerce_variant_id': bc_variant_id,
                                    })
                                    _logger.debug(f"Created variant mapping for variant {matching_variant.id}, BC variant {bc_variant_id}")
                            except Exception as mapping_error:
                                _logger.warning(
                                    f"Error with variant mapping for Odoo variant {matching_variant.id}, BC variant {bc_variant_id}: {str(mapping_error)}"
                                )
                                try:
                                    variant_savepoint.rollback()
                                except Exception:
                                    pass
                    except Exception as mapping_error:
                        _logger.error(f"Error handling mappings for variant {bc_variant_id}: {str(mapping_error)}", exc_info=True)
                
                # Keep legacy field for backward compatibility (computed from first mapping)
                # variant_vals['bigcommerce_variant_id'] = bc_variant_id
                
                # Always set SKU as internal reference (default_code) from BigCommerce variant
                # According to BigCommerce API, SKU is a required field, so it should always be present
                # Extract SKU value - handle None, empty string, or missing key
                bc_sku_raw = bc_variant.get('sku')
                if bc_sku_raw is not None:
                    bc_sku = str(bc_sku_raw).strip()
                else:
                    bc_sku = ''
                    _logger.warning(f"BC variant ID={bc_variant_id} has None or missing 'sku' field. Setting to empty string. Variant keys: {list(bc_variant.keys())}")
                
                # Always set default_code, even if SKU is empty (to sync from BigCommerce)
                variant_vals['default_code'] = bc_sku
                _logger.info(f"Setting variant SKU (default_code) to '{bc_sku}' for BC variant ID={bc_variant_id}, Odoo variant ID={matching_variant.id if matching_variant else 'N/A'}")
                
                # Update barcode if provided
                if bc_variant.get('upc') or bc_variant.get('barcode'):
                    variant_vals['barcode'] = bc_variant.get('upc', '') or bc_variant.get('barcode', '')
                
                # Set variant price from BigCommerce (always update if provided, even if same as template)
                variant_price = None
                bc_price_raw = bc_variant.get('price')
                bc_calculated_price_raw = bc_variant.get('calculated_price')
                
                if bc_price_raw:
                    try:
                        variant_price = float(bc_price_raw)
                    except (ValueError, TypeError):
                        pass
                elif bc_calculated_price_raw:
                    try:
                        variant_price = float(bc_calculated_price_raw)
                    except (ValueError, TypeError):
                        pass
                
                # Store variant price for direct variant pricing (product_variant_pricing module)
                # Set list_price_override and variant_list_price so BC price is used directly on the variant
                variant_price_for_extra = None
                if variant_price is not None and variant_price >= 0:
                    variant_price_for_extra = variant_price
                    variant_vals['list_price_override'] = True
                    variant_vals['variant_list_price'] = variant_price_for_extra
                    _logger.debug(f"BC Variant ID={bc_variant_id}, SKU={bc_variant.get('sku', 'N/A')}, Price={variant_price}")
                else:
                    _logger.debug(f"BC Variant ID={bc_variant_id} has no valid price (price={bc_price_raw}, calculated_price={bc_calculated_price_raw})")
                
                # Set variant cost if available
                if bc_variant.get('cost_price'):
                    try:
                        variant_cost = float(bc_variant.get('cost_price', 0))
                        if variant_cost > 0:
                            variant_vals['standard_price'] = variant_cost
                    except (ValueError, TypeError):
                        pass
                
                # Set weight: use variant weight if present, otherwise fall back to parent product weight
                variant_weight = bc_variant.get('weight')
                if variant_weight is not None:
                    try:
                        variant_vals['weight'] = float(variant_weight)
                    except (ValueError, TypeError):
                        pass
                if 'weight' not in variant_vals:
                    # Variant has no weight — inherit from parent product
                    parent_weight = bc_product.get('weight') if bc_product else None
                    if parent_weight is not None:
                        try:
                            variant_vals['weight'] = float(parent_weight)
                        except (ValueError, TypeError):
                            pass
                
                # Set volume from parent product dimensions (variants don't have their own dimensions in BigCommerce)
                if bc_product:
                    try:
                        p_length = float(bc_product.get('depth', 0) or 0)
                        p_width = float(bc_product.get('width', 0) or 0)
                        p_height = float(bc_product.get('height', 0) or 0)
                        # BigCommerce dimensions are in inches; Odoo volume is in cubic feet (1 ft³ = 1728 in³)
                        variant_volume = (p_length * p_width * p_height) / 1728.0
                        variant_vals['volume'] = variant_volume
                    except (ValueError, TypeError):
                        pass
                
                # Collect variant update for batch processing
                if matching_variant:
                    # Mark this variant as matched to prevent duplicate matches
                    matched_odoo_variant_ids.add(matching_variant.id)
                    _logger.debug(f"Matched variant: Odoo ID={matching_variant.id} (SKU={matching_variant.default_code}) → BC ID={bc_variant_id}")
                    # Create a deep copy of variant_vals to ensure each variant gets its own dictionary
                    import copy
                    variant_vals_copy = copy.deepcopy(variant_vals)
                    # Store the variant price separately for price_extra calculation
                    # IMPORTANT: Also store a copy of bc_variant so we have the correct variant data when syncing images
                    bc_variant_copy = copy.deepcopy(bc_variant)
                    variant_updates.append((matching_variant, variant_vals_copy, bc_variant_id, variant_price_for_extra, product_template, bc_variant_copy))
                else:
                    # No matching variant found - this shouldn't happen if attribute lines were created correctly
                    # But don't create a new variant - Odoo should have created all variants automatically
                    bc_sku = bc_variant.get('sku', '').strip()
                    bc_option_values_count = len(bc_option_values) if 'bc_option_values' in locals() else 0
                    _logger.warning(f"✗ No matching variant found for BC variant ID={bc_variant_id}, SKU={bc_sku}")
                    _logger.warning(f"  BC Variant data: price={bc_variant.get('price')}, calculated_price={bc_variant.get('calculated_price')}, option_values={bc_option_values_count}")
                    _logger.warning(f"  Available Odoo variants ({len(existing_variants)}): {[f'ID={v.id}, SKU={v.default_code}, BC_ID={v.bigcommerce_variant_id}, Price={v.list_price}' for v in existing_variants[:5]]}")
                    _logger.warning(f"  Attribute value IDs attempted: {variant_attribute_value_ids}")
                    _logger.warning(f"  Total BC variants: {len(bc_variants)}, Total Odoo variants: {len(existing_variants)}")
                    # Log this as a warning but don't create a duplicate variant
                    self._create_log(
                        'warning',
                        f"No matching variant found for BigCommerce variant ID {bc_variant_id}. Variant skipped to prevent duplicates.",
                        product_id=bc_product_id,
                        product_name=product_template.name,
                        error_details=f"BC SKU: {bc_sku}, Attribute value IDs: {variant_attribute_value_ids if 'variant_attribute_value_ids' in locals() else 'N/A'}. Available variants: {len(existing_variants)}",
                        response_data=str(bc_variant)
                    )
            
            # Batch update all variants
            if variant_updates:
                _logger.debug(f"Batch updating {len(variant_updates)} variants for product BC ID={bc_product_id}")
                
                try:
                    # Check for duplicate Odoo variant IDs in the update list
                    odoo_ids_in_updates = [v[0].id for v in variant_updates]
                    duplicates = [oid for oid in set(odoo_ids_in_updates) if odoo_ids_in_updates.count(oid) > 1]
                    if duplicates:
                        _logger.error(f"ERROR: Found duplicate Odoo variant IDs in update list: {duplicates}")
                        _logger.error(f"  This means multiple BC variants are trying to update the same Odoo variant!")
                        for update_item in variant_updates:
                            matching_variant = update_item[0]
                            bc_variant_id = update_item[2]
                            variant_price = update_item[3] if len(update_item) > 3 else 'N/A'
                            if matching_variant.id in duplicates:
                                _logger.error(f"  Odoo Variant ID={matching_variant.id} matched to BC Variant ID={bc_variant_id}, Price={variant_price}")
                    
                    updated_count = 0
                    processed_odoo_ids = set()
                    # Process each variant update with individual error handling
                    for update_item in variant_updates:
                        matching_variant = update_item[0]
                        variant_vals = update_item[1]
                        bc_variant_id = update_item[2]
                        variant_price_for_extra = update_item[3] if len(update_item) > 3 else None
                        product_template = update_item[4] if len(update_item) > 4 else None
                        bc_variant = update_item[5] if len(update_item) > 5 else {}  # Get the correct bc_variant for this update
                        
                        # CRITICAL: Validate that bc_variant matches bc_variant_id to ensure we have the correct variant data
                        if bc_variant and bc_variant.get('id') != bc_variant_id:
                            _logger.error(f"ERROR: bc_variant ID mismatch! Expected {bc_variant_id}, but bc_variant has ID {bc_variant.get('id')}")
                            _logger.error(f"  This indicates a bug in variant data storage. Skipping image sync for this variant.")
                            # Continue with other updates but skip image sync
                            bc_variant = {}
                        
                        try:
                            # Prevent duplicate updates
                            if matching_variant.id in processed_odoo_ids:
                                _logger.error(f"SKIPPING duplicate update for Odoo variant ID={matching_variant.id} (BC ID={bc_variant_id})")
                                continue
                            processed_odoo_ids.add(matching_variant.id)
                            
                            # Log before update - handle potential transaction errors when accessing fields
                            try:
                                old_price = matching_variant.list_price
                                _logger.debug(f"Updating variant Odoo ID={matching_variant.id} (BC ID={bc_variant_id}), price: {old_price} → {variant_price_for_extra if variant_price_for_extra is not None else 'N/A'}")
                            except Exception as field_error:
                                if 'transaction' in str(field_error).lower() or 'aborted' in str(field_error).lower():
                                    _logger.warning(f"Transaction error accessing variant {matching_variant.id} fields - rolling back and skipping this variant")
                                    try:
                                        self.env.cr.rollback()
                                    except:
                                        pass
                                    continue
                                else:
                                    old_price = 0.0
                                    _logger.debug(f"Could not read variant price, using 0.0: {str(field_error)}")
                            
                            # Update variant fields (SKU, barcode, list_price_override, variant_list_price, cost, etc.)
                            if variant_vals:
                                try:
                                    _logger.info(f"Writing variant_vals to Odoo variant ID={matching_variant.id}: {variant_vals}")
                                    matching_variant.write(variant_vals)
                                    # Verify the write
                                    matching_variant.invalidate_recordset()
                                    _logger.info(f"After write - Odoo variant ID={matching_variant.id} default_code='{matching_variant.default_code}'")
                                except Exception as write_error:
                                    # Check if it's a transaction error
                                    if 'transaction' in str(write_error).lower() or 'aborted' in str(write_error).lower():
                                        _logger.error(f"Transaction error writing variant {matching_variant.id}: {str(write_error)}")
                                        # Transaction is already aborted - we can't continue with this variant
                                        # Re-raise to be caught by outer handler
                                        raise
                                    else:
                                        _logger.error(f"Error writing variant {matching_variant.id}: {str(write_error)}", exc_info=True)
                                        # Continue with next variant for non-transaction errors
                                        continue
                            else:
                                _logger.warning(f"variant_vals is empty for Odoo variant ID={matching_variant.id} (BC ID={bc_variant_id}) - nothing to update")
                            
                            # Sync variant images if enabled (check sync operation field first, then config)
                            if sync_images:
                                try:
                                    _logger.info(f"=== Syncing image for variant ===")
                                    _logger.info(f"  Odoo Variant: ID={matching_variant.id}, Name='{matching_variant.name}', SKU={matching_variant.default_code}")
                                    _logger.info(f"  BC Variant ID from tuple: {bc_variant_id}")
                                    _logger.info(f"  BC Variant ID from bc_variant dict: {bc_variant.get('id', 'MISSING')}")
                                    _logger.info(f"  BC Variant SKU: {bc_variant.get('sku', 'N/A')}")
                                    _logger.info(f"  BC Variant data keys: {list(bc_variant.keys())}")
                                    _logger.info(f"  BC Variant image_id: {bc_variant.get('image_id', 'Not present')}")
                                    _logger.info(f"  BC Variant image_url: {bc_variant.get('image_url', 'Not present')}")
                                    
                                    # Validate bc_variant is not empty before syncing
                                    if not bc_variant:
                                        _logger.warning(f"  bc_variant is empty for variant {bc_variant_id} - skipping image sync")
                                    else:
                                        # Pass API client and product ID for proper variant image retrieval via dedicated endpoint
                                        # Also pass product_images as fallback
                                        # IMPORTANT: Each variant should get its own unique image from BigCommerce
                                        # Now using the correct bc_variant from the update_item tuple
                                        self._sync_variant_image(
                                            matching_variant, 
                                            bc_variant, 
                                            api_client=api_client,
                                            bc_product_id=bc_product_id,
                                            product_images=product_images
                                        )
                                except Exception as img_error:
                                    _logger.warning(f"Error syncing image for variant {matching_variant.name} (BC Variant ID: {bc_variant_id}): {str(img_error)}", exc_info=True)
                            
                            # Set attribute price_extra to 0 for all variants (pricing is via variant_list_price from product_variant_pricing)
                            variant_ptavs = matching_variant.product_template_attribute_value_ids
                            if variant_ptavs:
                                for ptav in variant_ptavs:
                                    if ptav.price_extra != 0:
                                        ptav.write({'price_extra': 0.0})
                                _logger.debug(f"  Set price_extra=0 for {len(variant_ptavs)} attribute value(s) on variant {matching_variant.id}")
                            
                            # Commit to ensure changes are persisted
                            self.env.cr.commit()
                            
                            # Invalidate and reload to verify the update
                            matching_variant.invalidate_recordset()
                            product_template.invalidate_recordset() if product_template else None
                            # Access fields to reload from database (use variant_list_price when using direct variant pricing)
                            new_price = matching_variant.variant_list_price if matching_variant.list_price_override else matching_variant.list_price
                            new_sku = matching_variant.default_code
                            
                            # Verify the price was set correctly
                            if variant_price_for_extra is not None:
                                price_diff = abs(float(new_price) - float(variant_price_for_extra))
                                if price_diff > 0.01:
                                    _logger.warning(f"Price difference detected! Expected {variant_price_for_extra}, got {new_price} (diff: {price_diff}) for variant {matching_variant.id}")
                            updated_count += 1
                        except Exception as update_error:
                            # Check if it's a transaction error
                            is_transaction_error = 'transaction' in str(update_error).lower() or 'aborted' in str(update_error).lower()
                            _logger.error(f"✗ Error updating variant Odoo ID={matching_variant.id}: {str(update_error)}", exc_info=True)
                            
                            if is_transaction_error:
                                _logger.error(f"Transaction aborted - cannot continue with variant updates. Error: {str(update_error)}")
                                # Try to rollback and break out of loop
                                try:
                                    self.env.cr.rollback()
                                except:
                                    pass
                                # Break out of the loop - transaction is aborted, can't continue
                                break
                            else:
                                # For non-transaction errors, try to create log entry
                                try:
                                    product_name = product_template.name if product_template else 'Unknown'
                                    self._create_log(
                                        'warning',
                                        f"Error updating variant: {str(update_error)}",
                                        product_id=bc_product_id,
                                        product_name=product_name,
                                        error_details=str(update_error),
                                        response_data=str(bc_variant_id)
                                    )
                                except Exception as log_error:
                                    _logger.warning(f"Could not create log entry: {str(log_error)}")
                                    # Continue with next variant
                                    continue
                
                except Exception as batch_error:
                    # Catch any transaction errors from the entire batch
                    if 'transaction' in str(batch_error).lower() or 'aborted' in str(batch_error).lower():
                        _logger.error(f"Transaction error in batch variant update: {str(batch_error)}")
                        try:
                            self.env.cr.rollback()
                            _logger.info(f"Rolled back transaction after batch variant update error")
                        except Exception as rollback_error:
                            _logger.error(f"Error rolling back transaction: {str(rollback_error)}")
                    else:
                        _logger.error(f"Error in batch variant update: {str(batch_error)}", exc_info=True)
                
                _logger.debug(f"Completed variant updates: {updated_count}/{len(variant_updates)} variants updated successfully for product BC ID={bc_product_id}")
            else:
                _logger.warning(f"No variants to update for product BC ID={bc_product_id}")
                _logger.warning(f"  Total BC variants: {len(bc_variants)}")
                _logger.warning(f"  Existing Odoo variants: {len(existing_variants)}")
                if existing_variants:
                    variant_details = [f'ID={v.id}, SKU={v.default_code}, BC_ID={v.bigcommerce_variant_id}' for v in existing_variants[:5]]
                    _logger.warning(f"  Odoo variant details: {variant_details}")
                if bc_variants:
                    bc_details = []
                    for v in bc_variants[:5]:
                        v_id = v.get('id', 'N/A')
                        v_sku = v.get('sku', 'N/A')
                        v_price = v.get('price') or v.get('calculated_price', 'N/A')
                        bc_details.append(f'ID={v_id}, SKU={v_sku}, Price={v_price}')
                    _logger.warning(f"  BC variant details: {bc_details}")
            
            _logger.debug(f"Completed syncing variants for product BC ID={bc_product_id}")
            
        except Exception as e:
            _logger.error(f"Error fetching variants for product BC ID={bc_product_id}: {str(e)}", exc_info=True)
            # Don't raise - variants are optional
            pass
    
    def _sync_to_bigcommerce(self, api):
        """Sync products from Odoo to BigCommerce"""
        _logger.info("Searching for Odoo products to sync to BigCommerce...")
        domain = [
            ('bigcommerce_config_id', '=', self.config_id.id),
            '|', ('bigcommerce_id', '=', False), ('bigcommerce_synced', '=', False)
        ]
        # Apply date filters
        if self.min_date_modified:
            domain.append(('write_date', '>=', self.min_date_modified))
            _logger.info(f"Filtering Odoo products by write_date >= {self.min_date_modified}")
        if self.date_from:
            domain.append(('create_date', '>=', self.date_from))
            _logger.info(f"Filtering Odoo products by create_date >= {self.date_from}")
        if self.date_to:
            domain.append(('create_date', '<=', self.date_to))
            _logger.info(f"Filtering Odoo products by create_date <= {self.date_to}")
        
        # Apply product tag filtering if tags are configured
        # Tags are only used for Odoo to BigCommerce sync to filter which products get synced
        if self.config_id and self.config_id.product_tag_ids:
            domain.append(('product_tag_ids', 'in', self.config_id.product_tag_ids.ids))
            _logger.info(f"Filtering Odoo products by product tags: {self.config_id.product_tag_ids.mapped('name')}")
        
        products = self.env['product.template'].search(domain)
        
        _logger.info(f"Found {len(products)} products to sync to BigCommerce")
        
        for idx, product in enumerate(products, 1):
            # Check if sync has been cancelled
            if self._check_cancelled():
                _logger.info("Product sync cancelled by user")
                raise UserError("Sync operation was cancelled by user")
            
            _logger.debug(f"Processing product {idx}/{len(products)}: Odoo ID={product.id}, Name='{product.name}', BC ID={product.bigcommerce_id or 'New'}")
            
            try:
                # Determine inventory_tracking value based on product configuration
                # BigCommerce accepts: "none", "product", or "variant"
                if product.product_variant_ids and len(product.product_variant_ids) > 1:
                    # Product has variants - track inventory at variant level
                    inventory_tracking = 'variant'
                elif hasattr(product, 'is_storable') and product.is_storable and product.tracking != 'none':
                    # Product has "Track Inventory" enabled and tracking is set - track at product level
                    inventory_tracking = 'product'
                elif hasattr(product, 'is_storable') and product.is_storable:
                    # Product has "Track Inventory" enabled but no tracking method set - track at product level
                    inventory_tracking = 'product'
                else:
                    # No inventory tracking
                    inventory_tracking = 'none'
                
                product_data = {
                    'name': product.name,
                    'type': 'physical',
                    'price': str(product.list_price),
                    'weight': product.weight or 0,
                    'depth': product.product_length or 0,  # BigCommerce uses 'depth' for length
                    'width': product.product_width or 0,
                    'height': product.product_height or 0,
                    'inventory_tracking': inventory_tracking,
                }
                
                if product.default_code:
                    product_data['sku'] = product.default_code
                
                if product.product_description or product.description:
                    product_data['description'] = product.product_description or product.description
                
                _logger.debug(f"Product data prepared for BigCommerce: {product_data}")
                
                if product.bigcommerce_id:
                    # Update existing product
                    _logger.info(f"Updating product in BigCommerce: BC ID={product.bigcommerce_id}, Odoo ID={product.id}")
                    api.update_product(product.bigcommerce_id, product_data)
                    self.products_updated += 1
                    _logger.info(f"Successfully updated product in BigCommerce: BC ID={product.bigcommerce_id}")
                else:
                    # Create new product
                    # BigCommerce API requires option_values field when creating products
                    product_data['option_values'] = []
                    _logger.info(f"Creating new product in BigCommerce for Odoo ID={product.id}")
                    _logger.debug(f"Product data with option_values: {product_data}")
                    bc_product = api.create_product(product_data)
                    bc_id = bc_product.get('id')
                    product.write({
                        'bigcommerce_id': bc_id,
                        'bigcommerce_synced': True,
                        'bigcommerce_last_sync': fields.Datetime.now(),
                    })
                    self.products_created += 1
                    _logger.info(f"Successfully created product in BigCommerce: BC ID={bc_id}, Odoo ID={product.id}")
                    
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                _logger.error(f"Error syncing product Odoo ID={product.id}, Name='{product.name}' to BigCommerce: {str(e)}", exc_info=True)
                self._create_log(
                    'error',
                    f"Error syncing product to BigCommerce: {str(e)}",
                    product_id=product.bigcommerce_id,
                    product_name=product.name,
                    error_details=error_trace,
                    response_data=str(product_data) if 'product_data' in locals() else None
                )
                self.products_failed += 1
    
    def _sync_product_images(self, product_template, bc_product, api, image_downloader=None):
        """Sync product images from BigCommerce to Odoo
        
        For products with variants, only set template image if no variants have images.
        For products without variants, set the template image as normal.
        
        OPTIMIZATION: Accepts optional image_downloader for async downloading.
        """
        try:
            # Check if product has variants - if so, don't set template image
            # Each variant should have its own image synced separately
            has_variants = product_template.product_variant_ids and len(product_template.product_variant_ids) > 1
            
            if has_variants:
                _logger.debug(f"Product {product_template.name} has variants - skipping template image sync. Each variant will have its own image.")
                return
            
            # For products without variants, sync the template image as normal
            # Get images from BigCommerce product
            # Images can be in 'images' field (when included) or 'primary_image' field
            images = []
            
            # Check if images are included in the product response
            if 'images' in bc_product and bc_product['images']:
                images = bc_product['images']
                if isinstance(images, dict) and 'data' in images:
                    images = images['data']
            elif 'primary_image' in bc_product and bc_product['primary_image']:
                # If only primary image is available, use it
                primary_image = bc_product['primary_image']
                if isinstance(primary_image, dict):
                    images = [primary_image]
            
            if not images:
                _logger.debug(f"No images found for product {product_template.name} (BC ID: {bc_product.get('id')})")
                return
            
            # OPTIMIZATION: Reduced logging for performance
            # _logger.info(f"Found {len(images)} image(s) for product {product_template.name} (BC ID: {bc_product.get('id')})")
            
            # Process images - use primary image first, then others
            # Sort images by is_thumbnail or image_url to prioritize primary
            sorted_images = sorted(images, key=lambda x: (
                0 if x.get('is_thumbnail') or x.get('is_primary') else 1,
                x.get('sort_order', 999)
            ))
            
            # Get the primary image (first in sorted list)
            primary_bc_image = sorted_images[0] if sorted_images else None
            if not primary_bc_image:
                return
            
            image_url = primary_bc_image.get('url_standard') or primary_bc_image.get('url_thumbnail') or primary_bc_image.get('url_tiny') or primary_bc_image.get('url')
            if not image_url:
                _logger.warning(f"Primary image for product {product_template.name} has no URL")
                return
            
            # OPTIMIZATION: Use async downloader utility function
            url, image_base64, error = download_image_async(image_url, timeout=30)
            
            if error:
                _logger.warning(f"Error downloading image from {image_url}: {error}")
                return
            
            if not image_base64:
                _logger.warning(f"Downloaded image from {image_url} is empty")
                return
            
            # Set as product primary image (only for products without variants)
            product_template.write({'image_1920': image_base64})
            _logger.debug(f"Set primary image for product {product_template.name}")
            
        except Exception as e:
            _logger.error(f"Error syncing images for product {product_template.name}: {str(e)}", exc_info=True)
            # Don't raise - image sync failure shouldn't fail the entire product sync
    
    def _sync_variant_image(self, product_variant, bc_variant, api_client=None, bc_product_id=None, product_images=None):
        """Sync variant image from BigCommerce to Odoo
        
        According to BigCommerce API v3 documentation, variant images are retrieved via:
        GET /v3/catalog/products/{product_id}/variants/{variant_id}/image
        
        This endpoint returns the image details including image_url and image_id.
        If a variant doesn't have an image, explicitly clear it to prevent inheriting template image.
        
        Args:
            product_variant: Odoo product variant (product.product)
            bc_variant: BigCommerce variant data dictionary
            api_client: BigCommerce API client (required for proper variant image retrieval)
            bc_product_id: BigCommerce product ID (required for proper variant image retrieval)
            product_images: Optional list of product images (fallback method if API endpoint unavailable)
        """
        try:
            image_url = None
            bc_variant_id = bc_variant.get('id')
            bc_variant_sku = bc_variant.get('sku', 'N/A')
            
            _logger.info(f"Syncing image for variant: Odoo Variant='{product_variant.name}' (ID={product_variant.id}), BC Variant ID={bc_variant_id}, BC SKU={bc_variant_sku}")
            _logger.info(f"  BC Variant data keys: {list(bc_variant.keys())}")
            _logger.info(f"  BC Variant image_id: {bc_variant.get('image_id', 'Not present')}")
            _logger.info(f"  BC Variant image_url: {bc_variant.get('image_url', 'Not present')}")
            _logger.info(f"  Product images count: {len(product_images) if product_images else 0}")
            
            # Method 1: Check for image_id in variant data FIRST (this is the most reliable)
            # BigCommerce variants in the GET /variants response include image_id field
            image_id = bc_variant.get('image_id')
            _logger.info(f"  Checking image_id method: image_id={image_id}, product_images={'present' if product_images else 'None/empty'}")
            
            if image_id is not None and product_images:
                _logger.info(f"  Variant {bc_variant_id} has image_id={image_id} (type: {type(image_id).__name__}), searching {len(product_images)} product images...")
                # Find the image with matching image_id in the product images array
                # Handle type mismatch (int vs string) by comparing both as strings and as their native types
                image_id_str = str(image_id)
                try:
                    image_id_int = int(image_id) if not isinstance(image_id, int) else image_id
                except (ValueError, TypeError):
                    image_id_int = None
                
                for img in product_images:
                    img_id = img.get('id')
                    # Compare with multiple methods to handle type mismatches
                    # Try direct comparison first (fastest), then string comparison, then int comparison
                    if (img_id == image_id or 
                        str(img_id) == image_id_str or 
                        (image_id_int is not None and isinstance(img_id, int) and img_id == image_id_int) or
                        (isinstance(image_id, int) and isinstance(img_id, str) and img_id.isdigit() and int(img_id) == image_id)):
                        # Found the variant's image - use the best quality URL available
                        image_url = (img.get('url_standard') or 
                                   img.get('url_thumbnail') or 
                                   img.get('url_tiny') or 
                                   img.get('url'))
                        if image_url:
                            _logger.info(f"✓ Found variant image via image_id {image_id} (matched img_id={img_id}, type: {type(img_id).__name__}) for variant {bc_variant_id} (SKU: {bc_variant_sku}): {image_url}")
                            break
                if not image_url:
                    _logger.warning(f"  image_id {image_id} found in variant data but no matching image in product images array (variant {bc_variant_id})")
                    _logger.info(f"  Available image IDs in product_images: {[img.get('id') for img in product_images]} (types: {[type(img.get('id')).__name__ for img in product_images]})")
            elif image_id is not None:
                _logger.warning(f"  Variant {bc_variant_id} has image_id {image_id} but product_images array is empty or None")
            else:
                _logger.info(f"  Variant {bc_variant_id} does not have image_id field - will try other methods")
            
            # Method 2: Check image_url/image in variant data before calling API (avoids 405 when BC already provides URL)
            if not image_url:
                if 'image_url' in bc_variant and bc_variant['image_url']:
                    image_url = bc_variant['image_url']
                    _logger.info(f"✓ Found variant image via image_url field for variant {bc_variant_id} (SKU: {bc_variant_sku}): {image_url}")
                # Check for image field (could be a URL string or object)
                elif 'image' in bc_variant:
                    image_data = bc_variant['image']
                    if isinstance(image_data, str):
                        image_url = image_data
                        _logger.info(f"✓ Found variant image via image string field for variant {bc_variant_id} (SKU: {bc_variant_sku}): {image_url}")
                    elif isinstance(image_data, dict):
                        image_url = image_data.get('url') or image_data.get('url_standard') or image_data.get('url_thumbnail')
                        if image_url:
                            _logger.info(f"✓ Found variant image via image dict field for variant {bc_variant_id} (SKU: {bc_variant_sku}): {image_url}")
            
            # Method 3: Use the dedicated variant image endpoint only if variant data had no image_url
            # GET /v3/catalog/products/{product_id}/variants/{variant_id}/image (may return 405 on some BC versions)
            if not image_url and api_client and bc_product_id and bc_variant_id:
                try:
                    _logger.debug(f"Attempting to fetch variant image via dedicated endpoint: product_id={bc_product_id}, variant_id={bc_variant_id}")
                    variant_image_data = api_client.get_variant_image(bc_product_id, bc_variant_id)
                    if variant_image_data:
                        image_url = variant_image_data.get('image_url')
                        if image_url:
                            _logger.info(f"✓ Found variant image via dedicated endpoint for variant {bc_variant_id} (SKU: {bc_variant_sku}): {image_url}")
                except Exception as endpoint_error:
                    _logger.debug(f"Could not fetch variant image via endpoint for variant {bc_variant_id}: {str(endpoint_error)}")
            
            if not image_url:
                # Variant doesn't have an image in BigCommerce - explicitly clear it
                # This prevents the variant from inheriting the template image
                _logger.info(f"✗ No image found for variant '{product_variant.name}' (BC Variant ID: {bc_variant_id}, SKU: {bc_variant_sku}) - clearing variant image")
                product_variant.write({'image_1920': False})
                return
            
            # Download image
            _logger.info(f"Downloading variant image from {image_url} for variant {bc_variant_id} (SKU: {bc_variant_sku})")
            response = requests.get(image_url, timeout=30, stream=True)
            response.raise_for_status()
            
            # Read image data
            image_data = response.content
            if not image_data:
                _logger.warning(f"Downloaded variant image from {image_url} is empty - clearing variant image")
                product_variant.write({'image_1920': False})
                return
            
            # Encode to base64 for Odoo
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # Set as variant image (product.product has image_1920 field)
            product_variant.write({'image_1920': image_base64})
            _logger.info(f"✓ Successfully set image for variant '{product_variant.name}' (Odoo ID: {product_variant.id}, BC Variant ID: {bc_variant_id}, SKU: {bc_variant_sku}) from {image_url}")
            
        except Exception as e:
            _logger.error(f"Error syncing variant image for {product_variant.name} (BC Variant ID: {bc_variant.get('id')}): {str(e)}", exc_info=True)
            # Don't raise - image sync failure shouldn't fail the entire variant sync

