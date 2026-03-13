from odoo import fields, models


RMA_DEFAULT_LOCATION_PARAM = "rma_odoo_19.default_location_id"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── Core RMA ────────────────────────────────────────────────────────
    group_rma_manual_finalization = fields.Boolean(
        string="Finish RMA manually choosing a reason",
        implied_group="rma_odoo_19.group_rma_manual_finalization",
    )
    rma_return_grouping = fields.Boolean(
        related="company_id.rma_return_grouping",
        readonly=False,
    )
    send_rma_confirmation = fields.Boolean(
        related="company_id.send_rma_confirmation",
        readonly=False,
    )
    rma_mail_confirmation_template_id = fields.Many2one(
        related="company_id.rma_mail_confirmation_template_id",
        domain="[('model', '=', 'rma')]",
        readonly=False,
    )
    send_rma_receipt_confirmation = fields.Boolean(
        related="company_id.send_rma_receipt_confirmation",
        readonly=False,
    )
    rma_mail_receipt_confirmation_template_id = fields.Many2one(
        related="company_id.rma_mail_receipt_confirmation_template_id",
        domain="[('model', '=', 'rma')]",
        readonly=False,
    )
    send_rma_draft_confirmation = fields.Boolean(
        related="company_id.send_rma_draft_confirmation",
        readonly=False,
    )
    rma_mail_draft_confirmation_template_id = fields.Many2one(
        related="company_id.rma_mail_draft_confirmation_template_id",
        domain="[('model', '=', 'rma')]",
        readonly=False,
    )

    # ── Sale RMA ────────────────────────────────────────────────────────
    show_full_page_sale_rma = fields.Boolean(
        related="company_id.show_full_page_sale_rma",
        readonly=False,
    )

    # ── Delivery carrier strategy ───────────────────────────────────────
    rma_delivery_strategy = fields.Selection(
        related="company_id.rma_delivery_strategy",
        readonly=False,
    )
    rma_fixed_delivery_method = fields.Many2one(
        related="company_id.rma_fixed_delivery_method",
        readonly=False,
    )
    rma_reception_strategy = fields.Selection(
        related="company_id.rma_reception_strategy",
        readonly=False,
    )
    rma_fixed_reception_strategy = fields.Many2one(
        related="company_id.rma_fixed_reception_strategy",
        readonly=False,
    )

    # ── Reason required ─────────────────────────────────────────────────
    is_rma_reason_required = fields.Boolean(
        related="company_id.is_rma_reason_required",
        readonly=False,
    )

    # ── IAG custom: default RMA location ────────────────────────────────
    rma_default_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Default RMA Location",
        domain="[('usage', '=', 'internal')]",
        help="Default stock location used for all RMAs.",
    )

    # ── IAG custom: default restocking fee ──────────────────────────────
    rma_restocking_fee_pct = fields.Float(
        related="company_id.default_restocking_fee_pct",
        readonly=False,
    )

    def get_values(self):
        res = super().get_values()
        loc_id = self.env["ir.config_parameter"].sudo().get_param(
            RMA_DEFAULT_LOCATION_PARAM, ""
        )
        res["rma_default_location_id"] = int(loc_id) if loc_id else False
        return res

    def set_values(self):
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            RMA_DEFAULT_LOCATION_PARAM,
            str(self.rma_default_location_id.id) if self.rma_default_location_id else "",
        )
