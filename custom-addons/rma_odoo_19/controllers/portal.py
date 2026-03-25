# Part of Odoo. See LICENSE file for full copyright and licensing details.

import math

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.portal.controllers.portal import get_records_pager
from odoo.addons.sale.controllers.portal import CustomerPortal as SaleCustomerPortal


class RmaPortal(SaleCustomerPortal):
    """Extend sale portal with RMA routes."""

    @staticmethod
    def _portal_integer_max_qty(raw_qty):
        """Whole units allowed to return (portal UX uses integer qty only)."""
        return max(0, int(math.floor(float(raw_qty) + 1e-9)))

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "rma_count" in counters:
            Rma = request.env["rma"]
            if Rma.has_access("read"):
                partner = request.env.user.partner_id
                domain = [
                    ("partner_id", "child_of", partner.commercial_partner_id.id),
                    ("state", "!=", "cancelled"),
                ]
                values["rma_count"] = Rma.search_count(domain)
            else:
                values["rma_count"] = 0
        return values

    @http.route(["/my/rmas", "/my/rmas/page/<int:page>"], type="http", auth="user", website=True)
    def portal_my_rmas(self, page=1, **kwargs):
        """List RMAs for the current portal user."""
        Rma = request.env["rma"]
        if not Rma.has_access("read"):
            return request.redirect("/my")
        partner = request.env.user.partner_id
        domain = [
            ("partner_id", "child_of", partner.commercial_partner_id.id),
            ("state", "!=", "cancelled"),
        ]
        rmas = Rma.search(domain, order="create_date desc")
        values = self._prepare_portal_layout_values()
        values.update(
            rmas=rmas,
            page_name="rma",
        )
        return request.render("rma_odoo_19.portal_my_rmas", values)

    @http.route(["/my/rmas/<int:rma_id>"], type="http", auth="public", website=True)
    def portal_my_rma(self, rma_id, access_token=None, **kwargs):
        """RMA detail page for portal user or token-based access."""
        try:
            rma_sudo = self._document_check_access("rma", rma_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect("/my")
        values = self._prepare_portal_layout_values()
        values.update(
            rma=rma_sudo,
            page_name="rma",
        )
        if access_token:
            values["access_token"] = access_token
        history = request.session.get("my_rmas_history", [])
        if rma_sudo.id not in history:
            history = [rma_sudo.id] + [x for x in history if x != rma_sudo.id][:99]
            request.session["my_rmas_history"] = history
        values.update(get_records_pager(history, rma_sudo))
        return request.render("rma_odoo_19.portal_my_rma", values)

    @http.route(
        ["/my/orders/<int:order_id>/rma"],
        type="http",
        auth="public",
        website=True,
        methods=["GET", "POST"],
    )
    def portal_order_rma(self, order_id, access_token=None, **kwargs):
        """RMA request form for a sale order. GET shows form, POST creates RMA(s)."""
        try:
            order_sudo = self._document_check_access(
                "sale.order", order_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        if order_sudo.state not in ("sale", "done"):
            return request.redirect(order_sudo.get_portal_url())
        values = self._prepare_portal_layout_values()
        values["sale_order"] = order_sudo
        values["res_company"] = order_sudo.company_id
        values["page_name"] = "rma_request"
        if access_token:
            values["access_token"] = access_token
        if request.httprequest.method == "POST":
            values["res_company"] = order_sudo.company_id
            return self._portal_order_rma_submit(order_sudo, access_token, values, **kwargs)
        # GET: prepare form data
        rma_data = order_sudo.get_delivery_rma_data()
        if not rma_data:
            values["error"] = _("No deliverable items found for this order.")
        else:
            Operation = request.env["rma.operation"].sudo()
            Reason = request.env["rma.reason"].sudo()
            operations = Operation.search([("active", "=", True)], order="sequence, name")
            reasons = Reason.search([], order="name")
            rma_lines = []
            for i, data in enumerate(rma_data):
                max_qty_int = self._portal_integer_max_qty(data["quantity"])
                rma_lines.append(
                    {
                        "index": i,
                        "product": data["product"],
                        "max_qty": data["quantity"],
                        "max_qty_int": max_qty_int,
                        "uom": data["uom"],
                        "picking": data.get("picking"),
                        "sale_line_id": data["sale_line_id"],
                        "move": data.get("move"),
                        "moves": data.get("moves", []),
                    }
                )
            values.update(
                rma_lines=rma_lines,
                operations=operations,
                reasons=reasons,
                is_reason_required=order_sudo.company_id.is_rma_reason_required,
                submitted_notes="",
            )
        return request.render("rma_odoo_19.portal_order_rma_form", values)

    def _portal_order_rma_submit(self, order_sudo, access_token, values, **kwargs):
        """Process RMA form submission and create RMA(s)."""
        from odoo import Command
        from odoo.exceptions import UserError, ValidationError

        rma_data = order_sudo.get_delivery_rma_data()
        if not rma_data:
            values["error"] = _("No deliverable items found for this order.")
            return request.render("rma_odoo_19.portal_order_rma_form", values)
        operation_id = (kwargs.get("operation_id") or "").strip()
        reason_id = (kwargs.get("reason_id") or "").strip()
        if not operation_id:
            values["error"] = _("Please select a return operation.")
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        if order_sudo.company_id.is_rma_reason_required and not reason_id:
            values["error"] = _("Return reason is required.")
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        operation = request.env["rma.operation"].sudo().browse(int(operation_id)).exists()
        reason = (
            request.env["rma.reason"].sudo().browse(int(reason_id)).exists()
            if reason_id
            else request.env["rma.reason"]
        )
        if not operation:
            values["error"] = _("Invalid operation selected.")
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        line_vals = []
        for i, data in enumerate(rma_data):
            qty_key = f"qty_{i}"
            qty = int(float(kwargs.get(qty_key, 0) or 0))
            if qty <= 0:
                continue
            max_qty_int = self._portal_integer_max_qty(data["quantity"])
            if qty > max_qty_int:
                qty = max_qty_int
            vals = {
                "product_id": data["product"].id,
                "quantity": float(qty),
                "allowed_quantity": float(max_qty_int),
                "sale_line_id": data["sale_line_id"].id,
                "uom_id": data["uom"].id,
                "picking_id": data.get("picking") and data["picking"].id,
                "operation_id": operation.id,
                "reason_id": reason.id if reason else False,
            }
            moves = data.get("moves") or (data.get("move") and [data["move"]] or [])
            if moves:
                vals["move_id"] = moves[0].id
                vals["move_ids"] = [(6, 0, [m.id for m in moves])]
            line_vals.append(Command.create(vals))
        if not line_vals:
            values["error"] = _(
                "No items to return. Enter a quantity greater than 0 for at least one product."
            )
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        Wizard = request.env["sale.order.rma.wizard"].sudo()
        location_id = (
            order_sudo.warehouse_id.rma_loc_id.id
            if order_sudo.warehouse_id.rma_loc_id
            else Wizard.with_context(active_id=order_sudo.id)
            .new({"order_id": order_sudo.id})
            ._get_effective_location_id()
        )
        if not location_id:
            values["error"] = _(
                "RMA location is not configured. Please contact support."
            )
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        customer_notes = (kwargs.get("customer_notes") or "").strip()[:4000]
        try:
            wizard = Wizard.with_context(active_id=order_sudo.id).create(
                {
                    "order_id": order_sudo.id,
                    "line_ids": line_vals,
                    "operation_id": operation.id,
                    "reason_id": reason.id if reason else False,
                    "location_id": location_id,
                    "custom_description": customer_notes or False,
                    "waive_restocking_fee": True,
                    "charge_return_shipping": False,
                }
            )
            wizard.create_rma(from_portal=True)
        except (UserError, ValidationError) as e:
            values["error"] = str(e)
            return self._portal_order_rma_show_form(
                order_sudo, access_token, values, rma_data, **kwargs
            )
        # Send customer back to the order with a confirmation message (not the draft RMA page).
        return request.redirect(
            order_sudo.get_portal_url(query_string="&message=rma_submitted")
        )

    def _portal_order_rma_show_form(self, order_sudo, access_token, values, rma_data, **kwargs):
        """Re-render the RMA form with error and existing POST data."""
        Operation = request.env["rma.operation"].sudo()
        Reason = request.env["rma.reason"].sudo()
        operations = Operation.search([("active", "=", True)], order="sequence, name")
        reasons = Reason.search([], order="name")
        rma_lines = []
        for i, data in enumerate(rma_data):
            qty_key = f"qty_{i}"
            qty_val = kwargs.get(qty_key, "")
            max_qty_int = self._portal_integer_max_qty(data["quantity"])
            rma_lines.append(
                {
                    "index": i,
                    "product": data["product"],
                    "max_qty": data["quantity"],
                    "max_qty_int": max_qty_int,
                    "uom": data["uom"],
                    "picking": data.get("picking"),
                    "sale_line_id": data["sale_line_id"],
                    "move": data.get("move"),
                    "moves": data.get("moves", []),
                    "submitted_qty": qty_val,
                }
            )
        values.update(
            rma_lines=rma_lines,
            operations=operations,
            reasons=reasons,
            is_reason_required=order_sudo.company_id.is_rma_reason_required,
            submitted_operation_id=kwargs.get("operation_id"),
            submitted_reason_id=kwargs.get("reason_id"),
            submitted_notes=(kwargs.get("customer_notes") or "")[:4000],
        )
        return request.render("rma_odoo_19.portal_order_rma_form", values)
