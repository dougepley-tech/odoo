# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    fishbowl_so_id = fields.Integer(string='Fishbowl SO id', index=True, copy=False)
    fishbowl_num = fields.Char(string='Fishbowl SO number', copy=False)
    fishbowl_status_name = fields.Char(string='Fishbowl status', readonly=True, copy=False)
    fishbowl_so_note = fields.Html(
        string='Fishbowl SO note',
        sanitize_attributes=True,
        copy=False,
        help='Imported from Fishbowl so.note. Also applied to the order Terms & conditions / notes field.',
    )
    fishbowl_amount_paid = fields.Monetary(
        string='Fishbowl amount paid',
        currency_field='currency_id',
        copy=False,
        readonly=True,
        help='Total payments recorded in Fishbowl MySQL (e.g. totalpaidview) at import. This is for '
        'reference only — it is not a registered payment in Odoo accounting.',
    )

    @api.depends('partner_id', 'fishbowl_so_id', 'fishbowl_so_note')
    def _compute_note(self):
        """Keep core invoice-terms behavior, then copy Fishbowl memo into ``note`` when present."""
        super()._compute_note()
        for order in self:
            if order.fishbowl_so_id and order.fishbowl_so_note:
                order.note = order.fishbowl_so_note

    def message_post(self, **kwargs):
        if self.env.context.get('fishbowl_import'):
            kwargs.setdefault('subtype_xmlid', False)
        return super().message_post(**kwargs)
