# -*- coding: utf-8 -*-
{
    'name': 'Fishbowl Open Import',
    'version': '19.0.1.0.139',
    'category': 'Inventory/Sales/Purchase',
    'summary': 'Import open Fishbowl sales/purchase orders, inventory, and master data via MySQL',
    'description': """
Fishbowl MySQL → Odoo 19
========================
* Connect to Fishbowl MySQL (PyMySQL).
* Configurable location mapping (location group + type → Odoo stock location).
* Status mapping for SO/PO.
* Import logs with batch tracking.
* Wizards: master data, open SO, open PO, inventory quantities.
* Optional create missing products (tagged Fishbowl Import) and partners.
* Atomic SO/PO imports; suppressed mail on import; optional Fishbowl Sales Rep (``sysuser`` first/last name) → Odoo salesperson on import; channel logins (BigC, IAGOFFROAD, Amazon, AmazonFBA) → ``crm.team`` without salesperson.
* Optional Fishbowl → Odoo fulfillment sync (shipped qty, picked flags, tracking on deliveries) and a dedicated HTML field for Fishbowl SO notes (so.note).
* Optional incoming receipt for **Credit Return** lines (setting on Fishbowl MySQL config; remaining qty from ``receiptitem``; optional receipt when Fishbowl already shows fully received; **RMA In** / RMA location when ``rma_odoo_19`` warehouse fields are set).
* Optional **order total alignment** (setting on Fishbowl MySQL config): paid-in-full → Odoo $0; partial payments → adjustment for amount paid so Odoo shows amount due; Fishbowl ``so.total`` (including net **$0** RMA). Fishbowl **Credit Return** rows import as extra SOL lines on the **Fishbowl SO adjustment** service product (negative amount; not the storable SKU, so no extra delivery for the return row).
* Optional import of Fishbowl Sales Order Memo tab rows (``memo`` + ``table`` or ``somemo``) as internal notes on the Odoo order chatter.
* Optional import of Fishbowl Purchase Order Memo tab rows (``memo`` + ``table`` or ``pomemo``) to the PO chatter.

Manual tests:
* Import an SO with partial ship data in Fishbowl: outgoing picking shows partial done qty, backorder for remainder, carrier tracking if present.
* Import an SO with text in Fishbowl so.note: fishbowl_so_note field on the SO shows the text.
* Disable “Sync picking/shipment from Fishbowl” and confirm no picking quantities change from Fishbowl.
    """,
    'author': 'Custom',
    'license': 'LGPL-3',
    'depends': [
        'sale_management',
        'sale_stock',
        'sale_mrp',
        'stock_delivery',
        'purchase_stock',
        'stock',
        'product',
    ],
    'external_dependencies': {
        'python': ['pymysql'],
    },
    'data': [
        'security/fishbowl_security.xml',
        'security/ir.model.access.csv',
        'data/fishbowl_product_tag.xml',
        'data/fishbowl_partner_category.xml',
        'data/fishbowl_adjustment_product.xml',
        'data/fishbowl_location_map_data.xml',
        'data/fishbowl_status_map_data.xml',
        'data/fishbowl_status_map_upgrade_issued.xml',
        'views/fishbowl_sync_config_views.xml',
        'views/fishbowl_location_map_views.xml',
        'views/fishbowl_status_map_views.xml',
        'views/fishbowl_import_log_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/product_template_views.xml',
        'wizards/import_master_wizard_views.xml',
        'wizards/import_so_wizard_views.xml',
        'wizards/import_po_wizard_views.xml',
        'wizards/import_inventory_wizard_views.xml',
        'views/fishbowl_import_menu.xml',
    ],
    'installable': True,
    'application': True,
    'post_init_hook': 'post_init_hook',
}
