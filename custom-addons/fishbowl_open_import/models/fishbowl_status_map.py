# -*- coding: utf-8 -*-

from odoo import fields, models


class FishbowlStatusMap(models.Model):
    _name = 'fishbowl.status.map'
    _description = 'Fishbowl Status → Odoo State'
    _order = 'direction, fishbowl_status_id'

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        ondelete='cascade',
    )
    direction = fields.Selection(
        [
            ('so', 'Sales order'),
            ('po', 'Purchase order'),
        ],
        string='Document',
        required=True,
    )
    fishbowl_status_id = fields.Integer(string='Fishbowl status id', required=True)
    fishbowl_status_name = fields.Char(string='Fishbowl status name')
    odoo_so_state = fields.Selection(
        [
            ('draft', 'Quotation'),
            ('sent', 'Quotation Sent'),
            ('sale', 'Sales Order'),
            ('cancel', 'Cancelled'),
        ],
        string='Odoo SO state',
        help='Fishbowl Issued → Sales Order (confirmed quotation). '
        'In Progress → Sales Order as well; partial picking is represented on deliveries/moves, not as a separate SO state.',
    )
    odoo_po_state = fields.Selection(
        [
            ('draft', 'RFQ'),
            ('sent', 'RFQ Sent'),
            ('to approve', 'To Approve'),
            ('purchase', 'Purchase Order'),
            ('cancel', 'Cancelled'),
        ],
        string='Odoo PO state',
    )
    active = fields.Boolean(default=True)
