# -*- coding: utf-8 -*-
import re

import lxml.html
from lxml import etree
from markupsafe import Markup

from odoo import models

from .warehouse_document import _logo_max_height_px


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _report_html_to_unicode(self, html):
        """Odoo may pass str or bytes to _prepare_html; regex and lxml need a consistent type."""
        if html is None:
            return ''
        if isinstance(html, bytes):
            return html.decode('utf-8', errors='replace')
        return html

    def _prepare_html_warehouse_branding_css(self, html, report_model=False):
        """CSS for PDF split: wkhtmltopdf re-renders web.minimal_layout without web.report_layout head."""
        self.ensure_one()
        helper = self.env['warehouse.document.helper']
        wh_id = self.env.context.get('active_report_warehouse_id')
        warehouse = self.env['stock.warehouse'].browse(wh_id) if wh_id else self.env['stock.warehouse']
        company = self.env.company.sudo()
        html_u = self._report_html_to_unicode(html)
        match = re.search(r'o_company_(\d+)_layout', html_u)
        if match:
            company = self.env['res.company'].browse(int(match.group(1))).sudo()
        if not warehouse and report_model:
            try:
                root = lxml.html.fromstring(html_u, parser=lxml.html.HTMLParser(encoding='utf-8'))
                klass = "//div[contains(concat(' ', normalize-space(@class), ' '), ' article ')]"
                for node in root.xpath(klass):
                    mid = node.get('data-oe-model')
                    oid = node.get('data-oe-id')
                    if mid and oid:
                        warehouse = helper.warehouse_for_record(self.env[mid].browse(int(oid)))
                        break
            except (ValueError, TypeError, KeyError):
                pass
        if not company:
            return Markup('')
        if not warehouse:
            return Markup('')
        if not warehouse._has_any_branding() and not _logo_max_height_px(warehouse):
            return Markup('')
        return helper.build_report_branding_css(company, warehouse)

    def _prepare_html(self, html, report_model=False):
        """Mirror ir.actions.report._prepare_html but pass report_branding_css into minimal_layout renders."""
        layout = self._get_layout()
        if not layout:
            return {}
        html = self._report_html_to_unicode(html)
        base_url = self._get_report_url(layout=layout)
        report_branding_css = self._prepare_html_warehouse_branding_css(html, report_model=report_model)

        root = lxml.html.fromstring(html, parser=lxml.html.HTMLParser(encoding='utf-8'))
        match_klass = "//div[contains(concat(' ', normalize-space(@class), ' '), ' {} ')]"

        header_node = etree.Element('div', id='minimal_layout_report_headers')
        footer_node = etree.Element('div', id='minimal_layout_report_footers')
        bodies = []
        res_ids = []

        body_parent = root.xpath('//main')[0]
        for node in root.xpath(match_klass.format('header')):
            body_parent = node.getparent()
            node.getparent().remove(node)
            header_node.append(node)

        for node in root.xpath(match_klass.format('footer')):
            body_parent = node.getparent()
            node.getparent().remove(node)
            footer_node.append(node)

        for node in root.xpath(match_klass.format('article')):
            IrQweb = self.env['ir.qweb']
            if node.get('data-oe-lang'):
                IrQweb = IrQweb.with_context(lang=node.get('data-oe-lang'))
            body = IrQweb._render(
                layout.id,
                {
                    'subst': False,
                    'body': Markup(lxml.html.tostring(node, encoding='unicode')),
                    'base_url': base_url,
                    'report_xml_id': self.xml_id,
                    'debug': self.env.context.get('debug'),
                    'report_branding_css': report_branding_css,
                },
                raise_if_not_found=False,
            )
            bodies.append(body)
            if node.get('data-oe-model') == report_model:
                res_ids.append(int(node.get('data-oe-id', 0)))
            else:
                res_ids.append(None)

        if not bodies:
            body = ''.join(lxml.html.tostring(c, encoding='unicode') for c in body_parent.getchildren())
            bodies.append(body)

        specific_paperformat_args = {}
        for attribute in root.items():
            if attribute[0].startswith('data-report-'):
                specific_paperformat_args[attribute[0]] = attribute[1]

        render_vals = {
            'base_url': base_url,
            'report_xml_id': self.xml_id,
            'debug': self.env.context.get('debug'),
            'report_branding_css': report_branding_css,
        }
        header = self.env['ir.qweb']._render(
            layout.id,
            {
                'subst': True,
                'body': Markup(lxml.html.tostring(header_node, encoding='unicode')),
                **render_vals,
            },
        )
        footer = self.env['ir.qweb']._render(
            layout.id,
            {
                'subst': True,
                'body': Markup(lxml.html.tostring(footer_node, encoding='unicode')),
                **render_vals,
            },
        )

        return bodies, res_ids, header, footer, specific_paperformat_args

    def get_paperformat(self):
        wh_id = self.env.context.get('active_report_warehouse_id')
        if wh_id:
            wh = self.env['stock.warehouse'].browse(wh_id)
            if wh.exists() and wh.paperformat_id:
                return wh.paperformat_id
        return super().get_paperformat()

    def _get_rendering_context(self, report, docids, data):
        data = super()._get_rendering_context(report, docids, data)
        if data is None:
            data = {}
        helper = self.env['warehouse.document.helper']

        # --- Document record (warehouse + company must match QWeb layout classes) ---
        docs = data.get('docs')
        doc_single = data.get('doc') or data.get('object')
        record = None
        if docs is not None and len(docs):
            record = docs[0]
        elif doc_single is not None and hasattr(doc_single, '_name'):
            record = doc_single

        warehouse = self.env['stock.warehouse']
        if record is not None:
            warehouse = helper.warehouse_for_record(record)
        elif report and report.model:
            effective = docids
            if not effective and isinstance(data, dict):
                effective = data.get('doc_ids') or data.get('ids')
            if effective:
                warehouse = helper.warehouse_for_report(report.model, effective)

        data['warehouse'] = warehouse
        data.setdefault('report_branding_css', Markup(''))

        # Layout uses o_company_<doc.company_id.id>_layout; CSS must use the same company id.
        company = data.get('company')
        if record is not None and hasattr(record, 'company_id') and record.company_id:
            company = record.company_id.sudo()
        elif not company and docs is not None and len(docs):
            d0 = docs[0]
            if hasattr(d0, 'company_id') and d0.company_id:
                company = d0.company_id.sudo()
        if not company and docids and report and report.model:
            recs = self.env[report.model].browse(docids)
            if recs and hasattr(recs[0], 'company_id') and recs[0].company_id:
                company = recs[0].company_id.sudo()
        if not company:
            company = self.env.company.sudo()
        data['company'] = company

        if warehouse and company and (
            warehouse._has_any_branding() or _logo_max_height_px(warehouse)
        ):
            data['report_branding_css'] = data['report_branding_css'] + helper.build_report_branding_css(
                company, warehouse
            )
        return data

    def _render_qweb_html(self, report_ref, docids=None, data=None):
        """PDF generation calls this, not _render_qweb_pdf; keep warehouse in env context too."""
        helper = self.env['warehouse.document.helper']
        report = self._get_report(report_ref)
        wh = helper.warehouse_for_report(report.model, docids or [])
        ctx = {}
        if wh:
            ctx['active_report_warehouse_id'] = wh.id
        return super(IrActionsReport, self.with_context(**ctx))._render_qweb_html(
            report_ref, docids=docids, data=data
        )

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        helper = self.env['warehouse.document.helper']
        report = self._get_report(report_ref)
        warehouse = helper.warehouse_for_report(report.model, res_ids or [])
        ctx = {}
        # Paper format override uses this; harmless when only branding is customized.
        if warehouse:
            ctx['active_report_warehouse_id'] = warehouse.id
        return super(IrActionsReport, self.with_context(**ctx))._render_qweb_pdf(
            report_ref, res_ids=res_ids, data=data
        )

    def report_action(self, docids, data=None, config=True):
        self.ensure_one()
        if self.model == 'stock.picking' and docids:
            ids = docids.ids if hasattr(docids, 'ids') else docids
            if isinstance(ids, int):
                ids = [ids]
            pickings = self.env['stock.picking'].browse(ids)
            if len(pickings) == 1:
                wh = self.env['warehouse.document.helper'].warehouse_for_record(pickings)
                delivery_ref = self.env.ref('stock.action_report_delivery', raise_if_not_found=False)
                if (
                    wh
                    and wh.delivery_report_id
                    and delivery_ref
                    and self.id == delivery_ref.id
                ):
                    return wh.delivery_report_id.report_action(docids, data=data, config=config)
        return super().report_action(docids, data=data, config=config)
