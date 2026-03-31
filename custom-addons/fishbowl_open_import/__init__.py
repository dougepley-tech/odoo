# -*- coding: utf-8 -*-

# Odoo 19+ passes a single ``Environment`` to ``post_init_hook`` (no ``cr``/``registry``).


def post_init_hook(env):
    """Backfill PO import whitelist; drop legacy "Big Request" line on upgrade."""
    env['fishbowl.sync.config'].sudo().search([]).sanitize_import_po_status_names()


from . import models
from . import wizards
