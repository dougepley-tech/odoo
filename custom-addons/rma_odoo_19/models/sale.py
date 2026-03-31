from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import float_is_zero


class SaleOrder(models.Model):
    _inherit = "sale.order"

    rma_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="order_id",
        string="RMAs",
        copy=False,
        compute="_compute_rma_relation_ids",
        search="_search_rma_ids",
        precompute=False,
    )
    replacement_rma_ids = fields.One2many(
        comodel_name="rma",
        inverse_name="replacement_order_id",
        string="RMAs (Replacement)",
        copy=False,
        compute="_compute_rma_relation_ids",
        precompute=False,
    )
    rma_count = fields.Integer(
        string="RMA count",
        compute="_compute_rma_count",
    )

    @api.model
    def _search_rma_ids(self, operator, value):
        """Allow ORM dependency resolution for invoice_ids / invoice_count (Odoo 19 searchable depends)."""
        if operator in Domain.NEGATIVE_OPERATORS:
            return NotImplemented
        if operator == "any":
            operator = "in"
            if isinstance(value, Domain):
                value = self.env["rma"]._search(value)
        query = self.env["rma"]._search([("id", operator, value)])
        return [("id", "in", query.subselect("order_id"))]

    def _compute_rma_relation_ids(self):
        """Compute RMA relations only for RMA users; others get empty to avoid access errors on SO/delivery/partner."""
        rma_model = self.env["rma"]
        if self.env.su or self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_ids = rma_model.search([("order_id", "=", record.id)])
                record.replacement_rma_ids = rma_model.search(
                    [("replacement_order_id", "=", record.id)]
                )
        else:
            empty = rma_model.browse()
            for record in self:
                record.rma_ids = empty
                record.replacement_rma_ids = empty

    def _compute_rma_count(self):
        if not self.env.su and not self.env.user.has_group("rma_odoo_19.group_rma_user"):
            for record in self:
                record.rma_count = 0
            return
        rma_data = self.env["rma"]._read_group(
            [
                "|",
                ("order_id", "in", self.ids),
                ("replacement_order_id", "in", self.ids),
            ],
            groupby=["order_id"],
            aggregates=["__count"],
        )
        mapped_data = {}
        for r in rma_data:
            order = r[0]
            if order:
                mapped_data[order.id] = mapped_data.get(order.id, 0) + r[1]
        repl_data = self.env["rma"]._read_group(
            [("replacement_order_id", "in", self.ids)],
            groupby=["replacement_order_id"],
            aggregates=["__count"],
        )
        for r in repl_data:
            order = r[0]
            if order:
                mapped_data[order.id] = mapped_data.get(order.id, 0) + r[1]
        for record in self:
            record.rma_count = mapped_data.get(record.id, 0)

    def _prepare_rma_wizard_line_vals(self, data):
        vals = {
            "product_id": data["product"].id,
            "quantity": data["quantity"],
            "allowed_quantity": data["quantity"],
            "sale_line_id": data["sale_line_id"].id,
            "uom_id": data["uom"].id,
            "picking_id": data["picking"] and data["picking"].id,
        }
        moves = data.get("moves") or (data.get("move") and [data["move"]] or [])
        if moves:
            vals["move_id"] = moves[0].id
            vals["move_ids"] = [(6, 0, [m.id for m in moves])]
        return vals

    def action_create_rma(self):
        self.ensure_one()
        if self.state != "sale":
            raise ValidationError(
                _("You may only create RMAs from a confirmed sale order.")
            )
        wizard_obj = self.env["sale.order.rma.wizard"]
        line_vals = [
            Command.create({**self._prepare_rma_wizard_line_vals(data), "quantity": 0})
            for data in self.get_delivery_rma_data()
        ]
        wizard = wizard_obj.with_context(active_id=self.id).create(
            {"line_ids": line_vals, "location_id": self.warehouse_id.rma_loc_id.id}
        )
        return {
            "name": _("Create RMA"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "sale.order.rma.wizard",
            "res_id": wizard.id,
            "target": "new",
        }

    def action_view_rma(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_action"
        )
        rma = self.rma_ids | self.replacement_rma_ids
        if len(rma) == 1:
            action.update(
                res_id=rma.id,
                view_mode="form",
                views=[],
            )
        else:
            action["domain"] = [("id", "in", rma.ids)]
        action["context"] = {}
        return action

    def get_delivery_rma_data(self):
        self.ensure_one()
        qty_dp = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        data = []
        for line in self.order_line:
            for data_line in line.prepare_sale_rma_data():
                if not float_is_zero(
                    data_line["quantity"], precision_digits=qty_dp
                ):
                    data.append(data_line)
        return data

    @api.depends("rma_ids.refund_id")
    def _get_invoiced(self):
        res = super()._get_invoiced()
        if not self.env.su and not self.env.user.has_group("rma_odoo_19.group_rma_user"):
            return res
        for order in self:
            refunds = order.sudo().rma_ids.mapped("refund_id")
            if not refunds:
                continue
            order.invoice_ids += refunds
            order.invoice_count = len(order.invoice_ids)
        return res


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def get_delivery_move(self):
        self.ensure_one()
        return self.move_ids.filtered(
            lambda r: (
                self == r.sale_line_id
                and r.state == "done"
                and not r.scrap_id
                and r.location_dest_id.usage == "customer"
                and (
                    not r.origin_returned_move_id
                    or (r.origin_returned_move_id and r.to_refund)
                )
            )
        )

    def prepare_sale_rma_data(self):
        self.ensure_one()

        def _get_chained_moves(_moves, done_moves=None):
            moves = _moves.browse()
            done_moves = done_moves or _moves.browse()
            for move in _moves:
                if move.location_dest_id.usage == "customer":
                    moves |= move.returned_move_ids
                else:
                    moves |= move.move_dest_ids
            done_moves |= _moves
            moves = moves.filtered(
                lambda r: r.state in [
                    "partially_available", "assigned", "done"
                ]
            )
            if not moves:
                return moves
            moves -= done_moves
            moves |= _get_chained_moves(moves, done_moves)
            return moves

        product = self.product_id
        moves = self.get_delivery_move()
        data = []
        if moves:
            if len(moves) > 1:
                # Kit: one wizard line per component; use sale line product when move differs.
                # Group moves that map to the same display product so we don't show duplicate lines.
                entries = []
                for move in moves:
                    if move.state not in ("partially_available", "assigned", "done"):
                        continue
                    disp_product = (
                        self.product_id
                        if move.product_id != self.product_id
                        else move.product_id
                    )
                    entries.append(
                        (disp_product, move.product_uom_qty, move)
                    )
                # Group by product
                seen_products = {}
                for disp_product, qty, move in entries:
                    key = disp_product.id
                    if key not in seen_products:
                        seen_products[key] = {
                            "product": disp_product,
                            "quantity": 0,
                            "moves": [],
                            "uom": move.product_uom,
                            "picking": move.picking_id,
                        }
                    seen_products[key]["quantity"] += qty
                    seen_products[key]["moves"].append(move)
                for item in seen_products.values():
                    moves_list = item["moves"]
                    data.append(
                        {
                            "product": item["product"],
                            "quantity": item["quantity"],
                            "uom": item["uom"],
                            "picking": item["picking"],
                            "sale_line_id": self,
                            "move": moves_list[0],
                            "moves": moves_list,
                        }
                    )
            else:
                # Single move (or single-component): use sale line product so internal reference matches SO (e.g. 5305BKBK not 5305BK)
                for move in moves:
                    qty = move.product_uom_qty
                    for _move in _get_chained_moves(move):
                        factor = 1
                        if _move.location_dest_id.usage != "customer":
                            factor = -1
                        qty += factor * _move.product_uom_qty
                    qty = max(0, qty)
                    data.append(
                        {
                            "product": self.product_id,
                            "quantity": qty,
                            "uom": move.product_uom,
                            "picking": move.picking_id,
                            "sale_line_id": self,
                            "move": move,
                        }
                    )
        else:
            if product.type == "consu":
                data.append(
                    {
                        "product": product,
                        "quantity": self.qty_delivered,
                        "uom": self.product_uom_id,
                        "picking": False,
                        "sale_line_id": self,
                    }
                )
        return data
