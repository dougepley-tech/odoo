# -*- coding: utf-8 -*-
#
# Fishbowl ``soitem`` rows with type **Credit Return** expect customer product to be received back.
# ``receiptitem.soItemId`` → ``soitem.id`` for sales-side receivals (see Fishbowl ``receipt`` / ``receiptitem``).
# PO receipts use ``receiptitem.poItemId``; legacy/alternate column names are still tried as fallbacks.
# When Fishbowl still shows **Entered** / partial receipt, create an Odoo **incoming** transfer for the
# remaining quantity only.

import logging

from markupsafe import Markup, escape

from odoo import Command, _, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


def fishbowl_line_is_credit_return(line):
    """True when Fishbowl ``soitem`` row type is Credit Return (``line_type_name``)."""
    t = (line.get('line_type_name') or '').strip().lower()
    if not t:
        return False
    return ' '.join(t.split()) == 'credit return'


def fishbowl_so_line_descriptor(line):
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


def _fishbowl_chatter_ctx_from_import_ctx(import_ctx):
    if not import_ctx:
        return {}
    out = dict(import_ctx)
    out.pop('fishbowl_import', None)
    return out


class FishbowlSyncConfig(models.Model):
    _inherit = 'fishbowl.sync.config'

    def fetch_so_credit_return_lines_with_receipts(self, so_id):
        """Return **Credit Return** ``soitem`` rows with status + ``receiptitem`` qty sum.

        Tries several MySQL spellings for ``receiptitem`` → ``soitem`` and ``soitem`` → ``soitemstatus``.
        """
        self.ensure_one()
        so_id = int(so_id)
        conn = self._get_connection()
        # ``receiptitem.soItemId`` → ``soitem.id`` (Fishbowl schema; not ``orderItemId``).
        # Prefer summing through ``receipt`` so totals match ``receipt.soId`` when present.
        receipt_sum_via_receipt = """
            (SELECT COALESCE(SUM(ri.qty), 0)
             FROM receiptitem ri
             INNER JOIN receipt r ON r.id = ri.receiptId
             WHERE ri.soItemId = si.id AND r.soId = si.soId) AS receipt_qty_sum
            """
        receipt_sum_soitem_only = """
            (SELECT COALESCE(SUM(ri.qty), 0)
             FROM receiptitem ri
             WHERE ri.soItemId = si.id) AS receipt_qty_sum
            """
        base_select = """
                SELECT si.id AS soitem_id, si.soId, si.productId, si.productNum,
                       si.description, si.qtyOrdered, si.unitPrice, si.totalPrice,
                       si.soLineItem, p.num AS part_num,
                       sit.name AS line_type_name,
                       st.name AS soitem_status_name,
                       COALESCE(si.qtyFulfilled, 0) AS qtyFulfilled,
                       {receipt_sub}
                FROM soitem si
                LEFT JOIN product p ON p.id = si.productId
                LEFT JOIN soitemtype sit ON sit.id = si.typeId
                LEFT JOIN soitemstatus st ON st.id = {status_join}
                WHERE si.soId = %s
                  AND LOWER(TRIM(sit.name)) = 'credit return'
                ORDER BY COALESCE(si.soLineItem, 0), si.id
                """
        queries = [
            (base_select.format(receipt_sub=receipt_sum_via_receipt, status_join='si.statusId'), (so_id,)),
            (base_select.format(receipt_sub=receipt_sum_soitem_only, status_join='si.statusId'), (so_id,)),
            (
                base_select.format(
                    receipt_sub=receipt_sum_soitem_only.replace('soItemId', 'soitemid'),
                    status_join='si.soItemStatusId',
                ),
                (so_id,),
            ),
            (
                base_select.format(
                    receipt_sub=receipt_sum_soitem_only,
                    status_join='si.soItemStatusId',
                ),
                (so_id,),
            ),
            (
                base_select.format(
                    receipt_sub="""
            (SELECT COALESCE(SUM(ri.qty), 0)
             FROM receiptitem ri
             WHERE ri.soitemid = si.id) AS receipt_qty_sum
            """,
                    status_join='si.statusId',
                ),
                (so_id,),
            ),
        ]
        try:
            return self._fb_try_queries(conn, queries, non_empty=False)
        finally:
            conn.close()

    def fetch_so_credit_return_enrichment_by_soitem_ids(self, so_id, soitem_ids):
        """Load ``receiptitem`` sums + ``soitemstatus`` for specific ``soitem`` ids.

        Does **not** filter on ``soitemtype`` name (avoids empty results when the type join or label
        differs from ``credit return``). Used for lines already identified as Credit Return in
        ``fetch_so_lines``.
        """
        self.ensure_one()
        so_id = int(so_id)
        ids = sorted({int(x) for x in (soitem_ids or []) if x})
        if not ids:
            return {}
        ph = ','.join(['%s'] * len(ids))
        params_base = (so_id,) + tuple(ids)
        conn = self._get_connection()
        # Schema: ``receiptitem.soItemId`` → ``soitem.id``; ``receipt.soId`` ties header to SO.
        sum_via_receipt = """
                       (SELECT COALESCE(SUM(ri.qty), 0)
                        FROM receiptitem ri
                        INNER JOIN receipt r ON r.id = ri.receiptId
                        WHERE ri.soItemId = si.id AND r.soId = si.soId) AS receipt_qty_sum
            """
        sum_soitem = """
                       (SELECT COALESCE(SUM(ri.qty), 0)
                        FROM receiptitem ri
                        WHERE ri.soItemId = si.id) AS receipt_qty_sum
            """
        sum_soitem_lower = sum_soitem.replace('soItemId', 'soitemid')
        enc = """
                SELECT si.id AS soitem_id,
                       st.name AS soitem_status_name,
                       {qty_fulfilled}
                       {receipt_sub}
                FROM soitem si
                LEFT JOIN soitemstatus st ON st.id = {status_join}
                WHERE si.soId = %s AND si.id IN ({ph})
                """
        qf_col = 'COALESCE(si.qtyFulfilled, 0) AS qtyFulfilled,\n'
        qf_zero = '0 AS qtyFulfilled,\n'
        queries = [
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_col,
                    receipt_sub=sum_via_receipt,
                    status_join='si.statusId',
                ),
                params_base,
            ),
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_col,
                    receipt_sub=sum_soitem,
                    status_join='si.statusId',
                ),
                params_base,
            ),
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_col,
                    receipt_sub=sum_via_receipt,
                    status_join='si.soItemStatusId',
                ),
                params_base,
            ),
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_col,
                    receipt_sub=sum_soitem,
                    status_join='si.soItemStatusId',
                ),
                params_base,
            ),
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_col,
                    receipt_sub=sum_soitem_lower,
                    status_join='si.statusId',
                ),
                params_base,
            ),
            (
                enc.format(
                    ph=ph,
                    qty_fulfilled=qf_zero,
                    receipt_sub=sum_soitem,
                    status_join='si.statusId',
                ),
                params_base,
            ),
        ]
        try:
            rows = self._fb_try_queries(conn, queries, non_empty=True)
        finally:
            conn.close()
        out = {}
        for r in rows or []:
            row = dict(r or {})
            sid = row.get('soitem_id')
            if sid is None:
                for k, v in row.items():
                    if str(k).lower() == 'soitem_id':
                        sid = v
                        break
            if sid is None:
                continue
            # Ensure canonical keys for merge (MySQL alias casing varies).
            for k, v in list(row.items()):
                lk = str(k).lower()
                if lk == 'receipt_qty_sum':
                    row['receipt_qty_sum'] = v
                elif lk == 'soitem_status_name':
                    row['soitem_status_name'] = v
                elif lk == 'qtyfulfilled':
                    row['qtyFulfilled'] = v
            out[int(sid)] = row
        return out

    @staticmethod
    def fb_so_credit_return_ordered_qty(line):
        """Ordered quantity on a Fishbowl credit-return ``soitem`` row."""
        for key in ('qtyOrdered', 'qty'):
            if line.get(key) is not None:
                v = float(line[key] or 0)
                if v > 0:
                    return v
        u = line.get('unitPrice')
        t = line.get('totalPrice')
        if u is not None and t is not None:
            uu, tt = float(u), float(t)
            if abs(uu) > 1e-9 and tt >= 0:
                q = abs(tt / uu)
                if q > 0:
                    return q
        return float(line.get('qtyOrdered') or line.get('qty') or 0)

    @staticmethod
    def fb_so_credit_return_fulfilled_qty(line):
        """Quantity already received in Fishbowl (``receiptitem`` sum only, capped at ordered)."""
        ordered = FishbowlSyncConfig.fb_so_credit_return_ordered_qty(line)
        if float_compare(ordered, 0, precision_digits=5) <= 0:
            return 0.0
        status = line.get('soitem_status_name')
        if status is not None and str(status).strip():
            if str(status).strip().lower() in ('cancelled', 'void'):
                return 0.0
        rq = line.get('receipt_qty_sum')
        if rq is None:
            return 0.0
        return max(0.0, min(ordered, float(rq)))

    @staticmethod
    def fb_so_credit_return_remaining_qty(line):
        """Quantity still to receive: ordered minus ``receiptitem`` sum.

        If ``receipt_qty_sum`` is missing (enrichment failed), assume **nothing** received yet and
        return the full ordered quantity so Odoo can still create an incoming receipt.
        """
        ordered = FishbowlSyncConfig.fb_so_credit_return_ordered_qty(line)
        if float_compare(ordered, 0, precision_digits=5) <= 0:
            return 0.0
        status = line.get('soitem_status_name')
        if status is not None and str(status).strip():
            if str(status).strip().lower() in ('cancelled', 'void'):
                return 0.0
        rq = line.get('receipt_qty_sum')
        if rq is None:
            return ordered
        recv = max(0.0, min(ordered, float(rq)))
        rem = ordered - recv
        if float_compare(rem, 0, precision_digits=5) <= 0:
            return 0.0
        if float_compare(rem, ordered, precision_digits=5) > 0:
            return ordered
        return rem

    def _fishbowl_find_done_outgoing_delivery(self, sale_order):
        """Latest **done** customer delivery on the SO (``WH/OUT``), for standard return linking."""
        order = sale_order.sudo()
        candidates = order.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'outgoing' and p.state == 'done'
        )
        if not candidates:
            return self.env['stock.picking']
        return candidates.sorted('date_done', reverse=True)[:1]

    def _try_create_credit_return_via_odoo_return_wizard(self, out_pick, product_qty_map):
        """Mirror the **Return** action on a delivery: ``Return of WH/OUT/…``, ``return_id``, chatter.

        Only used when every pending product exists on the done outgoing picking.
        """
        self.ensure_one()
        Picking = self.env['stock.picking']
        if not out_pick or out_pick.state != 'done':
            return Picking
        required = set(product_qty_map.keys())
        Wizard = self.env['stock.return.picking'].sudo()
        try:
            wiz = Wizard.create({'picking_id': out_pick.id})
        except Exception as e:
            _logger.info('Fishbowl credit return: stock.return.picking not available: %s', e)
            return Picking
        line_pids = set(wiz.product_return_moves.mapped('product_id').ids)
        if not required <= line_pids:
            wiz.unlink()
            return Picking
        for line in wiz.product_return_moves:
            pid = line.product_id.id
            if pid not in product_qty_map:
                line.quantity = 0.0
                continue
            cap = float(line.move_quantity or 0.0)
            if not cap and line.move_id:
                cap = float(line.move_id.quantity or 0.0)
            line.quantity = min(float(product_qty_map[pid]), cap)
        try:
            return wiz._create_return()
        except Exception as e:
            _logger.warning('Fishbowl credit return: _create_return failed: %s', e)
            try:
                wiz.unlink()
            except Exception:
                pass
            return Picking

    def create_odoo_incoming_for_pending_credit_returns(
        self,
        sale_order,
        pending_triples,
        fishbowl_ref=None,
    ):
        """Create one incoming return (``WH/IN``) for pending credit-return quantities.

        Prefer Odoo's standard :class:`~odoo.addons.stock.wizard.stock_picking_return.StockReturnPicking`
        flow when a **done** delivery (``WH/OUT``) exists on the order so the receipt matches
        **Return of WH/OUT/…** and links via ``return_id``. If there is no such delivery (e.g. import
        skipped procurement), build the same style of transfer manually.

        :param pending_triples: list of ``(fishbowl_line_dict, product.product record, remaining_qty)``
        :return: ``stock.picking`` or empty recordset if nothing to do
        """
        self.ensure_one()
        order = sale_order.sudo()
        if not pending_triples:
            return self.env['stock.picking']

        precision = self.env['decimal.precision'].precision_get('Product Unit')
        triples = []
        for fb_line, product, qty in pending_triples:
            if not product or not product.id:
                continue
            q = float(qty or 0)
            if float_compare(q, 0, precision_digits=precision) <= 0:
                continue
            triples.append((fb_line, product, q))
        if not triples:
            return self.env['stock.picking']

        product_qty_map = {p.id: float(q) for (_, p, q) in triples}
        out_pick = self._fishbowl_find_done_outgoing_delivery(order)

        warehouse = order.warehouse_id
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search(
                [('company_id', '=', order.company_id.id)], limit=1
            )
        if not warehouse:
            raise UserError(
                'Cannot create credit return receipt: no warehouse on the order and no default warehouse '
                'for company %s.' % (order.company_id.display_name,)
            )

        partner = order.partner_shipping_id or order.partner_id
        customer_loc = partner.property_stock_customer
        if not customer_loc:
            customer_loc = self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
        if not customer_loc:
            raise UserError(
                'Cannot create credit return receipt: no customer stock location (partner / Customers).'
            )

        # Match rma_odoo_19 ``rma._create_receipt``: RMA In type + RMA location (customer → internal RMA).
        use_rma_style = (
            'rma_in_type_id' in warehouse._fields
            and warehouse.rma_in_type_id
            and getattr(warehouse, 'rma_loc_id', False)
        )

        picking = self.env['stock.picking']
        if not use_rma_style:
            picking = self._try_create_credit_return_via_odoo_return_wizard(out_pick, product_qty_map)
        if picking:
            try:
                picking.sudo().write({'sale_id': order.id})
            except Exception as e:
                _logger.warning(
                    'Fishbowl credit return: could not set sale_id on picking %s: %s',
                    picking.name,
                    e,
                )
            return picking

        out_type = warehouse.out_type_id
        ret_type = out_type.return_picking_type_id if out_type else False
        if not ret_type or ret_type.code != 'incoming':
            ret_type = self.env['stock.picking.type'].search(
                [
                    ('warehouse_id', '=', warehouse.id),
                    ('code', '=', 'incoming'),
                ],
                limit=1,
            )
        if not use_rma_style and not ret_type:
            raise UserError(
                'Cannot create credit return receipt: no incoming operation type for warehouse %s.'
                % (warehouse.display_name,)
            )

        if use_rma_style:
            picking_type_id = warehouse.rma_in_type_id.id
            location_id = customer_loc.id
            location_dest_id = warehouse.rma_loc_id.id
            return_id = out_pick.id if out_pick else False
            if out_pick:
                origin = _('Return of %(picking_name)s', picking_name=out_pick.name)
            else:
                origin = _('Return of %(order_name)s', order_name=order.name)
        else:
            # Same defaults as stock.return.picking._prepare_picking_default_values_based_on when possible.
            picking_type_id = ret_type.id
            location_id = customer_loc.id
            location_dest_id = ret_type.default_location_dest_id.id if ret_type.default_location_dest_id else False
            return_id = False
            if out_pick:
                # Mirror stock.return.picking._prepare_picking_default_values_based_on(out_pick)
                loc = out_pick.location_dest_id
                rtype = out_pick.picking_type_id.return_picking_type_id
                if rtype and rtype.code == 'incoming':
                    location_dest_id = (
                        rtype.default_location_dest_id.id if rtype.default_location_dest_id else False
                    )
                    picking_type_id = rtype.id
                else:
                    location_dest_id = out_pick.location_id.id
                    picking_type_id = rtype.id if rtype else ret_type.id
                location_id = loc.id
                return_id = out_pick.id
                origin = _('Return of %(picking_name)s', picking_name=out_pick.name)
            else:
                if not location_dest_id:
                    location_dest_id = warehouse.lot_stock_id.id
                if not location_dest_id:
                    raise UserError(
                        'Cannot create credit return receipt: no destination stock location for warehouse %s.'
                        % (warehouse.display_name,)
                    )
                origin = _('Return of %(order_name)s', order_name=order.name)

        if not location_dest_id:
            location_dest_id = warehouse.lot_stock_id.id
        if not location_dest_id:
            raise UserError(
                'Cannot create credit return receipt: no destination stock location for warehouse %s.'
                % (warehouse.display_name,)
            )

        if fishbowl_ref:
            origin = '%s | FB %s' % (origin, fishbowl_ref)

        # Do not set move reference_ids before linking sale_id (sale_stock _set_sale_id / reassign).

        StockMove = self.env['stock.move']
        move_vals = []
        for fb_line, product, qty in triples:
            desc = (fb_line.get('description') or product.display_name or '')[:200]
            mvals = {
                'product_id': product.id,
                'product_uom_qty': qty,
                'product_uom': product.uom_id.id,
                'location_id': location_id,
                'location_dest_id': location_dest_id,
                'origin': origin,
                'picking_type_id': picking_type_id,
                'company_id': order.company_id.id,
                'partner_id': partner.id,
            }
            if 'description_picking' in StockMove._fields and desc:
                mvals['description_picking'] = desc
            if 'to_refund' in self.env['stock.move']._fields:
                mvals['to_refund'] = True
            pid = product.id
            sol = order.order_line.filtered(
                lambda l, pid=pid: not l.display_type and l.product_id.id == pid
            )[:1]
            if sol:
                mvals['sale_line_id'] = sol.id
            if out_pick:
                om = out_pick.move_ids.filtered(
                    lambda m, pid=pid: m.product_id.id == pid and m.state == 'done'
                )[:1]
                if om and 'origin_returned_move_id' in self.env['stock.move']._fields:
                    mvals['origin_returned_move_id'] = om.id
            move_vals.append((0, 0, mvals))

        pvals = {
            'picking_type_id': picking_type_id,
            'partner_id': partner.id,
            'location_id': location_id,
            'location_dest_id': location_dest_id,
            'origin': origin,
            'move_ids': move_vals,
        }
        if return_id:
            pvals['return_id'] = return_id

        picking = (
            self.env['stock.picking']
            .sudo()
            .with_context(fishbowl_import=True)
            .create(pvals)
        )
        try:
            picking.sudo().write({'sale_id': order.id})
        except Exception as e:
            _logger.warning(
                'Fishbowl credit return: could not set sale_id on picking %s: %s',
                picking.name,
                e,
            )
        try:
            picking.action_confirm()
            picking.action_assign()
        except Exception as e:
            _logger.warning(
                'Fishbowl credit return receipt: confirm/assign failed for %s: %s',
                picking.name,
                e,
            )
        return picking

    def _credit_return_receipt_skip_chatter_body(
        self,
        credit_return_lines,
        cr_lines,
        missing_cr,
        pending_triples,
        credit_returns_skipped_from_so,
    ):
        """Internal note when import requested a receipt but none was created (``silent`` imports)."""
        if pending_triples:
            return None
        parts = [
            Markup('<p><strong>Fishbowl credit return</strong></p>'),
            Markup('<p>No incoming transfer was created for Credit Return line(s).</p>'),
        ]
        if missing_cr:
            parts.append(
                Markup('<p>Missing product(s) in Odoo for: %s</p>')
                % escape('; '.join(missing_cr[:8]) + ('…' if len(missing_cr) > 8 else ''))
            )
            return Markup('').join(parts)
        if not credit_return_lines:
            return None
        if not cr_lines:
            parts.append(
                Markup(
                    '<p>Every Credit Return line is a kit <em>component</em> row in Fishbowl; '
                    'none could be mapped as a standalone receipt line. Check kit structure or '
                    'MySQL <code>soitem</code> types.</p>'
                )
            )
            return Markup('').join(parts)
        parts.append(
            Markup(
                '<p>Fishbowl shows <strong>ordered quantity or pending receive qty is zero</strong> '
                'on each Credit Return line (see <code>qtyOrdered</code> / <code>receiptitem</code>).</p>'
            )
        )
        if not credit_returns_skipped_from_so:
            parts.append(
                Markup(
                    '<p>For imports that skip Credit Return lines on the SO, pending receipt is '
                    'created using ordered qty even when Fishbowl already received. '
                    'Otherwise enable <em>Receipt when Fishbowl shows return fully received</em> '
                    'on Fishbowl MySQL configuration.</p>'
                )
            )
        return Markup('').join(parts)

    def run_credit_return_receipt_for_sale_order(
        self,
        sale_order,
        resolve_product_fn,
        fishbowl_ref=None,
        batch_id=None,
        Log=None,
        import_ctx=None,
        silent=True,
        credit_return_lines=None,
        credit_returns_skipped_from_so=False,
    ):
        """Create incoming transfer(s) for Fishbowl Credit Return lines with pending receive qty.

        :param resolve_product_fn: ``callable(line_dict) -> product.product`` (wizard passes
            ``_resolve_product`` so adjustment / create-missing behavior matches import).
        :param credit_return_lines: optional pre-fetched Credit Return rows (avoids a second MySQL fetch).
        :param silent: if False (e.g. manual button), raise ``UserError`` when nothing is created.
        :param credit_returns_skipped_from_so: set by SO import when Credit Return rows are not imported
            as order lines. Then, if Fishbowl ``receiptitem`` already covers the order, we still use
            **ordered** qty so an Odoo incoming transfer can be created for RMA paperwork.
        :return: ``stock.picking`` or empty recordset
        """
        self.ensure_one()
        env = self.env
        so = sale_order.sudo()
        pick = env['stock.picking']
        if not getattr(self, 'create_credit_return_receipts', True):
            if not silent:
                raise UserError(
                    _(
                        'Enable "Create Odoo incoming receipt for credit returns" on Fishbowl MySQL configuration.'
                    )
                )
            return pick
        so_fb_id = int(so.fishbowl_so_id or 0)
        if not so_fb_id:
            if not silent:
                raise UserError(_('This sales order has no Fishbowl SO id.'))
            return pick
        fb_ref = fishbowl_ref or so.fishbowl_num or ''
        if credit_return_lines is None:
            raw_lines = self.fetch_so_lines(so_fb_id)
            credit_return_lines = [l for l in raw_lines if fishbowl_line_is_credit_return(l)]
        if not credit_return_lines:
            if not silent:
                raise UserError(
                    _('Fishbowl has no Credit Return lines on this sales order (check MySQL / line type).')
                )
            return pick

        if Log:
            Log.log_line(
                'so',
                'Credit return receipt: starting (%s Fishbowl Credit Return line(s)).'
                % len(credit_return_lines),
                level='info',
                fishbowl_ref=fb_ref,
                batch_id=batch_id,
                sale_order=so,
            )

        parent_map = self.fetch_soitem_kit_parent_map(so_fb_id)
        child_ids = set(parent_map.keys())
        cr_lines = [
            l for l in credit_return_lines if int(l.get('soitem_id') or 0) not in child_ids
        ]
        if not cr_lines and credit_return_lines:
            cr_lines = list(credit_return_lines)
            if Log:
                Log.log_line(
                    'so',
                    'Credit return receipt: Credit Return line(s) are kit component rows only; '
                    'using those lines for the incoming transfer.',
                    level='info',
                    fishbowl_ref=fb_ref,
                    batch_id=batch_id,
                    sale_order=so,
                )
        cr_sids = [int(l.get('soitem_id') or 0) for l in cr_lines if int(l.get('soitem_id') or 0)]
        enriched_by_sid = self.fetch_so_credit_return_enrichment_by_soitem_ids(so_fb_id, cr_sids)
        if cr_sids and not enriched_by_sid and Log:
            Log.log_line(
                'so',
                'Credit return: could not load receipt/status from Fishbowl MySQL for '
                'soitem id(s) %s (check ``receiptitem`` / ``soitem`` columns). '
                'Pending receive qty uses only the raw SO line row.'
                % (cr_sids,),
                level='warning',
                fishbowl_ref=fb_ref,
                batch_id=batch_id,
                sale_order=so,
            )
        precision = env['decimal.precision'].precision_get('Product Unit')
        pending_triples = []
        missing_cr = []
        for line in cr_lines:
            sid = int(line.get('soitem_id') or 0)
            merged = dict(line)
            if sid in enriched_by_sid:
                merged.update(enriched_by_sid[sid])
            rem = self.fb_so_credit_return_remaining_qty(merged)
            if float_compare(rem, 0.0, precision_digits=precision) <= 0:
                if getattr(self, 'credit_return_receipt_when_fishbowl_fully_received', False) or (
                    credit_returns_skipped_from_so
                ):
                    rem = self.fb_so_credit_return_ordered_qty(merged)
                if float_compare(rem, 0.0, precision_digits=precision) <= 0:
                    continue
            prod = resolve_product_fn(merged)
            if not prod:
                missing_cr.append(fishbowl_so_line_descriptor(merged))
                continue
            pending_triples.append((merged, prod, rem))
        if missing_cr and Log:
            Log.log_line(
                'so',
                'Credit return receipt: skipped %s line(s) (product not in Odoo): %s'
                % (
                    len(missing_cr),
                    '; '.join(missing_cr[:5]) + ('…' if len(missing_cr) > 5 else ''),
                ),
                level='warning',
                fishbowl_ref=fb_ref,
                batch_id=batch_id,
                sale_order=so,
            )
        if cr_lines and not pending_triples and not missing_cr and Log:
            if not credit_returns_skipped_from_so:
                Log.log_line(
                    'so',
                    'Credit return receipt: nothing to create (Fishbowl receiptitem qty '
                    'already covers ordered qty for each credit return line).',
                    level='info',
                    fishbowl_ref=fb_ref,
                    batch_id=batch_id,
                    sale_order=so,
                )
            else:
                Log.log_line(
                    'so',
                    'Credit return receipt: nothing to create (ordered qty is zero on each '
                    'Credit Return line, or products could not be resolved).',
                    level='info',
                    fishbowl_ref=fb_ref,
                    batch_id=batch_id,
                    sale_order=so,
                )
        if pending_triples:
            pick = self.create_odoo_incoming_for_pending_credit_returns(
                so,
                pending_triples,
                fishbowl_ref=fb_ref,
            )
            if pick and Log:
                Log.log_line(
                    'so',
                    'Fishbowl credit return: created incoming receipt %s for pending '
                    'receive qty (%s line(s)).'
                    % (pick.name, len(pending_triples)),
                    level='info',
                    fishbowl_ref=fb_ref,
                    batch_id=batch_id,
                    sale_order=so,
                )
            if pick:
                chatter_ctx = _fishbowl_chatter_ctx_from_import_ctx(import_ctx)
                so.with_context(**chatter_ctx).message_post(
                    body=Markup(
                        '<p><strong>Fishbowl credit return</strong></p>'
                        '<p>Created pending incoming receipt %s for products not yet '
                        'received in Fishbowl (%s).</p>'
                    )
                    % (escape(pick.name), len(pending_triples)),
                    subtype_xmlid='mail.mt_note',
                    message_type='comment',
                )
        if silent:
            return pick
        if pick:
            return pick
        reasons = []
        if missing_cr and not pending_triples:
            reasons.append(
                _('Missing product(s) in Odoo for pending return line(s):\n%s')
                % '\n'.join('- %s' % m for m in missing_cr[:20])
            )
        elif cr_lines and not pending_triples and not missing_cr:
            reasons.append(
                _('Fishbowl receipt quantities already cover the ordered return quantity for each line.')
            )
            if not getattr(self, 'credit_return_receipt_when_fishbowl_fully_received', False) and (
                not credit_returns_skipped_from_so
            ):
                reasons.append(
                    _(
                        'Enable "Receipt when Fishbowl shows return fully received" on Fishbowl MySQL '
                        'configuration if you still need an Odoo incoming transfer for this order.'
                    )
                )
        else:
            reasons.append(_('Could not create an incoming receipt for this order.'))
        raise UserError('\n'.join(reasons))
