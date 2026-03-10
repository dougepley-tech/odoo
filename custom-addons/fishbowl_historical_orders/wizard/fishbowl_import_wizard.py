# -*- coding: utf-8 -*-

import re
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


def _normalize_phone(phone_str):
    """
    Normalize phone to Odoo format: + and digits only (no hyphens, parentheses, or spaces).
    E.g. +14802498989. Optional extension appended as " ext. N".
    """
    if not phone_str or not isinstance(phone_str, str):
        return (phone_str or '').strip() or None
    s = phone_str.strip()
    if not s:
        return None
    # Extract extension: "ext. 36007", "ext 123", "x1234", "extension 5"
    ext_match = re.search(r'(?:ext\.?|extension)\s*[:\-]?\s*(\d+)\s*$', s, re.I)
    if not ext_match:
        ext_match = re.search(r'\bx\s*[:\-]?\s*(\d+)\s*$', s, re.I)
    extension = ext_match.group(1) if ext_match else None
    if ext_match:
        s = s[:ext_match.start()].strip()
    digits = re.sub(r'\D', '', s)
    if not digits:
        return phone_str.strip() or None
    # US 11-digit with leading 1: keep as +1 + 10 digits for Odoo
    if len(digits) == 11 and digits.startswith('1'):
        normalized = '+' + digits
    elif len(digits) == 10 and digits.isdigit():
        normalized = '+1' + digits
    else:
        normalized = '+' + digits
    ext_suffix = (' ext. %s' % extension) if extension else ''
    return normalized + ext_suffix


# Fishbowl SO status values (common)
FB_STATUS = {
    10: 'estimate',
    20: 'issued',
    25: 'in_progress',
    60: 'fulfilled',
    80: 'void',
    85: 'cancelled',
    90: 'expired',
    95: 'historical',
}


class FishbowlImportWizard(models.TransientModel):
    _name = 'fishbowl.import.wizard'
    _description = 'Import Fishbowl Historical Orders'

    config_id = fields.Many2one(
        'fishbowl.config',
        string='Fishbowl Connection',
        required=True,
        domain=[('active', '=', True)],
    )
    date_from = fields.Date(
        string='From Date',
        default=lambda self: fields.Date.context_today(self) - timedelta(days=365 * 3),
        help='Optional. Limit to orders from this date. Leave empty when searching by SO#, PO, or customer.',
    )
    date_to = fields.Date(
        string='To Date',
        default=fields.Date.context_today,
        help='Optional. Limit to orders up to this date.',
    )
    batch_size = fields.Integer(
        string='Batch Size',
        default=500,
        help='Number of orders to import per batch (0 = no limit).',
    )
    fishbowl_so_number = fields.Char(
        string='Fishbowl SO#',
        help='Import only this sales order number (date range optional).',
    )
    customer_po = fields.Char(
        string='Customer PO',
        help='Import only orders with this Customer PO (date range optional).',
    )
    customer_email = fields.Char(
        string='Customer Email',
        help='Import all orders for a customer matching this email (date range optional).',
    )
    customer_name = fields.Char(
        string='Customer Name',
        help='Import all orders for customers whose name or Bill To contains this text.',
    )
    customer_phone = fields.Char(
        string='Customer Phone',
        help='Import all orders for a customer with this phone number (digits used for matching).',
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Done')],
        default='draft',
    )
    result_message = fields.Text(string='Result', readonly=True)

    def action_import(self):
        self.ensure_one()
        so_num = (self.fishbowl_so_number or '').strip() or None
        customer_po_val = (self.customer_po or '').strip() or None
        customer_email_val = (self.customer_email or '').strip() or None
        customer_name_val = (self.customer_name or '').strip() or None
        customer_phone_val = (self.customer_phone or '').strip() or None
        has_search = bool(so_num or customer_po_val or customer_email_val or customer_name_val or customer_phone_val)
        if not has_search and (not self.date_from or not self.date_to):
            raise UserError(
                'Either use a date range (From Date and To Date) or enter at least one search: '
                'Fishbowl SO#, Customer PO, Customer Email, Customer Name, or Customer Phone.'
            )
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise UserError('From Date must be before To Date.')
        customer_ids = None
        if customer_email_val or customer_name_val or customer_phone_val:
            ids = set()
            if customer_email_val:
                ids.update(self.config_id._get_customer_ids_by_email(customer_email_val))
            if customer_name_val:
                ids.update(self.config_id._get_customer_ids_by_name(customer_name_val))
            if customer_phone_val:
                ids.update(self.config_id._get_customer_ids_by_phone(customer_phone_val))
            customer_ids = list(ids) if ids else []
            if not customer_ids and has_search and not so_num and not customer_po_val:
                raise UserError('No Fishbowl customer found for the given email, name, or phone.')
        Order = self.env['fishbowl.historical.order']
        Line = self.env['fishbowl.historical.order.line']
        existing = set(
            Order.search([('fishbowl_id', '!=', 0)]).mapped('fishbowl_id')
        )
        total_created = 0
        total_skipped = 0
        total_lines = 0
        offset = 0
        batch_size = self.batch_size or 999999

        while True:
            rows = self.config_id._fetch_orders(
                date_from=self.date_from or None,
                date_to=self.date_to or None,
                limit=batch_size,
                offset=offset,
                so_num=so_num,
                customer_po=customer_po_val,
                customer_ids=customer_ids,
            )
            if not rows:
                break
            for row in rows:
                so_id = row.get('id')
                if so_id in existing:
                    total_skipped += 1
                    continue
                order_date = row.get('order_date')
                if isinstance(order_date, datetime):
                    order_date = order_date.date()
                date_completed = row.get('date_completed')
                if date_completed and isinstance(date_completed, datetime):
                    date_completed = date_completed.date()
                status = row.get('status')
                state = FB_STATUS.get(status, 'unknown') if status is not None else 'unknown'
                bill_to_name = row.get('billToName')
                customer_name = bill_to_name or self.config_id._fetch_customer_name(row.get('customerId'))
                customer_email = row.get('customer_email') or self.config_id._fetch_customer_email(row.get('customerId'))
                customer_phone = row.get('customer_phone') or self.config_id._fetch_customer_phone(row.get('customerId'))
                customer_phone = _normalize_phone(customer_phone)
                partner_id = self._match_partner(customer_email)
                order_vals = {
                    'name': row.get('num') or 'FB-%s' % so_id,
                    'fishbowl_order_num': row.get('num'),
                    'fishbowl_id': so_id,
                    'partner_id': partner_id and partner_id.id,
                    'fishbowl_customer_id': row.get('customerId'),
                    'fishbowl_customer_name': customer_name,
                    'fishbowl_customer_email': customer_email,
                    'fishbowl_customer_phone': customer_phone,
                    'customer_po': row.get('customerPO'),
                    'order_date': order_date,
                    'date_completed': date_completed,
                    'state': state,
                    'note': row.get('note'),
                }
                order = Order.create(order_vals)
                existing.add(so_id)
                total_created += 1
                # Lines
                try:
                    lines = self.config_id._fetch_order_lines(so_id)
                except Exception:
                    lines = []
                for seq, line_row in enumerate(lines, start=1):
                    qty = line_row.get('qty', 1)
                    unit_price = line_row.get('unit_price', 0)
                    total_price = line_row.get('total_price') or (float(qty) * float(unit_price))
                    Line.create({
                        'order_id': order.id,
                        'sequence': line_row.get('sequence', seq) or seq,
                        'fishbowl_part_id': line_row.get('partId') or line_row.get('productId'),
                        'part_num': line_row.get('partNum') or line_row.get('productNum'),
                        'product_num': line_row.get('product_num') or line_row.get('productNum') or line_row.get('partNum'),
                        'description': line_row.get('description'),
                        'quantity': qty,
                        'unit_price': unit_price,
                        'total_price': total_price,
                    })
                    total_lines += 1
            offset += len(rows)
            if len(rows) < batch_size:
                break

        msg = (
            'Import complete.\n'
            'Orders created: %s\n'
            'Orders skipped (already exist): %s\n'
            'Lines imported: %s'
        ) % (total_created, total_skipped, total_lines)
        self.write({'state': 'done', 'result_message': msg})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _match_partner(self, fishbowl_customer_email=None):
        """Match Fishbowl customer to res.partner by email only (email is unique)."""
        email = (fishbowl_customer_email or '').strip()
        if not email:
            return self.env['res.partner']
        return self.env['res.partner'].search(
            [('email', '=ilike', email)],
            limit=1,
        )
