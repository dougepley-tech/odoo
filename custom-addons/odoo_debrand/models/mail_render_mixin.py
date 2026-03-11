# Copyright 2019 O4SB - Graeme Gellatly
# Copyright 2019 Tecnativa - Ernesto Tejeda
# Copyright 2020 Onestein - Andrea Stirpe
# Copyright 2021 Tecnativa - João Marques
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import re

from lxml import etree, html
from markupsafe import Markup

from odoo import api, models


class MailRenderMixin(models.AbstractModel):
    _inherit = "mail.render.mixin"

    def remove_href_odoo(self, value, to_keep=None):
        """Remove odoo.com links and 'Powered by' from HTML content."""
        if not value or len(value) < 20:
            return value
        back_to_bytes = False
        back_to_markup = False
        if isinstance(value, bytes):
            back_to_bytes = True
            value = value.decode()
        if isinstance(value, Markup):
            back_to_markup = True
        if "<" in value:
            try:
                tree = html.fromstring(value)
                odoo_anchors = tree.xpath('//a[contains(@href,"odoo.com")]')
                for elem in odoo_anchors:
                    parent = elem.getparent()
                    if parent is None:
                        continue
                    previous = elem.getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + "\u00a0"
                    elif parent.text:
                        parent.text = (parent.text or "") + "\u00a0"
                    parent.remove(elem)
                value = etree.tostring(
                    tree, pretty_print=True, method="html", encoding="unicode"
                )
                if to_keep:
                    value = value.replace("\u00a0", to_keep)
            except (etree.XMLSyntaxError, etree.ParserError):
                pass
        if len(value) > 20:
            value = re.sub(r"\bodoo\b", " ", value, flags=re.IGNORECASE)
        if back_to_bytes:
            value = value.encode()
        elif back_to_markup:
            value = Markup(value)
        return value

    @api.model
    def _render_template(
        self,
        template_src,
        model,
        res_ids,
        engine="inline_template",
        add_context=None,
        options=None,
    ):
        """Post-process rendered template to remove Odoo branding."""
        rendered = super()._render_template(
            template_src,
            model,
            res_ids,
            engine=engine,
            add_context=add_context,
            options=options,
        )
        mixin = self.env["mail.render.mixin"]
        return {
            res_id: mixin.remove_href_odoo(html_str)
            for res_id, html_str in rendered.items()
        }
