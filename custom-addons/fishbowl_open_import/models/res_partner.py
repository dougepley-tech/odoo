# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ResPartnerCategory(models.Model):
    _inherit = 'res.partner.category'

    @api.model
    def _fishbowl_import_ensure_category_xmlid(self):
        """Bind ``fishbowl_open_import.res_partner_category_fishbowl_import`` to a category named
        Fishbowl Import, reusing an existing category if the name is already taken.
        """
        Imd = self.env['ir.model.data'].sudo()
        if Imd.search(
            [
                ('module', '=', 'fishbowl_open_import'),
                ('name', '=', 'res_partner_category_fishbowl_import'),
            ],
            limit=1,
        ):
            return
        cat = self.search([('name', '=', 'Fishbowl Import')], limit=1)
        if not cat:
            cat = self.create({'name': 'Fishbowl Import'})
        Imd._update_xmlids(
            [
                {
                    'xml_id': 'fishbowl_open_import.res_partner_category_fishbowl_import',
                    'record': cat,
                    'noupdate': True,
                }
            ]
        )


class ResPartner(models.Model):
    _inherit = 'res.partner'

    fishbowl_customer_id = fields.Integer(string='Fishbowl customer id', index=True, copy=False)
    fishbowl_vendor_id = fields.Integer(string='Fishbowl vendor id', index=True, copy=False)
