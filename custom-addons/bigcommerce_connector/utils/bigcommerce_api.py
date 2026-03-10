# -*- coding: utf-8 -*-

import requests
import logging
import time
from odoo import exceptions

_logger = logging.getLogger(__name__)

# Transient errors that are worth retrying (SSL/connection drops during long syncs)
REQUEST_RETRY_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class BigCommerceAPI:
    """BigCommerce API Client (V3)"""
    
    def __init__(self, store_hash, access_token, api_version='v3'):
        """
        Initialize BigCommerce API client
        
        :param store_hash: BigCommerce store hash
        :param access_token: BigCommerce API access token
        :param api_version: API version to use ('v2' or 'v3'), defaults to 'v3'
        """
        self.store_hash = store_hash
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://api.bigcommerce.com/stores/{store_hash}/{api_version}"
        self.headers = {
            'X-Auth-Token': access_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def _make_request(self, method, endpoint, data=None, params=None, max_retries=3):
        """
        Make API request to BigCommerce with rate limit handling
        
        :param method: HTTP method (GET, POST, PUT, DELETE)
        :param endpoint: API endpoint
        :param data: Request body data
        :param params: Query parameters
        :param max_retries: Maximum number of retries for rate limit errors (429)
        :return: Response data
        """
        url = f"{self.base_url}/{endpoint}"
        
        _logger.debug(f"BigCommerce API Request: {method} {url}")
        if params:
            _logger.debug(f"Request params: {params}")
        if data:
            _logger.debug(f"Request data: {data}")
        
        retry_count = 0
        while retry_count <= max_retries:
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=data,
                    params=params,
                    timeout=60  # Increased timeout for variant/attribute API calls
                )
                
                _logger.debug(f"Response status: {response.status_code}")
                _logger.debug(f"Response headers: {dict(response.headers)}")
                
                # Handle rate limiting (429 Too Many Requests)
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                            _logger.warning(f"Rate limit hit (429). Waiting {wait_time} seconds as specified by Retry-After header...")
                            time.sleep(wait_time)
                            retry_count += 1
                            continue  # Retry the request
                        except ValueError:
                            _logger.warning(f"Invalid Retry-After header value: {retry_after}")
                    
                    # If no Retry-After header or invalid, use exponential backoff
                    if retry_count < max_retries:
                        wait_time = (2 ** retry_count) * 1  # Exponential backoff: 1s, 2s, 4s
                        _logger.warning(f"Rate limit hit (429). Waiting {wait_time} seconds (retry {retry_count + 1}/{max_retries})...")
                        time.sleep(wait_time)
                        retry_count += 1
                        continue  # Retry the request
                    else:
                        error_msg = f"Rate limit exceeded after {max_retries} retries. Please wait before making more requests."
                        _logger.error(error_msg)
                        raise exceptions.UserError(error_msg)
                
                response.raise_for_status()
                
                if response.status_code == 204:  # No content
                    _logger.debug("Response: No content (204)")
                    return {}
                
                # Check content type
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    _logger.warning(f"Unexpected content type: {content_type}. Response text: {response.text[:200]}")
                
                try:
                    result = response.json()
                except ValueError as json_error:
                    _logger.error(f"Failed to parse JSON response: {json_error}")
                    _logger.error(f"Response text (first 500 chars): {response.text[:500]}")
                    raise exceptions.UserError(f"Invalid JSON response from BigCommerce API: {str(json_error)}")
                
                # Check for pagination metadata in response headers (V3 API)
                total_count = None
                if 'X-Total-Count' in response.headers:
                    try:
                        total_count = int(response.headers['X-Total-Count'])
                        _logger.info(f"Found total count in X-Total-Count header: {total_count}")
                    except (ValueError, TypeError):
                        pass
                
                # Also check if result is a dict with meta/data structure (V3 API)
                if isinstance(result, dict):
                    if 'meta' in result and 'pagination' in result['meta']:
                        pagination = result['meta']['pagination']
                        if 'total' in pagination:
                            total_count = pagination['total']
                            _logger.info(f"Found total count in meta.pagination.total: {total_count}")
                
                # Store total count in result if found
                if total_count is not None and isinstance(result, dict):
                    result['_total_count'] = total_count
                
                _logger.debug(f"Response data type: {type(result)}, length: {len(result) if isinstance(result, (list, dict)) else 'N/A'}")
                if isinstance(result, (list, dict)):
                    data_to_check = result.get('data', result) if isinstance(result, dict) else result
                    if isinstance(data_to_check, list) and len(data_to_check) > 0:
                        _logger.debug(f"First item sample type: {type(data_to_check[0])}, value: {data_to_check[0] if data_to_check else 'Empty'}")
                        if isinstance(data_to_check[0], dict):
                            _logger.debug(f"First item keys: {list(data_to_check[0].keys())[:10] if data_to_check[0] else 'Empty'}")
                
                return result
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code
                
                # For 404 errors (Not Found), return None instead of raising
                # This allows callers to handle missing resources gracefully
                if status_code == 404:
                    _logger.debug(f"Resource not found (404) for {method} {url}")
                    return None
                
                # For 403 errors (Forbidden/Authentication Required), provide detailed error message
                # According to BigCommerce API docs, 403 means:
                # - App lacks required OAuth scopes
                # - Store-owner account changed
                # - Operations exceed platform limits
                # - URL requested is incorrect
                if status_code == 403:
                    try:
                        error_data = e.response.json()
                        error_title = error_data.get('title', 'Authentication Required')
                        error_type = error_data.get('type', '')
                    except:
                        error_title = 'Authentication Required'
                        error_type = ''
                    
                    error_msg = f"BigCommerce API Error: {status_code} - {error_title}"
                    if error_type:
                        error_msg += f" ({error_type})"
                    
                    # Determine which operation is being performed based on endpoint
                    operation = "this operation"
                    if 'orders' in endpoint.lower():
                        operation = "order operations"
                    elif 'products' in endpoint.lower():
                        operation = "product operations"
                    elif 'customers' in endpoint.lower():
                        operation = "customer operations"
                    elif 'inventory' in endpoint.lower():
                        operation = "inventory operations"
                    
                    detailed_msg = (
                        f"{error_msg}\n\n"
                        f"This error typically means:\n"
                        f"1. Your API token is missing required OAuth scopes for {operation}\n"
                        f"2. The store owner account has changed\n"
                        f"3. The API token credentials are invalid or expired\n\n"
                        f"To fix this:\n"
                        f"- Check your OAuth scopes in BigCommerce Control Panel > API Accounts\n"
                    )
                    
                    # Add specific scope requirements based on operation
                    if 'orders' in endpoint.lower():
                        detailed_msg += f"- Ensure your API token has 'store_orders_read_only' or 'store_orders' scope\n"
                    elif 'products' in endpoint.lower():
                        detailed_msg += f"- Ensure your API token has 'store_catalog_read_only' or 'store_catalog' scope\n"
                    elif 'customers' in endpoint.lower():
                        detailed_msg += f"- Ensure your API token has 'store_customers_read_only' or 'store_customers' scope\n"
                    elif 'inventory' in endpoint.lower():
                        detailed_msg += f"- Ensure your API token has 'store_inventory_read_only' or 'store_inventory' scope\n"
                    
                    detailed_msg += (
                        f"- Verify your API token is still valid and hasn't been revoked\n"
                        f"- Check the BigCommerce API documentation: https://developer.bigcommerce.com/api-docs/getting-started/api-status-codes"
                    )
                    
                    _logger.error(detailed_msg)
                    _logger.error(f"Request URL: {url}")
                    _logger.error(f"Request method: {method}")
                    if params:
                        _logger.error(f"Request params: {params}")
                    if data:
                        _logger.error(f"Request data: {data}")
                    
                    # Raise UserError with detailed message for 403
                    raise exceptions.UserError(detailed_msg)
                
                # 405 Method Not Allowed - some endpoints (e.g. variant image) are not available on all BC versions; log at debug
                if status_code == 405:
                    _logger.debug(f"BigCommerce API 405 Method Not Allowed for {method} {url}: {e.response.text[:200]}")
                    raise
                # For other HTTP errors, log at error level and raise
                error_msg = f"BigCommerce API Error: {status_code} - {e.response.text}"
                _logger.error(error_msg)
                _logger.error(f"Request URL: {url}")
                _logger.error(f"Request method: {method}")
                if params:
                    _logger.error(f"Request params: {params}")
                if data:
                    _logger.error(f"Request data: {data}")
                
                # Raise UserError for non-404/403 errors
                raise exceptions.UserError(error_msg)
            except REQUEST_RETRY_EXCEPTIONS as e:
                # Transient SSL/connection errors during long syncs - retry with backoff
                error_msg = f"BigCommerce Connection Error: {str(e)}"
                _logger.warning(error_msg)
                _logger.warning(f"Request URL: {url} (retry {retry_count + 1}/{max_retries})")
                if retry_count < max_retries:
                    wait_time = (2 ** retry_count) * 5  # 5s, 10s, 20s backoff
                    _logger.warning(
                        "Transient connection/SSL error. Retrying in %d seconds... "
                        "This can happen during long syncs when the server closes the connection.",
                        wait_time
                    )
                    time.sleep(wait_time)
                    retry_count += 1
                    continue
                _logger.error(error_msg)
                _logger.error(f"Request URL: {url}")
                _logger.error(f"Request method: {method}")
                raise exceptions.UserError(
                    f"{error_msg} (failed after {max_retries} retries). "
                    "Check network stability and server logs, then try the sync again."
                )
            except requests.exceptions.RequestException as e:
                error_msg = f"BigCommerce Connection Error: {str(e)}"
                _logger.error(error_msg)
                _logger.error(f"Request URL: {url}")
                _logger.error(f"Request method: {method}")
                raise exceptions.UserError(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error in BigCommerce API request: {str(e)}"
                _logger.error(error_msg, exc_info=True)
                raise exceptions.UserError(error_msg)
    
    # Products API
    def get_products(self, page=1, limit=250, include_images=False, include_variants=False, include_options=False, **filters):
        """Get products from BigCommerce
        
        Args:
            include_images: If True, include images in the response (V3 API only)
            include_variants: If True, include variants in the response (V3 API only)
            include_options: If True, include options/attributes in the response (V3 API only)
        
        Returns:
            - If V3 API: dict with 'data' (list of products) and '_total_count' (if available)
            - If V2 API: list of products
            
        Note: Using include_variants and include_options significantly reduces API calls
        by fetching all data in a single request instead of N+1 requests.
        """
        if self.api_version == 'v3':
            # V3 uses catalog/products endpoint
            # V3 API returns data in a different format: {'data': [...], 'meta': {...}}
            params = {'page': page, 'limit': limit}
            # V3 API supports 'include' parameter to include related data
            # This is a MAJOR optimization - fetching variants/options inline avoids N+1 API calls
            include_parts = []
            if include_images:
                include_parts.append('images')
            if include_variants:
                include_parts.append('variants')
            if include_options:
                include_parts.append('options')
            if include_parts:
                params['include'] = ','.join(include_parts)
            params.update(filters)
            result = self._make_request('GET', 'catalog/products', params=params)
            # Return the full result dict (includes _total_count if available)
            return result
        else:
            # V2 uses products endpoint
            params = {'page': page, 'limit': limit}
            params.update(filters)
            result = self._make_request('GET', 'products', params=params)
            return result if isinstance(result, list) else []
    
    def get_product(self, product_id, include_images=False):
        """Get a single product by ID
        
        Args:
            product_id: BigCommerce product ID
            include_images: If True, include images in the response (V3 API only)
        
        Returns:
            - Product dictionary with product data
            - None if product not found
        """
        try:
            if self.api_version == 'v3':
                params = {}
                if include_images:
                    params['include'] = 'images'
                if params:
                    result = self._make_request('GET', f'catalog/products/{product_id}', params=params)
                else:
                    result = self._make_request('GET', f'catalog/products/{product_id}')
                # V3 API might wrap single product in 'data' key, or return it directly
                if isinstance(result, dict):
                    # Check if wrapped in 'data' key
                    if 'data' in result:
                        return result['data']
                    # Check if it's the product itself (has 'id' field)
                    elif 'id' in result:
                        return result
                    # Otherwise return as-is
                    return result
                return result
            else:
                return self._make_request('GET', f'products/{product_id}')
        except exceptions.UserError as e:
            # If product not found (404), return None instead of raising
            if '404' in str(e) or 'not found' in str(e).lower():
                _logger.warning(f"Product {product_id} not found in BigCommerce")
                return None
            raise
    
    def create_product(self, product_data):
        """Create a product in BigCommerce"""
        if self.api_version == 'v3':
            return self._make_request('POST', 'catalog/products', data=product_data)
        else:
            return self._make_request('POST', 'products', data=product_data)
    
    def update_product(self, product_id, product_data):
        """Update a product in BigCommerce"""
        if self.api_version == 'v3':
            return self._make_request('PUT', f'catalog/products/{product_id}', data=product_data)
        else:
            return self._make_request('PUT', f'products/{product_id}', data=product_data)
    
    def delete_product(self, product_id):
        """Delete a product from BigCommerce"""
        if self.api_version == 'v3':
            return self._make_request('DELETE', f'catalog/products/{product_id}')
        else:
            return self._make_request('DELETE', f'products/{product_id}')
    
    def search_variants_by_sku(self, sku):
        """Search across ALL variants in the catalog by SKU using GET /catalog/variants.

        This is dramatically faster than paginating through every product when
        looking for a variant-level SKU.  The endpoint supports a ``sku`` query
        parameter.  Returns a list of variant dicts, each including
        ``product_id``.

        Falls back to an empty list on 404 / unsupported API version.
        """
        if self.api_version != 'v3':
            _logger.debug("search_variants_by_sku requires V3 API; returning empty list")
            return []
        try:
            # BigCommerce V3: GET /catalog/variants?sku=<value>
            # The sku filter is exact-match and case-insensitive on most stores
            result = self._make_request('GET', 'catalog/variants', params={'sku': sku, 'limit': 10})
            if isinstance(result, dict) and 'data' in result:
                variants = result['data']
                if isinstance(variants, list):
                    _logger.debug(f"search_variants_by_sku('{sku}'): found {len(variants)} variant(s)")
                    return variants
            return result if isinstance(result, list) else []
        except Exception as e:
            _logger.debug(f"search_variants_by_sku('{sku}') failed: {e}")
            return []

    def get_product_variants(self, product_id):
        """Get variants for a product"""
        _logger.info(f"Fetching variants for product ID: {product_id}")
        if self.api_version == 'v3':
            result = self._make_request('GET', f'catalog/products/{product_id}/variants')
            # V3 API returns {'data': [...], 'meta': {...}}
            if isinstance(result, dict) and 'data' in result:
                variants = result['data']
                _logger.debug(f"Variants API response (V3) - Type: {type(variants)}, Length: {len(variants) if isinstance(variants, list) else 'N/A'}")
                if variants and isinstance(variants, list) and len(variants) > 0:
                    _logger.debug(f"First variant sample: {variants[0]}")
                return variants
            return result if isinstance(result, list) else []
        else:
            result = self._make_request('GET', f'products/{product_id}/variants')
            _logger.debug(f"Variants API response (V2) - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
            if result and isinstance(result, list) and len(result) > 0:
                _logger.debug(f"First variant sample: {result[0]}")
            return result
    
    def get_product_variant(self, product_id, variant_id):
        """Get a single variant by ID"""
        if self.api_version == 'v3':
            return self._make_request('GET', f'catalog/products/{product_id}/variants/{variant_id}')
        else:
            return self._make_request('GET', f'products/{product_id}/variants/{variant_id}')
    
    def create_product_variant(self, product_id, variant_data):
        """Create a variant for a product"""
        if self.api_version == 'v3':
            return self._make_request('POST', f'catalog/products/{product_id}/variants', data=variant_data)
        else:
            return self._make_request('POST', f'products/{product_id}/variants', data=variant_data)
    
    def update_product_variant(self, product_id, variant_id, variant_data):
        """Update a variant for a product"""
        if self.api_version == 'v3':
            return self._make_request('PUT', f'catalog/products/{product_id}/variants/{variant_id}', data=variant_data)
        else:
            return self._make_request('PUT', f'products/{product_id}/variants/{variant_id}', data=variant_data)
    
    def get_variant_image(self, product_id, variant_id):
        """Get the image associated with a product variant
        
        According to BigCommerce API v3, variants can have one image associated with them.
        This endpoint returns the image details including image_url and image_id.
        
        Returns:
            - Image data dictionary with 'image_url' and 'image_id' if variant has an image
            - None if variant doesn't have an image (404 response)
        """
        if self.api_version == 'v3':
            try:
                _logger.debug(f"Fetching variant image: GET /v3/catalog/products/{product_id}/variants/{variant_id}/image")
                result = self._make_request('GET', f'catalog/products/{product_id}/variants/{variant_id}/image')
                # V3 API returns {'data': {...}, 'meta': {...}}
                if isinstance(result, dict) and 'data' in result:
                    image_data = result['data']
                    _logger.debug(f"Variant image endpoint returned data: {image_data}")
                    return image_data
                _logger.debug(f"Variant image endpoint returned: {result}")
                return result
            except (exceptions.UserError, requests.HTTPError) as e:
                # 404 means variant doesn't have an image - this is normal, not an error
                # 405 Method Not Allowed - this endpoint is not available on some BigCommerce versions; caller will use image_url fallback
                error_str = str(e).lower()
                if hasattr(e, 'response') and e.response is not None:
                    if e.response.status_code == 404:
                        _logger.debug(f"Variant {variant_id} of product {product_id} does not have an associated image (404)")
                        return None
                    if e.response.status_code == 405:
                        _logger.debug(f"Variant image endpoint not available (405) for variant {variant_id} of product {product_id}; use image_url in variant data")
                        return None
                elif '404' in error_str or 'not found' in error_str:
                    _logger.debug(f"Variant {variant_id} of product {product_id} does not have an associated image")
                    return None
                # Re-raise if it's a different error
                _logger.warning(f"Error fetching variant image for variant {variant_id} of product {product_id}: {str(e)}")
                raise
            except Exception as e:
                # Catch any other exceptions and log, but don't fail the sync
                _logger.warning(f"Unexpected error fetching variant image for variant {variant_id} of product {product_id}: {str(e)}", exc_info=True)
                return None
        else:
            # V2 API doesn't have a dedicated variant image endpoint
            return None
    
    def get_product_options(self, product_id):
        """Get options (attributes) for a product"""
        _logger.debug(f"Fetching options for product ID: {product_id}")
        if self.api_version == 'v3':
            # V3 uses catalog/products/{id}/options
            try:
                result = self._make_request('GET', f'catalog/products/{product_id}/options')
                # V3 API returns {'data': [...], 'meta': {...}}
                if isinstance(result, dict) and 'data' in result:
                    options = result['data']
                    _logger.debug(f"Options API response (V3) - Type: {type(options)}, Length: {len(options) if isinstance(options, list) else 'N/A'}")
                    if options and isinstance(options, list) and len(options) > 0:
                        _logger.debug(f"First option sample: {options[0]}")
                    return options
                _logger.debug(f"Options API response (V3) - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
                if result and isinstance(result, list) and len(result) > 0:
                    _logger.debug(f"First option sample: {result[0]}")
                return result if isinstance(result, list) else []
            except Exception as e:
                _logger.error(f"Failed to get options: {str(e)}", exc_info=True)
                return []
        else:
            # V2 uses products/{id}/options
            try:
                result = self._make_request('GET', f'products/{product_id}/options')
                _logger.info(f"Options API response - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
                if result and isinstance(result, list) and len(result) > 0:
                    _logger.info(f"First option sample: {result[0]}")
                return result
            except Exception as e:
                _logger.warning(f"Failed to get options from products/{product_id}/options, trying alternative: {str(e)}", exc_info=True)
                try:
                    result = self._make_request('GET', f'catalog/products/{product_id}/options')
                    _logger.info(f"Options API response (alternative) - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
                    return result
                except Exception as e2:
                    _logger.error(f"Failed to get options from alternative endpoint: {str(e2)}", exc_info=True)
                    return []
    
    def get_product_option_values(self, product_id, option_id):
        """Get option values for a product option"""
        _logger.debug(f"Fetching option values for product ID: {product_id}, option ID: {option_id}")
        if self.api_version == 'v3':
            # V3 uses catalog/products/{id}/options/{option_id}/values
            try:
                result = self._make_request('GET', f'catalog/products/{product_id}/options/{option_id}/values')
                # V3 API returns {'data': [...], 'meta': {...}}
                if isinstance(result, dict) and 'data' in result:
                    values = result['data']
                    _logger.info(f"Option values API response (V3) - Type: {type(values)}, Length: {len(values) if isinstance(values, list) else 'N/A'}")
                    if values and isinstance(values, list) and len(values) > 0:
                        _logger.info(f"First option value sample: {values[0]}")
                    return values
                _logger.info(f"Option values API response (V3) - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
                if result and isinstance(result, list) and len(result) > 0:
                    _logger.info(f"First option value sample: {result[0]}")
                return result if isinstance(result, list) else []
            except Exception as e:
                _logger.error(f"Failed to get option values: {str(e)}", exc_info=True)
                return []
        else:
            # V2 uses products/{id}/options/{option_id}/values
            try:
                result = self._make_request('GET', f'products/{product_id}/options/{option_id}/values')
                _logger.info(f"Option values API response - Type: {type(result)}, Length: {len(result) if isinstance(result, list) else 'N/A'}")
                if result and isinstance(result, list) and len(result) > 0:
                    _logger.info(f"First option value sample: {result[0]}")
                return result
            except Exception as e:
                _logger.warning(f"Failed to get option values, trying alternative: {str(e)}", exc_info=True)
                try:
                    return self._make_request('GET', f'catalog/products/{product_id}/options/{option_id}/values')
                except Exception as e2:
                    _logger.error(f"Failed to get option values from alternative endpoint: {str(e2)}")
                    return []
    
    # Orders API
    def get_orders(self, page=1, limit=250, **filters):
        """Get orders from BigCommerce
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        
        Returns:
            - List of orders if successful
            - None if 404 (not found) or 403 (permission denied)
            - Raises UserError for other errors
        """
        params = {'page': page, 'limit': limit}
        params.update(filters)
        
        # Orders API is only available in V2, so we need to use V2 endpoint
        # Temporarily switch to V2 for this request
        original_api_version = self.api_version
        original_base_url = self.base_url
        
        try:
            # Use V2 API for orders
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', 'orders', params=params)
        finally:
            # Restore original API version
            self.api_version = original_api_version
            self.base_url = original_base_url
        
        # Handle None (404 or 403)
        if result is None:
            return None
        
        # V2 API returns a list directly
        return result if isinstance(result, list) else []
    
    def get_order(self, order_id):
        """Get a single order by ID
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', f'orders/{order_id}')
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result
    
    def update_order(self, order_id, order_data):
        """Update an order in BigCommerce
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('PUT', f'orders/{order_id}', data=order_data)
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result
    
    def get_order_products(self, order_id):
        """Get order products for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', f'orders/{order_id}/products')
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result if isinstance(result, list) else []
    
    def get_order_payments(self, order_id):
        """Get payment information for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', f'orders/{order_id}/payment_events')
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result if isinstance(result, list) else []
    
    def get_order_shipments(self, order_id):
        """Get shipments for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', f'orders/{order_id}/shipments')
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result if isinstance(result, list) else []
    
    def get_order_shipping_addresses(self, order_id):
        """Get shipping addresses for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        
        Returns:
            List of shipping address dictionaries, each containing an 'id' field (order_address_id)
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('GET', f'orders/{order_id}/shipping_addresses')
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result if isinstance(result, list) else []
    
    def create_order_shipment(self, order_id, shipment_data):
        """Create a shipment for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('POST', f'orders/{order_id}/shipments', data=shipment_data)
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result
    
    def update_order_shipment(self, order_id, shipment_id, shipment_data):
        """Update a shipment for an order
        
        Note: Orders API is only available in V2, not V3.
        This method automatically uses V2 API regardless of the default api_version setting.
        """
        # Orders API is only available in V2
        original_api_version = self.api_version
        original_base_url = self.base_url
        try:
            self.api_version = 'v2'
            self.base_url = f"https://api.bigcommerce.com/stores/{self.store_hash}/v2"
            result = self._make_request('PUT', f'orders/{order_id}/shipments/{shipment_id}', data=shipment_data)
        finally:
            self.api_version = original_api_version
            self.base_url = original_base_url
        return result
    
    # Inventory API
    def get_product_inventory(self, product_id):
        """Get inventory for a product
        
        Note: In V3 API, inventory is managed through variants or inventory locations.
        This method tries multiple approaches:
        1. Try catalog/products/{id}/inventory (if available)
        2. Fall back to getting product data which includes inventory info
        3. For V2, use products/{id}/inventory
        """
        if self.api_version == 'v3':
            # Try the inventory endpoint first
            result = self._make_request('GET', f'catalog/products/{product_id}/inventory')
            
            # If 404, try getting product data which may include inventory
            if result is None:
                _logger.debug(f"Inventory endpoint returned 404 for product {product_id}, trying product data...")
                # Get product data - inventory might be in the product or variant data
                product_data = self.get_product(product_id)
                if product_data:
                    # Check if inventory info is in product data
                    if 'inventory_tracking' in product_data or 'inventory_level' in product_data:
                        return {
                            'inventory_level': product_data.get('inventory_level', 0),
                            'inventory_warning_level': product_data.get('inventory_warning_level', 0),
                            'inventory_tracking': product_data.get('inventory_tracking', 'none'),
                        }
                    # If product has variants, we might need to get variant inventory
                    # For now, return None to indicate inventory not found
                return None
            
            # V3 API returns {'data': {...}}
            if isinstance(result, dict) and 'data' in result:
                return result['data']
            return result
        else:
            return self._make_request('GET', f'products/{product_id}/inventory')
    
    def update_product_inventory(self, product_id, inventory_data):
        """Update inventory for a product
        
        Note: BigCommerce API updates product inventory by updating the product itself
        with the inventory_level field, not through a separate inventory endpoint.
        For products with variants, inventory should be tracked at the variant level.
        """
        # Update the product with inventory data included
        # According to BigCommerce API docs, inventory_level is a field on the product
        product_data = {
            'inventory_level': inventory_data.get('inventory_level', 0),
            'inventory_warning_level': inventory_data.get('inventory_warning_level', 0),
        }
        
        # Include inventory_tracking if provided (for products without variants)
        if 'inventory_tracking' in inventory_data:
            product_data['inventory_tracking'] = inventory_data['inventory_tracking']
        
        # Use the standard product update endpoint
        return self.update_product(product_id, product_data)
    
    def get_variant_inventory(self, product_id, variant_id):
        """Get inventory for a specific product variant
        
        Note: BigCommerce V3 API uses variant-level inventory endpoints.
        """
        if self.api_version == 'v3':
            # V3 API: catalog/products/{product_id}/variants/{variant_id}/inventory
            result = self._make_request('GET', f'catalog/products/{product_id}/variants/{variant_id}/inventory')
            
            if result is None:
                _logger.debug(f"Variant inventory endpoint returned 404 for product {product_id}, variant {variant_id}")
                return None
            
            # V3 API returns {'data': {...}}
            if isinstance(result, dict) and 'data' in result:
                return result['data']
            return result
        else:
            # V2 API: products/{product_id}/variants/{variant_id}/inventory
            return self._make_request('GET', f'products/{product_id}/variants/{variant_id}/inventory')
    
    def update_variant_inventory(self, product_id, variant_id, inventory_data):
        """Update inventory for a specific product variant
        
        Note: BigCommerce API updates variant inventory by updating the variant itself
        with the inventory_level field, not through a separate inventory endpoint.
        """
        # Update the variant with inventory data included
        # According to BigCommerce API docs, inventory_level is a field on the variant
        variant_data = {
            'inventory_level': inventory_data.get('inventory_level', 0),
            'inventory_warning_level': inventory_data.get('inventory_warning_level', 0),
        }
        
        # Use the standard variant update endpoint
        return self.update_product_variant(product_id, variant_id, variant_data)
    
    # Customers API
    def get_customers(self, page=1, limit=250, include_addresses=True, **filters):
        """Get customers from BigCommerce
        
        Args:
            include_addresses: If True, include addresses in the response (V3 API only)
        
        Returns:
            - If V3 API: dict with 'data' (list of customers) and '_total_count' (if available)
            - If V2 API: list of customers
        """
        params = {'page': page, 'limit': limit}
        # V3 API supports 'include' parameter to include addresses
        if self.api_version == 'v3' and include_addresses:
            params['include'] = 'addresses'
        params.update(filters)
        result = self._make_request('GET', 'customers', params=params)
        # V3 API returns {'data': [...], 'meta': {...}} or {'data': [...], '_total_count': ...}
        if self.api_version == 'v3' and isinstance(result, dict):
            # Return the full result dict (includes _total_count if available)
            return result
        return result if isinstance(result, list) else []
    
    def get_customer(self, customer_id):
        """Get a single customer by ID"""
        result = self._make_request('GET', f'customers/{customer_id}')
        # V3 API might wrap single customer in 'data' key
        if self.api_version == 'v3' and isinstance(result, dict):
            if 'data' in result:
                return result['data']
            elif 'id' in result:
                return result
        return result
    
    def create_customer(self, customer_data):
        """Create a customer in BigCommerce"""
        result = self._make_request('POST', 'customers', data=customer_data)
        # V3 API returns {'data': {...}}
        if self.api_version == 'v3' and isinstance(result, dict) and 'data' in result:
            return result['data']
        return result
    
    def update_customer(self, customer_id, customer_data):
        """Update a customer in BigCommerce"""
        result = self._make_request('PUT', f'customers/{customer_id}', data=customer_data)
        # V3 API returns {'data': {...}}
        if self.api_version == 'v3' and isinstance(result, dict) and 'data' in result:
            return result['data']
        return result
    
    def get_customer_addresses(self, customer_id):
        """Get addresses for a specific customer from BigCommerce"""
        if self.api_version == 'v3':
            # V3 API uses customers/{id}/addresses endpoint
            result = self._make_request('GET', f'customers/{customer_id}/addresses')
            # V3 API returns {'data': [...], 'meta': {...}}
            if isinstance(result, dict):
                if 'data' in result:
                    return result['data']
                elif isinstance(result, list):
                    return result
            return result if isinstance(result, list) else []
        else:
            # V2 API uses customers/{id}/addresses endpoint
            result = self._make_request('GET', f'customers/{customer_id}/addresses')
            return result if isinstance(result, list) else []
    
    # Categories API (V3)
    def get_categories(self, page=1, limit=250, **filters):
        """Get categories from BigCommerce"""
        if self.api_version == 'v3':
            params = {'page': page, 'limit': limit}
            params.update(filters)
            return self._make_request('GET', 'catalog/categories', params=params)
        else:
            # V2 doesn't have categories endpoint
            return []
    
    def get_category(self, category_id):
        """Get a single category by ID"""
        if self.api_version == 'v3':
            return self._make_request('GET', f'catalog/categories/{category_id}')
        else:
            return None
    
    def create_category(self, category_data):
        """Create a category in BigCommerce"""
        if self.api_version == 'v3':
            return self._make_request('POST', 'catalog/categories', data=category_data)
        else:
            raise exceptions.UserError("Category creation requires V3 API")
    
    def update_category(self, category_id, category_data):
        """Update a category in BigCommerce"""
        if self.api_version == 'v3':
            return self._make_request('PUT', f'catalog/categories/{category_id}', data=category_data)
        else:
            raise exceptions.UserError("Category update requires V3 API")
    
    def test_connection(self):
        """Test connection to BigCommerce API"""
        try:
            # Try to get first page of products as a connection test
            self.get_products(page=1, limit=1)
            return True
        except Exception as e:
            _logger.error(f"Connection test failed: {str(e)}")
            return False

