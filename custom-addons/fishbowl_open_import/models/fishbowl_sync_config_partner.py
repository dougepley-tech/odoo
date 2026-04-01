# -*- coding: utf-8 -*-

import logging
import re

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _norm_phone(phone_str):
    if not phone_str or not isinstance(phone_str, str):
        return False
    s = phone_str.strip()
    if not s:
        return False
    digits = re.sub(r'\D', '', s)
    if not digits:
        return s
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    if len(digits) == 10:
        return '+1' + digits
    return '+' + digits


def _fishbowl_line_part_descriptor(line):
    """Identify a Fishbowl SO/PO line when part lookup fails (for error messages / logs)."""
    bits = []
    part_num = (line.get('part_num') or line.get('partNum') or '').strip()
    if part_num:
        bits.append('part %s' % part_num)
    prod_num = (line.get('productNum') or '').strip()
    if prod_num and prod_num != part_num:
        bits.append('productNum %s' % prod_num)
    if line.get('productId'):
        bits.append('productId %s' % line['productId'])
    if line.get('partId'):
        bits.append('partId %s' % line['partId'])
    if line.get('soitem_id'):
        bits.append('soitem %s' % line['soitem_id'])
    if line.get('poitem_id'):
        bits.append('poitem %s' % line['poitem_id'])
    desc = (line.get('description') or '').strip()
    if desc:
        bits.append('"%s"' % (desc[:120] + ('…' if len(desc) > 120 else '')))
    return ', '.join(bits) if bits else '(no identifiers on line)'


class FishbowlSyncConfigPartner(models.Model):
    _inherit = 'fishbowl.sync.config'

    def _fishbowl_ctx(self):
        return {
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
            'mail_notify_force_send': False,
            'mail_post_autofollow': False,
            'fishbowl_import': True,
        }

    def _get_fishbowl_import_tag(self):
        """Return the ``Fishbowl Import`` product tag (create / bind xml id if missing)."""
        ProductTag = self.env['product.tag'].sudo()
        ProductTag._fishbowl_import_ensure_tag_xmlid()
        tag = self.env.ref('fishbowl_open_import.product_tag_fishbowl_import', raise_if_not_found=False)
        if tag:
            return tag
        tag = ProductTag.search([('name', '=', 'Fishbowl Import')], limit=1)
        if tag:
            return tag
        return ProductTag.create({'name': 'Fishbowl Import'})

    def _get_fishbowl_import_partner_category(self):
        """Return the ``Fishbowl Import`` contact tag (``res.partner.category``)."""
        Category = self.env['res.partner.category'].sudo()
        Category._fishbowl_import_ensure_category_xmlid()
        cat = self.env.ref(
            'fishbowl_open_import.res_partner_category_fishbowl_import',
            raise_if_not_found=False,
        )
        if cat:
            return cat
        cat = Category.search([('name', '=', 'Fishbowl Import')], limit=1)
        if cat:
            return cat
        return Category.create({'name': 'Fishbowl Import'})

    def _po_line_purchase_unit_cost(self, part, po_line):
        """Unit cost from PO line dict; optional ``part`` dict for Fishbowl standard cost fallback."""
        self.ensure_one()
        part = part or {}
        if not po_line:
            return float(part.get('standardCost') or part.get('standard_cost') or 0.0)
        v = po_line.get('unitCost')
        if v is None:
            v = po_line.get('unit_cost')
        if v is not None:
            return float(v)
        qty = self.fb_po_line_ordered_qty(po_line)
        total = po_line.get('totalCost') or po_line.get('total_cost')
        if total is not None and qty:
            return float(total) / qty
        return float(part.get('standardCost') or part.get('standard_cost') or 0.0)

    def _fishbowl_add_product_supplierinfo(self, tmpl, partner, price):
        """Attach vendor to product template with purchase price (create or update existing row)."""
        self.ensure_one()
        Supplierinfo = self.env['product.supplierinfo']
        si = Supplierinfo.search(
            [
                ('product_tmpl_id', '=', tmpl.id),
                ('partner_id', '=', partner.id),
            ],
            limit=1,
        )
        vals = {
            'partner_id': partner.id,
            'product_tmpl_id': tmpl.id,
            'price': price,
            'min_qty': 0.0,
        }
        if si:
            si.write({'price': price})
        else:
            Supplierinfo.with_context(**self._fishbowl_ctx()).create(vals)

    def create_or_get_customer_partner(self, customer_id):
        self.ensure_one()
        if not customer_id:
            raise UserError('Missing Fishbowl customer id.')
        existing = self.env['res.partner'].search(
            [
                ('fishbowl_customer_id', '=', int(customer_id)),
                '|',
                ('company_id', '=', False),
                ('company_id', 'in', self.company_id.ids),
            ],
            limit=1,
        )
        if existing:
            return existing
        row = self.fetch_customer_row(customer_id)
        if not row or not (row.get('name') or '').strip():
            raise UserError('Fishbowl customer %s has no name.' % customer_id)
        account_id = row.get('accountId')
        email, phone = self.fetch_customer_email_phone(account_id)
        addr = self.fetch_address_for_account(account_id)
        phone = _norm_phone(phone) if phone else False
        is_company = True
        name = row['name'].strip()
        cat = self._get_fishbowl_import_partner_category()
        vals = {
            'name': name,
            'is_company': is_company,
            'customer_rank': 1,
            'fishbowl_customer_id': int(customer_id),
            'email': email or False,
            'phone': phone,
            'street': addr.get('street') or False,
            'city': addr.get('city') or False,
            'zip': addr.get('zip') or False,
            'category_id': [(6, 0, [cat.id])],
        }
        return self.env['res.partner'].with_context(**self._fishbowl_ctx()).create(vals)

    def create_or_get_vendor_partner(self, vendor_id):
        self.ensure_one()
        if not vendor_id:
            raise UserError('Missing Fishbowl vendor id.')
        existing = self.env['res.partner'].search(
            [
                ('fishbowl_vendor_id', '=', int(vendor_id)),
                '|',
                ('company_id', '=', False),
                ('company_id', 'in', self.company_id.ids),
            ],
            limit=1,
        )
        if existing:
            return existing
        row = self.fetch_vendor_row(vendor_id)
        if not row or not (row.get('name') or '').strip():
            raise UserError('Fishbowl vendor %s has no name.' % vendor_id)
        account_id = row.get('accountId')
        email, phone = self.fetch_customer_email_phone(account_id)
        addr = self.fetch_address_for_account(account_id)
        phone = _norm_phone(phone) if phone else False
        cat = self._get_fishbowl_import_partner_category()
        vals = {
            'name': row['name'].strip(),
            'is_company': True,
            'supplier_rank': 1,
            'fishbowl_vendor_id': int(vendor_id),
            'email': email or False,
            'phone': phone,
            'street': addr.get('street') or False,
            'city': addr.get('city') or False,
            'zip': addr.get('zip') or False,
            'category_id': [(6, 0, [cat.id])],
        }
        return self.env['res.partner'].with_context(**self._fishbowl_ctx()).create(vals)

    def create_product_from_fishbowl_line(self, line, is_so_line=True, po_vendor_id=None):
        """Create product from Fishbowl SOITEM or POITEM row dict.

        For PO lines, pass ``po_vendor_id`` (Fishbowl ``po.vendorId``) so the Odoo product gets
        ``standard_price`` and ``product.supplierinfo`` for the PO vendor (partner created if missing).
        """
        self.ensure_one()
        part = None
        if line.get('productId'):
            part = self.fetch_part_by_product_id(line['productId'])
        if not part and line.get('partId'):
            part = self.fetch_part(line['partId'])
        if not part and line.get('poitem_id'):
            part = self.fetch_part_by_poitem_id(line['poitem_id'])
        if not part:
            pn = (line.get('part_num') or line.get('partNum') or line.get('productNum') or '').strip()
            if pn:
                part = self.fetch_part_by_num(pn)
        if not part:
            return self._create_product_from_fishbowl_line_stub(
                line, is_so_line=is_so_line, po_vendor_id=po_vendor_id
            )
        return self.create_product_from_fishbowl_part(
            part['id'],
            so_line=line if is_so_line else None,
            po_line=None if is_so_line else line,
            po_vendor_id=po_vendor_id,
        )

    def resolve_product_for_so_line(self, line, create_missing_products=False):
        """Resolve ``product.product`` from a Fishbowl SO line dict (default_code / ``productId`` → part).

        When ``create_missing_products`` is True, delegates to :meth:`create_product_from_fishbowl_line`
        (may raise ``UserError``).
        """
        self.ensure_one()
        Product = self.env['product.product']
        num = (line.get('part_num') or line.get('productNum') or '').strip()
        if num:
            p = Product.search([('default_code', '=', num)], limit=1)
            if p:
                return p
        part = None
        if line.get('productId'):
            part = self.fetch_part_by_product_id(line['productId'])
        if part:
            p = Product.search(
                [('product_tmpl_id.fishbowl_part_id', '=', int(part['id']))],
                limit=1,
            )
            if p:
                return p
        if create_missing_products:
            return self.create_product_from_fishbowl_line(line, is_so_line=True)
        return Product.browse()

    def create_product_from_fishbowl_part(self, part_id, so_line=None, po_line=None, po_vendor_id=None):
        """Create product.template + variant from Fishbowl part id."""
        self.ensure_one()
        part = self.fetch_part(part_id)
        if not part:
            raise UserError('Fishbowl part %s not found.' % part_id)
        num = (part.get('num') or '').strip()
        if not num:
            raise UserError('Fishbowl part %s has no part number.' % part_id)
        existing = self.env['product.template'].search(
            [('fishbowl_part_id', '=', int(part_id))],
            limit=1,
        )
        if existing:
            return existing.product_variant_ids[:1]
        existing_code = self.env['product.product'].search([('default_code', '=', num)], limit=1)
        if existing_code:
            return existing_code
        desc = (part.get('description') or num).strip()
        list_price = float(part.get('salesPrice') or part.get('sales_price') or 0.0)
        part_std_cost = float(part.get('standardCost') or part.get('standard_cost') or 0.0)
        std_cost = part_std_cost
        if so_line:
            list_price = float(so_line.get('unitPrice') or so_line.get('unit_price') or list_price)
        if po_line:
            std_cost = self._po_line_purchase_unit_cost(part, po_line)
        tag = self._get_fishbowl_import_tag()
        tmpl_vals = {
            'name': desc,
            'default_code': num,
            'list_price': list_price,
            'standard_price': std_cost,
            'type': 'consu',
            'is_storable': True,
            'fishbowl_part_id': int(part_id),
            'sale_ok': bool(so_line),
            'purchase_ok': True,
            'product_tag_ids': [(6, 0, [tag.id])],
        }
        tmpl = self.env['product.template'].with_context(**self._fishbowl_ctx()).create(tmpl_vals)

        po_v = None
        if po_vendor_id is not None and po_vendor_id != '':
            try:
                po_v = int(po_vendor_id)
            except (TypeError, ValueError):
                po_v = None
        part_v = None
        if part.get('defaultVendorId'):
            try:
                part_v = int(part['defaultVendorId'])
            except (TypeError, ValueError):
                part_v = None

        if po_v:
            try:
                vendor = self.create_or_get_vendor_partner(po_v)
                self._fishbowl_add_product_supplierinfo(tmpl, vendor, std_cost)
            except Exception as exc:
                _logger.warning(
                    'Fishbowl product import: could not attach PO vendor %s to %s: %s', po_v, num, exc
                )
        if part_v and part_v != po_v:
            try:
                vendor = self.create_or_get_vendor_partner(part_v)
                self._fishbowl_add_product_supplierinfo(tmpl, vendor, part_std_cost)
            except Exception as exc:
                _logger.warning(
                    'Fishbowl product import: could not attach part default vendor %s to %s: %s',
                    part_v,
                    num,
                    exc,
                )
        return tmpl.product_variant_ids[:1]

    def _create_product_from_fishbowl_line_stub(self, line, is_so_line=True, po_vendor_id=None):
        """Create a product when Fishbowl has no ``part`` row (orphan line / broken links).

        Uses line description and part number from the import row; applies Fishbowl Import tag,
        PO cost, and PO vendor when applicable.
        """
        self.ensure_one()
        Product = self.env['product.product']
        pn = (line.get('part_num') or line.get('partNum') or line.get('productNum') or '').strip()
        if not pn and line.get('poitem_id'):
            pn = 'FB-POITEM-%s' % line['poitem_id']
        if not pn and line.get('soitem_id'):
            pn = 'FB-SOITEM-%s' % line['soitem_id']
        if not pn:
            pn = 'FB-IMPORT'
        existing = Product.search([('default_code', '=', pn)], limit=1)
        if existing:
            return existing
        base = pn
        seq = 1
        while Product.search([('default_code', '=', pn)], limit=1):
            pn = '%s-%s' % (base, seq)
            seq += 1
        desc = (line.get('description') or pn).strip() or pn
        if is_so_line:
            list_price = float(line.get('unitPrice') or line.get('unit_price') or 0.0)
            std_cost = 0.0
            po_line = None
        else:
            list_price = 0.0
            po_line = line
            std_cost = self._po_line_purchase_unit_cost({}, po_line)
        tag = self._get_fishbowl_import_tag()
        tmpl_vals = {
            'name': desc,
            'default_code': pn,
            'list_price': list_price,
            'standard_price': std_cost,
            'type': 'consu',
            'is_storable': True,
            'sale_ok': bool(is_so_line),
            'purchase_ok': True,
            'product_tag_ids': [(6, 0, [tag.id])],
        }
        tmpl = self.env['product.template'].with_context(**self._fishbowl_ctx()).create(tmpl_vals)
        _logger.warning(
            'Fishbowl import: created product without Fishbowl part row for %s',
            _fishbowl_line_part_descriptor(line),
        )
        if not is_so_line and po_vendor_id:
            try:
                po_v = int(po_vendor_id)
                vendor = self.create_or_get_vendor_partner(po_v)
                self._fishbowl_add_product_supplierinfo(tmpl, vendor, std_cost)
            except Exception as exc:
                _logger.warning(
                    'Fishbowl stub product: could not attach PO vendor %s: %s', po_vendor_id, exc
                )
        return tmpl.product_variant_ids[:1]
