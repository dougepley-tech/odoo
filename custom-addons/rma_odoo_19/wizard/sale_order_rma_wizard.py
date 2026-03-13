from markupsafe import Markup

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_compare


class SaleOrderRmaWizardReturnRate(models.TransientModel):
    _name = "sale.order.rma.wizard.return.rate"
    _description = "Create RMA wizard: return shipping rate line"

    wizard_id = fields.Many2one(
        comodel_name="sale.order.rma.wizard",
        ondelete="cascade",
    )
    carrier = fields.Char(string="Carrier")
    service_name = fields.Char(string="Service")
    amount = fields.Float(string="Amount")
    rate_object_id = fields.Char(string="Shippo Rate ID")
    currency = fields.Char(default="USD")

    def action_select_rate(self):
        """Set this rate as selected on the wizard; then reopen main Create RMA form so user can continue."""
        self.ensure_one()
        if self.wizard_id:
            self.wizard_id.selected_return_rate_id = self
            # Reopen the main Create RMA wizard (so user can click Create RMA(s)); do not just close
            return {
                "type": "ir.actions.act_window",
                "name": _("RMA"),
                "res_model": self.wizard_id._name,
                "res_id": self.wizard_id.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "new",
            }
        return True


class SaleOrderRmaWizard(models.TransientModel):
    _name = "sale.order.rma.wizard"
    _description = "Sale Order RMA Wizard"

    def _default_location_id(self):
        return self._get_effective_location_id()

    def _get_effective_location_id(self):
        """Resolve RMA location: config param, then order warehouse, then first internal."""
        loc_id = self.env["ir.config_parameter"].sudo().get_param(
            "rma_odoo_19.default_location_id", False
        )
        if loc_id:
            loc = self.env["stock.location"].browse(int(loc_id)).exists()
            if loc:
                return loc.id
        order = self.order_id or self.env["sale.order"].browse(
            self.env.context.get("active_id")
        )
        if order and order.warehouse_id.rma_loc_id:
            return order.warehouse_id.rma_loc_id.id
        company = order.company_id if order else self.env.company
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)], limit=1
        )
        if warehouse.rma_loc_id:
            return warehouse.rma_loc_id.id
        loc = self.env["stock.location"].search(
            [("usage", "=", "internal"), ("company_id", "=", company.id)],
            limit=1,
        )
        return loc.id if loc else False

    operation_id = fields.Many2one(
        comodel_name="rma.operation",
        string="Requested operation",
    )
    reason_id = fields.Many2one(
        comodel_name="rma.reason",
        string="Return Reason",
    )
    order_id = fields.Many2one(
        comodel_name="sale.order",
        default=lambda self: self.env.context.get("active_id", False),
    )
    line_ids = fields.One2many(
        comodel_name="sale.order.line.rma.wizard",
        inverse_name="wizard_id",
        string="Lines",
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="RMA location",
        domain="[('usage', '=', 'internal')]",
        default=_default_location_id,
    )
    commercial_partner_id = fields.Many2one(
        comodel_name="res.partner",
        related="order_id.partner_id.commercial_partner_id",
        string="Commercial entity",
    )
    partner_shipping_id = fields.Many2one(
        comodel_name="res.partner",
        string="Shipping Address",
        compute="_compute_partner_shipping_id",
        store=True,
        readonly=False,
    )
    custom_description = fields.Text()
    is_return_all = fields.Boolean(string="Return All?", default=False)
    is_cross_ship = fields.Boolean(
        string="Cross-Ship Replacement",
        default=False,
        help="Ship replacement before the return is received. "
        "A new sales order will be created for the replacement product when the RMA is confirmed.",
    )
    waive_restocking_fee = fields.Boolean(
        string="Waive restocking fee",
        default=True,
        help="When checked, no restocking fee will be applied to the created RMA(s).",
    )
    # Return shipping: get rates in separate wizard, apply on first RMA and generate label on Create
    charge_return_shipping = fields.Boolean(
        string="Charge return shipping",
        default=False,
        help="Deduct return shipping cost from the credit note.",
    )
    return_carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Return shipping method",
        domain=[("delivery_type", "=", "shippo")],
        help="Shippo delivery method used to get return rates.",
    )
    # Package: standard type or editable weight/dimensions for return shipping
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
    return_rate_line_ids = fields.One2many(
        comodel_name="sale.order.rma.wizard.return.rate",
        inverse_name="wizard_id",
        string="Return shipping rates",
    )
    selected_return_rate_id = fields.Many2one(
        comodel_name="sale.order.rma.wizard.return.rate",
        string="Return shipping rate",
        domain="[('wizard_id', '=', id)]",
    )

    @api.depends("order_id")
    def _compute_partner_shipping_id(self):
        for rec in self:
            if rec.order_id:
                rec.partner_shipping_id = rec.order_id.partner_shipping_id
            else:
                rec.partner_shipping_id = False

    @api.onchange("is_return_all")
    def _onchange_is_return_all(self):
        """Update line quantities when "Return All?" is toggled (no compute overwriting user input)."""
        for line in self.line_ids:
            if self.is_return_all:
                line.quantity = line.allowed_quantity
            else:
                line.quantity = 0.0

    @api.onchange("line_ids", "line_ids.quantity", "line_ids.product_id")
    def _onchange_line_ids_parcel(self):
        """Update package weight and dimensions from selected products so they are visible before Get rates."""
        if not self.package_type_id and not self.custom_package and self.line_ids:
            parcel = self._compute_initial_parcel_from_rma_lines()
            self.weight = parcel["weight"]
            self.length = parcel["length"]
            self.width = parcel["width"]
            self.height = parcel["height"]

    def _compute_initial_parcel_from_rma_lines(self):
        """Return dict with weight, length, width, height from wizard lines (selected products with qty > 0)."""
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.product_id and l.quantity > 0)
        if not lines:
            return {"weight": 1.0, "length": 10, "width": 10, "height": 10}
        total_weight = sum(
            (l.product_id.weight or 0.0) * l.quantity for l in lines
        )
        products = list(lines.mapped("product_id"))
        weight = max(0.01, float(total_weight)) if total_weight > 0 else 1.0
        length = width = height = 10
        if len(products) == 1:
            p = products[0]
            length = max(1, float(getattr(p, "product_length", None) or 10))
            width = max(1, float(getattr(p, "product_width", None) or 10))
            height = max(1, float(getattr(p, "product_height", None) or 10))
        elif products:
            length = max(1, max(float(getattr(p, "product_length", None) or 10) for p in products))
            width = max(1, max(float(getattr(p, "product_width", None) or 10) for p in products))
            height = max(1, sum(float(getattr(p, "product_height", None) or 10) for p in products))
        return {"weight": weight, "length": length, "width": width, "height": height}

    def _get_parcel_for_return_rates(self):
        """Build one parcel dict for Shippo from wizard: package type or editable weight/dimensions."""
        self.ensure_one()
        if self.custom_package:
            length_in = self._rma_wizard_dim_to_inches(self.length)
            width_in = self._rma_wizard_dim_to_inches(self.width)
            height_in = self._rma_wizard_dim_to_inches(self.height)
            weight_lb = self._rma_wizard_weight_to_lb(self.weight)
            return {
                "length": str(round(max(0.1, length_in), 2)),
                "width": str(round(max(0.1, width_in), 2)),
                "height": str(round(max(0.1, height_in), 2)),
                "distance_unit": "in",
                "weight": str(round(max(0.1, weight_lb), 2)),
                "mass_unit": "lb",
            }
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
                weight_lb = self._rma_wizard_weight_to_lb(self.weight or 1.0)
            return {
                "length": str(round(max(0.1, length_in), 2)),
                "width": str(round(max(0.1, width_in), 2)),
                "height": str(round(max(0.1, height_in), 2)),
                "distance_unit": "in",
                "weight": str(round(max(0.1, weight_lb), 2)),
                "mass_unit": "lb",
            }
        length_in = self._rma_wizard_dim_to_inches(self.length)
        width_in = self._rma_wizard_dim_to_inches(self.width)
        height_in = self._rma_wizard_dim_to_inches(self.height)
        weight_lb = self._rma_wizard_weight_to_lb(self.weight)
        return {
            "length": str(round(max(0.1, length_in), 2)),
            "width": str(round(max(0.1, width_in), 2)),
            "height": str(round(max(0.1, height_in), 2)),
            "distance_unit": "in",
            "weight": str(round(max(0.1, weight_lb), 2)),
            "mass_unit": "lb",
        }

    def _rma_wizard_dim_to_inches(self, value):
        if self.distance_unit == "cm":
            return float(value or 0) * 0.393701
        return float(value or 0)

    def _rma_wizard_weight_to_lb(self, value):
        if self.mass_unit == "kg":
            return float(value or 0) * 2.20462
        return float(value or 0)

    def action_get_return_shipping_rates(self):
        """Fetch return shipping rates (customer → warehouse) and show in wizard."""
        self.ensure_one()
        if not self.order_id:
            raise UserError(_("No order selected."))
        if not self.return_carrier_id:
            raise UserError(
                _("Select a Shippo return shipping method before getting rates.")
            )
        order = self.order_id
        partner_from = self.partner_shipping_id or order.partner_shipping_id
        if not partner_from:
            raise UserError(_("No shipping address on the order."))
        warehouse = order.warehouse_id
        if not warehouse or not warehouse.partner_id and not order.company_id.partner_id:
            raise UserError(_("No warehouse/company address for return destination."))
        wh_partner = warehouse.partner_id or order.company_id.partner_id

        from odoo.addons.delivery_shippo_iag.models.shippo_api import (
            create_address,
            create_shipment,
            get_shipment_rates,
            ShippoAPIError,
            _address_is_valid_for_shipment,
        )

        api_key, use_test = self.return_carrier_id._get_shippo_api_key_and_env()
        if not api_key:
            raise UserError(
                _("No Shippo API key configured for %s.", self.return_carrier_id.name)
            )

        state_from = (partner_from.state_id.code or partner_from.state_id.name or "") if partner_from.state_id else ""
        address_from_dict = {
            "name": partner_from.name or "Customer",
            "street1": partner_from.street or " ",
            "street2": partner_from.street2 or "",
            "city": partner_from.city or " ",
            "state": state_from,
            "zip": partner_from.zip or " ",
            "country": partner_from.country_id.code if partner_from.country_id else "US",
            "phone": partner_from.phone or "",
        }
        try:
            addr_from = create_address(
                api_key, address_from_dict, validate=True, use_test_env=use_test
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate customer address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_from):
            raise UserError(_("Customer address could not be validated by Shippo."))
        address_from_id = addr_from.get("object_id")
        if not address_from_id:
            raise UserError(_("Shippo did not return a valid address."))

        state_to = (wh_partner.state_id.code or wh_partner.state_id.name or "") if wh_partner.state_id else ""
        address_to_dict = {
            "name": wh_partner.name or "Warehouse",
            "street1": wh_partner.street or " ",
            "street2": wh_partner.street2 or "",
            "city": wh_partner.city or " ",
            "state": state_to,
            "zip": wh_partner.zip or " ",
            "country": wh_partner.country_id.code if wh_partner.country_id else "US",
            "phone": wh_partner.phone or "",
        }
        try:
            addr_to = create_address(
                api_key, address_to_dict, validate=True, use_test_env=use_test
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate warehouse address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_to):
            raise UserError(_("Warehouse address could not be validated by Shippo."))
        address_to_id = addr_to.get("object_id")
        if not address_to_id:
            raise UserError(_("Shippo did not return a valid warehouse address."))

        # Prefill weight/dimensions from selected RMA lines if not already set
        if not self.package_type_id and self.line_ids:
            initial = self._compute_initial_parcel_from_rma_lines()
            self.write({
                "weight": initial["weight"],
                "length": initial["length"],
                "width": initial["width"],
                "height": initial["height"],
            })
        parcel = self._get_parcel_for_return_rates()
        try:
            shipment = create_shipment(
                api_key,
                address_from=address_from_id,
                address_to=address_to_id,
                parcels=[parcel],
                carrier_accounts=None,
                extra=None,
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo rates failed: %s", str(e))) from e

        rates = shipment.get("rates") or shipment.get("rates_list") or []
        if not rates and shipment.get("object_id"):
            try:
                rates_payload = get_shipment_rates(
                    api_key, shipment["object_id"], currency="USD", use_test_env=use_test
                )
                if isinstance(rates_payload, list):
                    rates = rates_payload
                else:
                    rates = rates_payload.get("rates") or rates_payload.get("results") or []
            except ShippoAPIError:
                pass
        if not rates:
            shippo_messages = shipment.get("messages") or []
            msg_texts = []
            for m in shippo_messages if isinstance(shippo_messages, list) else []:
                if isinstance(m, dict):
                    msg_texts.append(m.get("text") or m.get("message") or str(m))
                else:
                    msg_texts.append(str(m))
            shippo_reason = (" Shippo: %s." % " | ".join(msg_texts)) if msg_texts else ""
            raise UserError(
                _("No return shipping rates available.%s") % shippo_reason
            )

        # Deduplicate by (carrier, service_name, amount) and sort by carrier then service for grouping
        seen = set()
        rate_tuples = []
        for r in rates:
            sl = r.get("servicelevel") or {}
            service_name = sl.get("name", "") if isinstance(sl, dict) else ""
            amount = float(r.get("amount_local") or r.get("amount", 0))
            carrier = (r.get("provider") or r.get("carrier", "")).strip()
            key = (carrier, service_name, amount)
            if key in seen:
                continue
            seen.add(key)
            rate_tuples.append({
                "carrier": carrier,
                "service_name": service_name,
                "amount": amount,
                "rate_object_id": r.get("object_id", ""),
                "currency": r.get("currency_local") or r.get("currency", "USD"),
            })
        rate_tuples.sort(key=lambda x: (x["carrier"].upper(), x["service_name"] or "", x["amount"]))
        RateLine = self.env["sale.order.rma.wizard.return.rate"]
        self.return_rate_line_ids = [(5, 0, 0)]
        for rt in rate_tuples:
            RateLine.create({
                "wizard_id": self.id,
                "carrier": rt["carrier"],
                "service_name": rt["service_name"],
                "amount": rt["amount"],
                "rate_object_id": rt["rate_object_id"],
                "currency": rt["currency"],
            })
        # Open rate selection in a separate dialog; closing it returns to main wizard
        return {
            "type": "ir.actions.act_window",
            "name": _("Select return shipping rate"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("rma_odoo_19.sale_order_rma_wizard_view_form_rate_selection").id, "form")],
            "target": "new",
            "context": {"from_rate_selection_dialog": True},
        }

    def create_rma(self, from_portal=False):
        self.ensure_one()
        location_id = self.location_id.id if self.location_id else self._get_effective_location_id()
        if not location_id:
            raise ValidationError(
                _(
                    "No RMA location could be determined. Please set "
                    "'Default RMA Location' in RMA Settings (Settings → RMA)."
                )
            )
        user_has_group_portal = self.env.user.has_group(
            "base.group_portal"
        ) or self.env.user.has_group("base.group_public")
        lines = self.line_ids.filtered(lambda r: r.quantity > 0.0)
        if not lines:
            raise UserError(
                _(
                    "No items to return. Enter a quantity greater than 0 for at least one product, "
                    "or ensure the order has delivered quantity to return."
                )
            )
        # Group by picking_id only: one RMA per delivery with all selected products as components
        groups = {}
        for line in lines:
            key = line.picking_id.id if line.picking_id else None
            groups.setdefault(key, []).append(line)
        val_list = []
        for _key, group in groups.items():
            if len(group) > 1:
                # Multiple products from same delivery: one RMA with kit_component_ids for all
                first = group[0]
                base_vals = first._prepare_rma_values()
                base_vals["product_id"] = first.product_id.id
                base_vals["product_uom_qty"] = 1.0
                base_vals["product_uom"] = first.uom_id.id
                base_vals["move_id"] = False
                base_vals["kit_component_ids"] = [
                    (0, 0, {
                        "move_id": line.move_id.id if line.move_id else False,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.quantity,
                        "max_quantity": line.allowed_quantity,
                        "product_uom": line.uom_id.id,
                    })
                    for line in group
                ]
                val_list.append(base_vals)
            else:
                first = group[0]
                # One wizard line with multiple moves (aggregated same product): one RMA with kit components
                if first.move_ids and len(first.move_ids) > 1:
                    base_vals = first._prepare_rma_values()
                    base_vals["move_id"] = False
                    base_vals["kit_component_ids"] = [
                        (0, 0, {
                            "move_id": m.id,
                            "product_id": first.product_id.id,
                            "product_uom_qty": m.product_uom_qty,
                            "max_quantity": m.product_uom_qty,
                            "product_uom": first.uom_id.id,
                        })
                        for m in first.move_ids
                    ]
                    val_list.append(base_vals)
                # Single line: kit delivery (multiple moves) → one RMA with one component; else normal RMA
                elif (
                    first.picking_id
                    and len(first.picking_id.move_ids) > 1
                    and first.sale_line_id
                ):
                    base_vals = first._prepare_rma_values()
                    base_vals["product_id"] = first.sale_line_id.product_id.id
                    base_vals["product_uom_qty"] = 1.0
                    base_vals["product_uom"] = first.sale_line_id.product_uom_id.id
                    base_vals["move_id"] = False
                    base_vals["kit_component_ids"] = [
                        (0, 0, {
                            "move_id": first.move_id.id if first.move_id else False,
                            "product_id": first.product_id.id,
                            "product_uom_qty": first.quantity,
                            "max_quantity": first.allowed_quantity,
                            "product_uom": first.uom_id.id,
                        })
                    ]
                    val_list.append(base_vals)
                else:
                    val_list.append(first._prepare_rma_values())
        rma_model = (
            self.env["rma"].with_user(SUPERUSER_ID)
            if user_has_group_portal
            else self.env["rma"]
        )
        rma = rma_model.create(val_list)
        if from_portal:
            for r in rma:
                r._add_message_subscribe_partner()
        msg_list = [
            f'<a href="/odoo/rma/{r.id}">{r.name}</a>' for r in rma
        ]
        msg = Markup(", ".join(msg_list))
        if len(msg_list) == 1:
            self.order_id.message_post(body=_(msg + " has been created."))
        elif len(msg_list) > 1:
            self.order_id.message_post(body=_(msg + " have been created."))
        rma.message_post_with_source(
            "mail.message_origin_link",
            render_values={"self": rma, "origin": self.order_id},
            subtype_xmlid="mail.mt_note",
        )
        return rma

    def create_and_open_rma(self):
        self.ensure_one()
        rma = self.create_rma()
        if not rma:
            return
        first_rma = rma[0]
        selected_rate = self.selected_return_rate_id
        if selected_rate and selected_rate.rate_object_id and self.charge_return_shipping:
            first_rma.write({
                "return_shipping_cost": selected_rate.amount,
                "charge_return_shipping": True,
            })
        rma.action_confirm()
        if selected_rate and selected_rate.rate_object_id:
            self._generate_return_label_for_rma(first_rma, selected_rate)
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma_odoo_19.rma_action"
        )
        if len(rma) > 1:
            action["domain"] = [("id", "in", rma.ids)]
        elif rma:
            action.update(
                res_id=rma.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        return action

    def _generate_return_label_for_rma(self, rma, rate_line):
        """Generate Shippo return label for the given RMA using the selected rate."""
        import base64
        import logging
        carrier = self.return_carrier_id
        if not carrier:
            return
        api_key, use_test = carrier._get_shippo_api_key_and_env()
        if not api_key:
            return
        from odoo.addons.delivery_shippo_iag.models.shippo_api import (
            create_transaction,
            get_transaction,
            ShippoAPIError,
        )
        _logger = logging.getLogger(__name__)
        try:
            transaction = create_transaction(
                api_key,
                rate_line.rate_object_id,
                label_file_type="PDF_4x6",
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo return label failed: %s", str(e))) from e
        if transaction.get("status") == "QUEUED" and transaction.get("object_id"):
            import time
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
                msg[0].get("text", "Unknown error") if msg and isinstance(msg[0], dict) else "Unknown error"
            )
            raise UserError(_("Return label generation failed: %s", error_text))
        tracking_number = transaction.get("tracking_number", "")
        tracking_url = transaction.get("tracking_url_provider", "") or transaction.get("tracking_url", "")
        label_url = transaction.get("label_url", "")
        transaction_id = transaction.get("object_id", "")
        if not label_url and transaction_id:
            import time
            time.sleep(1)
            try:
                transaction = get_transaction(
                    api_key, transaction_id, use_test_env=use_test
                )
                label_url = transaction.get("label_url", "") or ""
            except ShippoAPIError as e:
                _logger.warning("Could not refetch Shippo transaction for label URL: %s", e)
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
                    att = self.env["ir.attachment"].create({
                        "name": f"Return_Label_{rma.name}.pdf",
                        "type": "binary",
                        "datas": base64.b64encode(resp.content).decode("ascii"),
                        "res_model": "rma",
                        "res_id": rma.id,
                        "mimetype": "application/pdf",
                    })
                    attachment_ids.append(att.id)
            except Exception as e:
                _logger.warning("Could not download return label PDF for RMA %s: %s", rma.name, e)
        # Post note with PDF attached so it appears in RMA chatter
        rma.message_post(
            body=_(
                "Return shipping label generated (Create RMA). "
                "Carrier: %(carrier)s %(service)s, Cost: $%(cost).2f, Tracking: %(tracking)s",
                carrier=rate_line.carrier or "",
                service=rate_line.service_name or "",
                cost=rate_line.amount,
                tracking=tracking_number,
            ),
            attachment_ids=attachment_ids,
        )


class SaleOrderLineRmaWizard(models.TransientModel):
    _name = "sale.order.line.rma.wizard"
    _description = "Sale Order Line RMA Wizard"

    wizard_id = fields.Many2one(
        comodel_name="sale.order.rma.wizard",
        string="Wizard",
    )
    order_id = fields.Many2one(
        comodel_name="sale.order",
        default=lambda self: self.env["sale.order"].browse(
            self.env.context.get("active_id", False)
        ),
    )
    allowed_product_ids = fields.Many2many(
        comodel_name="product.product",
        compute="_compute_allowed_product_ids",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
        domain="[('id', 'in', allowed_product_ids)]",
    )
    quantity = fields.Float(
        digits="Product Unit of Measure",
        required=True,
    )
    allowed_quantity = fields.Float(
        digits="Product Unit of Measure",
        readonly=True,
    )
    uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Unit of Measure",
        required=True,
    )
    allowed_picking_ids = fields.Many2many(
        comodel_name="stock.picking",
        compute="_compute_allowed_picking_ids",
    )
    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Delivery order",
        domain="[('id', 'in', allowed_picking_ids)]",
    )
    move_id = fields.Many2one(
        comodel_name="stock.move",
        compute="_compute_move_id",
        store=True,
        readonly=False,
    )
    move_ids = fields.Many2many(
        comodel_name="stock.move",
        string="Moves",
        help="All delivery moves for this line (when aggregated from same product).",
    )
    operation_id = fields.Many2one(
        comodel_name="rma.operation",
        string="Requested operation",
        compute="_compute_operation_id",
        store=True,
        readonly=False,
    )
    reason_id = fields.Many2one(
        comodel_name="rma.reason",
        string="Return Reason",
        compute="_compute_reason_id",
        store=True,
        readonly=False,
    )
    sale_line_id = fields.Many2one(comodel_name="sale.order.line")
    description = fields.Text()
    return_product_id = fields.Many2one("product.product")
    exchange_product_id = fields.Many2one(
        "product.product",
        string="Exchange Product",
    )
    different_return_product = fields.Boolean(
        related="operation_id.different_return_product",
    )
    # Carrier from rma_sale_delivery
    carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Carrier",
    )

    @api.depends("wizard_id.operation_id")
    def _compute_operation_id(self):
        for rec in self:
            if rec.wizard_id.operation_id:
                rec.operation_id = rec.wizard_id.operation_id

    @api.depends("wizard_id.reason_id")
    def _compute_reason_id(self):
        for rec in self:
            if rec.wizard_id.reason_id:
                rec.reason_id = rec.wizard_id.reason_id

    @api.onchange("product_id")
    def onchange_product_id(self):
        self.picking_id = False
        self.uom_id = self.product_id.uom_id

    @api.depends("picking_id", "product_id", "sale_line_id")
    def _compute_move_id(self):
        for record in self:
            # Keep move_id when set (e.g. kit component from wizard line vals)
            if record.move_id:
                continue
            move_id = False
            if record.picking_id and record.sale_line_id and record.product_id:
                moves = record.picking_id.move_ids.filtered(
                    lambda r, record=record: (
                        r.sale_line_id == record.sale_line_id
                        and r.product_id == record.product_id
                        and r.sale_line_id.order_id == record.order_id
                        and r.state == "done"
                    )
                )
                if len(moves) == 1:
                    move_id = moves
            record.move_id = move_id

    @api.depends("order_id")
    def _compute_allowed_product_ids(self):
        for record in self:
            record.allowed_product_ids = (
                record.order_id.order_line.product_id
            )

    @api.depends("product_id")
    def _compute_allowed_picking_ids(self):
        for record in self:
            line = record.order_id.order_line.filtered(
                lambda r, record=record: r.product_id == record.product_id
            )
            record.allowed_picking_ids = line.mapped(
                "move_ids.picking_id"
            ).filtered(lambda x: x.state == "done")

    @api.constrains("quantity", "allowed_quantity")
    def _check_quantity(self):
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        for rec in self:
            if (
                float_compare(
                    rec.quantity,
                    rec.allowed_quantity,
                    precision_digits=precision,
                )
                == 1
            ):
                raise ValidationError(
                    _(
                        "You can't exceed the allowed quantity for "
                        "returning product %(product)s.",
                        product=rec.product_id.display_name,
                    )
                )

    def _prepare_rma_values(self):
        self.ensure_one()
        partner_shipping = (
            self.wizard_id.partner_shipping_id
            or self.order_id.partner_shipping_id
        )
        description = (self.description or "") + (
            self.wizard_id.custom_description or ""
        )
        # For a kit (picking with multiple moves), do not set move_id so RMA
        # keeps product_id as the kit; receipt will create one move per component.
        move_id = self.move_id.id if self.move_id else False
        if (
            self.picking_id
            and len(self.picking_id.move_ids) > 1
            and self.product_id == self.sale_line_id.product_id
        ):
            move_id = False
        vals = {
            "partner_id": self.order_id.partner_id.id,
            "partner_invoice_id": self.order_id.partner_invoice_id.id,
            "partner_shipping_id": partner_shipping.id,
            "origin": self.order_id.name,
            "company_id": self.order_id.company_id.id,
            "order_id": self.order_id.id,
            "picking_id": self.picking_id.id,
            "move_id": move_id,
            "product_id": self.product_id.id,
            "product_uom_qty": self.quantity,
            "product_uom": self.uom_id.id,
            "operation_id": self.operation_id.id,
            "reason_id": self.reason_id.id if self.reason_id else False,
            "description": description,
            "return_product_id": self.return_product_id.id,
            "exchange_product_id": self.exchange_product_id.id
            if self.exchange_product_id
            else False,
            "carrier_id": self.carrier_id.id if self.carrier_id else False,
            "charge_return_shipping": self.wizard_id.charge_return_shipping,
            "is_cross_ship": self.wizard_id.is_cross_ship,
            "no_restocking_fee": self.wizard_id.waive_restocking_fee,
        }
        location_id = (
            self.wizard_id.location_id.id
            if self.wizard_id.location_id
            else self.wizard_id._get_effective_location_id()
        )
        if location_id:
            vals["location_id"] = location_id
        return vals
