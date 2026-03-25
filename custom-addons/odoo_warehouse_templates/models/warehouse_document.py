# -*- coding: utf-8 -*-
"""Resolve warehouse from business documents; build extra report CSS for warehouse branding."""
import logging

from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)


def _hex_to_rgb_tuple(hex_color):
    if not hex_color or not isinstance(hex_color, str):
        return None
    h = hex_color.strip()
    if h.startswith('#'):
        h = h[1:]
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _logo_max_height_px(warehouse):
    """Return a clamped pixel height for report logos, or None when layout default applies."""
    if not warehouse or not warehouse.report_logo_max_height:
        return None
    try:
        h = int(warehouse.report_logo_max_height)
    except (TypeError, ValueError):
        return None
    if h < 1:
        return None
    return max(16, min(h, 600))


def _contrast_text_color(hex_color):
    """Pick black or white text for a solid background (same idea as Odoo's preview-color-contrast)."""
    rgb = _hex_to_rgb_tuple(hex_color)
    if not rgb:
        return '#ffffff'
    r, g, b = rgb
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return '#000000' if luminance > 0.65 else '#ffffff'


def _normalize_hex_color(val, fallback):
    """Return a CSS color string; color widgets may omit '#'."""
    if not val or not isinstance(val, str):
        return fallback
    v = val.strip()
    if not v:
        return fallback
    if v.startswith('#'):
        return v
    if len(v) in (3, 6) and all(c in '0123456789abcdefABCDEF' for c in v):
        return '#' + v
    return v


class WarehouseDocumentHelper(models.AbstractModel):
    _name = 'warehouse.document.helper'
    _description = 'Warehouse resolution and report branding'

    @api.model
    def warehouse_for_record(self, record):
        """Return a stock.warehouse for a single record, or empty recordset."""
        if not record:
            return self.env['stock.warehouse']
        model = record._name
        if model == 'sale.order':
            return self._warehouse_for_sale_order(record)
        if model == 'stock.picking':
            wh = record.picking_type_id.warehouse_id or getattr(record, 'warehouse_id', False)
            return wh[:1] if wh else self.env['stock.warehouse']
        if model == 'account.move':
            return self._warehouse_for_account_move(record)
        return self.env['stock.warehouse']

    @api.model
    def _warehouse_for_sale_order(self, order):
        """Resolve warehouse for a sale order.

        Prefer ``sale.order.warehouse_id``. When it is missing (common on e-commerce / connector
        orders), infer from outgoing pickings or stock moves so report branding still applies.
        """
        order = order.sudo()
        if order.warehouse_id:
            return order.warehouse_id[:1]
        pickings = order.picking_ids.filtered(lambda p: p.state != 'cancel')
        if pickings:
            pwh = pickings.mapped('picking_type_id.warehouse_id').filtered('id')
            if pwh:
                if len(pwh) > 1:
                    _logger.warning(
                        'odoo_warehouse_templates: order %s has pickings from multiple warehouses; using %s',
                        order.name,
                        pwh[0].display_name,
                    )
                return pwh[0]
        moves = order.order_line.mapped('move_ids').filtered(lambda m: m.state != 'cancel')
        if moves:
            mwh = moves.mapped('picking_id.picking_type_id.warehouse_id').filtered('id')
            if mwh:
                return mwh[0]
            for m in moves:
                loc = m.location_id
                if loc and getattr(loc, 'warehouse_id', False):
                    return loc.warehouse_id[:1]
        return self.env['stock.warehouse']

    @api.model
    def _warehouse_for_account_move(self, move):
        """Prefer sale order warehouse from invoice lines; else pickings from sale stock moves."""
        warehouses = self.env['stock.warehouse']
        sale_lines = move.invoice_line_ids.sale_line_ids
        sale_orders = sale_lines.order_id
        if sale_orders:
            for so in sale_orders:
                wh = self._warehouse_for_sale_order(so)
                if wh:
                    warehouses |= wh
        if not warehouses and sale_lines:
            pickings = sale_lines.move_ids.picking_id
            warehouses |= pickings.picking_type_id.mapped('warehouse_id')
            warehouses |= pickings.mapped('warehouse_id')
        warehouses = warehouses.filtered('id')
        if len(warehouses) > 1:
            _logger.warning(
                'odoo_warehouse_templates: multiple warehouses on invoice %s; using %s',
                move.display_name,
                warehouses[0].display_name,
            )
        return warehouses[:1]

    @api.model
    def warehouse_for_report(self, report_model, docids):
        """Resolve warehouse from the first document of a report."""
        if not docids:
            return self.env['stock.warehouse']
        try:
            docs = self.env[report_model].browse(docids)
        except KeyError:
            return self.env['stock.warehouse']
        if not docs:
            return self.env['stock.warehouse']
        return self.warehouse_for_record(docs[0])

    @api.model
    def build_report_branding_css(self, company, warehouse):
        """Warehouse PDF extras: color/font overrides and optional logo max height."""
        company = company.sudo()
        if not warehouse:
            return Markup('')
        chunks = []
        if warehouse._has_any_branding():
            chunks.append(self._build_report_color_font_css(company, warehouse))
        lh = _logo_max_height_px(warehouse)
        if lh:
            chunks.append(self._build_report_logo_max_height_css(company, lh))
        if warehouse._has_any_branding() or lh:
            chunks.append(self._build_report_header_address_alignment_css(company))
        return Markup(''.join(chunks)) if chunks else Markup('')

    @api.model
    def _build_report_header_address_alignment_css(self, company):
        """wkhtmltopdf: right-align logo + company address in Bubble/Wave headers (nested widgets ignore text-end)."""
        cid = company.sudo().id
        return Markup(
            """
<style type="text/css">
/* Bubble: logo stacked above address in last header cell */
.o_company_{cid}_layout.o_report_layout_bubble .header table {{
    table-layout: fixed !important;
    width: 100% !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header table tr td {{
    vertical-align: top !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header table tr td:first-child {{
    width: 50% !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header table tr td:last-child {{
    width: 50% !important;
    text-align: right !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header table tr td:only-child {{
    width: 100% !important;
    text-align: right !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header img.o_company_logo {{
    display: block !important;
    margin-left: auto !important;
    margin-right: 0 !important;
    margin-bottom: 0.35rem !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header div[name="company_address"],
.o_company_{cid}_layout.o_report_layout_bubble .header ul.wh_report_company_address {{
    text-align: right !important;
    margin-left: auto !important;
    margin-right: 0 !important;
    max-width: 100% !important;
    padding-left: 0 !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header ul.wh_report_company_address li {{
    text-align: right !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble .header div[name="company_address"] p,
.o_company_{cid}_layout.o_report_layout_bubble .header div[name="company_address"] div:not(.bg-light),
.o_company_{cid}_layout.o_report_layout_bubble .header ul.wh_report_company_address span {{
    text-align: right !important;
}}
/* Wave: logo/tagline left, address right */
.o_company_{cid}_layout.o_report_layout_wave .header .d-flex.justify-content-between > div.w-50.text-end,
.o_company_{cid}_layout.o_report_layout_wave .header .d-flex > div.text-end {{
    text-align: right !important;
}}
.o_company_{cid}_layout.o_report_layout_wave .header ul.wh_report_company_address {{
    text-align: right !important;
    margin-left: auto !important;
    margin-right: 0 !important;
    width: 100% !important;
}}
.o_company_{cid}_layout.o_report_layout_wave .header ul.wh_report_company_address li {{
    text-align: right !important;
}}
.o_company_{cid}_layout.o_report_layout_wave .header div[name="company_address"] p,
.o_company_{cid}_layout.o_report_layout_wave .header div[name="company_address"] div:not(.bg-light) {{
    text-align: right !important;
}}
</style>
"""
        ).format(cid=cid)

    @api.model
    def _build_report_logo_max_height_css(self, company, lh_px):
        cid = company.sudo().id
        return Markup(
            """
<style type="text/css">
/* Per-warehouse logo size (matches o_company_logo / _big / _small on external layouts) */
.o_company_{cid}_layout img.o_company_logo,
.o_company_{cid}_layout img.o_company_logo_big,
.o_company_{cid}_layout img.o_company_logo_small {{
    max-height: {lh}px !important;
    max-width: 100% !important;
    width: auto !important;
    height: auto !important;
    object-fit: contain;
}}
</style>
"""
        ).format(cid=cid, lh=int(lh_px))

    @api.model
    def _build_report_color_font_css(self, company, warehouse):
        """Color/font rules (original build_report_branding_css body)."""
        company = company.sudo()
        font = warehouse.font or company.font or 'Lato'
        c_primary = company.primary_color or '#212529'
        c_secondary = company.secondary_color or '#212529'
        wp = (warehouse.primary_color or '').strip()
        ws = (warehouse.secondary_color or '').strip()
        primary = _normalize_hex_color(wp, c_primary) if wp else c_primary
        secondary = _normalize_hex_color(ws, c_secondary) if ws else c_secondary
        cid = company.id
        thead_fg = _contrast_text_color(primary)
        # Mirrors web.styles_company_report layout-specific rules, forced to warehouse colors.
        return Markup(
            """
<style type="text/css">
.o_company_{cid}_layout {{
    font-family: {font}, sans-serif !important;
}}
.o_company_{cid}_layout h2,
.o_company_{cid}_layout h3,
.o_company_{cid}_layout.article .page h2 {{
    color: {primary} !important;
}}
.o_company_{cid}_layout #informations strong {{
    color: {secondary} !important;
}}
.o_company_{cid}_layout .o_total strong {{
    color: {primary} !important;
}}
.o_company_{cid}_layout .o_company_tagline {{
    color: {primary} !important;
}}
/* Generic sale/invoice line tables (covers Bubble, Striped, etc.) */
.o_company_{cid}_layout .o_main_table thead th {{
    background-color: {primary} !important;
    color: {thead_fg} !important;
}}
.o_company_{cid}_layout .o_main_table thead th strong {{
    color: {thead_fg} !important;
}}
/* Delivery slip / stock: div.article (not <article>) — tables are table-sm without .o_main_table */
.o_company_{cid}_layout.article table:not(.o_main_table) thead th,
.o_company_{cid}_layout.article .page table thead th {{
    background-color: {primary} !important;
    color: {thead_fg} !important;
}}
.o_company_{cid}_layout.article table:not(.o_main_table) thead th strong,
.o_company_{cid}_layout.article .page table thead th strong {{
    color: {thead_fg} !important;
}}
.o_company_{cid}_layout #total .o_total td {{
    background-color: {primary} !important;
    color: {thead_fg} !important;
}}
.o_company_{cid}_layout #total .o_total td strong {{
    color: {thead_fg} !important;
}}
/* Boxed */
.o_company_{cid}_layout.o_report_layout_boxed #total .o_total td {{
    background-color: {primary} !important;
}}
.o_company_{cid}_layout.o_report_layout_boxed #total .o_total td strong {{
    color: {thead_fg} !important;
}}
/* Bold */
.o_company_{cid}_layout.o_report_layout_bold .o_main_table thead th {{
    border-top: 3px solid {secondary} !important;
}}
.o_company_{cid}_layout.o_report_layout_bold .o_main_table tbody tr:last-child td {{
    border-bottom: 3px solid {secondary} !important;
}}
/* Wave */
.o_company_{cid}_layout.o_report_layout_wave #informations {{
    border-color: {secondary} !important;
}}
/* Bubble (sale order table header uses these selectors) */
.o_company_{cid}_layout.o_report_layout_bubble #informations {{
    border-color: {secondary} !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble thead th,
.o_company_{cid}_layout.o_report_layout_bubble #total .o_total td {{
    background-color: {primary} !important;
    color: {thead_fg} !important;
}}
.o_company_{cid}_layout.o_report_layout_bubble thead th strong,
.o_company_{cid}_layout.o_report_layout_bubble #total .o_total td strong {{
    color: {thead_fg} !important;
}}
/* Bubble: header decorative circle — warehouse primary at 10% opacity (same idea as stock QWeb) */
.o_company_{cid}_layout svg.o_shape_bubble_1 circle {{
    fill: {primary} !important;
    fill-opacity: 0.1 !important;
}}
</style>
"""
        ).format(
            cid=cid,
            font=font,
            primary=primary,
            secondary=secondary,
            thead_fg=thead_fg,
        )
