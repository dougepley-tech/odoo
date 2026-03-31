# -*- coding: utf-8 -*-

from odoo import api, models


class ProductTag(models.Model):
    _inherit = 'product.tag'

    @api.model
    def _fishbowl_import_ensure_tag_xmlid(self):
        """Bind fishbowl_open_import.product_tag_fishbowl_import to a tag named
        Fishbowl Import, reusing an existing tag if the name is already taken
        (unique constraint on product.tag.name).
        """
        Imd = self.env['ir.model.data'].sudo()
        if Imd.search(
            [
                ('module', '=', 'fishbowl_open_import'),
                ('name', '=', 'product_tag_fishbowl_import'),
            ],
            limit=1,
        ):
            return
        tag = self.search([('name', '=', 'Fishbowl Import')], limit=1)
        if not tag:
            tag = self.create({'name': 'Fishbowl Import'})
        Imd._update_xmlids(
            [
                {
                    'xml_id': 'fishbowl_open_import.product_tag_fishbowl_import',
                    'record': tag,
                    'noupdate': True,
                }
            ]
        )
