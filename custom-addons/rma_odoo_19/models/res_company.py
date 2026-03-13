from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    def _default_rma_mail_confirmation_template(self):
        try:
            return self.env.ref(
                "rma_odoo_19.mail_template_rma_notification"
            ).id
        except ValueError:
            return False

    def _default_rma_mail_receipt_template(self):
        try:
            return self.env.ref(
                "rma_odoo_19.mail_template_rma_receipt_notification"
            ).id
        except ValueError:
            return False

    def _default_rma_mail_draft_template(self):
        try:
            return self.env.ref(
                "rma_odoo_19.mail_template_rma_draft_notification"
            ).id
        except ValueError:
            return False

    # ── Core RMA settings ───────────────────────────────────────────────
    rma_return_grouping = fields.Boolean(
        string="Group RMA returns by customer address and warehouse",
        default=True,
    )
    send_rma_confirmation = fields.Boolean(string="Send RMA Confirmation")
    send_rma_receipt_confirmation = fields.Boolean(
        string="Send RMA Receipt Confirmation",
    )
    send_rma_draft_confirmation = fields.Boolean(
        string="Send RMA draft Confirmation",
    )
    rma_mail_confirmation_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Email Template confirmation for RMA",
        domain="[('model', '=', 'rma')]",
        default=_default_rma_mail_confirmation_template,
    )
    rma_mail_receipt_confirmation_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Email Template receipt confirmation for RMA",
        domain="[('model', '=', 'rma')]",
        default=_default_rma_mail_receipt_template,
    )
    rma_mail_draft_confirmation_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Email Template draft notification for RMA",
        domain="[('model', '=', 'rma')]",
        default=_default_rma_mail_draft_template,
    )

    # ── Sale RMA settings ───────────────────────────────────────────────
    show_full_page_sale_rma = fields.Boolean(
        string="Full page RMA creation",
    )

    # ── Delivery carrier settings (from rma_delivery) ───────────────────
    rma_delivery_strategy = fields.Selection(
        selection=[
            ("fixed_method", "Fixed method"),
            ("customer_method", "Customer method"),
            ("mixed_method", "Customer method (fallback to fixed)"),
            ("rma_method", "RMA method"),
        ],
        string="RMA delivery method strategy",
        default="mixed_method",
    )
    rma_fixed_delivery_method = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Default RMA delivery method",
    )
    rma_reception_strategy = fields.Selection(
        selection=[
            ("fixed_method", "Fixed method"),
            ("customer_method", "Customer method"),
            ("mixed_method", "Customer method (fallback to fixed)"),
            ("rma_method", "RMA method"),
        ],
        string="RMA reception method strategy",
        default="mixed_method",
    )
    rma_fixed_reception_strategy = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Default RMA reception method",
    )

    # ── Reason settings (from rma_reason) ───────────────────────────────
    is_rma_reason_required = fields.Boolean(
        string="RMA Reason Required",
        help="If enabled, a reason is required when creating an RMA.",
    )

    # ── IAG custom: default restocking fee ──────────────────────────────
    default_restocking_fee_pct = fields.Float(
        string="Default Restocking Fee %",
        default=20.0,
    )

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        for company in companies:
            company.create_rma_index()
        return companies

    def create_rma_index(self):
        return (
            self.env["ir.sequence"]
            .sudo()
            .create(
                {
                    "name": self.env._("RMA Code"),
                    "prefix": "RMA",
                    "code": "rma",
                    "padding": 4,
                    "company_id": self.id,
                }
            )
        )
