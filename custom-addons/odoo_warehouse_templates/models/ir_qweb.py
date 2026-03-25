# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import models


class IrQweb(models.AbstractModel):
    _inherit = 'ir.qweb'

    def _render(self, template, values=None, **kwargs):
        """Ensure minimal_layout always has report_branding_css (QWeb t-if / t-out)."""
        vals = dict(values or {})
        minimal = self.env.ref('web.minimal_layout', raise_if_not_found=False)
        if minimal:
            # Public user cannot read ir.ui.view; sudo only for id/key comparison.
            minimal = minimal.sudo()
            is_minimal = template == minimal.id
            if not is_minimal and isinstance(template, str):
                is_minimal = template in (minimal.key, 'web.minimal_layout')
            if is_minimal:
                vals.setdefault('report_branding_css', Markup(''))
        return super()._render(template, vals, **kwargs)
