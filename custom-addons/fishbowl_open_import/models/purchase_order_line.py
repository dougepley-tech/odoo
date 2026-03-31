# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.tools.float_utils import float_compare, float_is_zero


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    fishbowl_poitem_id = fields.Integer(string='Fishbowl PO line id', index=True, copy=False)
    fishbowl_prior_received_qty = fields.Float(
        string='Received in Fishbowl (prior)',
        digits='Product Unit',
        default=0.0,
        copy=False,
        help='Quantity already received in Fishbowl before this Odoo PO. Counts toward the Received '
        'column without creating or validating an Odoo receipt for that quantity.',
    )

    @api.depends(
        'move_ids.state',
        'move_ids.product_uom',
        'move_ids.quantity',
        'qty_received_method',
        'qty_received_manual',
        'fishbowl_prior_received_qty',
    )
    def _compute_qty_received(self):
        return super()._compute_qty_received()

    def _prepare_qty_received(self):
        received_qties = super()._prepare_qty_received()
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        for line in self:
            prior = line.fishbowl_prior_received_qty or 0.0
            if float_is_zero(prior, precision_digits=precision):
                continue
            received_qties[line] = received_qties.get(line, 0.0) + prior
        return received_qties

    def _fishbowl_po_line_remaining_for_stock_moves(self):
        """Qty still to receive in Odoo (ordered on line minus Fishbowl prior received)."""
        self.ensure_one()
        if not self.order_id.fishbowl_po_id:
            return self.product_qty
        return max(
            0.0,
            self.product_qty - (self.fishbowl_prior_received_qty or 0.0),
        )

    def _prepare_stock_moves(self, picking):
        """Do not create receipt moves for Fishbowl-already-received qty (only ``remaining`` demand).

        Post-confirm cancellation left lines on the receipt; creating only the needed moves avoids
        those lines entirely (same goal as SO import: no picking line when nothing to ship/receive).
        """
        self.ensure_one()
        if self.order_id.fishbowl_po_id:
            remaining = self._fishbowl_po_line_remaining_for_stock_moves()
            rounding = self.product_uom_id.rounding
            if float_compare(remaining, 0, precision_rounding=rounding) <= 0:
                return []
            if float_compare(remaining, self.product_qty, precision_rounding=rounding) < 0:
                return self._prepare_stock_moves_with_effective_qty(picking, remaining)
        return super()._prepare_stock_moves(picking)

    def _prepare_stock_moves_with_effective_qty(self, picking, effective_qty):
        """Mirror ``purchase_stock._prepare_stock_moves`` using ``effective_qty`` instead of ``product_qty``."""
        self.ensure_one()
        res = []
        if self.product_id.type != 'consu':
            return res
        price_unit = self._get_stock_move_price_unit()
        qty = self._get_qty_procurement()
        move_dests = self.move_dest_ids or self.move_ids.move_dest_ids
        move_dests = move_dests.filtered(lambda m: m.state != 'cancel' and not m._is_purchase_return())
        if not move_dests:
            qty_to_attach = 0
            qty_to_push = effective_qty - qty
        else:
            move_dests_initial_demand = self._get_move_dests_initial_demand(move_dests)
            qty_to_attach = move_dests_initial_demand - qty
            qty_to_push = effective_qty - move_dests_initial_demand
        if self.product_uom_id.compare(qty_to_attach, 0.0) > 0:
            product_uom_qty, product_uom = self.product_uom_id._adjust_uom_quantities(
                qty_to_attach, self.product_id.uom_id
            )
            res.append(self._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom))
        if not self.product_uom_id.is_zero(qty_to_push):
            product_uom_qty, product_uom = self.product_uom_id._adjust_uom_quantities(
                qty_to_push, self.product_id.uom_id
            )
            extra_move_vals = self._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom)
            extra_move_vals['move_dest_ids'] = False
            res.append(extra_move_vals)
        return res

    @api.depends('product_qty', 'product_uom_id', 'company_id', 'order_id.partner_id')
    def _compute_price_unit_and_date_planned_and_name(self):
        """Keep Fishbowl unit costs and line descriptions on imported POs.

        Standard compute overwrites ``name`` (description) when it matches default product text.
        On **create**, ``order_id.fishbowl_po_id`` may not be set yet when this runs, so we also
        skip lines that already have a Fishbowl poitem id (import always sets it).
        """
        skip = self.filtered(
            lambda l: l.order_id.fishbowl_po_id or l.fishbowl_poitem_id
        )
        super(PurchaseOrderLine, self - skip)._compute_price_unit_and_date_planned_and_name()
