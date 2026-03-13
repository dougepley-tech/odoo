import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RmaCreditNoteShippingWizard(models.TransientModel):
    _name = "rma.credit.note.shipping.wizard"
    _description = "Select Shippo Return Shipping Rate for Credit Note"

    credit_note_id = fields.Many2one(
        comodel_name="account.move",
        string="Credit Note",
        required=True,
    )
    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA",
        required=True,
    )
    state = fields.Selection(
        [("init", "Init"), ("rates", "Select Rate")],
        default="init",
    )
    carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Delivery Method",
        domain="[('delivery_type', '=', 'shippo')]",
    )
    # Package: standard type or editable weight/dimensions
    package_type_id = fields.Many2one(
        comodel_name="stock.package.type",
        string="Package Type",
        help="Select a standard package (Inventory → Package Types). Dimensions come from the type.",
    )
    custom_package = fields.Boolean(
        string="Custom Package",
        default=False,
        help="Check to enter length, width, and height manually instead of using a package type.",
    )
    length = fields.Float(string="Length (in)", default=10)
    width = fields.Float(string="Width (in)", default=10)
    height = fields.Float(string="Height (in)", default=10)
    weight = fields.Float(string="Weight (lb)", default=1.0)
    distance_unit = fields.Selection(
        [("in", "in"), ("cm", "cm")],
        default="in",
        string="Dimension Unit",
    )
    mass_unit = fields.Selection(
        [("lb", "lb"), ("kg", "kg")],
        default="lb",
        string="Mass Unit",
    )
    rate_line_ids = fields.One2many(
        comodel_name="rma.credit.note.shipping.wizard.line",
        inverse_name="wizard_id",
        string="Available Rates",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        rma_id = res.get("rma_id")
        credit_note_id = res.get("credit_note_id")
        if rma_id and credit_note_id and any(f in fields_list for f in ("weight", "length", "width", "height")):
            rma = self.env["rma"].browse(rma_id)
            credit_note = self.env["account.move"].browse(credit_note_id)
            parcel = self._compute_initial_parcel_from_returned(rma, credit_note)
            if "weight" in fields_list:
                res["weight"] = parcel["weight"]
            if "length" in fields_list:
                res["length"] = parcel["length"]
            if "width" in fields_list:
                res["width"] = parcel["width"]
            if "height" in fields_list:
                res["height"] = parcel["height"]
        return res

    @api.model_create_multi
    def create(self, vals_list):
        """Pre-fill weight/dimensions from returned products when wizard is opened with rma + credit note."""
        for vals in vals_list:
            rma_id = vals.get("rma_id")
            credit_note_id = vals.get("credit_note_id")
            if rma_id and credit_note_id:
                rma = self.env["rma"].browse(rma_id)
                credit_note = self.env["account.move"].browse(credit_note_id)
                parcel = self._compute_initial_parcel_from_returned(rma, credit_note)
                vals.setdefault("weight", parcel["weight"])
                vals.setdefault("length", parcel["length"])
                vals.setdefault("width", parcel["width"])
                vals.setdefault("height", parcel["height"])
        return super().create(vals_list)

    @api.model
    def _compute_initial_parcel_from_returned(self, rma, credit_note):
        """Return dict with weight, length, width, height (numbers) from returned products."""
        # Prefer lines linked to this RMA; fallback to all product lines (e.g. if rma_id not set on draft)
        lines = credit_note.invoice_line_ids.filtered(
            lambda l: l.product_id and l.quantity > 0 and (l.rma_id == rma or not l.rma_id)
        )
        if lines:
            total_weight = sum(
                (l.product_id.weight or 0.0) * l.quantity for l in lines
            )
            products_with_dims = list(lines.mapped("product_id"))
        elif rma.kit_component_ids:
            kit_lines = rma.kit_component_ids.filtered(
                lambda kl: kl.product_uom_qty > 0 and kl.product_id
            )
            if kit_lines:
                total_weight = sum(
                    (kl.product_id.weight or 0.0) * kl.product_uom_qty
                    for kl in kit_lines
                )
                products_with_dims = kit_lines.mapped("product_id")
            else:
                total_weight = rma.product_id.weight or 1.0
                products_with_dims = [rma.product_id]
        else:
            product = rma.product_id
            total_weight = product.weight or 1.0
            products_with_dims = [product]
        # Use summed weight when we have product lines; minimum 0.01 for Shippo, 1.0 only when no lines
        weight = float(total_weight) if total_weight > 0 else (1.0 if not lines else 0.01)
        weight = max(0.01, weight)
        length = width = height = 10
        if len(products_with_dims) == 1:
            p = products_with_dims[0]
            length = max(1, float(getattr(p, "product_length", None) or 10))
            width = max(1, float(getattr(p, "product_width", None) or 10))
            height = max(1, float(getattr(p, "product_height", None) or 10))
        elif products_with_dims:
            length = max(1, max(float(getattr(p, "product_length", None) or 10) for p in products_with_dims))
            width = max(1, max(float(getattr(p, "product_width", None) or 10) for p in products_with_dims))
            height = max(1, sum(float(getattr(p, "product_height", None) or 10) for p in products_with_dims))
        return {"weight": weight, "length": length, "width": width, "height": height}

    def _get_carrier_account_ids(self, carrier):
        """Return list of Shippo carrier_account object_ids for create_shipment.
        When empty, Shippo returns published rates; when set, negotiated rates.
        """
        if getattr(carrier, "shippo_carrier_account_ids", None):
            accounts = carrier.shippo_carrier_account_ids.filtered("active")
            if accounts:
                return [a.object_id for a in accounts if a.object_id]
        accounts = self.env["shippo.carrier.account"].search(
            [("company_id", "in", (self.env.company.id, False)), ("active", "=", True)]
        )
        return [a.object_id for a in accounts if a.object_id]

    def _get_parcel_from_returned_products(self):
        """Build parcel dict (weight + dimensions) from products actually being returned.
        Uses credit note lines (or RMA kit components) so kit returns use component weights, not the kit product.
        """
        self.ensure_one()
        rma = self.rma_id
        # Prefer credit note product lines (what we're actually refunding)
        lines = self.credit_note_id.invoice_line_ids.filtered(
            lambda l: l.product_id and l.rma_id == rma and l.quantity > 0
        )
        if lines:
            total_weight = sum(
                (l.product_id.weight or 0.0) * l.quantity for l in lines
            )
            products_with_dims = list(lines.mapped("product_id"))
        elif rma.kit_component_ids:
            # No credit note lines yet: use RMA kit components with qty to return
            kit_lines = rma.kit_component_ids.filtered(lambda kl: kl.product_uom_qty > 0 and kl.product_id)
            if kit_lines:
                total_weight = sum(
                    (kl.product_id.weight or 0.0) * kl.product_uom_qty
                    for kl in kit_lines
                )
                products_with_dims = kit_lines.mapped("product_id")
            else:
                total_weight = rma.product_id.weight or 1.0
                products_with_dims = [rma.product_id]
        else:
            product = rma.product_id
            total_weight = product.weight or 1.0
            products_with_dims = [product]

        weight = max(1.0, float(total_weight))
        length = width = height = 10
        if len(products_with_dims) == 1:
            p = products_with_dims[0]
            length = max(1, int(getattr(p, "product_length", None) or 10))
            width = max(1, int(getattr(p, "product_width", None) or 10))
            height = max(1, int(getattr(p, "product_height", None) or 10))
        elif products_with_dims:
            # Multiple products: max L/W, sum heights (stacked)
            length = max(1, max(int(getattr(p, "product_length", None) or 10) for p in products_with_dims))
            width = max(1, max(int(getattr(p, "product_width", None) or 10) for p in products_with_dims))
            height = max(1, sum(int(getattr(p, "product_height", None) or 10) for p in products_with_dims))
        return {
            "length": str(length),
            "width": str(width),
            "height": str(height),
            "distance_unit": "in",
            "weight": str(round(weight, 2)),
            "mass_unit": "lb",
        }

    def _get_parcel_for_rates(self):
        """Build one parcel dict for Shippo from wizard: package type or editable weight/dimensions."""
        self.ensure_one()
        # Custom dimensions (user-edited length/width/height)
        if self.custom_package:
            length_in = self._dim_to_inches(self.length)
            width_in = self._dim_to_inches(self.width)
            height_in = self._dim_to_inches(self.height)
            weight_lb = self._weight_to_lb(self.weight)
            return {
                "length": str(round(max(0.1, length_in), 2)),
                "width": str(round(max(0.1, width_in), 2)),
                "height": str(round(max(0.1, height_in), 2)),
                "distance_unit": "in",
                "weight": str(round(max(0.1, weight_lb), 2)),
                "mass_unit": "lb",
            }
        # Standard package type
        if self.package_type_id:
            pt = self.package_type_id
            length_uom = getattr(pt, "length_uom_name", None) or "in"
            w_uom = getattr(pt, "weight_uom_name", None) or "lb"
            length_val = float(getattr(pt, "packaging_length", 0) or 1)
            width_val = float(getattr(pt, "width", 0) or 1)
            height_val = float(getattr(pt, "height", 0) or 1)
            if length_uom and "cm" in (length_uom or "").lower():
                length_in = length_val * 0.393701
                width_in = width_val * 0.393701
                height_in = height_val * 0.393701
            else:
                length_in, width_in, height_in = length_val, width_val, height_val
            base_w = float(getattr(pt, "base_weight", 0) or 0)
            if w_uom and "kg" in (w_uom or "").lower():
                weight_lb = base_w * 2.20462
            else:
                weight_lb = base_w
            if weight_lb <= 0:
                weight_lb = self._weight_to_lb(self.weight or 1.0)
            return {
                "length": str(round(max(0.1, length_in), 2)),
                "width": str(round(max(0.1, width_in), 2)),
                "height": str(round(max(0.1, height_in), 2)),
                "distance_unit": "in",
                "weight": str(round(max(0.1, weight_lb), 2)),
                "mass_unit": "lb",
            }
        # No package type: use wizard dimensions/weight (editable, prefilled from products)
        length_in = self._dim_to_inches(self.length)
        width_in = self._dim_to_inches(self.width)
        height_in = self._dim_to_inches(self.height)
        weight_lb = self._weight_to_lb(self.weight)
        return {
            "length": str(round(max(0.1, length_in), 2)),
            "width": str(round(max(0.1, width_in), 2)),
            "height": str(round(max(0.1, height_in), 2)),
            "distance_unit": "in",
            "weight": str(round(max(0.1, weight_lb), 2)),
            "mass_unit": "lb",
        }

    def _dim_to_inches(self, value):
        """Convert dimension to inches (wizard distance_unit)."""
        if self.distance_unit == "cm":
            return float(value or 0) * 0.393701
        return float(value or 0)

    def _weight_to_lb(self, value):
        """Convert weight to lb (wizard mass_unit)."""
        if self.mass_unit == "kg":
            return float(value or 0) * 2.20462
        return float(value or 0)

    def _partner_to_shippo_address(self, partner, name_fallback="Address"):
        """Build address dict for Shippo create_address (return: customer → warehouse)."""
        state_code = ""
        if partner.state_id:
            state_code = partner.state_id.code or partner.state_id.name or ""
        elif getattr(partner, "state", None):
            state_code = partner.state
        return {
            "name": partner.name or name_fallback,
            "street1": partner.street or " ",
            "street2": partner.street2 or "",
            "city": partner.city or " ",
            "state": state_code,
            "zip": partner.zip or " ",
            "country": partner.country_id.code if partner.country_id else "US",
            "phone": partner.phone or "",
        }

    def action_get_rates(self):
        self.ensure_one()
        if not self.carrier_id:
            raise UserError(_("Please select a Shippo delivery method."))

        carrier = self.carrier_id
        api_key, use_test = carrier._get_shippo_api_key_and_env()
        if not api_key:
            raise UserError(
                _("No Shippo API key configured for %s.", carrier.name)
            )

        from odoo.addons.delivery_shippo_iag.models.shippo_api import (
            create_address,
            create_shipment,
            ShippoAPIError,
        )

        partner = self.rma_id.partner_shipping_id
        address_from_dict = self._partner_to_shippo_address(
            partner, name_fallback=_("Customer")
        )
        try:
            address_from = create_address(
                api_key,
                address_from_dict,
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate the return-from address: %s", str(e))
            ) from e

        warehouse = self.rma_id.warehouse_id
        wh_partner = warehouse.partner_id or self.rma_id.company_id.partner_id
        address_to_dict = self._partner_to_shippo_address(
            wh_partner, name_fallback=_("Warehouse")
        )
        try:
            address_to = create_address(
                api_key,
                address_to_dict,
                validate=True,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate the return-to (warehouse) address: %s", str(e))
            ) from e

        address_from_id = address_from.get("object_id") if isinstance(address_from, dict) else None
        address_to_id = address_to.get("object_id") if isinstance(address_to, dict) else None
        if not address_from_id or not address_to_id:
            raise UserError(
                _("Shippo did not return valid address IDs for the return shipment.")
            )

        carrier_accounts = self._get_carrier_account_ids(carrier)
        parcel = self._get_parcel_for_rates()

        try:
            shipment = create_shipment(
                api_key,
                address_from=address_from_id,
                address_to=address_to_id,
                parcels=[parcel],
                carrier_accounts=carrier_accounts or None,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo shipment/rates failed: %s", str(e))) from e

        rates = shipment.get("rates") or []
        if not rates and carrier_accounts:
            try:
                shipment = create_shipment(
                    api_key,
                    address_from=address_from_id,
                    address_to=address_to_id,
                    parcels=[parcel],
                    carrier_accounts=None,
                    use_test_env=use_test,
                )
                rates = shipment.get("rates") or []
            except ShippoAPIError:
                pass
        if not rates:
            raise UserError(
                _(
                    "No shipping rates available for this return shipment. "
                    "Check that the Shippo delivery method has carrier accounts connected "
                    "(Delivery Methods → Shippo Shipping → Carrier Accounts), or that "
                    "addresses and parcel dimensions are valid for the route."
                )
            )

        def _rate_sort_key(r):
            provider = r.get("provider") or r.get("carrier") or ""
            amount = float(r.get("amount_local") or r.get("amount") or 0)
            return (provider, amount)
        rates = sorted(rates, key=_rate_sort_key)

        # Use same markup as sales/delivery: delivery method's Margin on Rate %,
        # fixed margin, and Shippo markup rules (Delivery Methods → Shippo Shipping).
        markup_helper = self.env["shippo.rate.wizard"].new({
            "delivery_carrier_id": self.carrier_id.id,
        })
        seen = set()
        line_vals = []
        for rate in rates:
            amount = float(rate.get("amount_local") or rate.get("amount") or 0)
            provider = (rate.get("provider") or rate.get("carrier") or "").strip()
            sl = rate.get("servicelevel") or {}
            token = sl.get("token", "") if isinstance(sl, dict) else getattr(sl, "token", "")
            service_name = sl.get("name", "") if isinstance(sl, dict) else getattr(sl, "name", "")
            key = (provider, service_name, amount)
            if key in seen:
                continue
            seen.add(key)
            markup_amt, final = markup_helper._apply_markup(provider, token, amount)
            line_vals.append({
                "wizard_id": self.id,
                "shippo_rate_id": rate.get("object_id", ""),
                "carrier_name": provider,
                "service_name": service_name,
                "amount": amount,
                "markup": markup_amt,
                "final_amount": final,
                "currency": rate.get("currency_local") or rate.get("currency", "USD"),
                "estimated_days": rate.get("estimated_days") or 0,
            })
        line_vals.sort(key=lambda v: (v["carrier_name"].upper(), v["service_name"] or "", v["amount"]))
        self.rate_line_ids = [(5, 0, 0)]
        self.rate_line_ids = [(0, 0, v) for v in line_vals]
        self.state = "rates"

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _generate_return_label(self, selected):
        """Purchase return label via Shippo, update RMA, attach PDF."""
        self.ensure_one()
        carrier = self.carrier_id
        api_key, use_test = carrier._get_shippo_api_key_and_env()
        if not api_key:
            raise UserError(
                _("No Shippo API key configured for %s.", carrier.name)
            )
        from odoo.addons.delivery_shippo_iag.models.shippo_api import (
            create_transaction,
            get_transaction,
            ShippoAPIError,
        )
        try:
            transaction = create_transaction(
                api_key,
                selected.shippo_rate_id,
                label_file_type="PDF_4x6",
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo label purchase failed: %s", str(e))) from e
        import time
        if transaction.get("status") == "QUEUED" and transaction.get("object_id"):
            for _attempt in range(30):
                time.sleep(1)
                transaction = get_transaction(
                    api_key,
                    transaction["object_id"],
                    use_test_env=use_test,
                )
                if transaction.get("status") != "QUEUED":
                    break
        if transaction.get("status") != "SUCCESS":
            msg = transaction.get("messages") or []
            error_text = (
                msg[0].get("text", "Unknown error")
                if msg and isinstance(msg[0], dict)
                else "Unknown error"
            )
            raise UserError(_("Label generation failed: %s", error_text))
        tracking_number = transaction.get("tracking_number", "")
        tracking_url = (
            transaction.get("tracking_url_provider", "")
            or transaction.get("tracking_url", "")
        )
        label_url = transaction.get("label_url", "") or transaction.get("label_url")
        transaction_id = transaction.get("object_id", "")
        rma = self.rma_id
        if not label_url and transaction_id:
            for _attempt in range(5):
                time.sleep(2)
                try:
                    transaction = get_transaction(
                        api_key, transaction_id, use_test_env=use_test
                    )
                    label_url = transaction.get("label_url", "") or ""
                    if label_url:
                        break
                except ShippoAPIError as e:
                    _logger.warning(
                        "Refetch Shippo transaction for label URL (attempt %s): %s",
                        _attempt + 1,
                        e,
                    )
        rma.write({
            "return_label_generated": True,
            "return_shippo_transaction_id": transaction_id,
            "return_tracking_number": tracking_number,
            "return_tracking_url": tracking_url,
        })
        attachment_ids = []
        if label_url:
            import requests
            try:
                resp = requests.get(label_url, timeout=30)
                if resp.status_code == 200 and resp.content:
                    pdf_b64 = base64.b64encode(resp.content).decode("ascii")
                    att_rma = self.env["ir.attachment"].create({
                        "name": f"Return_Label_{rma.name}.pdf",
                        "type": "binary",
                        "datas": pdf_b64,
                        "res_model": "rma",
                        "res_id": rma.id,
                        "mimetype": "application/pdf",
                    })
                    attachment_ids.append(att_rma.id)
                    credit_note = self.credit_note_id
                    # Note only on credit note (no PDF) to avoid giant document preview; PDF is on RMA chatter
                    credit_note.with_context(mail_post_autofollow=False).message_post(
                        body=_(
                            "Return shipping label generated (from credit note). "
                            "Carrier: %(carrier)s %(service)s, Cost: $%(cost).2f, Tracking: %(tracking)s. "
                            "Label PDF is attached on the linked RMA.",
                            carrier=selected.carrier_name,
                            service=selected.service_name,
                            cost=selected.amount,
                            tracking=tracking_number,
                        ),
                        subtype_xmlid="mail.mt_note",
                    )
            except Exception as e:
                _logger.warning(
                    "Could not download return label PDF for RMA %s: %s",
                    rma.name,
                    e,
                )
                rma.message_post(
                    body=_(
                        "Return label purchased but PDF download failed (%s). "
                        "You can retrieve the label from the carrier using tracking: %s",
                        str(e),
                        tracking_number,
                    ),
                )
        body = _(
            "Return shipping label generated (from credit note). "
            "Carrier: %(carrier)s %(service)s, Cost: $%(cost).2f, Tracking: %(tracking)s",
            carrier=selected.carrier_name,
            service=selected.service_name,
            cost=selected.amount,
            tracking=tracking_number,
        )
        if not label_url:
            body += _(
                " The label PDF was not yet available from Shippo; "
                "check the carrier site using the tracking number to print the label."
            )
        # Post note with PDF attached so it appears in RMA chatter
        rma.message_post(body=body, attachment_ids=attachment_ids)

    def action_apply_rate(self):
        """Generate return label, then add selected rate as deduction on the credit note."""
        self.ensure_one()
        selected = self.rate_line_ids.filtered("is_selected")
        if not selected:
            raise UserError(_("Please select a rate first."))
        selected = selected[0]

        shipping_cost = getattr(selected, "final_amount", None) or selected.amount
        credit_note = self.credit_note_id
        rma = self.rma_id

        self._generate_return_label(selected)

        rma.write({"return_shipping_cost": shipping_cost, "charge_return_shipping": True})

        product = self.env.ref(
            "rma_odoo_19.product_return_shipping_fee", raise_if_not_found=False
        )
        line_name = _(
            "Return Shipping - %(carrier)s %(service)s",
            carrier=selected.carrier_name,
            service=selected.service_name,
        )

        existing_line = credit_note.rma_return_shipping_line_id
        if existing_line:
            credit_note.write({
                "invoice_line_ids": [(1, existing_line.id, {
                    "name": line_name,
                    "price_unit": -shipping_cost,
                })],
            })
        else:
            credit_note.write({
                "invoice_line_ids": [(0, 0, {
                    "name": line_name,
                    "product_id": product and product.id or False,
                    "quantity": 1,
                    "price_unit": -shipping_cost,
                    "tax_ids": [(5, 0, 0)],
                })],
            })
            shipping_line = credit_note.invoice_line_ids.filtered(
                lambda l: l.name == line_name and l.price_unit == -shipping_cost
            )[:1]
            if shipping_line:
                credit_note.rma_return_shipping_line_id = shipping_line

        return {"type": "ir.actions.act_window_close"}


class RmaCreditNoteShippingWizardLine(models.TransientModel):
    _name = "rma.credit.note.shipping.wizard.line"
    _description = "Credit Note Shippo Rate Line"

    wizard_id = fields.Many2one(
        comodel_name="rma.credit.note.shipping.wizard",
        ondelete="cascade",
    )
    shippo_rate_id = fields.Char(string="Shippo Rate ID")
    carrier_name = fields.Char(string="Carrier")
    service_name = fields.Char(string="Service")
    amount = fields.Float(string="Cost")
    markup = fields.Float(string="Markup")
    final_amount = fields.Float(string="Total")
    currency = fields.Char(string="Currency", default="USD")
    estimated_days = fields.Integer(string="Est. Days")
    is_selected = fields.Boolean(string="Select")

    def action_select_rate(self):
        """Select this rate, generate return label, add deduction to credit note, close wizard."""
        self.ensure_one()
        self.wizard_id.rate_line_ids.write({"is_selected": False})
        self.is_selected = True
        return self.wizard_id.action_apply_rate()
