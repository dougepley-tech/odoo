import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RmaReturnShippingWizard(models.TransientModel):
    _name = "rma.return.shipping.wizard"
    _description = "RMA Return Shipping Label Wizard"

    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    state = fields.Selection(
        [("init", "Init"), ("rates", "Rates"), ("done", "Done")],
        default="init",
    )
    carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Delivery Method",
        domain="[('delivery_type', '=', 'shippo')]",
        help="Select a Shippo-based delivery method",
    )
    partner_id = fields.Many2one(
        related="rma_id.partner_shipping_id",
        string="Customer Address (Origin)",
    )
    warehouse_id = fields.Many2one(
        related="rma_id.warehouse_id",
        string="Warehouse (Destination)",
    )
    label_format = fields.Selection(
        [
            ("PDF", "PDF"),
            ("PNG", "PNG"),
            ("PDF_4x6", "PDF 4x6"),
            ("ZPLII", "ZPL II"),
        ],
        default="PDF",
        string="Label Format",
    )
    rate_line_ids = fields.One2many(
        comodel_name="rma.return.shipping.wizard.line",
        inverse_name="wizard_id",
        string="Available Rates",
    )
    selected_rate_amount = fields.Float(
        string="Selected Rate",
        readonly=True,
    )
    selected_rate_carrier = fields.Char(
        string="Selected Carrier / Service",
        readonly=True,
    )

    def action_get_rates(self):
        """Fetch return shipping rates from Shippo.
        Origin = customer address, Destination = warehouse address.
        Uses delivery_shippo_iag function API (create_address, create_shipment).
        """
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
            get_shipment_rates,
            ShippoAPIError,
            _address_is_valid_for_shipment,
        )

        partner = self.rma_id.partner_shipping_id
        state_from = (partner.state_id.code or partner.state_id.name or "") if partner.state_id else ""
        address_from_dict = {
            "name": partner.name or "Customer",
            "street1": partner.street or " ",
            "street2": partner.street2 or "",
            "city": partner.city or " ",
            "state": state_from,
            "zip": partner.zip or " ",
            "country": partner.country_id.code if partner.country_id else "US",
            "phone": partner.phone or "",
        }
        try:
            addr_from = create_address(
                api_key, address_from_dict, validate=True, use_test_env=use_test
            )
        except ShippoAPIError as e:
            raise UserError(
                _("Shippo could not validate customer (return-from) address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_from):
            raise UserError(_("Customer address could not be validated by Shippo."))
        address_from_id = addr_from.get("object_id")
        if not address_from_id:
            raise UserError(_("Shippo did not return a valid return-from address."))

        warehouse = self.rma_id.warehouse_id
        wh_partner = warehouse.partner_id or self.rma_id.company_id.partner_id
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
                _("Shippo could not validate warehouse (return-to) address: %s", str(e))
            ) from e
        if not _address_is_valid_for_shipment(addr_to):
            raise UserError(_("Warehouse address could not be validated by Shippo."))
        address_to_id = addr_to.get("object_id")
        if not address_to_id:
            raise UserError(_("Shippo did not return a valid return-to address."))

        product = self.rma_id.product_id
        weight = max(1.0, float(product.weight or 1))
        parcel = {
            "length": str(max(1, int(product.product_length or 10))),
            "width": str(max(1, int(product.product_width or 10))),
            "height": str(max(1, int(product.product_height or 6))),
            "distance_unit": "in",
            "weight": str(round(weight, 2)),
            "mass_unit": "lb",
        }
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
            raise UserError(_("Shippo shipment/rates failed: %s", str(e))) from e

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
                    rates = rates_payload.get("rates") or rates_payload.get("results") or []
            except ShippoAPIError:
                pass
        if not rates:
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
                "Shippo return label wizard: no rates. object_id=%s messages=%s",
                shipment.get("object_id"),
                shippo_messages,
            )
            raise UserError(
                _(
                    "No shipping rates available for this return shipment.%s "
                    "Check that customer and warehouse addresses are complete and valid; "
                    "ensure weight is at least 1 lb."
                )
                % shippo_reason
            )

        seen = set()
        line_vals = []
        for rate in rates:
            sl = rate.get("servicelevel") or {}
            service_name = sl.get("name", "") if isinstance(sl, dict) else ""
            carrier_name = (rate.get("provider") or rate.get("carrier") or "").strip()
            amount = float(rate.get("amount_local") or rate.get("amount", 0))
            key = (carrier_name, service_name, amount)
            if key in seen:
                continue
            seen.add(key)
            line_vals.append({
                "wizard_id": self.id,
                "shippo_rate_id": rate.get("object_id", ""),
                "carrier_name": carrier_name,
                "service_name": service_name,
                "amount": amount,
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

    def action_generate_label(self):
        """Generate a return shipping label for the selected rate."""
        self.ensure_one()
        selected = self.rate_line_ids.filtered("is_selected")
        if not selected:
            raise UserError(_("Please select a rate first."))
        selected = selected[0]

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
                label_file_type=self.label_format or "PDF_4x6",
                async_=False,
                use_test_env=use_test,
            )
        except ShippoAPIError as e:
            raise UserError(_("Shippo label purchase failed: %s", str(e))) from e

        # If async, poll for completion; otherwise use response as-is
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
            raise UserError(
                _("Label generation failed: %s", error_text)
            )

        tracking_number = transaction.get("tracking_number", "")
        tracking_url = transaction.get("tracking_url_provider", "") or transaction.get("tracking_url", "")
        label_url = transaction.get("label_url", "")
        transaction_id = transaction.get("object_id", "")

        # Shippo may not include label_url in the first response; refetch once if needed
        if not label_url and transaction_id:
            import time
            time.sleep(1)
            try:
                transaction = get_transaction(
                    api_key, transaction_id, use_test_env=use_test
                )
                label_url = transaction.get("label_url", "") or ""
            except ShippoAPIError as e:
                _logger.warning(
                    "Could not refetch Shippo transaction for label URL: %s", e
                )

        self.rma_id.write({
            "return_shipping_cost": selected.amount,
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
                        "name": f"Return_Label_{self.rma_id.name}.pdf",
                        "type": "binary",
                        "datas": base64.b64encode(resp.content).decode("ascii"),
                        "res_model": "rma",
                        "res_id": self.rma_id.id,
                        "mimetype": "application/pdf",
                    })
                    attachment_ids.append(att.id)
            except Exception as e:
                _logger.warning(
                    "Could not download return label PDF for RMA %s: %s",
                    self.rma_id.name,
                    e,
                )

        # Post note with PDF attached so it appears in RMA chatter
        self.rma_id.message_post(
            body=_(
                "Return shipping label generated. "
                "Carrier: %(carrier)s %(service)s, "
                "Cost: $%(cost).2f, "
                "Tracking: %(tracking)s",
                carrier=selected.carrier_name,
                service=selected.service_name,
                cost=selected.amount,
                tracking=tracking_number,
            ),
            attachment_ids=attachment_ids,
        )

        self.selected_rate_amount = selected.amount
        self.selected_rate_carrier = (
            f"{selected.carrier_name} {selected.service_name}"
        )
        self.state = "done"

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class RmaReturnShippingWizardLine(models.TransientModel):
    _name = "rma.return.shipping.wizard.line"
    _description = "RMA Return Shipping Rate Line"

    wizard_id = fields.Many2one(
        comodel_name="rma.return.shipping.wizard",
        ondelete="cascade",
    )
    shippo_rate_id = fields.Char(string="Shippo Rate ID")
    carrier_name = fields.Char(string="Carrier")
    service_name = fields.Char(string="Service")
    amount = fields.Float(string="Cost")
    currency = fields.Char(string="Currency", default="USD")
    estimated_days = fields.Integer(string="Est. Days")
    is_selected = fields.Boolean(string="Select")

    def action_select_rate(self):
        self.ensure_one()
        self.wizard_id.rate_line_ids.write({"is_selected": False})
        self.is_selected = True
        return {
            "type": "ir.actions.act_window",
            "res_model": self.wizard_id._name,
            "res_id": self.wizard_id.id,
            "view_mode": "form",
            "target": "new",
        }
