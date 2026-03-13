from collections import defaultdict

from odoo import _, api, fields, models


PROCESSED_STATES = ["received", "refunded", "replaced", "finished"]
AWAITING_ACTION_STATES = ["waiting_return", "waiting_replacement", "confirmed"]


class RmaOperation(models.Model):
    _name = "rma.operation"
    _description = "RMA requested operation"

    active = fields.Boolean(default=True)
    name = fields.Char(required=True, translate=True)
    color = fields.Integer()
    count_rma_draft = fields.Integer(compute="_compute_count_rma")
    count_rma_awaiting_action = fields.Integer(compute="_compute_count_rma")
    count_rma_processed = fields.Integer(compute="_compute_count_rma")
    action_create_receipt = fields.Selection(
        [
            ("manual_on_confirm", "Manually on Confirm"),
            ("automatic_on_confirm", "Automatically on Confirm"),
        ],
        string="Create Receipt",
        default="automatic_on_confirm",
    )
    different_return_product = fields.Boolean(
        help="If checked, allows the return of a product different from the one "
        "originally ordered.",
    )
    auto_confirm_reception = fields.Boolean(
        help="Enable this option to automatically confirm the reception when "
        "the RMA is confirmed.",
    )
    action_create_delivery = fields.Selection(
        [
            ("manual_on_confirm", "Manually on Confirm"),
            ("automatic_on_confirm", "Automatically on Confirm"),
            ("manual_after_receipt", "Manually After Receipt"),
            ("automatic_after_receipt", "Automatically After Receipt"),
        ],
        string="Delivery Action",
        default="manual_after_receipt",
    )
    action_create_refund = fields.Selection(
        [
            ("manual_on_confirm", "Manually on Confirm"),
            ("automatic_on_confirm", "Automatically on Confirm"),
            ("manual_after_receipt", "Manually After Receipt"),
            ("automatic_after_receipt", "Automatically After Receipt"),
            ("update_quantity", "Update Quantities"),
        ],
        string="Refund Action",
        default="manual_after_receipt",
    )
    prevent_delivery_grouping = fields.Boolean(
        string="Do not group deliveries",
    )

    _sql_constraints = [
        ("name_uniq", "unique (name)", "That operation name already exists !"),
    ]

    @api.model
    def _get_rma_draft_domain(self):
        return [("state", "=", "draft")]

    @api.model
    def _get_rma_awaiting_action_domain(self):
        return [("state", "in", AWAITING_ACTION_STATES)]

    @api.model
    def _get_rma_processed_domain(self):
        return [("state", "in", PROCESSED_STATES)]

    def _compute_count_rma(self):
        self.update(
            {
                "count_rma_draft": 0,
                "count_rma_processed": 0,
                "count_rma_awaiting_action": 0,
            }
        )
        state_by_op = defaultdict(int)
        for group in self.env["rma"]._read_group(
            [("operation_id", "!=", False)],
            groupby=["operation_id", "state"],
            aggregates=["__count"],
        ):
            operation_id = group[0].id
            state = group[1]
            count = group[2]
            if state == "draft":
                state_by_op[(operation_id, "count_rma_draft")] += count
            if state in PROCESSED_STATES:
                state_by_op[(operation_id, "count_rma_processed")] += count
            if state in AWAITING_ACTION_STATES:
                state_by_op[(operation_id, "count_rma_awaiting_action")] += count
        for (operation_id, field), count in state_by_op.items():
            self.browse(operation_id).update({field: count})

    def _get_action(self, name, domain):
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rma_odoo_19.rma_action"
        )
        action["display_name"] = name
        context = {
            "search_default_operation_id": [self.id],
            "default_operation_id": self.id,
        }
        import ast

        action_context = ast.literal_eval(action.get("context", "{}"))
        context = {**action_context, **context}
        action["context"] = context
        action["domain"] = domain
        return action

    def get_action_rma_tree_draft(self):
        self.ensure_one()
        name = self.display_name + ": " + _("Draft")
        return self._get_action(
            name,
            domain=[
                ("operation_id", "=", self.id),
                ("state", "=", "draft"),
            ],
        )

    def get_action_rma_tree_awaiting_action(self):
        self.ensure_one()
        name = self.display_name + ": " + _("Awaiting Action")
        return self._get_action(
            name,
            domain=[
                ("operation_id", "=", self.id),
                ("state", "in", AWAITING_ACTION_STATES),
            ],
        )

    def get_action_rma_tree_processed(self):
        self.ensure_one()
        name = self.display_name + ": " + _("Processed")
        return self._get_action(
            name,
            domain=[
                ("operation_id", "=", self.id),
                ("state", "in", PROCESSED_STATES),
            ],
        )

    def get_action_all_rma(self):
        self.ensure_one()
        return self._get_action(
            self.display_name,
            domain=[("operation_id", "=", self.id)],
        )

    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {}, name=_("%s (copy)", self.name))
        return super().copy(default)
