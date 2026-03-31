# -*- coding: utf-8 -*-
"""Shared defaults for Fishbowl import wizards."""


def default_fishbowl_sync_config(env):
    """Return the active fishbowl.sync.config for the current company, if any."""
    return env['fishbowl.sync.config'].search(
        [
            ('company_id', '=', env.company.id),
            ('active', '=', True),
        ],
        limit=1,
        order='id',
    )
