# -*- coding: utf-8 -*-
{
    'name': 'Payment Provider: Affirm',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Affirm Payment Gateway Integration - Buy Now, Pay Later',
    'description': """
Affirm Payment Provider Integration
====================================

This module integrates the Affirm payment gateway with Odoo, providing:
- Buy Now, Pay Later payment option for customers
- Seamless checkout experience with Affirm modal
- Support for both sandbox and production environments
- Automatic transaction authorization and capture
- Refund support
- Payment status tracking

Features:
---------
* Direct API integration with Affirm
* Configurable public and private API keys
* Sandbox mode for testing
* Automatic payment validation
* Support for full refunds
* Real-time payment status updates
* Compatible with Odoo eCommerce

Configuration:
-------------
1. Sign up for Affirm merchant account at https://www.affirm.com/business
2. Get your API keys from Affirm Merchant Dashboard
3. Configure the payment provider in Odoo under Payment Providers
4. Enter your Public and Private API keys
5. Enable sandbox mode for testing or production mode for live transactions
    """,
    'author': 'IAG Performance',
    'website': 'https://www.iagperformance.com',
    'license': 'LGPL-3',
    'depends': ['payment', 'sale', 'account'],
    'data': [
        'views/payment_affirm_templates.xml',
        'views/payment_provider_views.xml',
        'views/account_move_views.xml',
        'data/payment_provider_data.xml',
        'data/payment_affirm_post_load.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_affirm/static/src/js/payment_form.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
