# -*- coding: utf-8 -*-

from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    fishbowl_historical_order_ids = fields.One2many(
        'fishbowl.historical.order',
        'partner_id',
        string='Fishbowl Historical Orders',
    )
    fishbowl_historical_order_count = fields.Integer(
        compute='_compute_fishbowl_historical_order_count',
        string='Fishbowl Orders',
    )

    @api.depends('fishbowl_historical_order_ids')
    def _compute_fishbowl_historical_order_count(self):
        for partner in self:
            partner.fishbowl_historical_order_count = len(partner.fishbowl_historical_order_ids)

    def action_import_fishbowl_orders(self):
        """Import Fishbowl orders for this contact: use company Fishbowl connection and contact email/name/phone."""
        self.ensure_one()
        from ..wizard.fishbowl_import_wizard import FB_STATUS, _normalize_phone

        config = self.env['fishbowl.config'].search([
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
        ], limit=1)
        if not config:
            raise UserError(
                'No Fishbowl connection configured for this company. '
                'Set one up under Fishbowl → Connection.'
            )

        ids = set()
        if self.email:
            ids.update(config._get_customer_ids_by_email(self.email))
        if self.name:
            ids.update(config._get_customer_ids_by_name(self.name))
        phone_val = self.phone or getattr(self, 'mobile', None)
        if phone_val:
            ids.update(config._get_customer_ids_by_phone(phone_val))
        customer_ids = list(ids)
        if not customer_ids:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Fishbowl customer found',
                    'message': "No Fishbowl customer matched this contact's name, email, or phone.",
                    'type': 'warning',
                    'sticky': False,
                },
            }

        Order = self.env['fishbowl.historical.order']
        Line = self.env['fishbowl.historical.order.line']
        existing = set(Order.search([('fishbowl_id', '!=', 0)]).mapped('fishbowl_id'))
        total_created = 0
        total_skipped = 0
        total_lines = 0
        offset = 0
        batch_size = 500

        while True:
            rows = config._fetch_orders(
                date_from=None,
                date_to=None,
                limit=batch_size,
                offset=offset,
                so_num=None,
                customer_po=None,
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
                customer_name = bill_to_name or config._fetch_customer_name(row.get('customerId'))
                customer_email = row.get('customer_email') or config._fetch_customer_email(row.get('customerId'))
                customer_phone = row.get('customer_phone') or config._fetch_customer_phone(row.get('customerId'))
                customer_phone = _normalize_phone(customer_phone)
                order_vals = {
                    'name': row.get('num') or 'FB-%s' % so_id,
                    'fishbowl_order_num': row.get('num'),
                    'fishbowl_id': so_id,
                    'partner_id': self.id,
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
                try:
                    lines = config._fetch_order_lines(so_id)
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

        if total_created or total_skipped:
            msg = 'Imported %s order(s), %s line(s).' % (total_created, total_lines)
            if total_skipped:
                msg += ' %s order(s) already existed and were skipped.' % total_skipped
        else:
            msg = 'No orders found for this customer in Fishbowl.'
        # Reload the contact form so Fishbowl History list shows the new orders
        reload_action = {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fishbowl import complete',
                'message': msg,
                'type': 'success' if total_created else 'info',
                'sticky': False,
                'next': reload_action,
            },
        }
