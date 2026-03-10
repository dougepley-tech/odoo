# -*- coding: utf-8 -*-

import logging
import pprint

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AffirmController(http.Controller):
    """Controller to handle Affirm payment provider callbacks."""
    
    _return_url = '/payment/affirm/return'
    _cancel_url = '/payment/affirm/cancel'

    @http.route(
        _return_url, 
        type='http', 
        auth='public', 
        methods=['GET', 'POST'], 
        csrf=False, 
        save_session=False
    )
    def affirm_return_from_checkout(self, **data):
        """
        Process the callback from Affirm after customer authorization.
        
        Affirm redirects here with the checkout_token after the user completes
        the checkout flow in the Affirm modal.
        """
        try:
            dbname = getattr(request.session, 'db', None) or (request.env and request.env.cr.dbname)
        except Exception:
            dbname = 'unknown'
        _logger.info("Handling Affirm return (db=%s) with data:\n%s", dbname, pprint.pformat(data))
        
        checkout_token = data.get('checkout_token')
        reference = data.get('reference')
        
        if not checkout_token:
            _logger.error("Missing checkout_token in Affirm callback")
            return request.redirect('/payment/status')
        
        if not reference:
            _logger.error("Missing reference in Affirm callback")
            return request.redirect('/payment/status')
        
        try:
            notification_data = {
                'checkout_token': checkout_token,
                'reference': reference,
            }
            tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                'affirm', notification_data
            )
            if tx_sudo:
                tx_sudo._process_notification_data(notification_data)
        except Exception as e:
            _logger.exception("Error processing Affirm return: %s", e)
        
        return request.redirect('/payment/status')

    @http.route(
        _cancel_url, 
        type='http', 
        auth='public', 
        methods=['GET', 'POST'], 
        csrf=False, 
        save_session=False
    )
    def affirm_cancel_checkout(self, **data):
        """Handle Affirm checkout cancellation by the user."""
        _logger.info("Affirm checkout cancelled with data:\n%s", pprint.pformat(data))
        
        reference = data.get('reference')
        if reference:
            try:
                tx_sudo = request.env['payment.transaction'].sudo().search([
                    ('reference', '=', reference),
                    ('provider_code', '=', 'affirm')
                ], limit=1)
                
                if tx_sudo and tx_sudo.state not in ('done', 'cancel'):
                    tx_sudo._set_canceled()
            except Exception as e:
                _logger.exception("Error canceling Affirm transaction: %s", e)
        
        return request.redirect('/payment/status')
