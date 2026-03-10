# -*- coding: utf-8 -*-

from odoo import api, fields, models


def _domain_or(conditions):
    """Build domain OR(conditions) without using deprecated odoo.osv.expression (Odoo 19)."""
    if not conditions:
        return [('id', '=', 0)]
    if len(conditions) == 1:
        return list(conditions[0]) if isinstance(conditions[0], (list, tuple)) else conditions
    return ['|'] * (len(conditions) - 1) + list(conditions)


def _domain_and(domain, other):
    """Build domain AND(domain, other) without using deprecated odoo.osv.expression (Odoo 19)."""
    if not domain:
        return other
    if not other:
        return domain
    return ['&', domain, other]


class FishbowlHistoricalOrder(models.Model):
    _name = 'fishbowl.historical.order'
    _description = 'Fishbowl Historical Order'
    _order = 'order_date desc, fishbowl_order_num desc'

    name = fields.Char(string='Order Reference', required=True, index=True)
    fishbowl_order_num = fields.Char(string='Fishbowl SO#', index=True, help='Sales order number in Fishbowl')
    fishbowl_id = fields.Integer(string='Fishbowl SO ID', index=True, help='Primary key from Fishbowl SO table')
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        ondelete='set null',
        index=True,
        help='Odoo contact matched to Fishbowl customer (optional)',
    )
    partner_email = fields.Char(
        related='partner_id.email',
        string='Customer Email',
        readonly=True,
        store=False,
    )
    fishbowl_customer_id = fields.Integer(string='Fishbowl Customer ID', index=True)
    fishbowl_customer_name = fields.Char(
        string='Fishbowl Customer Name',
        help='Bill To name from the Fishbowl sales order.',
    )
    fishbowl_customer_email = fields.Char(
        string='Fishbowl Customer Email',
        help='Customer email from Fishbowl (CUSTOMER or contact/address).',
    )
    fishbowl_customer_phone = fields.Char(
        string='Fishbowl Customer Phone',
        help='Customer phone from Fishbowl (CUSTOMER or contact/address).',
    )
    customer_po = fields.Char(string='Customer PO')
    order_date = fields.Date(string='Order Date', index=True)
    date_completed = fields.Date(string='Date Completed')
    state = fields.Selection(
        [
            ('estimate', 'Estimate'),
            ('issued', 'Issued'),
            ('in_progress', 'In Progress'),
            ('fulfilled', 'Fulfilled'),
            ('void', 'Void'),
            ('cancelled', 'Cancelled'),
            ('expired', 'Expired'),
            ('historical', 'Historical'),
            ('unknown', 'Unknown'),
        ],
        string='Status',
        default='unknown',
        index=True,
    )
    note = fields.Text(string='Notes')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        ondelete='cascade',
    )
    line_ids = fields.One2many(
        'fishbowl.historical.order.line',
        'order_id',
        string='Order Lines',
    )
    line_count = fields.Integer(compute='_compute_line_count', string='Lines')
    part_numbers = fields.Char(
        compute='_compute_part_numbers',
        store=True,
        string='Part Numbers',
        search='_search_part_numbers',
        help='Concatenated part numbers from lines (for search).',
    )

    @api.depends('line_ids.part_num')
    def _compute_part_numbers(self):
        for order in self:
            order.part_numbers = ' '.join(
                (line.part_num or '') for line in order.line_ids
            ) if order.line_ids else ''

    def _search_part_numbers(self, operator, value):
        if operator == 'ilike' and value:
            return [('line_ids.part_num', 'ilike', value)]
        if operator == '=' and value:
            return [('line_ids.part_num', '=', value)]
        return []

    # Default search box: OR across these fields (same as Custom Filter code)
    _search_box_fields = [
        'fishbowl_order_num',
        'fishbowl_customer_name',
        'fishbowl_customer_email',
        'fishbowl_customer_phone',
        'customer_po',
    ]

    @api.depends('line_ids')
    def _compute_line_count(self):
        for order in self:
            order.line_count = len(order.line_ids)

    @api.model
    def _expand_search_domain(self, domain, term):
        """Build OR domain for default search: Order Reference + Fishbowl fields only (no Customer, no Part Number)."""
        if not term:
            return domain
        operator = 'ilike'
        or_domain = _domain_or([
            [('name', operator, term)],
            [('fishbowl_order_num', operator, term)],
            [('fishbowl_customer_name', operator, term)],
            [('fishbowl_customer_email', operator, term)],
            [('fishbowl_customer_phone', operator, term)],
            [('customer_po', operator, term)],
        ])
        return _domain_and(domain, or_domain)

    def _is_search_leaf(self, leaf):
        """True if leaf is (field, op, value) on a search-box field with a string value."""
        if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
            return False
        left, op, right = leaf
        return (
            op in ('ilike', '=ilike', 'like', '=', '!=', '=like', 'not ilike')
            and isinstance(right, str)
            and bool(right.strip())
            and (left in self._search_box_fields or left == 'name')
        )

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """
        Override _search (like BigCommerce product) so the search bar automatically
        searches Order Reference + Fishbowl SO#, Customer Name, Email, Phone, Customer PO.
        Any single condition on name or one of these fields is replaced with OR across all.
        """
        if domain and isinstance(domain, list):
            new_domain = []
            i = 0
            while i < len(domain):
                item = domain[i]
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    field, op, value = item
                    if self._is_search_leaf(item):
                        # Replace with OR: name | fishbowl_order_num | fishbowl_customer_name | fishbowl_customer_email | fishbowl_customer_phone | customer_po
                        # 6 conditions need 5 '|'
                        new_domain.append('|')
                        new_domain.append('|')
                        new_domain.append('|')
                        new_domain.append('|')
                        new_domain.append('|')
                        new_domain.append(('name', op, value))
                        new_domain.append(('fishbowl_order_num', op, value))
                        new_domain.append(('fishbowl_customer_name', op, value))
                        new_domain.append(('fishbowl_customer_email', op, value))
                        new_domain.append(('fishbowl_customer_phone', op, value))
                        new_domain.append(('customer_po', op, value))
                        i += 1
                        continue
                new_domain.append(item)
                i += 1
            domain = new_domain
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    @api.model
    def _name_search(self, name='', domain=None, operator='ilike', limit=100, order=None):
        """Search across all fields when using the search box (match any): same as default Custom Filter OR."""
        if not name or not (name or '').strip():
            return super()._name_search(
                name=name, domain=domain, operator=operator, limit=limit, order=order
            )
        term = (name or '').strip()
        search_domain = self._expand_search_domain(domain or [], term)
        orders = self.search(search_domain, limit=limit, order=order or self._order)
        return orders.name_get()


class FishbowlHistoricalOrderLine(models.Model):
    _name = 'fishbowl.historical.order.line'
    _description = 'Fishbowl Historical Order Line'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one(
        'fishbowl.historical.order',
        string='Order',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Sequence', default=10)
    fishbowl_part_id = fields.Integer(string='Fishbowl Part ID')
    part_num = fields.Char(string='Part Number', index=True, help='From SOITEM (e.g. partNum).')
    product_num = fields.Char(
        string='Product Number',
        index=True,
        help='From Fishbowl product/PART table (PART.num).',
    )
    description = fields.Char(string='Description')
    quantity = fields.Float(string='Quantity', digits=(16, 2), default=1.0)
    uom = fields.Char(string='UoM')
    unit_price = fields.Float(string='Unit Price', digits=(16, 2))
    total_price = fields.Float(string='Total', digits=(16, 2))
    currency_id = fields.Many2one(
        'res.currency',
        related='order_id.company_id.currency_id',
        string='Currency',
        readonly=True,
    )
