# -*- coding: utf-8 -*-
{
    'name': 'Warehouse Document Templates',
    'version': '19.0.1.0.12',
    'category': 'Inventory/Inventory',
    'summary': 'Per-warehouse PDF branding, reports, and mail templates',
    'description': """
Warehouse-specific document layout (branding), email templates, and optional
PDF report actions. When a warehouse field is empty, standard Odoo defaults apply.
    """,
    'author': 'Doug Epley',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'stock',
        'web',
        'sale_stock',
        'account',
    ],
    'data': [
        'views/stock_warehouse_views.xml',
        'views/report_templates.xml',
    ],
    'installable': True,
    'application': False,
}
