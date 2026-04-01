# -*- coding: utf-8 -*-

import logging
import uuid
from datetime import datetime

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

from .fishbowl_defaults import default_fishbowl_sync_config

_logger = logging.getLogger(__name__)


class FishbowlImportSoWizard(models.TransientModel):
    _name = 'fishbowl.import.so.wizard'
    _description = 'Import Fishbowl Open Sales Orders'

    @api.model
    def _default_config_id(self):
        return default_fishbowl_sync_config(self.env)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'wizard_so_status_names' in fields_list and not res.get('wizard_so_status_names'):
            cfg = default_fishbowl_sync_config(self.env)
            if cfg and cfg.import_so_status_names:
                res['wizard_so_status_names'] = cfg.import_so_status_names
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
    fishbowl_so_num = fields.Char(string='Only this Fishbowl SO# (optional)')
    wizard_so_status_names = fields.Text(
        string='Fishbowl SO statuses to import',
        help='One Fishbowl sostatus.name per line (case-insensitive). Clear the field to use the '
        'connection defaults (Configuration → MySQL).',
    )
    create_missing_products = fields.Boolean(
        string='Create missing products',
        help='Create Odoo products from Fishbowl part rows when a matching part exists. '
        'Discount and misc-credit lines use a service adjustment line; subtotals and Fishbowl tax summary rows are skipped.',
    )
    sync_fishbowl_fulfillment = fields.Boolean(
        string='Sync picking / shipment from Fishbowl',
        default=True,
        help='After import, read Fishbowl ship/shipitem (and pick when available) and update outgoing '
        'deliveries: done quantities, picked flags, tracking reference, then validate partial transfers. '
        'Skipped for lines that are fully shipped in Fishbowl when “Shipped lines without stock” is enabled.',
    )
    fishbowl_ship_import_without_stock = fields.Boolean(
        string='Shipped lines without stock moves',
        default=True,
        help='When Fishbowl shows a line as fully shipped (ship/shipitem qty ≥ ordered qty), skip creating '
        'delivery stock moves and set delivered quantity on the sales order line so Odoo inventory is not '
        'affected. Partially shipped lines still get normal stock moves for the full ordered quantity.',
    )
    post_fishbowl_note_to_chatter = fields.Boolean(
        string='Post Fishbowl SO note to chatter',
        default=False,
        help='If set, post Fishbowl so.note text to the order chatter once (in addition to the Fishbowl SO note field).',
    )
    post_fishbowl_memos_to_chatter = fields.Boolean(
        string='Post Fishbowl SO Memos (tab) to chatter',
        default=True,
        help='Post each Fishbowl Sales Order Memo tab entry as a separate internal note on the order.',
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
        """Build HTML body for one Fishbowl ``somemo`` row."""
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

    def _post_fishbowl_memos_to_chatter(self, so, config, so_fb_id, ctx):
        """Post each Fishbowl ``somemo`` row as a log note (``mail.mt_note``) on the sale order."""
        self.ensure_one()
        memos = config.fetch_so_memos(int(so_fb_id))
        if not memos:
            _logger.warning(
                'Fishbowl memos: no rows from MySQL for so.id=%s (so.num=%s); '
                'check ``memo`` (recordId + tableId for SO) or ``somemo`` and ``DESCRIBE`` if needed.',
                so_fb_id,
                so.fishbowl_num or '',
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
            so.with_context(**chatter_ctx).message_post(**post_vals)

    def _so_status_whitelist_for_fetch(self):
        """Return None to use connection defaults; else tuple of lowercase status names."""
        self.ensure_one()
        txt = (self.wizard_so_status_names or '').strip()
        if not txt:
            return None
        return tuple(n.lower() for n in self.config_id._parse_status_lines(txt) if n.strip())

    def _get_so_headers(self):
        self.ensure_one()
        config = self.config_id
        headers = config.fetch_open_sales_orders(
            allowed_status_names=self._so_status_whitelist_for_fetch(),
        )
        if self.fishbowl_so_num:
            sn = self.fishbowl_so_num.strip()
            headers = [h for h in headers if (h.get('num') or '') == sn]
        return headers

    def _preview_so_counts(self):
        """Return (to_import, skipped, list of nums to import)."""
        self.ensure_one()
        config = self.config_id
        headers = self._get_so_headers()
        to_import = 0
        skipped = 0
        nums = []
        for hdr in headers:
            existing = self.env['sale.order'].search(
                [
                    ('fishbowl_so_id', '=', int(hdr['id'])),
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
        to_import, skipped, nums = self._preview_so_counts()
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

    def _fishbowl_so_line_unit_price(self, line):
        """Unit sale price from Fishbowl soitem (prefer unit price; else total / qty).

        Rounded to 2 decimal places to match typical currency display and Odoo line subtotals.
        """
        v = line.get('unitPrice')
        if v is None:
            v = line.get('unit_price')
        if v is not None:
            price = float(v)
        else:
            qty = float(line.get('qtyOrdered') or line.get('qty') or 0)
            total = line.get('totalPrice') or line.get('total_price')
            if total is not None and qty:
                price = float(total) / qty
            else:
                price = 0.0
        return float_round(price, precision_digits=2)

    def _get_fishbowl_adjustment_product(self):
        """Service product for Fishbowl discount / misc-credit / non-part lines (negative or positive amounts)."""
        tmpl = self.env.ref('fishbowl_open_import.product_template_fishbowl_so_adjustment')
        return tmpl.product_variant_id

    def _add_fishbowl_paid_balance_line(self, so, ctx, config):
        """Add a negative adjustment line so ``amount_total`` is zero when Fishbowl shows the SO paid.

        Uses ``config.zero_balance_when_fishbowl_paid`` (Fishbowl MySQL config) so the option is not lost
        when the import wizard confirm step drops hidden option fields.

        Returns whether a line was created.
        """
        self.ensure_one()
        if not getattr(config, 'zero_balance_when_fishbowl_paid', True):
            return False
        so.env.flush_all()
        so.invalidate_recordset()
        amt = so.amount_total
        prec = so.currency_id.rounding or 0.01
        if float_compare(amt, 0.0, precision_rounding=prec) <= 0:
            return False
        adj = self._get_fishbowl_adjustment_product()
        neg = -amt
        self.env['sale.order.line'].with_context(**ctx).create(
            {
                'order_id': so.id,
                'product_id': adj.id,
                'product_uom_qty': 1.0,
                'price_unit': neg,
                'technical_price_unit': neg,
                'fishbowl_line_label': 'Fishbowl: paid in full (import adjustment to zero balance)',
            }
        )
        return True

    def _fishbowl_line_display_label(self, line):
        """Description for adjustment lines (product # + Fishbowl text)."""
        parts = []
        pn = (line.get('productNum') or '').strip()
        if pn:
            parts.append(pn)
        desc = (line.get('description') or '').strip()
        if desc:
            parts.append(desc)
        return ' — '.join(parts) if parts else 'Fishbowl adjustment'

    def _is_fishbowl_adjustment_import_line(self, line):
        """Lines without a stock part: discounts, tax, misc credits, shipping without part, etc."""
        t = (line.get('line_type_name') or '').strip().lower()
        if t:
            if t == 'tax':
                return True
            if 'discount' in t:
                return True
            if 'misc' in t and 'credit' in t:
                return True
            if t == 'shipping' and not (line.get('part_num') or '').strip():
                return True
        prod_num = (line.get('productNum') or '').strip().lower()
        if prod_num and ('discount' in prod_num or prod_num.endswith(' off')):
            return True
        desc = (line.get('description') or '').strip().lower()
        if desc and '100%' in desc and 'off' in desc:
            return True
        return False

    def _resolve_product(self, config, line):
        Product = self.env['product.product']
        if self.create_missing_products:
            try:
                p = config.resolve_product_for_so_line(line, create_missing_products=True)
            except UserError:
                if self._is_fishbowl_adjustment_import_line(line):
                    return self._get_fishbowl_adjustment_product()
                raise
        else:
            p = config.resolve_product_for_so_line(line, create_missing_products=False)
        if p:
            return p
        if self._is_fishbowl_adjustment_import_line(line):
            return self._get_fishbowl_adjustment_product()
        return Product.browse()

    def _fishbowl_line_descriptor(self, line):
        """Human-readable Fishbowl line id for logs (part / product / soitem / description)."""
        bits = []
        part_num = (line.get('part_num') or '').strip()
        if part_num:
            bits.append('part %s' % part_num)
        prod_num = (line.get('productNum') or '').strip()
        if prod_num and prod_num != part_num:
            bits.append('productNum %s' % prod_num)
        if line.get('productId'):
            bits.append('productId %s' % line['productId'])
        if line.get('soitem_id'):
            bits.append('soitem %s' % line['soitem_id'])
        desc = (line.get('description') or '').strip()
        if desc:
            bits.append('"%s"' % (desc[:120] + ('…' if len(desc) > 120 else '')))
        return ', '.join(bits) if bits else '(no identifiers on line)'

    def _is_fishbowl_subtotal_line(self, line):
        """True for Fishbowl SO lines of type Subtotal (UI Type column / description)."""
        t = (line.get('line_type_name') or '').strip().lower()
        if t == 'subtotal':
            return True
        d = (line.get('description') or '').strip().lower()
        if d == 'subtotal':
            return True
        return False

    def _is_fishbowl_credit_return_line(self, line):
        """Fishbowl UI Type column ``Credit Return`` — do not import; note in chatter instead."""
        t = (line.get('line_type_name') or '').strip().lower()
        if not t:
            return False
        return ' '.join(t.split()) == 'credit return'

    def _is_fishbowl_skip_line(self, line):
        """Do not import as SO lines: subtotals, credit returns (tax rows use adjustment lines)."""
        if self._is_fishbowl_credit_return_line(line):
            return True
        return self._is_fishbowl_subtotal_line(line)

    def _filter_out_fishbowl_kit_component_lines(self, config, so_fb_id, lines):
        """Return (lines, parent_map) with kit **component** soitem rows removed.

        Fishbowl lists the kit parent and each component as separate ``soitem`` rows. Odoo should only
        have the kit product on the sales order; the BOM drives component moves on pickings.
        """
        parent_map = config.fetch_soitem_kit_parent_map(so_fb_id)
        child_ids = set(parent_map.keys())
        out = [l for l in lines if int(l.get('soitem_id') or 0) not in child_ids]
        return out, parent_map

    def _credit_return_chatter_body(self, credit_return_lines):
        """HTML body for internal note when Credit Return lines were skipped."""
        parts = [
            Markup('<p><strong>Credit Return</strong></p>'),
            Markup(
                '<p>Skipped %s Fishbowl line(s) with type Credit Return (not imported).</p>'
            )
            % len(credit_return_lines),
        ]
        for line in credit_return_lines:
            parts.append(Markup('<p>%s</p>') % escape(self._fishbowl_line_display_label(line)))
        return Markup('').join(parts)

    def _post_credit_return_chatter_note(self, so, credit_return_lines, ctx):
        """Post internal note so Credit Return skips appear in chatter."""
        self.ensure_one()
        if not credit_return_lines:
            return
        chatter_ctx = self._memo_chatter_ctx(ctx)
        so.with_context(**chatter_ctx).message_post(
            body=self._credit_return_chatter_body(credit_return_lines),
            subtype_xmlid='mail.mt_note',
            message_type='comment',
        )

    def _apply_so_state(self, so, mapped_state):
        """Apply Fishbowl-mapped Odoo state. *sale* always confirms the SO (action_confirm)."""
        if mapped_state == 'sent':
            if so.state == 'draft':
                so.action_quotation_sent()
        elif mapped_state == 'sale':
            if so.state in ('draft', 'sent'):
                so.action_confirm()
        elif mapped_state == 'cancel':
            so.write({'state': 'cancel'})

    def action_import(self):
        self.ensure_one()
        if self.step != 'confirm':
            raise UserError('Use Preview first, then confirm with Import.')
        if self.preview_import_count <= 0:
            raise UserError('No sales orders to import. Adjust filters or Fishbowl data.')
        config = self.config_id
        batch = uuid.uuid4().hex
        Log = self.env['fishbowl.import.log']
        ctx = self._ctx()
        imported = 0
        failed = 0
        headers = self._get_so_headers()
        config.enrich_so_headers_payment_flags(headers)
        config.enrich_so_headers_payment_memo_hints(headers)
        for hdr in headers:
            try:
                with self.env.cr.savepoint():
                    so_fb_id = hdr['id']
                    existing = self.env['sale.order'].search(
                        [
                            ('fishbowl_so_id', '=', int(so_fb_id)),
                            ('company_id', '=', config.company_id.id),
                        ],
                        limit=1,
                    )
                    if existing:
                        Log.log_line(
                            'so',
                            'Skipped: already imported.',
                            level='info',
                            fishbowl_ref=hdr.get('num'),
                            batch_id=batch,
                            sale_order=existing,
                        )
                        continue
                    raw_lines = config.fetch_so_lines(so_fb_id)
                    credit_return_lines = [
                        l for l in raw_lines if self._is_fishbowl_credit_return_line(l)
                    ]
                    lines = [l for l in raw_lines if not self._is_fishbowl_skip_line(l)]
                    n_lines_before_kit = len(lines)
                    lines, parent_map = self._filter_out_fishbowl_kit_component_lines(
                        config, so_fb_id, lines
                    )
                    if len(lines) < n_lines_before_kit:
                        Log.log_line(
                            'so',
                            'Omitted %s Fishbowl kit component line(s); only the parent kit line is imported.'
                            % (n_lines_before_kit - len(lines)),
                            level='info',
                            fishbowl_ref=hdr.get('num'),
                            batch_id=batch,
                        )
                    partner = config.create_or_get_customer_partner(hdr['customerId'])
                    missing_desc = []
                    resolved = []
                    for line in lines:
                        prod = self._resolve_product(config, line)
                        if not prod:
                            missing_desc.append(self._fishbowl_line_descriptor(line))
                        else:
                            resolved.append((line, prod))
                    if missing_desc:
                        bullet = '\n'.join('- %s' % d for d in missing_desc)
                        raise UserError(
                            'Missing product(s) in Odoo (%s):\n%s'
                            % (len(missing_desc), bullet)
                        )
                    order_lines = []
                    adj = self._get_fishbowl_adjustment_product()
                    shipped_by_item_raw = config.fetch_so_shipment_qty_by_soitem(so_fb_id)
                    shipped_by_item = config._rollup_fb_qty_by_parent_soitem(
                        shipped_by_item_raw, parent_map
                    )
                    parent_to_children = {}
                    for cid, pid in parent_map.items():
                        if pid:
                            parent_to_children.setdefault(int(pid), []).append(int(cid))
                    soitem_detail = config.fetch_soitem_product_qty_by_id(so_fb_id)
                    fulfilled_soitem_ids = config.fetch_soitem_fulfilled_soitem_ids(so_fb_id)
                    for line, prod in resolved:
                        price = self._fishbowl_so_line_unit_price(line)
                        raw_qty = float(line.get('qtyOrdered') or line.get('qty') or 0)
                        qty = raw_qty if raw_qty else 1.0
                        soitem_id = int(line.get('soitem_id') or 0)
                        sol_vals = {
                            'product_id': prod.id,
                            'product_uom_qty': qty,
                            'price_unit': price,
                            'technical_price_unit': price,
                            'fishbowl_soitem_id': soitem_id,
                        }
                        if prod.id == adj.id:
                            sol_vals['fishbowl_line_label'] = self._fishbowl_line_display_label(line)
                        if (
                            self.fishbowl_ship_import_without_stock
                            and prod.id != adj.id
                            and config.fishbowl_sales_line_fully_shipped_for_import(
                                so_fb_id,
                                line,
                                qty,
                                shipped_by_item,
                                parent_map,
                                shipped_by_item_raw=shipped_by_item_raw,
                                parent_to_children=parent_to_children,
                                soitem_detail=soitem_detail,
                                fulfilled_soitem_ids=fulfilled_soitem_ids,
                            )
                        ):
                            sol_vals['fishbowl_skip_procurement'] = True
                        order_lines.append((0, 0, sol_vals))
                    if not order_lines:
                        raise UserError('No order lines.')
                    fb_so_num = (hdr.get('num') or '').strip()
                    if not fb_so_num:
                        raise UserError('Fishbowl sales order has no number (so.num).')
                    odoo_so_name = 'FB%s' % fb_so_num
                    currency = config.get_currency(hdr.get('currencyId'))
                    mapped_state = config.map_so_state(hdr.get('statusId'))
                    fb_dt = config._fishbowl_header_to_odoo_datetime(hdr)
                    note_raw = hdr.get('note')
                    so_vals = {
                        'name': odoo_so_name,
                        'partner_id': partner.id,
                        'company_id': config.company_id.id,
                        'currency_id': currency.id,
                        'fishbowl_so_id': int(so_fb_id),
                        'fishbowl_num': fb_so_num,
                        'fishbowl_status_name': hdr.get('status_name'),
                        'client_order_ref': hdr.get('customerPO') or False,
                        'order_line': order_lines,
                    }
                    if note_raw:
                        so_vals['fishbowl_so_note'] = Markup('<p>%s</p>') % escape(str(note_raw))
                    if fb_dt:
                        so_vals['date_order'] = fb_dt
                    so = self.env['sale.order'].with_context(**ctx).create(so_vals)
                    # After create; ``user_id`` may be overwritten by sale.order ``_compute_user_id``
                    # from the customer — channel rules clear it; name-based rules set salesperson.
                    config.apply_odoo_sales_team_and_user_from_fishbowl_header(so, hdr)
                    self._apply_so_state(so, mapped_state)
                    if self.fishbowl_ship_import_without_stock:
                        config.fishbowl_apply_skip_shipped_lines_after_confirm(so)
                    if hdr.get('fishbowl_paid_in_full') and self._add_fishbowl_paid_balance_line(so, ctx, config):
                        Log.log_line(
                            'so',
                            'Fishbowl order paid in full: added adjustment line so Odoo order total is zero.',
                            level='info',
                            fishbowl_ref=hdr.get('num'),
                            batch_id=batch,
                            sale_order=so,
                        )
                    if credit_return_lines:
                        self._post_credit_return_chatter_note(so, credit_return_lines, ctx)
                        Log.log_line(
                            'so',
                            'Skipped %s Fishbowl Credit Return line(s) (not imported).'
                            % len(credit_return_lines),
                            level='info',
                            fishbowl_ref=hdr.get('num'),
                            batch_id=batch,
                            sale_order=so,
                        )
                    if config.create_credit_return_receipts and credit_return_lines:
                        try:
                            config.run_credit_return_receipt_for_sale_order(
                                so,
                                lambda line: self._resolve_product(config, line),
                                fishbowl_ref=hdr.get('num'),
                                batch_id=batch,
                                Log=Log,
                                import_ctx=ctx,
                                silent=True,
                                credit_return_lines=credit_return_lines,
                                credit_returns_skipped_from_so=True,
                            )
                        except Exception as cr_err:
                            _logger.exception(
                                'Fishbowl credit return receipt failed for SO %s: %s',
                                hdr.get('num'),
                                cr_err,
                            )
                            Log.log_line(
                                'so',
                                'Credit return receipt not created: %s' % cr_err,
                                level='warning',
                                fishbowl_ref=hdr.get('num'),
                                batch_id=batch,
                                sale_order=so,
                            )
                            chatter_ctx = self._memo_chatter_ctx(ctx)
                            so.with_context(**chatter_ctx).message_post(
                                body=Markup(
                                    '<p><strong>Fishbowl credit return receipt failed</strong></p>'
                                    '<p>%s</p>'
                                )
                                % escape(str(cr_err)),
                                subtype_xmlid='mail.mt_note',
                                message_type='comment',
                            )
                    # action_confirm() sets date_order to now; recreate_date is blocked on create — fix via SQL.
                    config.apply_odoo_order_dates_from_fishbowl_header(so, hdr)
                    if self.post_fishbowl_note_to_chatter and note_raw and str(note_raw).strip():
                        so.with_context(**ctx).message_post(
                            body=Markup('<p>%s</p>') % escape(str(note_raw)),
                            subtype_xmlid=False,
                        )
                    if self.post_fishbowl_memos_to_chatter:
                        try:
                            self._post_fishbowl_memos_to_chatter(so, config, so_fb_id, ctx)
                        except Exception as memo_err:
                            _logger.exception(
                                'Fishbowl SO memos → chatter failed for SO id %s: %s',
                                so_fb_id,
                                memo_err,
                            )
                            Log.log_line(
                                'so',
                                'Fishbowl memos not posted to chatter: %s' % memo_err,
                                level='warning',
                                fishbowl_ref=hdr.get('num'),
                                batch_id=batch,
                                sale_order=so,
                            )
                    if self.sync_fishbowl_fulfillment and so.state == 'sale':
                        if so.order_line.filtered(lambda l: not l.fishbowl_skip_procurement):
                            config.apply_fishbowl_fulfillment_to_sale_order(
                                so,
                                log=Log,
                                batch_id=batch,
                                fishbowl_ref=hdr.get('num'),
                            )
                        else:
                            pts = config.fetch_so_shipment_tracking_strings(so_fb_id)
                            if pts:
                                config.post_fishbowl_shipment_tracking_to_chatter(so, pts)
                    Log.log_line(
                        'so',
                        'Imported SO %s' % hdr.get('num'),
                        level='info',
                        fishbowl_ref=hdr.get('num'),
                        batch_id=batch,
                        sale_order=so,
                    )
                    imported += 1
            except Exception as e:
                failed += 1
                err_text = e.args[0] if isinstance(e, UserError) and e.args else str(e)
                Log.log_line(
                    'so',
                    'Failed SO %s: %s' % (hdr.get('num'), err_text),
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
