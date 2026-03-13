# -*- coding: utf-8 -*-
"""Extend Shippo rate wizard for RMA return shipping on credit notes.

When opened from a credit note with default_rma_id and default_credit_note_id,
the same "Get Shippo Rates" wizard is used; origin/destination are taken from
the RMA (customer → warehouse). Applying a rate adds the deduction to the
credit note and updates the RMA return shipping cost.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class ShippoRateWizardRma(models.TransientModel):
    _inherit = "shippo.rate.wizard"

    credit_note_id = fields.Many2one(
        "account.move",
        string="Credit Note",
        ondelete="cascade",
        help="When set, applying a rate adds the return shipping deduction to this credit note.",
    )
    rma_id = fields.Many2one(
        "rma",
        string="RMA",
        ondelete="cascade",
        help="RMA for this return shipment (customer → warehouse).",
    )
    rma_return_from_display = fields.Char(
        string="Return From",
        compute="_compute_rma_return_address_display",
        help="Customer address (ship-from) for return rates.",
    )
    rma_return_to_display = fields.Char(
        string="Return To",
        compute="_compute_rma_return_address_display",
        help="Warehouse address (ship-to) for return rates.",
    )

    @api.depends("rma_id", "rma_id.partner_shipping_id", "rma_id.warehouse_id")
    def _compute_rma_return_address_display(self):
        for wiz in self:
            if not wiz.rma_id:
                wiz.rma_return_from_display = False
                wiz.rma_return_to_display = False
                continue
            rma = wiz.rma_id
            # From = customer (return ship-from)
            partner_from = rma.partner_shipping_id
            if partner_from:
                wiz.rma_return_from_display = self._format_address_display(partner_from)
            else:
                wiz.rma_return_from_display = _("(No customer address)")
            # To = warehouse
            wh = rma.warehouse_id
            partner_to = wh.partner_id if wh else rma.company_id.partner_id
            if partner_to:
                wiz.rma_return_to_display = self._format_address_display(partner_to)
            else:
                wiz.rma_return_to_display = _("(No warehouse address)")

    def _format_address_display(self, partner):
        parts = [partner.name or ""]
        if partner.street:
            parts.append(partner.street)
        if partner.street2:
            parts.append(partner.street2)
        city_state_zip = []
        if partner.city:
            city_state_zip.append(partner.city)
        if partner.state_id:
            city_state_zip.append(partner.state_id.code or partner.state_id.name)
        if partner.zip:
            city_state_zip.append(partner.zip)
        if city_state_zip:
            parts.append(", ".join(city_state_zip))
        if partner.country_id:
            parts.append(partner.country_id.name)
        return " | ".join(p for p in parts if p).strip() or partner.display_name

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        rma_id = self.env.context.get("default_rma_id")
        credit_note_id = self.env.context.get("default_credit_note_id")
        if rma_id and credit_note_id and "rma_id" in fields_list:
            res["rma_id"] = rma_id
            res["credit_note_id"] = credit_note_id
            rma = self.env["rma"].browse(rma_id)
            if rma.exists():
                # Use warehouse origin so the form has a valid origin_address_id
                # (for return we use customer→warehouse in action_get_rates)
                origin = self.env["shippo.origin.address"].search(
                    [("company_id", "in", (rma.company_id.id, False))],
                    limit=1,
                )
                if origin and "origin_address_id" in fields_list:
                    res["origin_address_id"] = origin.id
                if "delivery_carrier_id" in fields_list and not res.get("delivery_carrier_id"):
                    carrier = self.env["delivery.carrier"].search(
                        [("delivery_type", "=", "shippo")],
                        limit=1,
                    )
                    if carrier:
                        res["delivery_carrier_id"] = carrier.id
                product = rma.product_id
                if not product and credit_note_id:
                    move = self.env["account.move"].browse(credit_note_id)
                    if move.exists():
                        line = move.invoice_line_ids.filtered(
                            lambda l: l.product_id and l.product_id.type in ("product", "consu")
                        )[:1]
                        if line:
                            product = line.product_id
                if product:
                    # Always set weight and dimensions from product so they are applied
                    res["weight"] = max(1.0, float(product.weight or 0))
                    res["length"] = float(getattr(product, "product_length", None) or 10)
                    res["width"] = float(getattr(product, "product_width", None) or 8)
                    res["height"] = float(getattr(product, "product_height", None) or 6)
                    res["custom_package"] = True
        return res

    def _build_destination_address(self):
        """For RMA return: destination is warehouse (return-to)."""
        if self.rma_id:
            warehouse = self.rma_id.warehouse_id
            partner = warehouse.partner_id or self.rma_id.company_id.partner_id
            if not partner:
                raise UserError(_("No warehouse/company address for return destination."))
            state_code = ""
            if partner.state_id:
                state_code = partner.state_id.code or partner.state_id.name or ""
            elif getattr(partner, "state", None):
                state_code = partner.state
            # Minimal address: omit is_residential so carriers that don't support it still return rates
            return {
                "name": partner.name or "Warehouse",
                "street1": partner.street or " ",
                "street2": partner.street2 or "",
                "city": partner.city or " ",
                "state": state_code,
                "zip": partner.zip or " ",
                "country": partner.country_id.code if partner.country_id else "US",
                "phone": partner.phone or "",
            }
        return super()._build_destination_address()

    def action_get_rates(self):
        """When rma_id is set, use customer as origin and warehouse as destination."""
        if self.rma_id and self.credit_note_id:
            return self._action_get_rates_rma_return()
        return super().action_get_rates()

    def _action_get_rates_rma_return(self):
        """Get rates for return shipment: customer (from) → warehouse (to)."""
        self.ensure_one()
        from odoo.addons.delivery_shippo_iag.models.shippo_api import (
            create_address,
            create_shipment,
            get_shipment_rates,
            ShippoAPIError,
            _address_is_valid_for_shipment,
        )

        api_key = self._get_api_key()
        use_test = self._use_test_env()
        rma = self.rma_id
        partner_from = rma.partner_shipping_id
        state_code = ""
        if partner_from.state_id:
            state_code = partner_from.state_id.code or partner_from.state_id.name or ""
        elif getattr(partner_from, "state", None):
            state_code = partner_from.state
        # Minimal address: omit is_residential so carriers that don't support it still return rates
        address_from_dict = {
            "name": partner_from.name or "Customer",
            "street1": partner_from.street or " ",
            "street2": partner_from.street2 or "",
            "city": partner_from.city or " ",
            "state": state_code,
            "zip": partner_from.zip or " ",
            "country": partner_from.country_id.code if partner_from.country_id else "US",
            "phone": partner_from.phone or "",
        }
        try:
            addr_from = create_address(
                api_key,
                address_from_dict,
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate the return-from (customer) address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_from):
            raise UserError(
                _("Return-from (customer) address could not be validated by Shippo.")
            )
        address_from_id = addr_from.get("object_id")
        if not address_from_id:
            raise UserError(_("Shippo did not return a valid return-from address."))

        address_to_dict = self._build_destination_address()
        try:
            addr_to = create_address(
                api_key,
                address_to_dict,
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate the return-to (warehouse) address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_to):
            raise UserError(
                _("Return-to (warehouse) address could not be validated by Shippo.")
            )
        address_to_id = addr_to.get("object_id")
        if not address_to_id:
            raise UserError(_("Shippo did not return a valid return-to address."))

        parcels = self._build_parcel()
        # Enforce minimum weight and dimensions so carriers return rates (many have 1 lb / 1 in minimums)
        for p in parcels:
            w = max(1.0, float(p.get("weight", 1)))
            p["weight"] = str(round(w, 2))
            for dim in ("length", "width", "height"):
                if dim in p:
                    p[dim] = str(max(1, round(float(p[dim]), 2)))
        carrier_accounts = self._get_carrier_account_ids()
        # Omit extra (insurance, signature) for RMA return rate request so all carrier accounts
        # return rates; some (e.g. COURIERSPLEASE, USPS) fail with "doesn't support shipment options"
        # or "Invalid Shipment Contents Value" when optional options are included.
        try:
            shipment = create_shipment(
                api_key,
                address_from=address_from_id,
                address_to=address_to_id,
                parcels=parcels,
                carrier_accounts=carrier_accounts if carrier_accounts else None,
                extra=None,
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo shipment/rates failed: %s", str(e))) from e
        # Same rate extraction as base wizard; Shippo may omit rates in create response and require GET
        rates = shipment.get("rates") or shipment.get("rates_list") or []
        rates_payload = None
        if not rates and shipment.get("object_id"):
            try:
                rates_payload = get_shipment_rates(
                    api_key,
                    shipment["object_id"],
                    currency="USD",
                    use_test_env=use_test,
                )
                if isinstance(rates_payload, list):
                    rates = rates_payload
                else:
                    rates = rates_payload.get("rates") or rates_payload.get("results") or rates_payload.get("rates_list") or []
            except ShippoAPIError as e:
                _logger.warning(
                    "Shippo get_shipment_rates failed for return shipment %s: %s",
                    shipment.get("object_id"),
                    e,
                )
        if not rates:
            # Build user-visible message from Shippo's messages (explains why no rates)
            shippo_messages = shipment.get("messages") or []
            if rates_payload and isinstance(rates_payload, dict):
                shippo_messages = shippo_messages or rates_payload.get("messages") or []
            msg_texts = []
            for m in shippo_messages if isinstance(shippo_messages, list) else []:
                if isinstance(m, dict):
                    msg_texts.append(m.get("text") or m.get("message") or str(m))
                else:
                    msg_texts.append(str(m))
            shippo_reason = (" Shippo: %s." % " | ".join(msg_texts)) if msg_texts else ""
            _logger.warning(
                "Shippo return shipment returned no rates. object_id=%s keys=%s messages=%s",
                shipment.get("object_id"),
                list(shipment.keys()),
                shippo_messages,
            )
            rate_type_label = self.rate_type or "unknown"
            if self.rate_type == "negotiated":
                hint = _(
                    "Negotiated accounts are often for outbound only; try Rate Type: Published. "
                )
            else:
                hint = _(
                    "Check that customer and warehouse addresses are complete and valid, "
                    "and that your Shippo account has carriers enabled for this lane. "
                )
            raise UserError(
                _(
                    "No shipping rates available for this return shipment (rate type: %s).%s "
                    "%sIf still stuck: ensure weight ≥ 1 lb and addresses are valid."
                )
                % (rate_type_label, shippo_reason, hint)
            )
        # Same sort as base: by carrier then amount
        def _rate_sort_key(r):
            provider = r.get("provider") or r.get("carrier") or ""
            amount = float(r.get("amount_local") or r.get("amount") or 0)
            return (provider, amount)

        rates = sorted(rates, key=_rate_sort_key)

        Line = self.env["shippo.rate.wizard.line"]
        self.rate_line_ids.unlink()
        for r in rates:
            amount = float(r.get("amount_local") or r.get("amount") or 0)
            provider = r.get("provider") or r.get("carrier") or ""
            sl = r.get("servicelevel") or {}
            token = sl.get("token") if isinstance(sl, dict) else getattr(sl, "token", "")
            markup_amt, final = self._apply_markup(provider, token, amount)
            service_name = sl.get("name", "") if isinstance(sl, dict) else getattr(sl, "name", "")
            Line.create({
                "wizard_id": self.id,
                "rate_object_id": r.get("object_id"),
                "carrier": provider,
                "service_name": service_name,
                "estimated_days": r.get("estimated_days"),
                "amount": amount,
                "markup": markup_amt,
                "final_amount": final,
                "currency": r.get("currency_local") or r.get("currency") or "USD",
            })
        self.state = "rates"
        return {
            "type": "ir.actions.act_window",
            "name": _("Get Shippo Rates"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self.env.context,
        }

    def action_apply_to_credit_note(self):
        """Apply the selected rate as return shipping deduction on the credit note."""
        self.ensure_one()
        if not self.credit_note_id or not self.rma_id:
            raise UserError(_("This action is only for RMA return credit notes."))
        selected = self.selected_rate_id
        if not selected:
            raise UserError(_("Please select a rate first."))
        credit_note = self.credit_note_id
        rma = self.rma_id
        shipping_cost = selected.final_amount
        rma.write({"return_shipping_cost": shipping_cost})
        product = self.env.ref(
            "rma_odoo_19.product_return_shipping_fee",
            raise_if_not_found=False,
        )
        line_name = _(
            "Return Shipping - %(carrier)s %(service)s",
            carrier=selected.carrier or "",
            service=selected.service_name or "",
        )
        # Account for the deduction line: use product income account or first line's account
        account_id = None
        if product:
            account_id = (
                product.property_account_income_id
                or product.categ_id.property_account_income_categ_id
            )
        if not account_id and credit_note.invoice_line_ids:
            account_id = credit_note.invoice_line_ids[0].account_id
        if not account_id and credit_note.journal_id:
            account_id = credit_note.journal_id.default_account_id

        if not account_id:
            raise UserError(
                _(
                    "Could not determine account for return shipping line. "
                    "Set an income account on the Return Shipping Fee product or on the credit note."
                )
            )
        existing_line = credit_note.rma_return_shipping_line_id
        line_vals = {
            "name": line_name,
            "quantity": 1,
            "price_unit": -shipping_cost,
            "tax_ids": [(5, 0, 0)],
            "account_id": account_id.id,
        }
        if product:
            line_vals["product_id"] = product.id

        if existing_line:
            credit_note.write({
                "invoice_line_ids": [
                    (1, existing_line.id, line_vals)
                ],
            })
        else:
            credit_note.write({
                "invoice_line_ids": [(0, 0, line_vals)],
            })

        # Reload and link the line to the credit note for future updates
        credit_note.invalidate_recordset()
        precision = self.env["decimal.precision"].precision_get("Product Price")
        shipping_line = credit_note.invoice_line_ids.filtered(
            lambda l: "Return Shipping" in (l.name or "")
            and float_compare(l.price_unit, -shipping_cost, precision_digits=precision) == 0
        )[:1]
        if not shipping_line and credit_note.invoice_line_ids:
            shipping_line = credit_note.invoice_line_ids.filtered(
                lambda l: "Return Shipping" in (l.name or "")
            )[-1:]
        if shipping_line:
            credit_note.rma_return_shipping_line_id = shipping_line
        return {"type": "ir.actions.act_window_close"}


class ShippoRateWizardLineRma(models.TransientModel):
    """When user clicks Select Rate from credit note wizard, apply to credit note in one click."""

    _inherit = "shippo.rate.wizard.line"

    def action_select_rate(self):
        res = super().action_select_rate()
        wizard = self.wizard_id
        if getattr(wizard, "credit_note_id", None) and wizard.credit_note_id:
            return wizard.action_apply_to_credit_note()
        return res
