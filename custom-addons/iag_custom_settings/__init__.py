# -*- coding: utf-8 -*-

from . import models


def pre_init_hook(env):
    """Odoo 19 passes an Environment, not a raw cursor."""
    from .hooks import pre_init_schema

    pre_init_schema(env)


def post_init_hook(env):
    from .hooks import post_init_hook as migrate

    migrate(env)
