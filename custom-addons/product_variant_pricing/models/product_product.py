# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # When True, use variant_list_price as the sales price instead of template list_price + price_extra
    list_price_override = fields.Boolean(
        string='Custom Variant Price',
        default=False,
        help='When set, the Sales Price below is specific to this variant instead of the template.',
    )
    variant_list_price = fields.Float(
        string='Variant Sales Price',
        digits='Product Price',
        help='Sales price for this variant when "Custom Variant Price" is set.',
    )

    @api.depends('list_price_override', 'variant_list_price', 'list_price', 'price_extra')
    @api.depends_context('uom')
    def _compute_product_lst_price(self):
        """Use variant-level price when list_price_override is set, else template + extra."""
        to_uom = None
        if 'uom' in self.env.context:
            to_uom = self.env['uom.uom'].browse(self.env.context['uom'])

        for product in self:
            if product.list_price_override and product.variant_list_price is not None:
                raw_price = product.variant_list_price
            else:
                raw_price = product.list_price + product.price_extra
            if to_uom:
                raw_price = product.uom_id._compute_price(raw_price, to_uom)
            product.lst_price = raw_price

    def _set_product_lst_price(self):
        """Store edited sales price as variant-specific when inverse is triggered."""
        for product in self:
            if self.env.context.get('uom'):
                value = self.env['uom.uom'].browse(self.env.context['uom'])._compute_price(
                    product.lst_price, product.uom_id
                )
            else:
                value = product.lst_price
            product.write({
                'list_price_override': True,
                'variant_list_price': value,
            })

    def _price_compute(self, price_type, uom=None, currency=None, company=None, date=False):
        """Use variant-level list price when set, so pricelists and sale orders use it."""
        if price_type == 'list_price':
            company = company or self.env.company
            date = date or fields.Date.context_today(self)
            self = self.with_company(company)
            prices = dict.fromkeys(self.ids, 0.0)
            for product in self:
                if product.list_price_override and product.variant_list_price is not None:
                    price = product.variant_list_price
                else:
                    price = (product.list_price or 0.0) + product._get_attributes_extra_price()
                price_currency = product.currency_id
                if uom:
                    price = product.uom_id._compute_price(price, uom)
                if currency:
                    price = price_currency._convert(price, currency, company, date)
                prices[product.id] = price
            return prices
        return super()._price_compute(price_type, uom=uom, currency=currency, company=company, date=date)
