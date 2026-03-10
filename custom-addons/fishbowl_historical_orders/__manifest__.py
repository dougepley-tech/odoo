# -*- coding: utf-8 -*-
{
    'name': 'Fishbowl Historical Orders',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Import and display historical Fishbowl order data in Odoo (no accounting)',
    'description': """
Fishbowl Historical Orders
==========================
* Exports historical order data from a Fishbowl MySQL server.
* Stores data separately from Odoo sales orders (no accounting integration).
* Shows historical orders in a tab on customer (Contact) records for matched customers.
* Batch import for the last 3 years of data.
* Search and filter on orders and lines.
* Does not require matching to Odoo products; displays what was purchased in Fishbowl.
    """,
    'author': 'Custom',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['base'],
    'external_dependencies': {
        'python': ['pymysql'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/fishbowl_historical_order_views.xml',
        'views/fishbowl_config_views.xml',
        'views/res_partner_views.xml',
        'wizard/fishbowl_import_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
