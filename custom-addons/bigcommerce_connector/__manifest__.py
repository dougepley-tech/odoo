# -*- coding: utf-8 -*-
{
    'name': 'BigCommerce Connector',
    'version': '19.0.1.0.8',
    'category': 'Sales',
    'summary': 'Connect Odoo to BigCommerce API for syncing products, orders, inventory, and customers',
    'description': """
BigCommerce Connector
=====================
This module provides integration between Odoo and BigCommerce via API.

Features:
---------
* Sync products from BigCommerce to Odoo and vice versa
* Sync orders from BigCommerce to Odoo
* Sync inventory levels between systems
* Sync customers between BigCommerce and Odoo
* Automatic and manual sync options
* Webhook support for real-time updates
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    # product_variant_pricing must be in addons path and installed, or this module will not load.
    'depends': [
        'base',
        'sale',
        'product',
        'product_variant_pricing',
        'stock',
        'contacts',
        'delivery',
        'mail',
    ],
    'data': [
        'security/bigcommerce_security.xml',
        'security/ir.model.access.csv',
        'data/bigcommerce_cron.xml',
        'data/bigcommerce_order_status_data.xml',
        'views/bigcommerce_config_views.xml',
        'views/bigcommerce_product_views.xml',
        'views/bigcommerce_product_mapping_views.xml',
        'views/bigcommerce_mapping_deduplicate_views.xml',
        'views/bigcommerce_order_views.xml',
        'views/bigcommerce_inventory_views.xml',
        'views/bigcommerce_customer_views.xml',
        'views/bigcommerce_sync_operation_views.xml',
        'views/bigcommerce_sync_log_views.xml',
        'views/bigcommerce_fulfillment_views.xml',
        'views/bigcommerce_tax_mapping_views.xml',
        'views/bigcommerce_category_mapping_views.xml',
        'views/bigcommerce_category_rule_views.xml',
        'views/bigcommerce_field_mapping_views.xml',
        'views/bigcommerce_location_mapping_views.xml',
        'views/bigcommerce_warehouse_mapping_views.xml',
        'views/bigcommerce_dashboard_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bigcommerce_connector/static/src/js/sync_auto_refresh.js',
            'bigcommerce_connector/static/src/js/dashboard_auto_refresh.js',
            'bigcommerce_connector/static/src/css/bigcommerce_config.css',
        ],
    },
    'images': [
        'static/description/icon.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

