# -*- coding: utf-8 -*-

from . import models
from . import controllers


def post_init_hook(env):
    """Runs only on first install. Code is also set via data file (runs on upgrade)."""
    from odoo.addons.payment import reset_payment_provider
    reset_payment_provider(env, 'affirm')
    env['payment.provider']._payment_affirm_force_code()


def uninstall_hook(env):
    """Uninstallation hook to neutralize the payment provider."""
    from odoo.addons.payment import reset_payment_provider
    reset_payment_provider(env, 'affirm', neutralize=True)
