# -*- coding: utf-8 -*-

import logging
import uuid
from datetime import datetime

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .fishbowl_defaults import default_fishbowl_sync_config

_logger = logging.getLogger(__name__)


class FishbowlImportPoWizard(models.TransientModel):
    _name = 'fishbowl.import.po.wizard'
    _description = 'Import Fishbowl Open Purchase Orders'

    @api.model
    def _default_config_id(self):
        return default_fishbowl_sync_config(self.env)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'wizard_po_status_names' in fields_list and not res.get('wizard_po_status_names'):
            cfg = default_fishbowl_sync_config(self.env)
            if cfg:
                cfg.sudo().sanitize_import_po_status_names()
                if cfg.import_po_status_names:
                    res['wizard_po_status_names'] = cfg.import_po_status_names
        return res

    step = fields.Selection(
        [
            ('options', 'Options'),
            ('confirm', 'Confirm'),
            ('done', 'Done'),
        ],
        default='options',
        required=True,
    )
    config_id = fields.Many2one(
        'fishbowl.sync.config',
        string='Fishbowl connection',
        required=True,
        domain=[('active', '=', True)],
        default=_default_config_id,
    )
    fishbowl_po_num = fields.Char(string='Only this Fishbowl PO# (optional)')
    wizard_po_status_names = fields.Text(
        string='Fishbowl PO statuses to import',
        help='Optional: one Fishbowl postatus.name per line (case-insensitive whitelist). '
        'Leave empty to use closed-status exclusion from the connection (Configuration → MySQL).',
    )
    create_missing_products = fields.Boolean(
        string='Create missing products',
        help='Create Odoo products from Fishbowl part rows when no match exists by code or Fishbowl part id. '
        'Uses PO line cost for standard price, attaches the PO vendor (and part default vendor if different) '
        'via supplier pricelist; missing vendors are created like on sales import.',
    )
    post_fishbowl_memos_to_chatter = fields.Boolean(
        string='Post Fishbowl PO Memos (tab) to chatter',
        default=True,
        help='Post each Fishbowl Purchase Order Memo tab entry as an internal note on the Odoo PO.',
    )
    preview_import_count = fields.Integer(string='Orders to import', readonly=True)
    preview_skip_count = fields.Integer(string='Skipped (already in Odoo)', readonly=True)
    preview_summary = fields.Text(string='Preview', readonly=True)
    result_message = fields.Text(string='Result', readonly=True)

    def _ctx(self):
        return {
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
            'mail_notify_force_send': False,
            'mail_post_autofollow': False,
            'fishbowl_import': True,
        }

    def _fishbowl_memo_chatter_body(self, row):
        """Build HTML body for one Fishbowl PO memo row (same shape as SO memos)."""
        text = row.get('memo_text')
        if text is None:
            text = row.get('memo')
        text = (text or '').strip()
        if not text:
            return None
        parts = [Markup('<p><span class="text-muted">Fishbowl memo</span></p>')]
        md = row.get('memo_date')
        if md:
            if isinstance(md, datetime):
                parts.append(
                    Markup('<p><strong>%s</strong></p>')
                    % escape(fields.Datetime.to_string(md))
                )
            else:
                parts.append(Markup('<p><strong>%s</strong></p>') % escape(str(md)))
        un = row.get('user_name') or row.get('username')
        if un:
            parts.append(Markup('<p><em>%s</em></p>') % escape(str(un)))
        joined = Markup('<br/>').join(escape(line) for line in str(text).split('\n'))
        parts.append(Markup('<p>%s</p>') % joined)
        return Markup('').join(parts)

    def _memo_chatter_ctx(self, import_ctx):
        """Post memos without ``fishbowl_import`` so ``mail.mt_note`` log notes appear in chatter."""
        out = dict(import_ctx)
        out.pop('fishbowl_import', None)
        return out

    def _post_fishbowl_memos_to_chatter(self, po, config, po_fb_id, ctx):
        """Post each Fishbowl PO memo row as a log note on the purchase order."""
        self.ensure_one()
        memos = config.fetch_po_memos(int(po_fb_id))
        if not memos:
            _logger.debug(
                'Fishbowl PO memos: no rows for po.id=%s (num=%s); optional: set PO memo tableId on config.',
                po_fb_id,
                po.fishbowl_num or '',
            )
        chatter_ctx = self._memo_chatter_ctx(ctx)
        for row in memos:
            body = self._fishbowl_memo_chatter_body(row)
            if not body:
                continue
            post_vals = {
                'body': body,
                'subtype_xmlid': 'mail.mt_note',
                'message_type': 'comment',
            }
            md = row.get('memo_date')
            if isinstance(md, datetime):
                post_vals['date'] = md
            po.with_context(**chatter_ctx).message_post(**post_vals)

    def _po_status_whitelist_for_fetch(self):
        """Return None to use connection closed-list exclusion; else whitelist tuple."""
        self.ensure_one()
        txt = (self.wizard_po_status_names or '').strip()
        if not txt:
            return None
        return tuple(n.lower() for n in self.config_id._parse_status_lines(txt) if n.strip())

    def _get_po_headers(self):
        self.ensure_one()
        config = self.config_id
        headers = config.fetch_open_purchase_orders(
            allowed_status_names=self._po_status_whitelist_for_fetch(),
        )
        if self.fishbowl_po_num:
            pn = self.fishbowl_po_num.strip()
            headers = [h for h in headers if (h.get('num') or '') == pn]
        return headers

    def _preview_po_counts(self):
        self.ensure_one()
        config = self.config_id
        headers = self._get_po_headers()
        to_import = 0
        skipped = 0
        nums = []
        for hdr in headers:
            existing = self.env['purchase.order'].search(
                [
                    ('fishbowl_po_id', '=', int(hdr['id'])),
                    ('company_id', '=', config.company_id.id),
                ],
                limit=1,
            )
            if existing:
                skipped += 1
            else:
                to_import += 1
                nums.append((hdr.get('num') or '?').strip())
        return to_import, skipped, nums

    def _reopen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_preview(self):
        self.ensure_one()
        if not self.config_id:
            raise UserError('Select a Fishbowl connection.')
        to_import, skipped, nums = self._preview_po_counts()
        lines = []
        for n in nums[:80]:
            lines.append('- %s' % n)
        if len(nums) > 80:
            lines.append('... and %s more' % (len(nums) - 80))
        body = '\n'.join(lines) if lines else '(none)'
        summary = (
            'Orders that will be imported: %s\n'
            'Already in Odoo (skipped): %s\n\n'
            'Order numbers:\n%s'
        ) % (to_import, skipped, body)
        self.write(
            {
                'step': 'confirm',
                'preview_import_count': to_import,
                'preview_skip_count': skipped,
                'preview_summary': summary,
            }
        )
        return self._reopen()

    def action_back_to_options(self):
        self.write(
            {
                'step': 'options',
                'preview_import_count': 0,
                'preview_skip_count': 0,
                'preview_summary': False,
                'result_message': False,
            }
        )
        return self._reopen()

    def _po_line_type_note_for_import(self, line):
        """When Fishbowl PO line Type is Credit Return or Misc. Credit, return a label for the Odoo line description."""
        raw = (line.get('poitem_type_name') or line.get('poitemtype_name') or '').strip()
        if not raw:
            # PyMySQL key casing / alternate column names
            for key, val in line.items():
                if not val or not isinstance(key, str):
                    continue
                kl = key.lower()
                if 'poitem_type' in kl or kl == 'typename':
                    raw = str(val).strip()
                    break
        t = ' '.join(raw.lower().split())
        if not t:
            return ''
        if t == 'credit return' or t.startswith('credit return'):
            return 'Credit Return'
        if 'misc' in t and 'credit' in t:
            return 'Misc. Credit'
        return ''

    def _fishbowl_po_line_name(self, prod, partner, line):
        """Odoo PO line ``name``: default purchase description plus optional type note on a second line.

        Built from ``product.product`` / template only. We do **not** call
        ``purchase.order.line._get_product_purchase_description``: on Odoo 19+ its ``product_lang``
        argument must be a ``res.lang`` record (or compatible), not a language code string; passing
        ``partner.lang`` caused ``'str' object has no attribute 'display_name'`` inside standard code.
        """
        self.ensure_one()
        if not prod:
            return ''
        product = prod.with_context(lang=partner.lang) if partner and partner.lang else prod
        base = ''
        try:
            if hasattr(product, '_get_description_purchase'):
                d = product._get_description_purchase()
                if d:
                    base = str(d).strip()
        except Exception:
            base = ''
        if not base:
            tmpl = product.product_tmpl_id
            dp = tmpl.description_purchase if tmpl else False
            if dp:
                base = str(dp).strip()
        if not base:
            base = (product.display_name or product.name or '').strip()
        note = self._po_line_type_note_for_import(line)
        if note:
            return '%s\n%s' % (base, note)
        return base

    def _fishbowl_po_line_unit_cost(self, config, line):
        """Unit price on the PO line: Fishbowl ``poitem.unitCost`` (same as Unit Cost in Fishbowl PO UI)."""
        v = line.get('unitCost')
        if v is None:
            v = line.get('unit_cost')
        if v is not None:
            return float(v)
        qty = config.fb_po_line_ordered_qty(line) if config else float(
            line.get('qtyToFulfill') or line.get('qty') or 0
        )
        total = line.get('totalCost') or line.get('total_cost')
        if total is not None and qty:
            return float(total) / qty
        return 0.0

    def _resolve_product(self, config, line, po_vendor_id=None):
        Product = self.env['product.product']
        num = (line.get('part_num') or line.get('partNum') or '').strip()
        if num:
            p = Product.search([('default_code', '=', num)], limit=1)
            if p:
                return p
        part = None
        if line.get('partId'):
            part = config.fetch_part(line['partId'])
        if part:
            p = Product.search(
                [('product_tmpl_id.fishbowl_part_id', '=', int(part['id']))],
                limit=1,
            )
            if p:
                return p
        if self.create_missing_products:
            return config.create_product_from_fishbowl_line(
                line, is_so_line=False, po_vendor_id=po_vendor_id
            )
        return Product.browse()

    def action_import(self):
        self.ensure_one()
        if self.step != 'confirm':
            raise UserError('Use Preview first, then confirm with Import.')
        if self.preview_import_count <= 0:
            raise UserError('No purchase orders to import. Adjust filters or Fishbowl data.')
        config = self.config_id
        batch = uuid.uuid4().hex
        Log = self.env['fishbowl.import.log']
        ctx = self._ctx()
        imported = 0
        failed = 0
        headers = self._get_po_headers()
        for hdr in headers:
            try:
                with self.env.cr.savepoint():
                    po_fb_id = hdr['id']
                    existing = self.env['purchase.order'].search(
                        [
                            ('fishbowl_po_id', '=', int(po_fb_id)),
                            ('company_id', '=', config.company_id.id),
                        ],
                        limit=1,
                    )
                    if existing:
                        Log.log_line(
                            'po',
                            'Skipped: already imported.',
                            level='info',
                            fishbowl_ref=hdr.get('num'),
                            batch_id=batch,
                            purchase_order=existing,
                        )
                        continue
                    lines = config.fetch_po_lines(po_fb_id)
                    partner = config.create_or_get_vendor_partner(hdr['vendorId'])
                    fb_dt = config._fishbowl_header_to_odoo_datetime(hdr)
                    schedule_dt = fb_dt or fields.Datetime.now()
                    precision = self.env['decimal.precision'].precision_get('Product Unit')
                    order_lines = []
                    for line in lines:
                        prod = self._resolve_product(
                            config, line, po_vendor_id=hdr.get('vendorId')
                        )
                        if not prod:
                            raise UserError(
                                'No product for line (part %s).'
                                % (line.get('part_num') or line.get('partNum') or '?')
                            )
                        ordered = config.fb_po_line_ordered_qty(line)
                        if float_compare(ordered, 0, precision_digits=precision) <= 0:
                            raise UserError(
                                'No order quantity for line (part %s).'
                                % (line.get('part_num') or line.get('partNum') or '?')
                            )
                        fulfilled = config.fb_po_line_fulfilled_qty(line)
                        if float_compare(fulfilled, ordered, precision_digits=precision) > 0:
                            fulfilled = ordered
                        if float_compare(fulfilled, 0, precision_digits=precision) < 0:
                            fulfilled = 0.0
                        price = self._fishbowl_po_line_unit_cost(config, line)
                        line_vals = {
                            'product_id': prod.id,
                            'name': self._fishbowl_po_line_name(prod, partner, line),
                            'product_qty': ordered,
                            'fishbowl_prior_received_qty': fulfilled,
                            'price_unit': price,
                            'technical_price_unit': price,
                            'date_planned': schedule_dt,
                        }
                        if line.get('poitem_id') is not None:
                            line_vals['fishbowl_poitem_id'] = int(line['poitem_id'])
                        order_lines.append((0, 0, line_vals))
                    if not order_lines:
                        raise UserError('No PO lines.')
                    fb_po_num = (hdr.get('num') or '').strip()
                    if not fb_po_num:
                        raise UserError('Fishbowl purchase order has no number (po.num).')
                    odoo_po_name = 'FB%s' % fb_po_num
                    currency = config.get_currency(hdr.get('currencyId'))
                    mapped_state = config.map_po_state(hdr.get('statusId'), hdr.get('status_name'))
                    note_val = False
                    if hdr.get('note'):
                        note_val = Markup('<p>%s</p>') % str(hdr['note'])
                    po_vals = {
                        'name': odoo_po_name,
                        'partner_id': partner.id,
                        'company_id': config.company_id.id,
                        'currency_id': currency.id,
                        'fishbowl_po_id': int(po_fb_id),
                        'fishbowl_num': fb_po_num,
                        'fishbowl_status_name': hdr.get('status_name'),
                        'note': note_val,
                        'order_line': order_lines,
                        'date_order': schedule_dt,
                    }
                    po = self.env['purchase.order'].with_context(**ctx).create(po_vals)
                    if mapped_state == 'cancel':
                        po.write({'state': 'cancel'})
                    elif mapped_state == 'purchase' and po.state == 'draft':
                        po.button_confirm()
                    elif mapped_state == 'sent' and po.state == 'draft':
                        po.write({'state': 'sent'})
                    config.apply_odoo_order_dates_from_fishbowl_header(po, hdr)
                    if po.state == 'purchase':
                        config.adjust_fishbowl_po_incoming_moves(po, lines)
                    if self.post_fishbowl_memos_to_chatter:
                        try:
                            self._post_fishbowl_memos_to_chatter(po, config, po_fb_id, ctx)
                        except Exception as memo_err:
                            _logger.exception(
                                'Fishbowl PO memos → chatter failed for po id %s: %s',
                                po_fb_id,
                                memo_err,
                            )
                            Log.log_line(
                                'po',
                                'Fishbowl PO memos not posted to chatter: %s' % memo_err,
                                level='warning',
                                fishbowl_ref=hdr.get('num'),
                                batch_id=batch,
                                purchase_order=po,
                            )
                    Log.log_line(
                        'po',
                        'Imported PO %s' % odoo_po_name,
                        level='info',
                        fishbowl_ref=hdr.get('num'),
                        batch_id=batch,
                        purchase_order=po,
                    )
                    imported += 1
            except Exception as e:
                failed += 1
                Log.log_line(
                    'po',
                    'Failed PO %s: %s' % (hdr.get('num'), e),
                    level='error',
                    fishbowl_ref=hdr.get('num'),
                    batch_id=batch,
                )
        msg = 'Imported: %s, failed: %s. Batch: %s' % (imported, failed, batch)
        self.write(
            {
                'step': 'done',
                'result_message': msg,
                'preview_summary': False,
            }
        )
        return self._reopen()
