# -*- coding: utf-8 -*-
{
    'name': 'Odoo Direct Print',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Add print option to document attachments',
    'description': """
Odoo Direct Print
=================
* Adds a Print option to the attachment hover actions (alongside Download, Remove)
* Print delivery slips, shipping labels, or any document from the chatter
* No wizard - print directly from the attachment preview
    """,
    'author': 'IAG Performance / IAG Off-Road',
    'website': 'https://www.odoo.com',
    'license': 'LGPL-3',
    'depends': [
        'mail',
        'stock',
        'web',
    ],
    'data': [
        'views/stock_picking_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'odoo_direct_print/static/src/attachment_list.xml',
            'odoo_direct_print/static/src/attachment_list_patch.js',
            'odoo_direct_print/static/src/file_viewer.xml',
            'odoo_direct_print/static/src/file_viewer_patch.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
