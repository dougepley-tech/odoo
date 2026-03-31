# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.tools.float_utils import float_compare


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    fishbowl_soitem_id = fields.Integer(string='Fishbowl SO line id', copy=False)
    fishbowl_line_label = fields.Text(
        string='Fishbowl line label',
        copy=False,
        help='When set (Fishbowl discount/credit/tax lines), overrides the line description.',
    )
    fishbowl_skip_procurement = fields.Boolean(
        string='Fishbowl: skip stock (already shipped)',
        default=False,
        copy=False,
        help='Set when Fishbowl shows this line fully shipped. Odoo skips procurement/stock moves and '
        'marks delivered quantity manually so inventory is not affected.',
    )

    @api.depends('fishbowl_skip_procurement', 'product_id', 'is_expense')
    def _compute_qty_delivered_method(self):
        super()._compute_qty_delivered_method()
        for line in self.filtered(lambda l: l.fishbowl_skip_procurement):
            line.qty_delivered_method = 'manual'

    @api.depends(
        'move_ids.state',
        'move_ids.location_dest_usage',
        'move_ids.quantity',
        'move_ids.product_uom',
        'move_ids.picked',
        'qty_delivered_method',
        'fishbowl_skip_procurement',
        'product_uom_qty',
        'state',
    )
    def _compute_qty_delivered(self):
        super()._compute_qty_delivered()
        fb = self.filtered(lambda l: l.fishbowl_skip_procurement and l.state == 'sale')
        for line in fb:
            line.qty_delivered = line.product_uom_qty

    def _prepare_qty_delivered(self):
        """Phantom kits on multi-step delivery: standard sale_mrp only counts moves with dest customer.

        Internal pick moves (Stock → Output) are not ``_is_outgoing()``, so kit qty_delivered stays 0
        until the last leg. For Fishbowl-imported orders, also count internal warehouse pick legs and
        (when picked) assigned moves so SO delivered matches shipped components.
        """
        delivered_qties = super()._prepare_qty_delivered()
        lines = self.filtered(
            lambda l: l.order_id.fishbowl_so_id
            and l.qty_delivered_method == 'stock_move'
            and l.state == 'sale'
            and not l.fishbowl_skip_procurement
        )
        if not lines or 'mrp.bom' not in self.env:
            return delivered_qties
        Bom = self.env['mrp.bom'].sudo()
        for order_line in lines:
            boms = order_line.move_ids.filtered(lambda m: m.state != 'cancel').bom_line_id.bom_id
            relevant_bom = boms.filtered(
                lambda b: b.type == 'phantom'
                and (
                    b.product_id == order_line.product_id
                    or (b.product_tmpl_id == order_line.product_id.product_tmpl_id and not b.product_id)
                )
            )
            if not relevant_bom:
                relevant_bom = Bom._bom_find(
                    order_line.product_id, company_id=order_line.company_id.id, bom_type='phantom'
                )[order_line.product_id]
            if not relevant_bom or relevant_bom.type != 'phantom':
                continue
            if any(m._is_dropshipped() for m in order_line.move_ids):
                continue
            moves = order_line.move_ids.filtered(
                lambda m: m.location_dest_usage != 'inventory'
                and m.state not in ('cancel',)
                and (m.state == 'done' or (m.picked and m.state == 'assigned'))
            )
            if not moves:
                continue
            filters = {
                'incoming_moves': lambda m: (
                    m._is_outgoing()
                    or (
                        m.location_dest_id.usage == 'internal'
                        and m.location_id.usage == 'internal'
                        and m.bom_line_id
                    )
                )
                and (not m.origin_returned_move_id or (m.origin_returned_move_id and m.to_refund)),
                'outgoing_moves': lambda m: m._is_incoming() and m.to_refund,
            }
            order_qty = order_line.product_uom_id._compute_quantity(
                order_line.product_uom_qty, relevant_bom.product_uom_id
            )
            qty_delivered = moves._compute_kit_quantities(
                order_line.product_id, order_qty, relevant_bom, filters
            )
            converted = relevant_bom.product_uom_id._compute_quantity(
                qty_delivered, order_line.product_uom_id
            )
            cur = delivered_qties.get(order_line, 0.0)
            sol_uom = order_line.product_uom_id or order_line.product_id.uom_id
            rounding = sol_uom.rounding if sol_uom else 0.01
            if float_compare(
                converted,
                cur,
                precision_rounding=rounding,
            ) > 0:
                delivered_qties[order_line] = converted
        return delivered_qties

    def _action_launch_stock_rule(self, *, previous_product_uom_qty=False):
        skip = self.filtered(lambda l: l.fishbowl_skip_procurement)
        return super(SaleOrderLine, self - skip)._action_launch_stock_rule(
            previous_product_uom_qty=previous_product_uom_qty
        )

    @api.depends('product_id', 'company_id', 'order_id.fishbowl_so_id')
    def _compute_tax_ids(self):
        """Fishbowl-imported orders: no automatic Odoo taxes (tax is a Fishbowl line amount)."""
        fishbowl = self.filtered(lambda l: l.order_id.fishbowl_so_id)
        super(SaleOrderLine, self - fishbowl)._compute_tax_ids()
        fishbowl.tax_ids = False

    @api.depends('product_id', 'linked_line_id', 'linked_line_ids', 'fishbowl_line_label')
    def _compute_name(self):
        labeled = self.filtered(lambda l: l.fishbowl_line_label)
        super(SaleOrderLine, self - labeled)._compute_name()
        for line in labeled:
            line.name = line.fishbowl_line_label

    def _compute_price_unit(self):
        """Keep Fishbowl unit prices on imported orders; skip pricelist recomputation."""
        skip = self.filtered(lambda l: l.order_id.fishbowl_so_id)
        super(SaleOrderLine, self - skip)._compute_price_unit()
