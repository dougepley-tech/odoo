# -*- coding: utf-8 -*-

import base64
import json
import logging
import pprint
import requests
from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # === AFFIRM-SPECIFIC FIELDS ===#
    
    affirm_checkout_token = fields.Char(
        string='Affirm Checkout Token',
        readonly=True,
        help='The checkout token returned by Affirm after customer authorization'
    )
    
    affirm_charge_id = fields.Char(
        string='Affirm Charge ID',
        readonly=True,
        help='The unique charge ID from Affirm used for captures and refunds'
    )

    # === BUSINESS METHODS ===#

    def _get_specific_rendering_values(self, processing_values):
        """Override to provide Affirm-specific rendering values for checkout."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'affirm':
            return res

        # Get base URL with multiple fallbacks
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if not base_url:
            # Try to get from company website
            base_url = self.provider_id.company_id.website or ''
        if not base_url:
            # Fallback to a sensible default
            base_url = 'https://odoo.iagindustries.com'
        base_url = base_url.rstrip('/')
        # In multi-db, Affirm's redirect must hit the same DB or dbfilter can reject the session
        dbname = self.env.cr.dbname
        url_params = f"?db={urls.url_quote(dbname)}" if dbname else ""
        
        _logger.info("Generating Affirm rendering values for transaction %s (base_url: %s, db: %s)", 
                     self.reference, base_url, dbname)
        
        partner = self.partner_id
        
        # Get phone number safely
        partner_phone = partner.phone or ''
        if hasattr(partner, 'mobile') and partner.mobile:
            partner_phone = partner_phone or partner.mobile
        
        # Affirm requires a 2-letter state code (e.g. TN, CA). Resolve from partner, then order, then company.
        partner_state = self._affirm_get_partner_state_code(partner)
        country_code = partner.country_id.code or 'US'
        if country_code == 'US' and not partner_state:
            raise ValidationError(_(
                'Affirm requires a valid US state. Please set the State/Province on the customer\'s '
                'billing or shipping address (e.g. Tennessee → TN), then try again.'
            ))
        
        # Affirm requires a valid zipcode. Resolve from partner, then order addresses, then company.
        partner_zip = self._affirm_get_partner_zip(partner)
        if not (partner_zip and partner_zip.strip()):
            raise ValidationError(_(
                'Affirm requires a valid zipcode. Please set the ZIP/Postal code on the customer\'s '
                'billing or shipping address, then try again.'
            ))
        
        # Build callback URLs (include db so redirect lands on same DB in multi-db setups)
        confirmation_url = f"{base_url}/payment/affirm/return{url_params}"
        cancel_url = f"{base_url}/payment/affirm/cancel{url_params}"
        
        # Affirm requires non-empty email/phone in shipping; use fallbacks to avoid generic checkout error
        partner_email = (partner.email or '').strip()
        if not partner_email and self.sale_order_ids:
            order = self.sale_order_ids[0]
            for p in [order.partner_invoice_id, order.partner_shipping_id]:
                if p and p.email:
                    partner_email = p.email.strip()
                    break
        if not partner_email:
            partner_email = (self.company_id or self.env.company).email or 'noreply@merchant.local'
        if not (partner_phone or '').strip() and self.sale_order_ids:
            order = self.sale_order_ids[0]
            for p in [order.partner_invoice_id, order.partner_shipping_id]:
                if p and (p.phone or getattr(p, 'mobile', None)):
                    partner_phone = (p.phone or getattr(p, 'mobile', '')) or ''
                    break
        partner_phone = (partner_phone or '').strip()
        if not partner_phone:
            company = self.company_id or self.env.company
            partner_phone = (company.phone or getattr(company, 'mobile', None) or '').strip() or '0000000000'

        affirm_values = {
            'public_api_key': self.provider_id.affirm_public_key,
            'js_url': self.provider_id._affirm_get_js_url(),
            'merchant_name': self.provider_id.affirm_merchant_name or self.provider_id.company_id.name,
            'confirmation_url': confirmation_url,
            'cancel_url': cancel_url,
            'reference': self.reference,
            'amount': int(self.amount * 100),  # Convert to cents
            'currency': self.currency_id.name,
            'partner_name': partner.name or 'Customer',
            'partner_email': partner_email,
            'partner_phone': partner_phone,
            'partner_street': partner.street or '',
            'partner_street2': partner.street2 or '',
            'partner_city': partner.city or '',
            'partner_state': partner_state or '',
            'partner_zip': (partner_zip or '').strip(),
            'partner_country': country_code,
        }
        
        # Build line items and calculate tax/shipping
        line_items, items_total, tax_amount, shipping_amount = self._affirm_get_line_items_with_totals()
        affirm_values['line_items'] = json.dumps(line_items)
        affirm_values['tax_amount'] = tax_amount
        affirm_values['shipping_amount'] = shipping_amount
        
        _logger.info("Affirm values for %s: confirmation_url=%s, cancel_url=%s, partner=%s, items_total=%d, tax=%d, shipping=%d", 
                     self.reference, confirmation_url, cancel_url, partner.name, items_total, tax_amount, shipping_amount)
        
        res.update(affirm_values)
        return res

    def _affirm_get_partner_state_code(self, partner):
        """Return 2-letter state code for Affirm (required). Try partner, then sale order address, then company."""
        code = partner.state_id.code if partner.state_id else None
        if code:
            return code.strip()[:2]
        if self.sale_order_ids:
            order = self.sale_order_ids[0]
            for p in [order.partner_shipping_id, order.partner_invoice_id]:
                if p and p.state_id and p.state_id.code:
                    return p.state_id.code.strip()[:2]
        if partner.country_id and partner.country_id.code == 'US':
            company = self.company_id or self.env.company
            if company.partner_id and company.partner_id.state_id and company.partner_id.state_id.code:
                return company.partner_id.state_id.code.strip()[:2]
        return None

    def _affirm_get_partner_zip(self, partner):
        """Return zip/postal code for Affirm. Try partner, then sale order shipping/invoice, then company."""
        zip_val = (partner.zip or '').strip()
        if zip_val:
            return zip_val
        if self.sale_order_ids:
            order = self.sale_order_ids[0]
            for p in [order.partner_shipping_id, order.partner_invoice_id]:
                if p and (p.zip or '').strip():
                    return (p.zip or '').strip()
        company = self.company_id or self.env.company
        if company.partner_id and (company.partner_id.zip or '').strip():
            return (company.partner_id.zip or '').strip()
        return None

    def _affirm_get_line_items_with_totals(self):
        """Build line items array for Affirm checkout with tax and shipping."""
        self.ensure_one()
        line_items = []
        items_total = 0
        tax_amount = 0
        shipping_amount = 0
        total_cents = int(self.amount * 100)
        
        # Try sale order first
        if self.sale_order_ids:
            order = self.sale_order_ids[0]
            for line in order.order_line:
                if line.display_type:
                    continue
                # Check if this is a shipping/delivery line
                if line.is_delivery if hasattr(line, 'is_delivery') else False:
                    shipping_amount += int(line.price_total * 100)
                elif line.product_id:
                    item_price = int(line.price_unit * 100)
                    item_qty = int(line.product_uom_qty)
                    line_items.append({
                        'display_name': line.product_id.display_name,
                        'sku': line.product_id.default_code or str(line.product_id.id),
                        'unit_price': item_price,
                        'qty': item_qty,
                    })
                    items_total += item_price * item_qty
            
            # Get tax from order
            tax_amount = int(order.amount_tax * 100)
        
        # Try invoice if no sale order lines
        elif hasattr(self, 'invoice_ids') and self.invoice_ids:
            invoice = self.invoice_ids[0]
            for line in invoice.invoice_line_ids:
                if line.display_type:
                    continue
                if line.product_id:
                    item_price = int(line.price_unit * 100)
                    item_qty = int(line.quantity)
                    line_items.append({
                        'display_name': line.product_id.display_name,
                        'sku': line.product_id.default_code or str(line.product_id.id),
                        'unit_price': item_price,
                        'qty': item_qty,
                    })
                    items_total += item_price * item_qty
            
            # Get tax from invoice
            tax_amount = int(invoice.amount_tax * 100)
        
        # Fallback to generic item
        if not line_items:
            line_items = [{
                'display_name': f'Payment {self.reference}',
                'sku': self.reference,
                'unit_price': total_cents,
                'qty': 1,
            }]
            items_total = total_cents
        
        # Calculate any remaining difference as adjustment
        # total = items + tax + shipping
        calculated_total = items_total + tax_amount + shipping_amount
        if calculated_total != total_cents:
            # Add the difference to tax (or could be fees, discounts adjustment)
            difference = total_cents - calculated_total
            _logger.info("Affirm checkout adjustment: calculated=%d, actual=%d, diff=%d", 
                        calculated_total, total_cents, difference)
            if difference > 0:
                # Additional fees/adjustments
                tax_amount += difference
            else:
                # Discount - Affirm doesn't have a discount field in basic API, adjust tax
                tax_amount += difference  # Will be negative, reducing tax
        
        return line_items, items_total, tax_amount, shipping_amount

    # === NOTIFICATION HANDLING ===#

    @api.model
    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Find transaction from Affirm notification data. Odoo 19 base may not define this method."""
        if provider_code != 'affirm':
            try:
                return super()._get_tx_from_notification_data(provider_code, notification_data)
            except AttributeError:
                return self.browse()
        reference = notification_data.get('reference')
        if not reference:
            raise ValidationError(_("Affirm: Missing reference in notification data"))
        tx = self.search([
            ('reference', '=', reference),
            ('provider_code', '=', 'affirm')
        ], limit=1)
        if not tx:
            raise ValidationError(_(
                "Affirm: No transaction found matching reference %s", reference
            ))
        return tx

    def _process_notification_data(self, notification_data):
        """Process Affirm notification data. Odoo 19 base may not define this method."""
        if self.provider_code != 'affirm':
            try:
                return super()._process_notification_data(notification_data)
            except AttributeError:
                return
        checkout_token = notification_data.get('checkout_token')
        if not checkout_token:
            raise ValidationError(_("Affirm: Missing checkout token in notification data"))
        self.affirm_checkout_token = checkout_token
        self._affirm_authorize_and_capture()

    # === AFFIRM API METHODS ===#

    def _affirm_authorize_and_capture(self):
        """Authorize and capture the transaction with Affirm API."""
        self.ensure_one()
        
        api_url = self.provider_id._affirm_get_api_url()
        auth = (self.provider_id.affirm_public_key, self.provider_id.affirm_private_key)
        
        # Step 1: Authorize (create charge)
        payload = {
            'checkout_token': self.affirm_checkout_token,
            'order_id': self.reference,
        }
        
        _logger.info(
            "Sending authorization request to Affirm for transaction %s:\n%s",
            self.reference, pprint.pformat(payload)
        )
        
        try:
            response = requests.post(
                f'{api_url}/charges',
                json=payload,
                auth=auth,
                headers={'Content-Type': 'application/json'},
                timeout=60
            )
            response.raise_for_status()
            response_data = response.json()
            
            _logger.info(
                "Affirm authorization response for transaction %s:\n%s",
                self.reference, pprint.pformat(response_data)
            )
            
            # Store the charge ID
            self.affirm_charge_id = response_data.get('id')
            
            # Validate the authorized amount
            authorized_amount = response_data.get('amount', 0) / 100.0
            if abs(authorized_amount - self.amount) > 0.01:
                self._set_error(_(
                    "Affirm: Authorized amount (%.2f) does not match transaction amount (%.2f)",
                    authorized_amount, self.amount
                ))
                return
            
            # Validate order ID
            if response_data.get('order_id') != self.reference:
                self._set_error(_(
                    "Affirm: Order ID mismatch. Expected %s, got %s",
                    self.reference, response_data.get('order_id')
                ))
                return
            
            # Check authorization status
            if response_data.get('status') != 'authorized':
                self._set_error(_(
                    "Affirm authorization failed with status: %s", response_data.get('status')
                ))
                return
            
            # Step 2: Capture the charge
            self._affirm_capture_charge()
                
        except requests.exceptions.RequestException as e:
            _logger.exception("Error authorizing Affirm transaction %s: %s", self.reference, e)
            self._set_error(_("Unable to authorize payment with Affirm: %s", str(e)))
        except Exception as e:
            _logger.exception("Unexpected error authorizing Affirm transaction %s: %s", self.reference, e)
            self._set_error(_("Unexpected error during Affirm authorization: %s", str(e)))

    def _affirm_capture_charge(self):
        """Capture an authorized Affirm charge."""
        self.ensure_one()
        
        if not self.affirm_charge_id:
            self._set_error(_("Cannot capture: No Affirm charge ID found"))
            return
        
        api_url = self.provider_id._affirm_get_api_url()
        auth = (self.provider_id.affirm_public_key, self.provider_id.affirm_private_key)
        
        _logger.info("Capturing Affirm charge %s for transaction %s", 
                     self.affirm_charge_id, self.reference)
        
        try:
            response = requests.post(
                f'{api_url}/charges/{self.affirm_charge_id}/capture',
                auth=auth,
                headers={'Content-Type': 'application/json'},
                timeout=60
            )
            response.raise_for_status()
            response_data = response.json()
            
            _logger.info(
                "Affirm capture response for transaction %s:\n%s",
                self.reference, pprint.pformat(response_data)
            )
            
            # v2 API returns type='capture'; v1 may return status='captured'
            if response_data.get('type') == 'capture' or response_data.get('status') == 'captured':
                self._set_done()
                self._affirm_confirm_linked_sale_orders()
                self._affirm_create_invoice_and_reconcile()
            else:
                self._set_error(_(
                    "Affirm capture returned unexpected response (type=%s, status=%s)",
                    response_data.get('type'), response_data.get('status')
                ))
                
        except requests.exceptions.RequestException as e:
            _logger.exception("Error capturing Affirm transaction %s: %s", self.reference, e)
            self._set_error(_("Unable to capture payment with Affirm: %s", str(e)))
        except Exception as e:
            _logger.exception("Unexpected error capturing Affirm transaction %s: %s", self.reference, e)
            self._set_error(_("Unexpected error during Affirm capture: %s", str(e)))

    def _affirm_confirm_linked_sale_orders(self):
        """Confirm any sale orders linked to this transaction that are still in quotation state."""
        self.ensure_one()
        if not getattr(self, 'sale_order_ids', None):
            return
        for order in self.sale_order_ids:
            if order.state in ('draft', 'sent'):
                try:
                    order.action_confirm()
                    _logger.info(
                        "payment_affirm: Confirmed sale order %s after successful Affirm payment.",
                        order.name,
                    )
                except Exception as e:
                    _logger.warning(
                        "payment_affirm: Could not confirm sale order %s: %s",
                        order.name, e,
                    )

    def _affirm_create_invoice_and_reconcile(self):
        """Create invoice(s) for linked sale orders and mark them as paid with this transaction."""
        self.ensure_one()
        if not getattr(self, 'sale_order_ids', None):
            return
        AccountPayment = self.env['account.payment'].sudo()
        AccountMove = self.env['account.move'].sudo()
        for order in self.sale_order_ids:
            if order.state != 'sale':
                continue
            try:
                invoices = order.invoice_ids.filtered(lambda m: m.state in ('draft', 'posted'))
                if not invoices:
                    # Create invoice: try _create_invoices() (full order) then wizard
                    try:
                        if hasattr(order, '_create_invoices'):
                            order._create_invoices()
                        else:
                            wiz = self.env['sale.advance.payment.inv'].with_context(
                                active_model='sale.order',
                                active_ids=order.ids,
                                active_id=order.id,
                            ).create({'advance_payment_method': 'delivered'})
                            wiz.create_invoices()
                    except Exception as e:
                        _logger.warning(
                            "payment_affirm: Could not create invoice for %s: %s",
                            order.name, e,
                        )
                    order.invalidate_recordset(['invoice_ids'])
                    invoices = order.invoice_ids.filtered(lambda m: m.state in ('draft', 'posted'))
                if not invoices:
                    continue
                amount_to_pay = self.amount
                for inv in invoices:
                    if amount_to_pay <= 0:
                        break
                    if inv.state == 'draft':
                        inv.action_post()
                    if inv.payment_state in ('paid', 'in_payment'):
                        continue
                    pay_amount = min(amount_to_pay, inv.amount_residual)
                    if pay_amount <= 0:
                        continue
                    journal = self.env['account.journal'].sudo().search([
                        ('type', 'in', ('bank', 'cash')),
                        ('company_id', '=', order.company_id.id),
                    ], limit=1)
                    if not journal:
                        _logger.warning(
                            "payment_affirm: No bank/cash journal for company %s; cannot register payment.",
                            order.company_id.name,
                        )
                        continue
                    payment_vals = {
                        'payment_type': 'inbound',
                        'partner_type': 'customer',
                        'amount': pay_amount,
                        'currency_id': inv.currency_id.id,
                        'partner_id': inv.partner_id.id,
                        'journal_id': journal.id,
                    }
                    # Memo/reference: use field that exists in this Odoo version (ref or communication)
                    for memo_field in ('ref', 'communication', 'narration'):
                        if memo_field in AccountPayment._fields:
                            payment_vals[memo_field] = _('Affirm payment %s') % self.reference
                            break
                    payment = AccountPayment.create(payment_vals)
                    payment.action_post()
                    # Reconcile: get receivable lines from payment move and invoice move
                    payment_lines = payment.move_id.line_ids if payment.move_id else getattr(payment, 'line_ids', self.env['account.move.line'])
                    inv_receivable = inv.line_ids.filtered(
                        lambda l: getattr(l, 'account_type', None) == 'asset_receivable' or (l.account_id and getattr(l.account_id, 'account_type', None) == 'asset_receivable')
                    )
                    pay_receivable = payment_lines.filtered(
                        lambda l: getattr(l, 'account_type', None) == 'asset_receivable' or (l.account_id and getattr(l.account_id, 'account_type', None) == 'asset_receivable')
                    )
                    to_reconcile = inv_receivable + pay_receivable
                    if to_reconcile and len(to_reconcile) >= 2:
                        to_reconcile.reconcile()
                    else:
                        _logger.warning(
                            "payment_affirm: Could not find receivable lines to reconcile (inv %s, payment %s).",
                            inv.name, payment.name,
                        )
                    amount_to_pay -= pay_amount
                    _logger.info(
                        "payment_affirm: Created invoice %s and marked as paid (%.2f) for order %s.",
                        inv.name, pay_amount, order.name,
                    )
                    # Generate invoice PDF and mark as sent (same as clicking Send: document created + is_move_sent)
                    try:
                        report_ref = None
                        for ref in ('account.account_invoices', 'account.report_invoice'):
                            r = self.env.ref(ref, raise_if_not_found=False)
                            if r and r._name == 'ir.actions.report':
                                report_ref = ref
                                break
                        if not report_ref:
                            _logger.warning(
                                "payment_affirm: No invoice report found for PDF (inv %s); Send/Print may stay pending.",
                                inv.name,
                            )
                        else:
                            # Call as model method: _render_qweb_pdf(report_ref, res_ids, data); do not pass inv.ids as first arg
                            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                                report_ref, res_ids=[inv.id], data=None
                            )
                            if pdf_content:
                                pdf_b64 = base64.b64encode(pdf_content)
                                attachment = self.env['ir.attachment'].sudo().create({
                                    'name': '%s.pdf' % inv.name,
                                    'type': 'binary',
                                    'datas': pdf_b64,
                                    'res_model': inv._name,
                                    'res_id': inv.id,
                                    'mimetype': 'application/pdf',
                                })
                                inv_sudo = inv.sudo()
                                # Set as main attachment so Documents button shows and Odoo treats it as the invoice PDF
                                if 'message_main_attachment_id' in inv_sudo._fields:
                                    inv_sudo.write({'message_main_attachment_id': attachment.id})
                                if 'invoice_pdf_report_file' in inv_sudo._fields:
                                    inv_sudo.write({'invoice_pdf_report_file': pdf_b64})
                                if 'is_move_sent' in inv_sudo._fields:
                                    inv_sudo.write({'is_move_sent': True})
                                    _logger.info(
                                        "payment_affirm: Invoice PDF attached and marked as sent for %s.",
                                        inv.name,
                                    )
                                else:
                                    _logger.info(
                                        "payment_affirm: Invoice PDF attached for %s (is_move_sent not on model).",
                                        inv.name,
                                    )
                            else:
                                _logger.warning(
                                    "payment_affirm: Invoice report returned empty PDF for %s.",
                                    inv.name,
                                )
                    except Exception as pdf_e:
                        _logger.warning(
                            "payment_affirm: Could not attach invoice PDF / mark sent for %s: %s",
                            inv.name, pdf_e,
                            exc_info=True,
                        )
                    # Mark as Reviewed so the "Reviewed" button no longer shows (matches credit card flow)
                    inv_sudo = inv.sudo()
                    if hasattr(inv_sudo, 'button_set_checked'):
                        try:
                            inv_sudo.button_set_checked()
                            _logger.info("payment_affirm: Called button_set_checked for %s.", inv.name)
                        except Exception as e:
                            _logger.warning(
                                "payment_affirm: button_set_checked failed for %s: %s", inv.name, e
                            )
                    vals = {}
                    if 'to_check' in inv_sudo._fields:
                        vals['to_check'] = False
                    if 'checked' in inv_sudo._fields:
                        vals['checked'] = True
                    if vals:
                        inv_sudo.write(vals)
                        try:
                            inv_sudo.flush_recordset(list(vals.keys()))
                        except Exception:
                            pass
                        _logger.info("payment_affirm: Set invoice %s as Reviewed (wrote %s).", inv.name, list(vals.keys()))
                    # Fallback: force DB update in case ORM compute overwrote the value (button hides when checked=True)
                    try:
                        tbl = inv_sudo._table
                        updates = []
                        params = []
                        if 'to_check' in inv_sudo._fields and inv_sudo._fields['to_check'].store:
                            updates.append('to_check = false')
                        if 'checked' in inv_sudo._fields and inv_sudo._fields['checked'].store:
                            updates.append('checked = true')
                        if updates:
                            self.env.cr.execute(
                                "UPDATE %s SET %s WHERE id = %%s" % (tbl, ', '.join(updates)),
                                [inv.id],
                            )
                            _logger.info("payment_affirm: Force-updated Reviewed flags in DB for %s.", inv.name)
                    except Exception as sql_e:
                        _logger.debug("payment_affirm: DB fallback for Reviewed failed: %s", sql_e)
            except Exception as e:
                _logger.warning(
                    "payment_affirm: Could not create/reconcile invoice for order %s: %s",
                    order.name, e,
                )

    # === POST-PROCESS (CRON) ===#

    def _create_payment(self, **kwargs):
        """Skip account_payment's payment creation for Affirm; we already created and reconciled in _affirm_create_invoice_and_reconcile."""
        if self.provider_code == 'affirm':
            return
        return super()._create_payment(**kwargs)

    # === REFUND HANDLING ===#

    def _send_refund_request(self, amount_to_refund=None):
        """Override to send refund request to Affirm."""
        if self.provider_code != 'affirm':
            return super()._send_refund_request(amount_to_refund=amount_to_refund)
        
        if not self.affirm_charge_id:
            raise ValidationError(_("Cannot refund: No Affirm charge ID found"))
        
        refund_amount = amount_to_refund or self.amount
        
        api_url = self.provider_id._affirm_get_api_url()
        auth = (self.provider_id.affirm_public_key, self.provider_id.affirm_private_key)
        
        payload = {
            'amount': int(refund_amount * 100)
        }
        
        _logger.info(
            "Sending refund request to Affirm for transaction %s (amount: %.2f):\n%s",
            self.reference, refund_amount, pprint.pformat(payload)
        )
        
        try:
            response = requests.post(
                f'{api_url}/charges/{self.affirm_charge_id}/refund',
                json=payload,
                auth=auth,
                headers={'Content-Type': 'application/json'},
                timeout=60
            )
            response.raise_for_status()
            response_data = response.json()
            
            _logger.info(
                "Affirm refund response for transaction %s:\n%s",
                self.reference, pprint.pformat(response_data)
            )
            
            # Return data for refund transaction creation
            return response_data
            
        except requests.exceptions.RequestException as e:
            _logger.exception("Error refunding Affirm transaction %s: %s", self.reference, e)
            raise ValidationError(_("Unable to process refund with Affirm: %s", str(e)))
        except Exception as e:
            _logger.exception("Unexpected error refunding Affirm transaction %s: %s", self.reference, e)
            raise ValidationError(_("Unexpected error during Affirm refund: %s", str(e)))
