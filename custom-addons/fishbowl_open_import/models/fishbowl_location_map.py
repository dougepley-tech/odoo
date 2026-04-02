# -*- coding: utf-8 -*-

from odoo import api, fields, models


class FishbowlLocationMap(models.Model):
    _name = 'fishbowl.location.map'
    _description = 'Fishbowl Location Group + Type (+ optional Name) → Odoo Stock Location'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        ondelete='cascade',
    )
    fishbowl_group_name = fields.Char(string='Fishbowl location group name', required=True)
    fishbowl_type_name = fields.Char(string='Fishbowl location type name', required=True)
    fishbowl_location_name = fields.Char(
        string='Fishbowl location name',
        help='Fishbowl location Name (bin), e.g. 2:A:1:A:01, from Location Details. '
        'Leave empty to map all bins under this group+type to the same Odoo location. '
        'When set, only inventory at that bin name (trim, case-insensitive) uses this mapping.',
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Odoo stock location',
        required=True,
        domain="[('usage', 'in', ['internal', 'transit'])]",
        check_company=True,
    )
    active = fields.Boolean(default=True)

    _group_type_name_company_uniq = models.Constraint(
        'UNIQUE(fishbowl_group_name, fishbowl_type_name, fishbowl_location_name, company_id)',
        'This group+type+location name mapping already exists for this company.',
    )

    @api.model
    def resolve_location(self, company, group_name, type_name, location_name=None):
        """Return stock.location or empty recordset.

        Matches **specific** maps (non-empty Fishbowl location name) first when the incoming bin name
        matches; otherwise falls back to maps with an empty location name (wildcard for all bins).
        """
        if not group_name or not type_name:
            return self.env['stock.location']
        g = (group_name or '').strip().lower()
        t = (type_name or '').strip().lower()
        ln = (location_name or '').strip().lower()
        maps = self.search(
            [
                ('company_id', '=', company.id),
                ('active', '=', True),
            ],
            order='sequence, id',
        )

        def _gt_match(m):
            return (
                (m.fishbowl_group_name or '').strip().lower() == g
                and (m.fishbowl_type_name or '').strip().lower() == t
            )

        specific = maps.filtered(lambda m: (m.fishbowl_location_name or '').strip())
        wild = maps - specific

        if ln:
            for m in specific:
                if _gt_match(m) and (m.fishbowl_location_name or '').strip().lower() == ln:
                    return m.location_id

        for m in wild:
            if _gt_match(m):
                return m.location_id
        return self.env['stock.location']
