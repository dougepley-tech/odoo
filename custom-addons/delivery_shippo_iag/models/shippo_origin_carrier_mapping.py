# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ShippoOriginCarrierMapping(models.Model):
    _name = 'shippo.origin.carrier.mapping'
    _description = 'Shippo Carrier Accounts per Origin Address'
    _rec_name = 'origin_id'

    delivery_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Delivery Carrier',
        required=True,
        ondelete='cascade',
    )
    origin_id = fields.Many2one(
        'shippo.origin.address',
        string='Origin Address',
        required=True,
        ondelete='cascade',
    )
    carrier_account_ids = fields.Many2many(
        'shippo.carrier.account',
        'shippo_origin_mapping_account_rel',
        'mapping_id',
        'account_id',
        string='Carrier Accounts',
        help='Carrier accounts used when this origin is selected for rate requests.',
        domain="[('active', '=', True)]",
    )

    _sql_constraint = [
        (
            'unique_carrier_origin',
            'UNIQUE(delivery_carrier_id, origin_id)',
            'This origin already has a mapping for this delivery method.',
        ),
    ]
