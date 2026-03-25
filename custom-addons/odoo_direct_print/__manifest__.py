# -*- coding: utf-8 -*-
{
    'name': 'Odoo Direct Print',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Add print option to document attachments + direct network printing',
    'description': """
Odoo Direct Print
=================
* Adds a Print option to the attachment hover actions (alongside Download, Remove)
* Print delivery slips, shipping labels, or any document from the chatter
* No wizard - print directly from the attachment preview

Direct Network Printing (from IAG Direct Print)
-----------------------------------------------
* Print any Odoo report directly to a network printer — no browser, no download
* Printer management: ZPL (Zebra/thermal), PDF via CUPS, PDF raw socket, IPP
* Auto-print scenarios: trigger print jobs on delivery validation, SO confirm, etc.
* User printer rules: default printer per user per report
* Print job log for auditing
* React web app at /iag/print/app for standalone print client
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
        'security/ir.model.access.csv',
        'views/iag_printer_views.xml',
        'views/iag_print_scenario_views.xml',
        'views/iag_print_job_views.xml',
        'views/iag_print_user_rule_views.xml',
        'views/iag_direct_print_menus.xml',
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
