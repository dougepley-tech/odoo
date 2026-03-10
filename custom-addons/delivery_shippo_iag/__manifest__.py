# -*- coding: utf-8 -*-
{
    'name': 'Shippo Shipping Integration (IAG)',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Multi-carrier rate retrieval, labels, and tracking via Shippo API',
    'description': """
Shippo Odoo 19 Integration
==========================
* Real-time multi-carrier rate retrieval from Shippo-connected carrier accounts
* Sales order rate quoting (Get Shipping Rates from SO)
* Delivery order rate selection and label generation
* Split-warehouse support (Hanover / Westminster or custom origins)
* Negotiated vs published rates; rate markups; shipment options (insurance, residential, signature)
* Label generation, tracking, and shipment cancellation
    """,
    'author': 'IAG Performance / IAG Off-Road',
    'website': 'https://www.odoo.com',
    'license': 'LGPL-3',
    'depends': [
        'stock',
        'delivery',
        'sale_stock',
        'mail',
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/shippo_origin_address_views.xml',
        'views/shippo_carrier_account_views.xml',
        'views/shippo_package_template_views.xml',
        'views/shippo_markup_rule_views.xml',
        'views/stock_delivery_carrier_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/choose_delivery_carrier_views.xml',
        'views/shippo_menus.xml',
        'wizard/shippo_rate_wizard_views.xml',
        'wizard/shippo_cancel_shipment_wizard_views.xml',
        'wizard/shippo_settings_wizard_views.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
