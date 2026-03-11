# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    "name": "Product Variant Pricing",
    "version": "19.0.1.0.0",
    "author": "Odoo Community",
    "category": "Sales/Sales",
    "license": "LGPL-3",
    "summary": "Allow individual sales price per product variant (Odoo 19.1 style)",
    "description": """
        In standard Odoo 19, the Sales Price on a product variant is read-only when
        the product has multiple variants; pricing is managed from the template and
        attribute extra prices only.
        This module allows setting a specific sales price on each variant directly,
        so quotations can use variant-level pricing without configuring extra attribute
        prices or pricelists.
    """,
    "depends": ["product"],
    "data": [
        "views/product_product_views.xml",
    ],
    "installable": True,
}
