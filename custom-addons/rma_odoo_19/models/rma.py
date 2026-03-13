import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

from odoo.addons.stock.models.stock_move import PROCUREMENT_PRIORITIES
from odoo.addons.stock.models.stock_rule import Procurement

_logger = logging.getLogger(__name__)


class Rma(models.Model):
    _name = "rma"
    _description = "RMA"
    _order = "date desc, priority"
    _inherit = ["mail.thread", "portal.mixin", "mail.activity.mixin"]

    def _domain_location_id(self):
        rma_loc = (
            self.env["stock.warehouse"]
            .search([("company_id", "in", self.env.companies.ids)])
            .mapped("rma_loc_id")
        )
        if rma_loc:
            return [("id", "child_of", rma_loc.ids)]
        return [("usage", "=", "internal")]

    # ── General fields ──────────────────────────────────────────────────
    sent = fields.Boolean()
    name = fields.Char(
        index=True,
        copy=False,
        default=lambda self: self.env._("New"),
    )
    origin = fields.Char(
        string="Source Document",
        help="Reference of the document that generated this RMA.",
    )
    date = fields.Datetime(
        default=fields.Datetime.now,
        index=True,
        required=True,
    )
    deadline = fields.Date(
        default=lambda self: fields.Date.today() + timedelta(days=30),
        help="Default: 30 days after RMA creation. Can be edited.",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsible",
        index=True,
        tracking=True,
    )
    team_id = fields.Many2one(
        comodel_name="rma.team",
        string="RMA team",
        index=True,
        compute="_compute_team_id",
        store=True,
    )
    tag_ids = fields.Many2many(comodel_name="rma.tag", string="Tags")
    finalization_id = fields.Many2one(
        string="Finalization Reason",
        comodel_name="rma.finalization",
        copy=False,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        related="company_id.currency_id",
    )
    partner_id = fields.Many2one(
        string="Customer",
        comodel_name="res.partner",
        index=True,
        tracking=True,
    )
    partner_shipping_id = fields.Many2one(
        string="Shipping Address",
        comodel_name="res.partner",
        help="Shipping address for current RMA.",
        compute="_compute_partner_shipping_id",
        store=True,
        readonly=False,
    )
    partner_invoice_id = fields.Many2one(
        string="Invoice Address",
        comodel_name="res.partner",
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="Refund address for current RMA.",
        compute="_compute_partner_invoice_id",
        store=True,
        readonly=False,
    )
    commercial_partner_id = fields.Many2one(
        comodel_name="res.partner",
        related="partner_id.commercial_partner_id",
    )
    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Origin Delivery",
        domain="["
        " ('state', '=', 'done'),"
        " ('picking_type_id.code', '=', 'outgoing'),"
        " ('partner_id', 'child_of', commercial_partner_id),"
        "]",
    )
    move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Origin move",
        domain="["
        " ('picking_id', '=', picking_id),"
        " ('picking_id', '!=', False)"
        "]",
        compute="_compute_move_id",
        store=True,
        readonly=False,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        domain=[("type", "in", ["consu", "product"])],
        compute="_compute_product_id",
        store=True,
        readonly=False,
    )
    product_uom_qty = fields.Float(
        string="Quantity",
        required=True,
        default=1.0,
        digits="Product Unit of Measure",
        compute="_compute_product_uom_qty",
        store=True,
        readonly=False,
    )
    product_uom = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        required=True,
        default=lambda self: self.env.ref("uom.product_uom_unit").id,
        compute="_compute_product_uom",
        store=True,
        readonly=False,
    )
    priority = fields.Selection(
        selection=PROCUREMENT_PRIORITIES,
        default="1",
    )
    operation_id = fields.Many2one(
        comodel_name="rma.operation",
        string="Requested operation",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("received", "Received"),
            ("waiting_return", "Waiting for return"),
            ("waiting_replacement", "Waiting for replacement"),
            ("refunded", "Refunded"),
            ("returned", "Returned"),
            ("replaced", "Replaced"),
            ("finished", "Finished"),
            ("locked", "Locked"),
            ("cancelled", "Canceled"),
            ("voided", "Voided"),
        ],
        default="draft",
        copy=False,
        tracking=True,
    )
    description = fields.Html()

    # ── Reception fields ────────────────────────────────────────────────
    location_id = fields.Many2one(
        comodel_name="stock.location",
        domain=_domain_location_id,
        compute="_compute_location_id",
        store=True,
        readonly=False,
    )
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        compute="_compute_warehouse_id",
        store=True,
    )
    reception_move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Reception move",
        copy=False,
    )

    # ── Refund fields ───────────────────────────────────────────────────
    refund_id = fields.Many2one(
        comodel_name="account.move",
        copy=False,
        index=True,
    )
    refund_line_id = fields.Many2one(
        comodel_name="account.move.line",
        copy=False,
    )
    can_be_refunded = fields.Boolean(compute="_compute_can_be_refunded")

    # ── Delivery fields ─────────────────────────────────────────────────
    delivery_move_ids = fields.One2many(
        comodel_name="stock.move",
        inverse_name="rma_id",
        string="Delivery reservation",
        copy=False,
    )
    delivery_picking_count = fields.Integer(
        string="Delivery count",
        compute="_compute_delivery_picking_count",
    )
    delivered_qty = fields.Float(
        digits="Product Unit of Measure",
        compute="_compute_delivered_qty",
        store=True,
    )
    can_be_returned = fields.Boolean(compute="_compute_can_be_returned")
    can_be_replaced = fields.Boolean(compute="_compute_can_be_replaced")
    can_be_locked = fields.Boolean(compute="_compute_can_be_locked")
    can_be_finished = fields.Boolean(compute="_compute_can_be_finished")
    remaining_qty = fields.Float(
        string="Remaining delivered qty",
        digits="Product Unit of Measure",
        compute="_compute_remaining_qty",
    )

    # ── Split fields ────────────────────────────────────────────────────
    can_be_split = fields.Boolean(compute="_compute_can_be_split")
    origin_split_rma_id = fields.Many2one(
        comodel_name="rma",
        string="Extracted from",
        copy=False,
    )
    show_create_receipt = fields.Boolean(
        string="Show Create Receipt Button",
        compute="_compute_show_create_receipt",
    )
    show_create_return = fields.Boolean(
        string="Show Create Return Button",
        compute="_compute_show_create_return",
    )
    show_create_replace = fields.Boolean(
        string="Show Create Replace Button",
        compute="_compute_show_create_replace",
    )
    show_create_refund = fields.Boolean(
        string="Show Create Refund Button",
        compute="_compute_show_refund_replace",
    )
    return_product_id = fields.Many2one(
        "product.product",
        help="Product to be returned if it's different from the originally "
        "delivered item.",
    )
    exchange_product_id = fields.Many2one(
        "product.product",
        string="Exchange Product",
        help="The new product the customer will receive in an exchange / substitution.",
    )
    different_return_product = fields.Boolean(
        related="operation_id.different_return_product",
    )
    manual_finish_allowed = fields.Boolean(
        compute="_compute_manual_finish_allowed",
        help="Indicates whether this RMA can be manually finished.",
    )
    void_reason_id = fields.Many2one(
        comodel_name="rma.void.reason",
        string="Void Reason",
        copy=False,
        readonly=True,
    )

    # ── Sale Order fields (from rma_sale) ───────────────────────────────
    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Sale Order",
        domain="["
        " ('partner_id', 'child_of', commercial_partner_id),"
        " ('state', '=', 'sale'),"
        "]",
        store=True,
        readonly=False,
        compute="_compute_order_id",
    )
    allowed_picking_ids = fields.Many2many(
        comodel_name="stock.picking",
        compute="_compute_allowed_picking_ids",
    )
    allowed_move_ids = fields.Many2many(
        comodel_name="stock.move",
        compute="_compute_allowed_move_ids",
    )
    sale_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Sale Order Line",
        compute="_compute_sale_line_id",
        store=True,
        readonly=False,
    )
    kit_component_ids = fields.One2many(
        comodel_name="rma.kit.line",
        inverse_name="rma_id",
        string="Kit Components",
        copy=False,
        help="For kit RMAs: components to return. Adjust quantity to return only some.",
    )
    allowed_product_ids = fields.Many2many(
        comodel_name="product.product",
        compute="_compute_allowed_product_ids",
    )

    # ── Delivery carrier fields (from rma_delivery) ─────────────────────
    carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Carrier",
    )
    rma_delivery_strategy = fields.Selection(
        related="company_id.rma_delivery_strategy",
    )
    reception_carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Reception Carrier",
    )
    rma_reception_strategy = fields.Selection(
        related="company_id.rma_reception_strategy",
    )

    # ── Reason fields (from rma_reason) ─────────────────────────────────
    reason_id = fields.Many2one(comodel_name="rma.reason", string="Return Reason")
    is_rma_reason_required = fields.Boolean(
        related="company_id.is_rma_reason_required",
    )
    operation_domain = fields.Binary(compute="_compute_operation_domain")

    # ── Restocking Fee fields (IAG custom) ──────────────────────────────
    restocking_fee_pct = fields.Float(
        string="Restocking Fee %",
        default=20.0,
        help="Percentage to deduct from refund as restocking fee",
    )
    restocking_fee_amount = fields.Monetary(
        string="Restocking Fee Amount",
        compute="_compute_restocking_fee_amount",
        store=True,
        currency_field="currency_id",
    )
    no_restocking_fee = fields.Boolean(
        string="Waive Restocking Fee",
        help="Check to waive the restocking fee (e.g. defective items, "
        "damaged in transit)",
    )

    # ── Return Shipping fields (IAG custom) ─────────────────────────────
    return_shipping_cost = fields.Monetary(
        string="Return Shipping Cost",
        currency_field="currency_id",
        help="Cost of return shipping label, deducted from refund",
    )
    return_label_generated = fields.Boolean(
        string="Return Label Generated",
        copy=False,
    )
    return_shippo_transaction_id = fields.Char(
        string="Return Shippo Transaction",
        copy=False,
    )
    return_tracking_number = fields.Char(
        string="Return Tracking Number",
        copy=False,
    )
    return_tracking_url = fields.Char(
        string="Return Tracking URL",
        copy=False,
    )
    charge_return_shipping = fields.Boolean(
        string="Charge Return Shipping",
        default=True,
        help="Deduct return shipping cost from refund",
    )

    # ── Cross-Ship Replacement fields (IAG custom) ──────────────────────
    is_cross_ship = fields.Boolean(
        string="Cross-Ship Replacement",
        help="Ship replacement before the return is received. "
        "A new sales order is created for the replacement product.",
    )
    replacement_order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Replacement Sales Order",
        copy=False,
        readonly=True,
    )

    # ── Drop Ship / Store Credit fields (IAG custom) ────────────────────
    is_drop_ship = fields.Boolean(
        string="Drop Ship Item",
        compute="_compute_is_drop_ship",
        store=True,
        help="Automatically detected when product default_code starts with 'ds_'",
    )
    drop_ship_vendor_rma = fields.Char(
        string="Vendor RMA #",
        help="RMA number from the drop ship vendor/warehouse",
    )
    refund_method = fields.Selection(
        [
            ("refund", "Refund to Original Payment"),
            ("store_credit", "Store Credit"),
        ],
        default="refund",
        string="Refund Method",
    )

    # ── Computed net refund ──────────────────────────────────────────────
    net_refund_amount = fields.Monetary(
        string="Net Refund Amount",
        compute="_compute_net_refund_amount",
        currency_field="currency_id",
        help="Estimated refund after restocking fee and shipping deductions",
    )

    # =====================================================================
    # COMPUTE METHODS
    # =====================================================================

    def _get_refund_value(self):
        """Total value to refund (before restocking/shipping). For kit with components, sum of (qty × product price) per component."""
        self.ensure_one()
        if self.kit_component_ids:
            # Use each component product's list price so credit note and RMA amounts match
            return sum(
                kl.product_uom_qty * (kl.product_id.lst_price or 0.0)
                for kl in self.kit_component_ids
            )
        if self.sale_line_id:
            return self.sale_line_id.price_unit * self.product_uom_qty
        if self.product_id:
            return self.product_id.lst_price * self.product_uom_qty
        return 0.0

    @api.depends(
        "product_id", "product_uom_qty", "restocking_fee_pct", "no_restocking_fee",
        "kit_component_ids", "kit_component_ids.product_uom_qty", "kit_component_ids.product_id",
        "kit_component_ids.product_id.lst_price",
    )
    def _compute_restocking_fee_amount(self):
        for rma in self:
            if rma.no_restocking_fee or not rma.restocking_fee_pct:
                rma.restocking_fee_amount = 0.0
            else:
                price = rma._get_refund_value()
                rma.restocking_fee_amount = price * (rma.restocking_fee_pct / 100.0)

    @api.depends(
        "product_id", "product_uom_qty", "restocking_fee_amount",
        "return_shipping_cost", "charge_return_shipping",
        "kit_component_ids", "kit_component_ids.product_uom_qty", "kit_component_ids.product_id",
        "kit_component_ids.product_id.lst_price",
    )
    def _compute_net_refund_amount(self):
        for rma in self:
            price = rma._get_refund_value()
            net = price - rma.restocking_fee_amount
            if rma.charge_return_shipping and rma.return_shipping_cost:
                net -= rma.return_shipping_cost
            rma.net_refund_amount = max(net, 0.0)

    @api.depends("product_id")
    def _compute_is_drop_ship(self):
        for rma in self:
            code = rma.product_id.default_code or ""
            rma.is_drop_ship = code.lower().startswith("ds_")

    @api.depends("reason_id")
    def _compute_operation_domain(self):
        for rec in self:
            if rec.reason_id and rec.reason_id.allowed_operation_ids:
                rec.operation_domain = [
                    ("id", "in", rec.reason_id.allowed_operation_ids.ids)
                ]
            else:
                rec.operation_domain = []

    @api.depends("operation_id", "reception_move_id.state")
    def _compute_manual_finish_allowed(self):
        for rma in self:
            rma.manual_finish_allowed = (
                (
                    rma.operation_id.action_create_receipt
                    and rma.reception_move_id.state != "done"
                )
                or rma.operation_id.action_create_delivery
                or rma.operation_id.action_create_refund
            )

    @api.depends("operation_id.action_create_receipt", "state", "reception_move_id")
    def _compute_show_create_receipt(self):
        for rec in self:
            rec.show_create_receipt = (
                not rec.reception_move_id
                and rec.operation_id.action_create_receipt == "manual_on_confirm"
                and rec.state == "confirmed"
            )

    @api.depends("operation_id.action_create_delivery", "can_be_returned")
    def _compute_show_create_return(self):
        for rec in self:
            rec.show_create_return = (
                rec.operation_id.action_create_delivery
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_returned
            )

    @api.depends("operation_id.action_create_delivery", "can_be_replaced")
    def _compute_show_create_replace(self):
        for rec in self:
            rec.show_create_replace = (
                rec.operation_id.action_create_delivery
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_replaced
            )

    @api.depends("operation_id.action_create_refund", "can_be_refunded")
    def _compute_show_refund_replace(self):
        for rec in self:
            rec.show_create_refund = (
                rec.operation_id.action_create_refund
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_refunded
            )

    def _compute_delivery_picking_count(self):
        for rma in self:
            rma.delivery_picking_count = len(rma.delivery_move_ids.picking_id)

    @api.depends(
        "delivery_move_ids",
        "delivery_move_ids.state",
        "delivery_move_ids.scrap_id",
        "delivery_move_ids.product_uom_qty",
        "delivery_move_ids.quantity",
        "delivery_move_ids.product_uom",
        "product_uom",
    )
    def _compute_delivered_qty(self):
        for record in self:
            delivered_qty = 0.0
            for move in record.delivery_move_ids.filtered(
                lambda r: r.state != "cancel" and not r.scrap_id
            ):
                if move.quantity:
                    quantity = move.product_uom._compute_quantity(
                        move.quantity, record.product_uom
                    )
                    delivered_qty += quantity
                elif move.product_uom_qty:
                    delivered_qty += move.product_uom._compute_quantity(
                        move.product_uom_qty, record.product_uom
                    )
            record.delivered_qty = delivered_qty

    @api.depends("product_uom_qty", "delivered_qty")
    def _compute_remaining_qty(self):
        for r in self:
            r.remaining_qty = r.product_uom_qty - r.delivered_qty

    @api.depends("state", "operation_id", "operation_id.action_create_refund")
    def _compute_can_be_refunded(self):
        for record in self:
            record.can_be_refunded = (
                record.operation_id.action_create_refund
                in ("manual_after_receipt", "automatic_after_receipt")
                and record.state == "received"
            ) or (
                record.operation_id.action_create_refund
                in ("manual_on_confirm", "automatic_on_confirm")
                and record.state in ("confirmed", "received")
            )

    @api.depends(
        "remaining_qty", "state", "operation_id",
        "operation_id.action_create_delivery",
    )
    def _compute_can_be_returned(self):
        for r in self:
            r.can_be_returned = r.remaining_qty > 0 and (
                (
                    r.operation_id.action_create_delivery
                    in ("manual_after_receipt", "automatic_after_receipt")
                    and r.state in ["received", "waiting_return"]
                )
                or (
                    r.operation_id.action_create_delivery
                    in ("manual_on_confirm", "automatic_on_confirm")
                    and r.state in ("confirmed", "received")
                )
            )

    @api.depends("state", "operation_id", "operation_id.action_create_delivery")
    def _compute_can_be_replaced(self):
        for r in self:
            r.can_be_replaced = (
                r.operation_id.action_create_delivery
                in ("manual_after_receipt", "automatic_after_receipt")
                and r.state in ["received", "waiting_replacement", "replaced"]
            ) or (
                r.operation_id.action_create_delivery
                in ("manual_on_confirm", "automatic_on_confirm")
                and r.state in ("confirmed", "received")
            )

    @api.depends("state", "remaining_qty", "manual_finish_allowed")
    def _compute_can_be_finished(self):
        for rma in self:
            # Can finish when: in progress with remaining qty, or no wizard required, or already in outcome state (refunded/returned/replaced)
            in_progress = (
                rma.state in {"received", "waiting_replacement", "waiting_return"}
                and rma.remaining_qty > 0
            )
            no_wizard = rma.state != "finished" and not rma.manual_finish_allowed
            outcome_state = rma.state in {"refunded", "returned", "replaced"}
            rma.can_be_finished = in_progress or no_wizard or outcome_state

    @api.depends("product_uom_qty", "state", "remaining_qty")
    def _compute_can_be_split(self):
        for r in self:
            r.can_be_split = r.product_uom_qty > 1 and (
                (r.state == "waiting_return" and r.remaining_qty > 0)
                or (r.state == "waiting_replacement" and r.remaining_qty > 0)
            )

    @api.depends("state")
    def _compute_can_be_locked(self):
        for r in self:
            r.can_be_locked = r.remaining_qty > 0 and r.state in [
                "received",
                "waiting_return",
                "waiting_replacement",
            ]

    @api.depends("location_id")
    def _compute_warehouse_id(self):
        for record in self.filtered("location_id"):
            record.warehouse_id = self.env["stock.warehouse"].search(
                [("rma_loc_id", "parent_of", record.location_id.id)], limit=1
            )

    @api.depends("user_id")
    def _compute_team_id(self):
        self.team_id = False
        for record in self.filtered("user_id"):
            record.team_id = (
                self.env["rma.team"]
                .sudo()
                .search(
                    [
                        "|",
                        ("user_id", "=", record.user_id.id),
                        ("member_ids", "=", record.user_id.id),
                        "|",
                        ("company_id", "=", False),
                        ("company_id", "child_of", record.company_id.ids),
                    ],
                    limit=1,
                )
            )

    @api.depends("partner_id")
    def _compute_partner_shipping_id(self):
        self.partner_shipping_id = False
        for record in self.filtered("partner_id"):
            address = record.partner_id.address_get(["delivery"])
            record.partner_shipping_id = address.get("delivery", False)

    @api.depends("partner_id")
    def _compute_partner_invoice_id(self):
        self.partner_invoice_id = False
        for record in self.filtered("partner_id"):
            address = record.partner_id.address_get(["invoice"])
            record.partner_invoice_id = address.get("invoice", False)

    @api.depends("picking_id")
    def _compute_move_id(self):
        self.move_id = False
        for record in self.filtered("picking_id"):
            if len(record.picking_id.move_ids) == 1:
                record.move_id = record.picking_id.move_ids.id

    @api.depends("move_id")
    def _compute_product_id(self):
        self.product_id = False
        for record in self.filtered("move_id"):
            record.product_id = record.move_id.product_id.id

    @api.depends("move_id")
    def _compute_product_uom_qty(self):
        self.product_uom_qty = False
        for record in self.filtered("move_id"):
            record.product_uom_qty = record.move_id.product_uom_qty

    @api.depends("move_id", "product_id")
    def _compute_product_uom(self):
        for record in self:
            if record.move_id:
                record.product_uom = record.move_id.product_uom.id
            elif record.product_id:
                record.product_uom = record.product_id.uom_id
            else:
                record.product_uom = False

    @api.depends("picking_id", "product_id", "company_id")
    def _get_default_rma_location(self):
        loc_id = self.env["ir.config_parameter"].sudo().get_param(
            "rma_odoo_19.default_location_id", False
        )
        if loc_id:
            return self.env["stock.location"].browse(int(loc_id)).exists()
        return self.env["stock.location"]

    def _compute_location_id(self):
        default_loc = self._get_default_rma_location()
        for record in self:
            if default_loc:
                record.location_id = default_loc.id
                continue
            if record.picking_id:
                warehouse = record.picking_id.picking_type_id.warehouse_id
                if warehouse.rma_loc_id:
                    record.location_id = warehouse.rma_loc_id.id
                    continue
            if not record.location_id:
                company = record.company_id or self.env.company
                warehouse = self.env["stock.warehouse"].search(
                    [("company_id", "=", company.id)], limit=1
                )
                if warehouse.rma_loc_id:
                    record.location_id = warehouse.rma_loc_id.id
                    continue
                loc = self.env["stock.location"].search(
                    [("usage", "=", "internal"), ("company_id", "=", company.id)],
                    limit=1,
                )
                if loc:
                    record.location_id = loc.id

    def _compute_access_url(self):
        for record in self:
            record.access_url = f"/my/rmas/{record.id}"

    # ── Sale order computes (from rma_sale) ─────────────────────────────

    @api.depends("partner_id", "order_id")
    def _compute_allowed_picking_ids(self):
        domain = [("state", "=", "done"), ("picking_type_id.code", "=", "outgoing")]
        for rec in self:
            if not rec.partner_id and not rec.order_id:
                rec.allowed_picking_ids = False
                continue
            domain2 = domain.copy()
            if rec.partner_id:
                commercial_partner = rec.partner_id.commercial_partner_id
                domain2.append(("partner_id", "child_of", commercial_partner.id))
            if rec.order_id:
                domain2.append(("sale_id", "=", rec.order_id.id))
            rec.allowed_picking_ids = self.env["stock.picking"].search(domain2)

    @api.depends("move_id", "picking_id", "picking_id.move_ids", "picking_id.move_ids.sale_line_id")
    def _compute_sale_line_id(self):
        for rec in self:
            if rec.move_id:
                rec.sale_line_id = rec.move_id.sale_line_id
            elif rec.picking_id and rec.picking_id.move_ids:
                rec.sale_line_id = rec.picking_id.move_ids[0].sale_line_id
            else:
                rec.sale_line_id = False

    @api.depends("order_id", "picking_id")
    def _compute_allowed_move_ids(self):
        for rec in self:
            if rec.order_id:
                order_move = rec.order_id.order_line.mapped("move_ids")
                rec.allowed_move_ids = order_move.filtered(
                    lambda r, rec=rec: r.picking_id == rec.picking_id and r.state == "done"
                ).ids
            else:
                rec.allowed_move_ids = rec.picking_id.move_ids.ids if rec.picking_id else []

    @api.depends("order_id")
    def _compute_allowed_product_ids(self):
        for rec in self:
            if rec.order_id:
                order_product = rec.order_id.order_line.mapped("product_id")
                rec.allowed_product_ids = order_product.filtered(
                    lambda r: r.type == "consu"
                ).ids
            else:
                rec.allowed_product_ids = False

    @api.depends("partner_id")
    def _compute_order_id(self):
        self.order_id = False

    @api.onchange("order_id")
    def _onchange_order_id(self):
        self.product_id = self.picking_id = False

    # ── Delivery carrier helpers (from rma_delivery) ────────────────────

    def _get_default_carrier_id(self, company, partner):
        strategy = company.rma_delivery_strategy
        delivery_method = company.rma_fixed_delivery_method
        partner_method = (
            partner.property_delivery_carrier_id
            or partner.commercial_partner_id.property_delivery_carrier_id
        )
        if strategy == "customer_method" or (
            strategy == "mixed_method" and partner_method
        ):
            delivery_method = partner_method
        return delivery_method

    def _get_carrier(self):
        self.ensure_one()
        if self.rma_delivery_strategy == "rma_method":
            return self.carrier_id
        return self._get_default_carrier_id(
            self.company_id, self.partner_shipping_id
        )

    def _get_default_reception_carrier_id(self, company, partner):
        strategy = company.rma_reception_strategy
        delivery_method = company.rma_fixed_reception_strategy
        partner_method = (
            partner.property_delivery_carrier_id
            or partner.commercial_partner_id.property_delivery_carrier_id
        )
        if strategy == "customer_method" or (
            strategy == "mixed_method" and partner_method
        ):
            delivery_method = partner_method
        return delivery_method

    def _get_reception_carrier(self):
        self.ensure_one()
        if self.rma_reception_strategy == "rma_method":
            return self.reception_carrier_id
        return self._get_default_reception_carrier_id(
            self.company_id, self.partner_shipping_id
        )

    # =====================================================================
    # CONSTRAINS
    # =====================================================================

    @api.constrains(
        "state", "partner_id", "partner_shipping_id",
        "partner_invoice_id", "product_id",
    )
    def _check_required_after_draft(self):
        rma = self.filtered(lambda r: r.state not in ["draft", "cancelled", "voided"])
        rma._ensure_required_fields()

    # =====================================================================
    # CRUD
    # =====================================================================

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                ir_sequence = self.env["ir.sequence"]
                if "company_id" in vals:
                    ir_sequence = ir_sequence.with_company(vals["company_id"])
                vals["name"] = ir_sequence.next_by_code("rma")
            if not vals.get("team_id"):
                vals["team_id"] = self.env["rma.team"].search([], limit=1).id
            if "user_id" not in vals:
                vals["user_id"] = self.env.user.id
        rmas = super().create(vals_list)
        for rma in rmas:
            if (
                rma.picking_id
                and len(rma.picking_id.move_ids) > 1
                and not rma.kit_component_ids
            ):
                # If RMA has a single move (e.g. from picking return wizard), only add that component
                moves = rma.picking_id.move_ids
                if rma.move_id and rma.move_id in moves:
                    moves = rma.move_id
                for out_move in moves:
                    if out_move.state not in ("partially_available", "assigned", "done"):
                        continue
                    qty = out_move.product_uom_qty
                    if out_move == rma.move_id and rma.product_uom_qty:
                        qty = min(rma.product_uom_qty, qty)
                    self.env["rma.kit.line"].create({
                        "rma_id": rma.id,
                        "move_id": out_move.id,
                        "product_id": out_move.product_id.id,
                        "product_uom_qty": qty,
                        "max_quantity": out_move.product_uom_qty,
                        "product_uom": out_move.product_uom.id,
                    })
        if self.env.context.get("from_portal"):
            rmas._send_draft_email()
        return rmas

    def copy(self, default=None):
        new_rmas = super().copy(default)
        for old_rma, new_rma in zip(self, new_rmas, strict=False):
            for follower in old_rma.message_follower_ids:
                new_rma.message_subscribe(
                    partner_ids=follower.partner_id.ids,
                    subtype_ids=follower.subtype_ids.ids,
                )
        return new_rmas

    def unlink(self):
        if self.filtered(lambda r: r.state != "draft"):
            raise ValidationError(
                self.env._("You cannot delete RMAs that are not in draft state")
            )
        return super().unlink()

    # =====================================================================
    # EMAIL METHODS
    # =====================================================================

    def _send_draft_email(self):
        for rma in self.filtered("company_id.send_rma_draft_confirmation"):
            rma.with_context(
                force_send=True, mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_draft_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_draft_confirmation_template_id.id
                ],
                subtype_xmlid="rma_odoo_19.mt_rma_notification",
            )

    def _send_confirmation_email(self):
        for rma in self.filtered(lambda p: p.company_id.send_rma_confirmation):
            rma.with_context(
                force_send=True, mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_confirmation_template_id.id
                ],
                subtype_xmlid="rma_odoo_19.mt_rma_notification",
            )

    def _send_receipt_confirmation_email(self):
        for rma in self.filtered("company_id.send_rma_receipt_confirmation"):
            rma.with_context(
                force_send=True, mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_receipt_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_receipt_confirmation_template_id.id
                ],
                subtype_xmlid="rma_odoo_19.mt_rma_notification",
            )

    # =====================================================================
    # ACTION METHODS
    # =====================================================================

    def action_rma_send(self):
        self.ensure_one()
        template = self.env.ref("rma_odoo_19.mail_template_rma_notification", False)
        template = self.company_id.rma_mail_confirmation_template_id or template
        form = self.env.ref("mail.email_compose_message_wizard_form", False)
        ctx = {
            "default_model": "rma",
            "default_subtype_id": self.env.ref(
                "rma_odoo_19.mt_rma_notification"
            ).id,
            "default_res_ids": self.ids,
            "default_use_template": bool(template),
            "default_template_id": template and template.id or False,
            "default_composition_mode": "comment",
            "mark_rma_as_sent": True,
            "model_description": "RMA",
            "force_email": True,
        }
        return {
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(form.id, "form")],
            "view_id": form.id,
            "target": "new",
            "context": ctx,
        }

    def _add_message_subscribe_partner(self):
        self.ensure_one()
        if self.partner_id and self.partner_id not in self.message_partner_ids:
            self.message_subscribe([self.partner_id.id])

    def _prepare_common_procurement_vals(
        self, warehouse=None, scheduled_date=None
    ):
        self.ensure_one()
        vals = {
            "company_id": self.company_id,
            "date_planned": scheduled_date or fields.Datetime.now(),
            "warehouse_id": warehouse or self.warehouse_id,
            "partner_id": self.partner_shipping_id.id,
            "priority": self.priority,
        }
        if self.sale_line_id:
            vals["sale_line_id"] = self.sale_line_id.id
        return vals

    def _prepare_reception_procurements(self):
        """Used only when receipt is created via stock rules (legacy path)."""
        procurements = []
        for rma in self:
            if not rma.product_id.is_storable:
                continue
            product = rma.product_id
            # Exchange/Substitution: receipt is for the original product(s) being returned; no return_product_id required
            if rma.different_return_product and rma.operation_id.name != "Exchange / Substitution":
                if not rma.return_product_id:
                    raise ValidationError(
                        _(
                            "The selected operation requires a return product "
                            "different from the originally delivered item. "
                            "Please select the product to return."
                        )
                    )
                product = rma.return_product_id
            origin = rma.name
            if rma.order_id:
                origin = f"{rma.name} ({rma.order_id.name})"
            procurements.append(
                Procurement(
                    product,
                    rma.product_uom_qty,
                    rma.product_uom,
                    rma.location_id,
                    product.display_name,
                    origin,
                    rma.company_id,
                    rma._prepare_reception_procurement_vals(),
                )
            )
        return procurements

    def _create_receipt(self):
        """Create incoming receipt (customer → RMA location) directly.

        No replenishment rules are used; the location is only the destination
        for the returned item. For kits (picking with multiple moves), one
        receipt move is created per component.
        """
        StockMove = self.env["stock.move"]
        StockPicking = self.env["stock.picking"]
        for rma in self:
            is_kit = bool(
                rma.picking_id and len(rma.picking_id.move_ids) > 1
            )
            if not is_kit and not rma.product_id.is_storable:
                continue
            product = rma.product_id
            # Exchange/Substitution: receipt is for the original product(s) being returned; no return_product_id required
            if rma.different_return_product and rma.operation_id.name != "Exchange / Substitution":
                if not rma.return_product_id:
                    raise ValidationError(
                        _(
                            "The selected operation requires a return product "
                            "different from the originally delivered item. "
                            "Please select the product to return."
                        )
                    )
                product = rma.return_product_id
            warehouse = rma.warehouse_id
            if not warehouse.rma_in_type_id:
                raise ValidationError(
                    _(
                        "Warehouse %(wh)s has no RMA In operation. "
                        "Please configure RMA on the warehouse."
                    )
                    % {"wh": warehouse.name}
                )
            origin = rma.name
            if rma.order_id:
                origin = f"{rma.name} ({rma.order_id.name})"
            location_src = rma.partner_shipping_id.property_stock_customer
            location_dest = rma.location_id
            to_refund = rma.operation_id.action_create_refund == "update_quantity"
            # Kit or multi-product: one receipt move per component (from kit lines if present, else all delivery moves)
            use_kit_path = (
                rma.kit_component_ids
                or (rma.picking_id and len(rma.picking_id.move_ids) > 1)
            )
            if use_kit_path:
                move_ids_commands = []
                if rma.kit_component_ids:
                    for line in rma.kit_component_ids:
                        if line.product_uom_qty <= 0:
                            continue
                        sale_line_id = (
                            line.move_id.sale_line_id.id
                            if line.move_id and line.move_id.sale_line_id
                            else (rma.sale_line_id.id if rma.sale_line_id else False)
                        )
                        move_ids_commands.append(
                            (
                                0,
                                0,
                                {
                                    "product_id": line.product_id.id,
                                    "product_uom_qty": line.product_uom_qty,
                                    "product_uom": line.product_uom.id,
                                    "location_id": location_src.id,
                                    "location_dest_id": location_dest.id,
                                    "origin": origin,
                                    "picking_type_id": warehouse.rma_in_type_id.id,
                                    "company_id": rma.company_id.id,
                                    "to_refund": to_refund,
                                    "sale_line_id": sale_line_id,
                                    "origin_returned_move_id": line.move_id.id if line.move_id else False,
                                },
                            )
                        )
                else:
                    for out_move in rma.picking_id.move_ids:
                        if out_move.state not in ("partially_available", "assigned", "done"):
                            continue
                        move_ids_commands.append(
                            (
                                0,
                                0,
                                {
                                    "product_id": out_move.product_id.id,
                                    "product_uom_qty": out_move.product_uom_qty,
                                    "product_uom": out_move.product_uom.id,
                                    "location_id": location_src.id,
                                    "location_dest_id": location_dest.id,
                                    "origin": origin,
                                    "picking_type_id": warehouse.rma_in_type_id.id,
                                    "company_id": rma.company_id.id,
                                    "to_refund": to_refund,
                                    "sale_line_id": rma.sale_line_id.id if rma.sale_line_id else False,
                                    "origin_returned_move_id": out_move.id,
                                },
                            )
                        )
                if not move_ids_commands:
                    continue
                picking_vals = {
                    "picking_type_id": warehouse.rma_in_type_id.id,
                    "partner_id": rma.partner_shipping_id.id,
                    "origin": origin,
                    "location_id": location_src.id,
                    "location_dest_id": location_dest.id,
                    "move_ids": move_ids_commands,
                }
                picking = StockPicking.create(picking_vals)
                picking.action_confirm()
                rma.reception_move_id = picking.move_ids[0]
            else:
                move_vals = {
                    "product_id": product.id,
                    "product_uom_qty": rma.product_uom_qty,
                    "product_uom": rma.product_uom.id,
                    "location_id": location_src.id,
                    "location_dest_id": location_dest.id,
                    "origin": origin,
                    "picking_type_id": warehouse.rma_in_type_id.id,
                    "company_id": rma.company_id.id,
                    "to_refund": to_refund,
                }
                if rma.sale_line_id:
                    move_vals["sale_line_id"] = rma.sale_line_id.id
                if rma.move_id:
                    move_vals["origin_returned_move_id"] = rma.move_id.id
                picking_vals = {
                    "picking_type_id": warehouse.rma_in_type_id.id,
                    "partner_id": rma.partner_shipping_id.id,
                    "origin": origin,
                    "location_id": location_src.id,
                    "location_dest_id": location_dest.id,
                    "move_ids": [(0, 0, move_vals)],
                }
                picking = StockPicking.create(picking_vals)
                picking.action_confirm()
                rma.reception_move_id = picking.move_ids[0]
            if rma.operation_id.auto_confirm_reception:
                for recv_move in picking.move_ids:
                    recv_move.picked = True
                    recv_move._action_done()
            elif picking.state in ("confirmed", "waiting", "assigned"):
                picking.action_assign()

    def action_create_receipt(self):
        self.ensure_one()
        self._create_receipt()
        return {
            "name": _("Receipt"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "stock.picking",
            "views": [[False, "form"]],
            "res_id": self.reception_move_id.picking_id.id,
        }

    def action_view_replacement_order(self):
        self.ensure_one()
        return {
            "name": _("Replacement Sales Order"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "sale.order",
            "views": [[False, "form"]],
            "res_id": self.replacement_order_id.id,
        }

    def action_confirm(self):
        self._ensure_required_fields()
        self = self.filtered(lambda rma: rma.state == "draft")
        if not self:
            return
        self.write({"state": "confirmed"})
        for rma in self:
            rma._add_message_subscribe_partner()
        self._send_confirmation_email()
        for rec in self:
            if rec.operation_id.action_create_receipt == "automatic_on_confirm":
                rec._create_receipt()
            # Exchange / Substitution: create credit note (via action_create_refund) and empty SO for manual exchange product
            if rec.operation_id.name == "Exchange / Substitution":
                rec._create_exchange_empty_sale_order()
            # Replacement: create replacement SO (quotation or confirmed)
            elif rec.operation_id.action_create_delivery and rec.operation_id.name == "Replacement":
                if rec.is_cross_ship:
                    rec._create_cross_ship_sale_order()
                else:
                    rec._create_replacement_quotation()
            elif rec.is_cross_ship and rec.operation_id.action_create_delivery:
                rec._create_cross_ship_sale_order()
            elif rec.operation_id.action_create_delivery == "automatic_on_confirm":
                rec.with_context(
                    rma_return_grouping=rec.env.company.rma_return_grouping
                ).create_replace(
                    fields.Datetime.now(),
                    rec.warehouse_id,
                    rec.product_id,
                    rec.product_uom_qty,
                    rec.product_uom,
                )
            if rec.operation_id.action_create_refund == "automatic_on_confirm":
                rec.action_refund()

    def action_refund(self):
        group_dict = {}
        for record in self.filtered("can_be_refunded"):
            key = (record.partner_invoice_id.id, record.company_id.id)
            group_dict.setdefault(key, self.env["rma"])
            group_dict[key] |= record
        for rmas in group_dict.values():
            origin = ", ".join(rmas.mapped("name"))
            refund_vals = rmas[0]._prepare_refund_vals(origin)
            for rma in rmas:
                for line_vals in rma._prepare_refund_line_vals_list():
                    refund_vals["invoice_line_ids"].append((0, 0, line_vals))
                restock_line = rma._prepare_restocking_fee_refund_line()
                if restock_line:
                    refund_vals["invoice_line_ids"].append((0, 0, restock_line))
                # Only add shipping deduction if cost is already known
                if rma.return_shipping_cost and rma.charge_return_shipping:
                    shipping_line = rma._prepare_return_shipping_refund_line()
                    if shipping_line:
                        refund_vals["invoice_line_ids"].append(
                            (0, 0, shipping_line)
                        )
            refund = self.env["account.move"].sudo().create(refund_vals)
            # Link each RMA to refund; for kit (multiple lines) use first line as refund_line_id
            for rma in rmas:
                rma_lines = refund.invoice_line_ids.filtered(
                    lambda l, rma=rma: l.rma_id == rma
                )
                if rma_lines:
                    rma.write(
                        {
                            "refund_line_id": rma_lines[0].id,
                            "refund_id": refund.id,
                        }
                    )
            refund.with_user(self.env.uid).message_post_with_source(
                "mail.message_origin_link",
                render_values={"self": refund, "origin": rmas},
                subtype_id=self.env["ir.model.data"]._xmlid_to_res_id(
                    "mail.mt_note"
                ),
            )

    def action_replace(self):
        self.ensure_one()
        self._ensure_can_be_replaced()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_delivery_wizard_action"
        )
        action["name"] = "Replace product(s)"
        action["context"] = dict(self.env.context)
        action["context"].update(
            active_id=self.id,
            active_ids=self.ids,
            rma_delivery_type="replace",
        )
        return action

    def action_return(self):
        self.ensure_one()
        self._ensure_can_be_returned()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_delivery_wizard_action"
        )
        action["context"] = dict(self.env.context)
        action["context"].update(
            active_id=self.id,
            active_ids=self.ids,
            rma_delivery_type="return",
        )
        return action

    def action_split(self):
        self.ensure_one()
        self._ensure_can_be_split()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_split_wizard_action"
        )
        action["context"] = dict(self.env.context)
        action["context"].update(active_id=self.id, active_ids=self.ids)
        return action

    def action_finish(self):
        self.ensure_one()
        if (
            self.operation_id.action_create_receipt
            and self.reception_move_id.state != "done"
        ):
            raise ValidationError(
                _("The reception must be done before finishing this rma")
            )
        # Skip return check when already in outcome state (refunded/returned/replaced)
        if self.state not in {"refunded", "returned", "replaced"}:
            self._ensure_can_be_returned()
        self.state = "finished"
        return {}

    def action_cancel(self):
        self.reception_move_id._action_cancel()
        self.write({"state": "cancelled"})

    def action_void(self):
        """Open the void wizard to select a reason, then cancel and set state to voided."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_void_wizard_action"
        )
        action["context"] = dict(self.env.context, active_id=self.id, active_ids=self.ids)
        return action

    def action_void_apply(self, void_reason):
        """Cancel the RMA and all related documents: credit note, replacement SO,
        delivery moves, and reception move; then set state to voided.
        """
        for rma in self:
            # Cancel credit note (refund) if present and not already cancelled
            if rma.refund_id and rma.refund_id.state != "cancel":
                rma.refund_id.button_cancel()
            # Cancel replacement sale order if present and not done/cancelled
            if rma.replacement_order_id and rma.replacement_order_id.state not in (
                "cancel",
                "done",
            ):
                rma.replacement_order_id.action_cancel()
            # Cancel delivery moves (e.g. replacement deliveries)
            for move in rma.delivery_move_ids:
                if move.state not in ("cancel", "done"):
                    move._action_cancel()
            # Cancel receipt: cancel the whole picking (transfer) so all moves are cancelled
            reception_picking = rma.reception_move_id.picking_id if rma.reception_move_id else self.env["stock.picking"]
            if reception_picking and reception_picking.state not in ("cancel", "done"):
                reception_picking.action_cancel()
            elif rma.reception_move_id and rma.reception_move_id.state not in ("cancel", "done"):
                # Fallback if move has no picking
                rma.reception_move_id._action_cancel()
            rma.write({
                "state": "voided",
                "void_reason_id": void_reason.id if void_reason else False,
            })

    def action_draft(self):
        cancelled_rma = self.filtered(lambda r: r.state in ("cancelled", "voided"))
        cancelled_rma.write({"state": "draft", "void_reason_id": False})

    def action_lock(self):
        self.filtered("can_be_locked").write({"state": "locked"})

    def action_unlock(self):
        locked_rma = self.filtered(lambda r: r.state == "locked")
        locked_rma.write({"state": "received"})

    def action_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": self.get_portal_url(),
        }

    def action_generate_return_label(self):
        """Open the Shippo return-shipping wizard."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_return_shipping_wizard_action"
        )
        action["context"] = dict(self.env.context, active_id=self.id)
        return action

    def _estimate_return_shipping_cost(self):
        """Fetch the cheapest Shippo return rate and set return_shipping_cost.
        Called automatically during confirm when charge_return_shipping is True
        and no cost has been manually entered yet.
        """
        self.ensure_one()
        carrier = self.env["delivery.carrier"].search(
            [("delivery_type", "=", "shippo")], limit=1
        )
        if not carrier:
            _logger.warning(
                "RMA %s: No Shippo carrier found, skipping shipping cost estimation.",
                self.name,
            )
            return
        try:
            api_key, _env = carrier._get_shippo_api_key_and_env()
            if not api_key:
                return
            from odoo.addons.delivery_shippo_iag.models.shippo_api import ShippoAPI
            shippo = ShippoAPI(api_key)

            partner = self.partner_shipping_id
            address_from = shippo.create_address(
                name=partner.name or "",
                street1=partner.street or "",
                street2=partner.street2 or "",
                city=partner.city or "",
                state=partner.state_id.code or "",
                zip_code=partner.zip or "",
                country=partner.country_id.code or "",
                phone=partner.phone or "",
                email=partner.email or "",
            )

            warehouse = self.warehouse_id
            wh_partner = warehouse.partner_id or self.company_id.partner_id
            address_to = shippo.create_address(
                name=wh_partner.name or "",
                street1=wh_partner.street or "",
                street2=wh_partner.street2 or "",
                city=wh_partner.city or "",
                state=wh_partner.state_id.code or "",
                zip_code=wh_partner.zip or "",
                country=wh_partner.country_id.code or "",
                phone=wh_partner.phone or "",
                email=wh_partner.email or "",
            )

            product = self.product_id
            weight = product.weight or 1.0
            parcel = {
                "length": str(product.product_length or 10),
                "width": str(product.product_width or 10),
                "height": str(product.product_height or 10),
                "distance_unit": "in",
                "weight": str(weight),
                "mass_unit": "lb",
            }

            shipment = shippo.create_shipment(
                address_from=address_from,
                address_to=address_to,
                parcels=[parcel],
            )

            rates = shipment.get("rates", [])
            if rates:
                cheapest = min(rates, key=lambda r: float(r.get("amount", 9999)))
                self.return_shipping_cost = float(cheapest.get("amount", 0))
                _logger.info(
                    "RMA %s: Estimated return shipping cost $%.2f (%s %s)",
                    self.name,
                    self.return_shipping_cost,
                    cheapest.get("provider", ""),
                    cheapest.get("servicelevel", {}).get("name", ""),
                )
        except Exception:
            _logger.warning(
                "RMA %s: Failed to estimate return shipping cost from Shippo.",
                self.name,
                exc_info=True,
            )

    def _action_view_pickings(self, pickings):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        if len(pickings) > 1:
            action["domain"] = [("id", "in", pickings.ids)]
        elif pickings:
            action.update(
                res_id=pickings.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        return action

    def action_view_receipt(self):
        return self._action_view_pickings(
            self.mapped("reception_move_id.picking_id")
        )

    def action_view_refund(self):
        self.ensure_one()
        return {
            "name": self.env._("Refund"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "account.move",
            "views": [(self.env.ref("account.view_move_form").id, "form")],
            "res_id": self.refund_id.id,
        }

    def action_view_delivery(self):
        return self._action_view_pickings(
            self.mapped("delivery_move_ids.picking_id")
        )

    # =====================================================================
    # VALIDATION HELPERS
    # =====================================================================

    def _ensure_required_fields(self):
        required = [
            "partner_id",
            "partner_shipping_id",
            "partner_invoice_id",
            "product_id",
            "location_id",
            "operation_id",
        ]
        for record in self:
            desc = ""
            for field in filter(lambda item: not record[item], required):
                field_record = (
                    self.env["ir.model.fields"]
                    .sudo()
                    .search(
                        [
                            ("model_id.model", "=", record._name),
                            ("name", "=", field),
                        ]
                    )
                )
                desc += f"\n{field_record.field_description}"
            if desc:
                raise ValidationError(self.env._("Required field(s):%s") % desc)

    def _ensure_can_be_returned(self):
        if len(self) == 1:
            if not self.can_be_returned:
                raise ValidationError(
                    self.env._("This RMA cannot perform a return.")
                )
        elif not self.filtered("can_be_returned"):
            raise ValidationError(
                self.env._("None of the selected RMAs can perform a return.")
            )

    def _ensure_can_be_replaced(self):
        if len(self) == 1:
            if not self.can_be_replaced:
                raise ValidationError(
                    self.env._("This RMA cannot perform a replacement.")
                )
        elif not self.filtered("can_be_replaced"):
            raise ValidationError(
                self.env._(
                    "None of the selected RMAs can perform a replacement."
                )
            )

    def _ensure_can_be_split(self):
        self.ensure_one()
        if not self.can_be_split:
            raise ValidationError(self.env._("This RMA cannot be split."))

    def _ensure_qty_to_return(self, qty=None, uom=None):
        if qty and uom:
            if uom != self.product_uom:
                qty = uom._compute_quantity(qty, self.product_uom)
            if qty > self.remaining_qty:
                raise ValidationError(
                    self.env._(
                        "The quantity to return is greater than remaining quantity."
                    )
                )

    def _ensure_qty_to_extract(self, qty, uom):
        to_split_uom_qty = qty
        if uom != self.product_uom:
            to_split_uom_qty = uom._compute_quantity(qty, self.product_uom)
        if to_split_uom_qty > self.remaining_qty:
            raise ValidationError(
                self.env._(
                    "Quantity to extract cannot be greater than remaining"
                    " delivery quantity (%(remaining_qty)s %(product_uom)s)"
                )
                % {
                    "remaining_qty": self.remaining_qty,
                    "product_uom": self.product_uom.name,
                }
            )

    # =====================================================================
    # EXTRACT / SPLIT
    # =====================================================================

    def extract_quantity(self, qty, uom):
        self.ensure_one()
        self._ensure_can_be_split()
        self._ensure_qty_to_extract(qty, uom)
        self.product_uom_qty -= uom._compute_quantity(qty, self.product_uom)
        if self.remaining_qty <= 0:
            if self.state == "waiting_return":
                self.state = "returned"
            elif self.state == "waiting_replacement":
                self.state = "replaced"
        extracted_rma = self.copy(
            {
                "origin": self.name,
                "product_uom_qty": qty,
                "product_uom": uom.id,
                "state": "received",
                "reception_move_id": self.reception_move_id.id,
                "origin_split_rma_id": self.id,
            }
        )
        extracted_rma.message_post_with_source(
            "mail.message_origin_link",
            render_values={"self": extracted_rma, "origin": self},
            subtype_id=self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_note"),
        )
        self.message_post(
            body=Markup(
                self.env._(
                    'Split: %(name)s has been created.'
                )
                % {"id": extracted_rma.id, "name": extracted_rma.name}
            )
        )
        return extracted_rma

    # =====================================================================
    # REFUND PREPARATION
    # =====================================================================

    def _prepare_refund_vals(self, origin=False):
        self.ensure_one()
        sale_origin_parts = []
        if origin:
            sale_origin_parts.append(origin)
        if self.order_id:
            sale_origin_parts.append(self.order_id.name)
        vals = {
            "move_type": "out_refund",
            "company_id": self.company_id.id,
            "partner_id": self.partner_invoice_id.id,
            "invoice_payment_term_id": False,
            "invoice_origin": ", ".join(dict.fromkeys(sale_origin_parts)),
            "invoice_line_ids": [],
        }
        if self.order_id:
            vals["invoice_user_id"] = self.order_id.user_id.id
        return vals

    def _prepare_refund_line_vals_list(self):
        """Return a list of invoice line vals: one per returned kit component, or one for non-kit.

        Each line uses unit price and discount from the sale order when available,
        so the credit note shows unit price, discount, and refund amount per product.
        """
        self.ensure_one()
        line = self.sale_line_id
        if self.kit_component_ids:
            result = []
            for seq, kit_line in enumerate(self.kit_component_ids):
                if kit_line.product_uom_qty <= 0:
                    continue
                # Prefer price and discount from the order line for this product
                price_unit = kit_line.product_id.lst_price or 0.0
                discount = 0.0
                if self.order_id:
                    sol = None
                    # 1) If this component came from a delivery move, use that move's sale line (correct when delivered product differs from SO product, e.g. 5305BK vs 5305BKBK)
                    if kit_line.move_id and kit_line.move_id.sale_line_id and kit_line.move_id.sale_line_id.order_id == self.order_id:
                        sol = kit_line.move_id.sale_line_id
                    if not sol:
                        # 2) Match by product, then by product template
                        sol = self.order_id.order_line.filtered(
                            lambda l, p=kit_line.product_id: l.product_id == p
                        )[:1]
                    if not sol and kit_line.product_id.product_tmpl_id:
                        sol = self.order_id.order_line.filtered(
                            lambda l, tmpl=kit_line.product_id.product_tmpl_id: l.product_id.product_tmpl_id == tmpl
                        )[:1]
                    if sol:
                        price_unit = sol.price_unit
                        discount = sol.discount
                result.append({
                    "product_id": kit_line.product_id.id,
                    "quantity": kit_line.product_uom_qty,
                    "product_uom_id": kit_line.product_uom.id,
                    "price_unit": price_unit,
                    "discount": discount,
                    "rma_id": self.id,
                    "sequence": (line.sequence or 0) + seq if line else seq,
                })
            if not result:
                return [self._prepare_refund_line_vals()]
            return result
        return [self._prepare_refund_line_vals()]

    def _prepare_refund_line_vals(self):
        """Credit note line: unit price, discount, and quantity so amount = refund per product."""
        self.ensure_one()
        refund_value = self._get_refund_value()
        vals = {
            "product_id": self.product_id.id,
            "quantity": 1.0,
            "product_uom_id": self.product_uom.id,
            "price_unit": refund_value,
            "rma_id": self.id,
            "discount": 0.0,
        }
        line = self.sale_line_id
        if line:
            vals["product_id"] = line.product_id.id
            vals["discount"] = line.discount
            vals["sequence"] = line.sequence
            vals["quantity"] = self.product_uom_qty
            vals["price_unit"] = line.price_unit
            move = self.reception_move_id
            if (
                move
                and float_compare(
                    self.product_uom_qty,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                )
                == 0
            ):
                vals["sale_line_ids"] = [(4, line.id)]
        else:
            vals["quantity"] = self.product_uom_qty
            vals["price_unit"] = self.product_id.lst_price
        return vals

    def _prepare_restocking_fee_refund_line(self):
        """Return a dict for a negative line representing the restocking fee deduction,
        or False if no fee applies."""
        self.ensure_one()
        if self.no_restocking_fee or not self.restocking_fee_pct:
            return False
        if not self.restocking_fee_amount:
            return False
        product = self.env.ref(
            "rma_odoo_19.product_restocking_fee", raise_if_not_found=False
        )
        return {
            "name": _("Restocking Fee (%(pct)s%%)", pct=self.restocking_fee_pct),
            "product_id": product and product.id or False,
            "quantity": 1,
            "price_unit": -self.restocking_fee_amount,
            "tax_ids": [],
        }

    def _prepare_return_shipping_refund_line(self):
        """Return a dict for a negative line representing return shipping deduction,
        or False if not applicable."""
        self.ensure_one()
        if not self.charge_return_shipping or not self.return_shipping_cost:
            return False
        product = self.env.ref(
            "rma_odoo_19.product_return_shipping_fee", raise_if_not_found=False
        )
        return {
            "name": _("Return Shipping Deduction"),
            "product_id": product and product.id or False,
            "quantity": 1,
            "price_unit": -self.return_shipping_cost,
            "tax_ids": [],
        }

    # ── Sale-order refund linking (from rma_sale) ───────────────────────

    def _link_refund_with_reception_move(self):
        self.ensure_one()
        move = self.reception_move_id
        if (
            move
            and float_compare(
                self.product_uom_qty,
                move.product_uom_qty,
                precision_rounding=move.product_uom.rounding,
            )
            == 0
        ):
            self.reception_move_id.sale_line_id = self.sale_line_id.id
            self.reception_move_id.to_refund = True

    def _unlink_refund_with_reception_move(self):
        self.ensure_one()
        self.reception_move_id.sale_line_id = False
        self.reception_move_id.to_refund = False

    # =====================================================================
    # PROCUREMENT / DELIVERY METHODS
    # =====================================================================

    def _prepare_delivery_procurement_vals(self, scheduled_date=None):
        vals = self._prepare_common_procurement_vals(scheduled_date=scheduled_date)
        vals["rma_id"] = self.id
        vals["route_ids"] = self.warehouse_id.rma_out_route_id
        vals["move_orig_ids"] = [(6, 0, self.reception_move_id.ids)]
        return vals

    def _prepare_replace_procurement_vals(self, warehouse=None, scheduled_date=None):
        vals = self._prepare_common_procurement_vals(
            warehouse=warehouse, scheduled_date=scheduled_date
        )
        vals["rma_id"] = self.id
        if self.warehouse_id.rma_out_replace_route_id:
            vals["route_ids"] = self.warehouse_id.rma_out_replace_route_id
        else:
            vals["route_ids"] = self.warehouse_id.rma_out_route_id
        return vals

    def _prepare_delivery_procurements(self, scheduled_date=None, qty=None, uom=None):
        self = self.with_context(ignore_rma_sale_order=True)
        procurements = []
        for rma in self:
            vals = rma._prepare_delivery_procurement_vals(scheduled_date)
            origin = rma.name
            if rma.order_id:
                origin = f"{rma.name} ({rma.order_id.name})"
            procurements.append(
                Procurement(
                    rma.product_id,
                    qty or rma.product_uom_qty,
                    uom or rma.product_uom,
                    rma._get_location_final(),
                    rma.product_id.display_name,
                    origin,
                    rma.company_id,
                    vals,
                )
            )
        return procurements

    def _get_location_final(self):
        self.ensure_one()
        return self.partner_shipping_id.property_stock_customer

    # ── Cross-Ship Replacement Sales Order ──────────────────────────────

    def _get_cross_ship_so_base_vals(self):
        """Common header values shared by replacement and exchange SOs."""
        self.ensure_one()
        origin = self.name
        if self.order_id:
            origin = f"{self.name} ({self.order_id.name})"
        vals = {
            "partner_id": self.partner_id.id,
            "partner_shipping_id": self.partner_shipping_id.id,
            "partner_invoice_id": self.partner_invoice_id.id,
            "origin": origin,
            "company_id": self.company_id.id,
            "warehouse_id": self.warehouse_id.id,
        }
        if self.order_id and self.order_id.user_id:
            vals["user_id"] = self.order_id.user_id.id
        if self.order_id and self.order_id.team_id:
            vals["team_id"] = self.order_id.team_id.id
        return vals

    def _get_original_price_unit(self):
        """Return the price the customer originally paid for the RMA product."""
        self.ensure_one()
        if self.sale_line_id:
            return self.sale_line_id.price_unit
        return self.product_id.lst_price if self.product_id else 0.0

    def _prepare_replacement_sale_order_vals(self):
        """Prepare values for the replacement sales order.

        Two lines are created so the SO totals $0:
        1. The replacement product at the normal price.
        2. A credit line (negative price) offsetting the original charge.
        """
        self.ensure_one()
        product = self.product_id
        price_unit = self._get_original_price_unit()
        vals = self._get_cross_ship_so_base_vals()
        vals["client_order_ref"] = f"Replacement for {self.name}"
        vals["order_line"] = [
            (
                0,
                0,
                {
                    "product_id": product.id,
                    "product_uom_qty": self.product_uom_qty,
                    "product_uom_id": self.product_uom.id,
                    "price_unit": price_unit,
                    "name": product.display_name,
                },
            ),
            (
                0,
                0,
                {
                    "product_id": product.id,
                    "product_uom_qty": self.product_uom_qty,
                    "product_uom_id": self.product_uom.id,
                    "price_unit": -price_unit,
                    "name": f"{product.display_name} (RMA Credit)",
                },
            ),
        ]
        return vals

    def _prepare_exchange_empty_sale_order_vals(self):
        """Prepare values for an empty draft SO for exchange / substitution.

        Used when the exchange product will be added manually. Credit note
        for the returned product(s) is created separately on confirm.
        """
        self.ensure_one()
        vals = self._get_cross_ship_so_base_vals()
        vals["client_order_ref"] = f"Exchange for {self.name}"
        vals["order_line"] = []
        return vals

    def _create_exchange_empty_sale_order(self):
        """Create a draft sales order with no lines for exchange / substitution.

        The credit note for the returned product value is created on confirm
        via action_create_refund. User adds the exchange product to this SO manually.
        Delivery block (Pay Before Delivery) is only applied when cross-ship is not selected.
        """
        self.ensure_one()
        so_vals = self._prepare_exchange_empty_sale_order_vals()
        new_so = self.env["sale.order"].sudo().create(so_vals)
        if not self.is_cross_ship:
            block_reason = self._get_pay_before_delivery_block_reason()
            if block_reason and hasattr(new_so, "delivery_block_id"):
                new_so.write({"delivery_block_id": block_reason.id})
        self.replacement_order_id = new_so
        self.message_post(
            body=Markup(
                self.env._(
                    'Exchange sales order '
                    '<a href="/odoo/sales/%(so_id)s">%(so_name)s</a> created. '
                    'Add the exchange product to the order line(s).'
                )
                % {"so_id": new_so.id, "so_name": new_so.name}
            )
        )
        return new_so

    def _prepare_exchange_sale_order_vals(self):
        """Prepare values for the exchange / substitution sales order.

        Two lines are created:
        1. The exchange product at its normal price.
        2. A credit line for the original product (negative of original price).
        The customer only pays the difference (or receives a credit if cheaper).
        """
        self.ensure_one()
        exchange_product = self.exchange_product_id
        original_product = self.product_id
        original_price = self._get_original_price_unit()
        exchange_price = exchange_product.lst_price
        vals = self._get_cross_ship_so_base_vals()
        vals["client_order_ref"] = f"Exchange for {self.name}"
        vals["order_line"] = [
            (
                0,
                0,
                {
                    "product_id": exchange_product.id,
                    "product_uom_qty": self.product_uom_qty,
                    "product_uom_id": self.product_uom.id,
                    "price_unit": exchange_price,
                    "name": exchange_product.display_name,
                },
            ),
            (
                0,
                0,
                {
                    "product_id": original_product.id,
                    "product_uom_qty": self.product_uom_qty,
                    "product_uom_id": self.product_uom.id,
                    "price_unit": -original_price,
                    "name": f"{original_product.display_name} (RMA Credit)",
                },
            ),
        ]
        return vals

    def _prepare_replacement_quotation_vals(self):
        """Prepare vals for a draft replacement quotation (customer + products only, no credit line).
        Used when not cross-ship: credit note is separate; quotation has products at normal price.
        """
        self.ensure_one()
        vals = self._get_cross_ship_so_base_vals()
        ref_label = "Exchange" if self.different_return_product else "Replacement"
        vals["client_order_ref"] = f"{ref_label} for {self.name}"
        order_lines = []
        if self.different_return_product and self.exchange_product_id:
            product = self.exchange_product_id
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": product.id,
                        "product_uom_qty": self.product_uom_qty,
                        "product_uom_id": self.product_uom.id,
                        "price_unit": product.lst_price or 0.0,
                        "name": product.display_name,
                    },
                )
            )
        elif self.kit_component_ids:
            for kit_line in self.kit_component_ids:
                if kit_line.product_uom_qty <= 0:
                    continue
                price = (
                    self.sale_line_id.price_unit
                    if self.sale_line_id and kit_line.product_id == self.product_id
                    else (kit_line.product_id.lst_price or 0.0)
                )
                # Try original sale line price per component if available
                if self.sale_line_id and self.order_id:
                    sol = self.order_id.order_line.filtered(
                        lambda l: l.product_id == kit_line.product_id
                    )[:1]
                    if sol:
                        price = sol.price_unit
                order_lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": kit_line.product_id.id,
                            "product_uom_qty": kit_line.product_uom_qty,
                            "product_uom_id": kit_line.product_uom.id,
                            "price_unit": price,
                            "name": kit_line.product_id.display_name,
                        },
                    )
                )
        else:
            price_unit = self._get_original_price_unit()
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": self.product_id.id,
                        "product_uom_qty": self.product_uom_qty,
                        "product_uom_id": self.product_uom.id,
                        "price_unit": price_unit,
                        "name": self.product_id.display_name,
                    },
                )
            )
        vals["order_line"] = order_lines
        return vals

    def _get_pay_before_delivery_block_reason(self):
        """Return the 'Pay Before Delivery' delivery block reason if the module is installed."""
        return self.env.ref(
            "sale_stock_picking_blocking.pay_before_delivery",
            raise_if_not_found=False,
        ) or self.env.ref(
            "sale_stock_picking_blocking_19_0_1_0_2.pay_before_delivery",
            raise_if_not_found=False,
        )

    def _create_replacement_quotation(self):
        """Create a draft replacement quotation; apply Pay Before Delivery block.
        Used when Replacement is selected but cross-ship is not (credit note is created separately).
        """
        self.ensure_one()
        so_vals = self._prepare_replacement_quotation_vals()
        new_so = self.env["sale.order"].sudo().create(so_vals)
        block_reason = self._get_pay_before_delivery_block_reason()
        if block_reason and hasattr(new_so, "delivery_block_id"):
            new_so.write({"delivery_block_id": block_reason.id})
        self.replacement_order_id = new_so
        self.message_post(
            body=Markup(
                self.env._(
                    'Replacement quotation '
                    '<a href="/odoo/sales/%(so_id)s">%(so_name)s</a> created '
                    '(Pay Before Delivery applied).'
                )
                % {"so_id": new_so.id, "so_name": new_so.name}
            )
        )
        return new_so

    def _create_cross_ship_sale_order(self):
        """Create a replacement or exchange sales order and confirm it."""
        self.ensure_one()
        is_exchange = self.different_return_product
        if is_exchange and not self.exchange_product_id:
            raise ValidationError(
                _(
                    "Please select the Exchange Product before confirming "
                    "a cross-ship exchange / substitution RMA."
                )
            )
        if is_exchange:
            so_vals = self._prepare_exchange_sale_order_vals()
            label = "Exchange"
        else:
            so_vals = self._prepare_replacement_sale_order_vals()
            label = "Replacement"
        new_so = self.env["sale.order"].sudo().create(so_vals)
        new_so.with_user(self.env.uid).action_confirm()
        self.replacement_order_id = new_so
        self.message_post(
            body=Markup(
                self.env._(
                    '%(label)s sales order '
                    '<a href="/odoo/sales/%(so_id)s">%(so_name)s</a> created.'
                )
                % {"label": label, "so_id": new_so.id, "so_name": new_so.name}
            )
        )
        return new_so

    # ── Return / Replace ────────────────────────────────────────────────

    def create_return(self, scheduled_date, qty=None, uom=None):
        self._ensure_can_be_returned()
        for rma in self:
            rma._ensure_qty_to_return(qty, uom)
        procurements = self._prepare_delivery_procurements(scheduled_date, qty, uom)
        if procurements:
            self.env["stock.rule"].run(procurements)
        for rma in self:
            rma.update_returned_state(set_waiting=True)

    def create_replace(self, scheduled_date, warehouse, product, qty, uom):
        self._ensure_can_be_replaced()
        moves_before = self.delivery_move_ids
        for rma in self:
            vals = rma._prepare_replace_procurement_vals(warehouse, scheduled_date)
            origin = rma.name
            if rma.order_id:
                origin = f"{rma.name} ({rma.order_id.name})"
            self.env["stock.rule"].run(
                [
                    Procurement(
                        product,
                        qty,
                        uom,
                        rma._get_location_final(),
                        product.display_name,
                        origin,
                        rma.company_id,
                        vals,
                    )
                ]
            )
        for rma in self:
            rma.update_replaced_state(set_waiting=True)

    # ── State transitions ───────────────────────────────────────────────

    def update_received_state(self):
        for rma in self:
            if rma.state in ("received",) and not rma.reception_move_id.filtered(
                lambda m: m.state == "done"
            ):
                rma.state = "confirmed"

    def update_received_state_on_reception(self):
        for rma in self:
            rma.state = "received"
            if rma.operation_id.action_create_delivery == "automatic_after_receipt":
                rma.with_context(
                    rma_return_grouping=rma.env.company.rma_return_grouping
                ).create_replace(
                    fields.Datetime.now(),
                    rma.warehouse_id,
                    rma.product_id,
                    rma.product_uom_qty,
                    rma.product_uom,
                )
            if rma.operation_id.action_create_refund == "automatic_after_receipt":
                rma.action_refund()
        self._send_receipt_confirmation_email()

    def update_returned_state(self, set_waiting=False):
        for rma in self:
            returned = rma.remaining_qty <= 0
            if returned and rma.state in ("waiting_return",):
                rma.state = "returned"
            elif set_waiting and rma.state not in (
                "waiting_return",
                "returned",
                "cancelled",
                "voided",
            ):
                rma.state = "waiting_return"

    def update_replaced_state(self, set_waiting=False):
        for rma in self:
            replaced = rma.remaining_qty <= 0
            if replaced and rma.state in ("waiting_replacement",):
                rma.state = "replaced"
            elif set_waiting and rma.state not in (
                "waiting_replacement",
                "replaced",
                "cancelled",
                "voided",
            ):
                rma.state = "waiting_replacement"

    def _prepare_reception_procurement_vals(self):
        vals = self._prepare_common_procurement_vals()
        vals["route_ids"] = self.warehouse_id.rma_in_route_id
        vals["rma_receiver_ids"] = [(6, 0, self.ids)]
        vals["to_refund"] = self.operation_id.action_create_refund == "update_quantity"
        if self.move_id:
            vals["origin_returned_move_id"] = self.move_id.id
            if not self.operation_id.different_return_product:
                vals["move_orig_ids"] = [(6, 0, self.move_id.ids)]
        return vals
