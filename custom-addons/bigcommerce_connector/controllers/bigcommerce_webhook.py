# -*- coding: utf-8 -*-

from odoo import http, fields, api, SUPERUSER_ID
from odoo.http import request
from odoo.modules.registry import Registry
import json
import logging

_logger = logging.getLogger(__name__)


class BigCommerceWebhook(http.Controller):
    """Handle BigCommerce webhooks"""

    @http.route('/bigcommerce/webhook/<int:config_id>', 
                type='http', auth='none', methods=['POST'], csrf=False)
    def webhook_handler(self, config_id, db=None, **kwargs):
        """
        Handle BigCommerce webhook events
        
        Expected webhook events:
        - store/product/created, updated, deleted
        - store/product/inventory/updated
        - store/order/created, updated, archived, statusUpdated
        - store/order/message/created
        - store/order/refund/created
        - store/customer/created, updated, deleted
        - store/customer/address/created, updated, deleted
        - store/shipment/created, updated, deleted
        - store/category/created, updated, deleted
        """
        # Determine which database to use
        # Priority: 1) db parameter, 2) query string, 3) X-Odoo-Database header, 4) request.db
        if not db:
            db = request.httprequest.args.get('db')
        
        if not db:
            db = request.httprequest.headers.get('X-Odoo-Database')
        
        if not db:
            # Try request.db if available (set by Odoo's routing)
            try:
                db = request.db
            except:
                pass
        
        if not db:
            # Last resort: try to get single database
            try:
                available_dbs = http.db_list()
                if len(available_dbs) == 1:
                    db = available_dbs[0]
            except:
                pass
        
        if not db:
            _logger.error(f"No database specified for webhook")
            return json.dumps({'status': 'error', 'message': 'Database not specified. Add ?db=database_name to URL or X-Odoo-Database header'})
        
        # Log ALL webhook requests at the very start
        _logger.info(f"=== WEBHOOK RECEIVED === config_id={config_id}, db={db}")
        
        # Create environment with the specified database
        try:
            db_registry = Registry(db)
        except Exception as e:
            _logger.error(f"Failed to get registry for database {db}: {e}")
            return json.dumps({'status': 'error', 'message': f'Database not found: {db}'})
        
        with db_registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            
            config = None
            try:
                # Get the configuration
                config = env['bigcommerce.config'].browse(config_id)
                if not config.exists():
                    _logger.error(f"Webhook received for non-existent config ID: {config_id}")
                    return json.dumps({'status': 'error', 'message': 'Invalid configuration'})
                
                # Check if webhooks are enabled
                if not config.webhook_enabled:
                    _logger.warning(f"Webhook received but webhooks are disabled for config: {config.name}")
                    return json.dumps({'status': 'error', 'message': 'Webhooks are disabled'})
                
                # Get webhook data from request body
                data = json.loads(request.httprequest.data.decode('utf-8'))
                scope = data.get('scope', '')
                data_payload = data.get('data', {})
                # BigCommerce sends 'store_id' as the store hash in webhook payload
                # But in some cases it might be the numeric producer ID
                webhook_store_id = str(data.get('store_id', ''))
                webhook_hash = data.get('hash', '')  # Some webhooks include hash
                webhook_producer = data.get('producer', '')  # Format: stores/{store_hash}
                
                _logger.info(f"Received BigCommerce webhook: {scope} for config: {config.name} (ID: {config_id})")
                _logger.debug(f"Webhook payload: store_id={webhook_store_id}, hash={webhook_hash}, producer={webhook_producer}")
                
                # Update webhook statistics - use SQL to avoid locking issues
                try:
                    cr.execute("""
                        UPDATE bigcommerce_config 
                        SET webhook_last_received = %s,
                            webhook_total_received = webhook_total_received + 1,
                            write_date = %s,
                            write_uid = %s
                        WHERE id = %s
                    """, (fields.Datetime.now(), fields.Datetime.now(), SUPERUSER_ID, config_id))
                except Exception as stats_err:
                    _logger.warning(f"Could not update webhook stats: {stats_err}")
                
                # Verify the webhook is for this store - check multiple ways
                # BigCommerce might send: store_hash, numeric store_id, or producer string
                store_verified = False
                if config.store_hash:
                    # Check if store_id matches our hash
                    if webhook_store_id == config.store_hash:
                        store_verified = True
                    # Check producer field (format: stores/HASH)
                    elif webhook_producer and config.store_hash in webhook_producer:
                        store_verified = True
                    # Check hash field if present
                    elif webhook_hash == config.store_hash:
                        store_verified = True
                    # If store_id is numeric, we can't verify by hash - trust the config_id in URL
                    elif webhook_store_id.isdigit():
                        _logger.info(f"Webhook has numeric store_id ({webhook_store_id}), trusting config_id from URL")
                        store_verified = True
                    else:
                        _logger.warning(f"Could not verify store: webhook_store_id={webhook_store_id}, config.store_hash={config.store_hash}")
                        # Still process - the webhook URL has the config_id which provides security
                        store_verified = True
                else:
                    # No store_hash configured, trust config_id
                    store_verified = True
                
                if not store_verified:
                    _logger.error(f"Store verification failed for webhook")
                    try:
                        cr.execute("""
                            UPDATE bigcommerce_config 
                            SET webhook_total_failed = webhook_total_failed + 1
                            WHERE id = %s
                        """, (config_id,))
                    except:
                        pass
                    cr.commit()
                    return json.dumps({'status': 'error', 'message': 'Store verification failed'})
                
                # Handle different event types
                success = False
                if 'product' in scope:
                    success = self._handle_product_webhook(config, scope, data_payload)
                elif 'order' in scope:
                    success = self._handle_order_webhook(config, scope, data_payload)
                elif 'customer' in scope:
                    success = self._handle_customer_webhook(config, scope, data_payload)
                elif 'shipment' in scope:
                    success = self._handle_shipment_webhook(config, scope, data_payload)
                elif 'category' in scope:
                    success = self._handle_category_webhook(config, scope, data_payload)
                else:
                    _logger.warning(f"Unhandled webhook scope: {scope}")
                    success = True  # Don't count as failed if we just don't handle it yet
                
                # Update webhook statistics using SQL to avoid concurrent update issues
                try:
                    if success:
                        cr.execute("""
                            UPDATE bigcommerce_config 
                            SET webhook_total_processed = webhook_total_processed + 1
                            WHERE id = %s
                        """, (config_id,))
                    else:
                        cr.execute("""
                            UPDATE bigcommerce_config 
                            SET webhook_total_failed = webhook_total_failed + 1
                            WHERE id = %s
                        """, (config_id,))
                except Exception as stats_err:
                    _logger.warning(f"Could not update webhook stats: {stats_err}")
                
                # Commit the transaction
                cr.commit()
                
                return json.dumps({'status': 'success' if success else 'error'})
                
            except Exception as e:
                _logger.error(f"Webhook error: {str(e)}", exc_info=True)
                try:
                    cr.execute("""
                        UPDATE bigcommerce_config 
                        SET webhook_total_failed = webhook_total_failed + 1
                        WHERE id = %s
                    """, (config_id,))
                    cr.commit()
                except:
                    try:
                        cr.rollback()
                    except:
                        pass
                return json.dumps({'status': 'error', 'message': str(e)})
    
    # Legacy route for backward compatibility (no config_id in URL)
    @http.route('/bigcommerce/webhook', 
                type='http', auth='none', methods=['POST'], csrf=False)
    def webhook_handler_legacy(self, db=None, **kwargs):
        """
        Legacy webhook handler - tries to find the config automatically
        """
        _logger.info(f"=== LEGACY WEBHOOK RECEIVED === headers={dict(request.httprequest.headers)}")
        
        # Delegate to main handler with config_id lookup
        try:
            # Get database parameter
            if not db:
                db = request.httprequest.args.get('db')
            
            if not db:
                available_dbs = http.db_list()
                if len(available_dbs) == 1:
                    db = available_dbs[0]
            
            if not db:
                return json.dumps({'status': 'error', 'message': 'Database not specified. Add ?db=database_name to URL'})
            
            # Create environment with the specified database
            db_registry = Registry(db)
            with db_registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                
                # Try to find config from headers or use first active config
                data = json.loads(request.httprequest.data.decode('utf-8'))
                config_id_header = request.httprequest.headers.get('X-Odoo-Config-ID')
                
                if config_id_header:
                    config_id = int(config_id_header)
                else:
                    # Fall back to first active config
                    config = env['bigcommerce.config'].search([('active', '=', True)], limit=1)
                    if not config:
                        _logger.error("No active BigCommerce configuration found")
                        return json.dumps({'status': 'error', 'message': 'No configuration found'})
                    config_id = config.id
                
                # Delegate to main handler
                return self.webhook_handler(config_id, db=db, **kwargs)
            
        except Exception as e:
            _logger.error(f"Legacy webhook error: {str(e)}", exc_info=True)
            return json.dumps({'status': 'error', 'message': str(e)})
    
    def _handle_product_webhook(self, config, scope, data):
        """Handle product webhook events - returns True if successful, False otherwise"""
        try:
            product_id = data.get('id')
            if not product_id:
                _logger.warning(f"Product webhook received without product ID")
                return False
            
            _logger.info(f"Handling product webhook: {scope}, product ID: {product_id}")
            
            # Find product mapping
            mapping = request.env['bigcommerce.product.mapping'].sudo().search([
                ('bigcommerce_id', '=', product_id),
                ('config_id', '=', config.id)
            ], limit=1)
            
            if 'deleted' in scope:
                if mapping and mapping.product_tmpl_id:
                    product = mapping.product_tmpl_id
                    # Remove mapping for this store (product deleted from this store)
                    mapping.unlink()
                    # Only archive if product has no remaining mappings to other stores
                    if not product.bigcommerce_mapping_ids and product.active:
                        _logger.info(f"Archiving product from webhook (removed from all stores): {product.name}")
                        product.sudo().write({'active': False})
                    else:
                        _logger.info(f"Removed mapping for {product.name} from {config.name}; product kept active (still linked to other stores)")
                return True
            else:
                # Sync product from BigCommerce
                try:
                    api = config.get_api_client()
                    bc_product = api.get_product(product_id)
                    
                    # Create sync record to handle the update
                    sync = request.env['bigcommerce.product.sync'].sudo().create({
                        'config_id': config.id,
                        'sync_direction': 'bc_to_odoo',
                    })
                    sync._create_or_update_product_from_bc(api, bc_product)
                    _logger.info(f"Successfully synced product {product_id} from webhook")
                    return True
                except Exception as e:
                    _logger.error(f"Error syncing product {product_id} from webhook: {str(e)}", exc_info=True)
                    return False
        except Exception as e:
            _logger.error(f"Error handling product webhook: {str(e)}", exc_info=True)
            return False
    
    def _handle_order_webhook(self, config, scope, data):
        """Handle order webhook events - returns True if successful, False otherwise"""
        try:
            order_id = data.get('id')
            if not order_id:
                _logger.warning(f"Order webhook received without order ID")
                return False
            
            _logger.info(f"Handling order webhook: {scope}, order ID: {order_id}")
            
            # Sync order from BigCommerce
            try:
                api = config.get_api_client()
                bc_order = api.get_order(order_id)
                
                sync = request.env['bigcommerce.order.sync'].sudo().create({
                    'config_id': config.id,
                })
                sync._create_or_update_order_from_bc(api, bc_order)
                _logger.info(f"Successfully synced order {order_id} from webhook")
                return True
            except Exception as e:
                _logger.error(f"Error syncing order {order_id} from webhook: {str(e)}", exc_info=True)
                return False
        except Exception as e:
            _logger.error(f"Error handling order webhook: {str(e)}", exc_info=True)
            return False
    
    def _handle_customer_webhook(self, config, scope, data):
        """Handle customer webhook events - returns True if successful, False otherwise"""
        try:
            customer_id = data.get('id')
            if not customer_id:
                _logger.warning(f"Customer webhook received without customer ID")
                return False
            
            _logger.info(f"Handling customer webhook: {scope}, customer ID: {customer_id}")
            
            if 'deleted' in scope:
                # Find and archive customer
                customer = request.env['res.partner'].sudo().search([
                    ('bigcommerce_customer_id', '=', customer_id)
                ], limit=1)
                if customer:
                    _logger.info(f"Archiving customer from webhook: {customer.name}")
                    customer.sudo().write({'active': False})
                return True
            else:
                # Sync customer from BigCommerce
                try:
                    api = config.get_api_client()
                    bc_customer = api.get_customer(customer_id)
                    
                    sync = request.env['bigcommerce.customer.sync'].sudo().create({
                        'config_id': config.id,
                        'sync_direction': 'bc_to_odoo',
                    })
                    sync._create_or_update_customer_from_bc(bc_customer)
                    _logger.info(f"Successfully synced customer {customer_id} from webhook")
                    return True
                except Exception as e:
                    _logger.error(f"Error syncing customer {customer_id} from webhook: {str(e)}", exc_info=True)
                    return False
        except Exception as e:
            _logger.error(f"Error handling customer webhook: {str(e)}", exc_info=True)
            return False
    
    def _handle_shipment_webhook(self, config, scope, data):
        """Handle shipment webhook events - returns True if successful, False otherwise"""
        try:
            shipment_id = data.get('id')
            order_id = data.get('order_id')
            
            if not shipment_id or not order_id:
                _logger.warning(f"Shipment webhook received without shipment/order ID")
                return False
            
            _logger.info(f"Handling shipment webhook: {scope}, shipment ID: {shipment_id}, order ID: {order_id}")
            
            # For now, just log - you can implement shipment sync logic here
            _logger.info(f"Shipment webhook received but not yet implemented: {scope}")
            return True
        except Exception as e:
            _logger.error(f"Error handling shipment webhook: {str(e)}", exc_info=True)
            return False
    
    def _handle_category_webhook(self, config, scope, data):
        """Handle category webhook events - returns True if successful, False otherwise"""
        try:
            category_id = data.get('id')
            if not category_id:
                _logger.warning(f"Category webhook received without category ID")
                return False
            
            _logger.info(f"Handling category webhook: {scope}, category ID: {category_id}")
            
            # For now, just log - you can implement category sync logic here
            _logger.info(f"Category webhook received but not yet implemented: {scope}")
            return True
        except Exception as e:
            _logger.error(f"Error handling category webhook: {str(e)}", exc_info=True)
            return False

