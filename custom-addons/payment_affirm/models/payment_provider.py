# -*- coding: utf-8 -*-

import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('affirm', 'Affirm')],
        ondelete={'affirm': 'set default'}
    )
    
    affirm_public_key = fields.Char(
        string='Affirm Public API Key',
        required_if_provider='affirm',
        groups='base.group_system',
        help='The Public API Key from your Affirm account'
    )
    
    affirm_private_key = fields.Char(
        string='Affirm Private API Key',
        required_if_provider='affirm',
        groups='base.group_system',
        help='The Private API Key from your Affirm account'
    )
    
    affirm_merchant_name = fields.Char(
        string='Merchant Display Name',
        help='Customer-facing merchant name shown during Affirm checkout',
        default=lambda self: self.env.company.name
    )

    # === BUSINESS METHODS ===#

    def _affirm_get_api_url(self):
        """Return the Charges API base URL (v2). Authorize/capture/refund use v2, not v1."""
        self.ensure_one()
        if self.state == 'test':
            return 'https://sandbox.affirm.com/api/v2'
        return 'https://api.affirm.com/api/v2'
    
    def _affirm_get_js_url(self):
        """Return the appropriate JavaScript URL based on state (test/enabled)."""
        self.ensure_one()
        if self.state == 'test':
            return 'https://cdn1-sandbox.affirm.com/js/v2/affirm.js'
        return 'https://cdn1.affirm.com/js/v2/affirm.js'

    # === CREDENTIAL / CODE FIX ===#

    @api.model
    def _payment_affirm_force_code(self):
        """Set code='affirm' on our provider via SQL. Called from data file so it runs on install and upgrade."""
        cr = self.env.cr
        # By redirect_form_view_id
        cr.execute(
            """
            UPDATE payment_provider
            SET code = 'affirm'
            WHERE redirect_form_view_id = (
                SELECT res_id::integer FROM ir_model_data
                WHERE module = 'payment_affirm' AND name = 'affirm_form' AND model = 'ir.ui.view'
                LIMIT 1
            )
            """
        )
        if cr.rowcount:
            return
        # By xml_id
        cr.execute(
            """
            UPDATE payment_provider SET code = 'affirm'
            WHERE id = (
                SELECT res_id::integer FROM ir_model_data
                WHERE module = 'payment_affirm' AND name = 'payment_provider_affirm' AND model = 'payment.provider'
                LIMIT 1
            )
            """
        )
        if cr.rowcount:
            return
        # By env.ref then id
        try:
            provider = self.env.ref("payment_affirm.payment_provider_affirm", raise_if_not_found=False)
            if provider and provider.code != "affirm":
                cr.execute("UPDATE payment_provider SET code = %s WHERE id = %s", ("affirm", provider.id))
        except Exception:
            pass
        if cr.rowcount:
            return
        # By name (JSONB text)
        cr.execute(
            """
            UPDATE payment_provider SET code = 'affirm'
            WHERE (code IS NULL OR code != 'affirm') AND name IS NOT NULL AND name::text LIKE %s
            """,
            ("%Affirm%",),
        )
        # Invalidate cache so next step (link) sees the updated code when searching
        self.env.invalidate_all()

    @api.model
    def _payment_affirm_set_module_id(self):
        """Set module_id on the Affirm provider so the Published stat button shows (module_state == 'installed')."""
        try:
            module = self.env.ref('base.module_payment_affirm', raise_if_not_found=False)
            if not module:
                return
            provider = self.env.sudo().ref(
                'payment_affirm.payment_provider_affirm',
                raise_if_not_found=False,
            )
            if not provider:
                provider = self.sudo().search([('code', '=', 'affirm')], limit=1)
            if provider and provider.module_id != module:
                provider.module_id = module
                _logger.info("payment_affirm: Set module_id on Affirm provider (id=%s) so Published button shows.", provider.id)
        except Exception as e:
            _logger.warning("payment_affirm: _payment_affirm_set_module_id failed: %s", e)

    @api.model
    def _payment_affirm_link_provider_to_method(self):
        """Link our Affirm payment provider to the Affirm payment method (provider_ids).
        Per Odoo 19: payment.method is supported by providers in provider_ids.
        Uses active_test=False so we find the method even when it is inactive (unchecked).
        """
        Method = self.env['payment.method'].sudo().with_context(active_test=False)
        methods = Method.search([('code', '=', 'affirm')])
        if not methods:
            try:
                methods = Method.create([{'name': 'Affirm', 'code': 'affirm'}])
                _logger.info("payment_affirm: Created payment.method 'Affirm' (code=affirm) for linking.")
            except Exception as e:
                _logger.warning("payment_affirm: No payment.method with code='affirm' and create failed: %s", e)
                return
        try:
            provider = self.env.sudo().ref(
                'payment_affirm.payment_provider_affirm',
                raise_if_not_found=False,
            )
        except Exception as e:
            _logger.warning("payment_affirm: env.ref(payment_provider_affirm) failed: %s", e)
            provider = None
        if not provider:
            providers = self.sudo().search([('code', '=', 'affirm')])
            provider = providers[:1] if providers else None
        if not provider:
            _logger.warning("payment_affirm: Affirm payment provider not found (ref or search); cannot link to method.")
            return
        linked = 0
        for method in methods:
            if provider not in method.provider_ids:
                method.provider_ids = method.provider_ids | provider
                linked += 1
        if linked:
            _logger.info("payment_affirm: Linked Affirm provider (id=%s) to %s payment method(s).", provider.id, linked)

    @api.model
    def _payment_affirm_activate_method_if_ready(self, provider):
        """Activate the Affirm payment method when the provider has credentials and is saved."""
        if not provider.affirm_public_key or not provider.affirm_private_key:
            return
        method = self.env['payment.method'].sudo().with_context(active_test=False).search([('code', '=', 'affirm')], limit=1)
        if method and not method.active:
            method.write({'active': True})

    def write(self, vals):
        """When saving Affirm credentials: set code if needed, link to method, activate method if credentials set."""
        if not self.env.context.get('affirm_skip_code_fix') and len(self) == 1:
            provider = self
            has_affirm_keys = (
                vals.get('affirm_public_key') or vals.get('affirm_private_key') or
                provider.affirm_public_key or provider.affirm_private_key
            )
            if provider.code != 'affirm' and has_affirm_keys:
                vals = dict(vals, code='affirm')
        res = super().write(vals)
        if self.env.context.get('affirm_skip_link'):
            return res
        for provider in self:
            if provider.code == 'affirm':
                self.env['payment.provider']._payment_affirm_link_provider_to_method()
                self.env['payment.provider']._payment_affirm_activate_method_if_ready(provider)
                break
        return res

    # === CONSTRAINT METHODS ===#

    @api.constrains('affirm_public_key', 'affirm_private_key')
    def _check_affirm_credentials(self):
        """Validate that API keys are provided when Affirm is enabled."""
        for provider in self:
            if provider.code == 'affirm' and provider.state in ['enabled', 'test']:
                if not provider.affirm_public_key or not provider.affirm_private_key:
                    raise ValidationError(_(
                        'Both Public and Private API keys are required for Affirm payment provider.'
                    ))

    # === OVERRIDE METHODS ===#

    def _get_supported_currencies(self):
        """Override to specify supported currencies for Affirm (USD only)."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'affirm':
            supported_currencies = supported_currencies.filtered(lambda c: c.name == 'USD')
        return supported_currencies

    def _get_default_payment_method_codes(self):
        """Override to add 'affirm' as a payment method code."""
        codes = super()._get_default_payment_method_codes()
        if self.code == 'affirm':
            codes.add('affirm')
        return codes
    
    def _compute_feature_support_fields(self):
        """Override to set feature support for Affirm provider."""
        super()._compute_feature_support_fields()
        for provider in self:
            if provider.code == 'affirm':
                provider.support_refund = 'full_only'
                provider.support_manual_capture = False
                provider.support_tokenization = False

    def _should_build_inline_form(self, is_validation=False):
        """Override to indicate Affirm uses redirect flow, not inline form."""
        if self.code == 'affirm':
            return False
        return super()._should_build_inline_form(is_validation=is_validation)

    def _get_redirect_form_view(self, is_validation=False):
        """Override to return the redirect form template for Affirm."""
        if self.code == 'affirm':
            return self.env.ref('payment_affirm.affirm_form')
        return super()._get_redirect_form_view(is_validation=is_validation)
