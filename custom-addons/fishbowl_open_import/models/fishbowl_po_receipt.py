# -*- coding: utf-8 -*-
#
# Fishbowl ``poitem`` exposes ``qtyFulfilled``, ``qtyToFulfill``, ``poitemstatus``, and ``receiptitem``.
# ``receiptitem.poItemId`` → ``poitem.id`` (canonical); ``orderItemId`` is tried as a fallback on older DBs.
# PO import creates **full** Odoo PO lines; Fishbowl-already-received qty is stored on the line as
# ``fishbowl_prior_received_qty`` (counts toward Received without inventory). Incoming pickings are
# adjusted so move demand is only the **remaining** Fishbowl qty (no receipt for prior receipts).

import logging

from odoo import models
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


class FishbowlSyncConfig(models.Model):
    _inherit = 'fishbowl.sync.config'

    def _enrich_po_lines_with_poitem_types(self, conn, po_id, rows):
        """Fill ``poitem_type_name`` when the main query succeeded without a ``poitemtype`` join."""
        if not rows:
            return
        missing = any(
            not (r.get('poitem_type_name') or r.get('poitemtype_name')) for r in rows
        )
        if not missing:
            return
        for sql in (
            """
            SELECT pi.id AS poitem_id, pit.name AS poitem_type_name
            FROM poitem pi
            LEFT JOIN poitemtype pit ON pit.id = pi.typeId
            WHERE pi.poId = %s
            """,
            """
            SELECT pi.id AS poitem_id, pit.name AS poitem_type_name
            FROM poitem pi
            LEFT JOIN poitemtype pit ON pit.id = pi.typeid
            WHERE pi.poId = %s
            """,
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (po_id,))
                    fetched = list(cur.fetchall())
                if not fetched:
                    continue
                by_id = {}
                for fr in fetched:
                    rid = fr.get('poitem_id')
                    nm = fr.get('poitem_type_name')
                    if rid is not None and nm:
                        by_id[int(rid)] = nm
                if not by_id:
                    continue
                for r in rows:
                    pid = r.get('poitem_id')
                    if pid is None:
                        continue
                    if r.get('poitem_type_name') or r.get('poitemtype_name'):
                        continue
                    tname = by_id.get(int(pid))
                    if tname:
                        r['poitem_type_name'] = tname
                return
            except Exception as exc:
                _logger.debug('Fishbowl poitemtype enrich: %s', exc)
                continue

    def fetch_po_lines(self, po_id):
        """Return poitem rows including fulfilled qty when the Fishbowl schema supports it.

        Prefer queries that join ``poitemstatus`` (Fulfilled vs Entered) and sum ``receiptitem.qty``
        for ``poItemId`` / ``orderItemId`` = ``poitem.id`` — those match the Fishbowl UI when ``qtyToFulfill`` /
        ``qtyFulfilled`` are stale in ``poitem``.

        When the schema supports it, joins ``poitemtype`` so each row includes ``poitem_type_name``
        (Fishbowl PO line **Type** column, e.g. Credit Return, Misc. Credit).
        """
        self.ensure_one()
        conn = self._get_connection()
        try:
            po_id = int(po_id)
            rows = self._fb_try_queries(
                conn,
                [
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               pit.name AS poitem_type_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.poItemId = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        LEFT JOIN poitemtype pit ON pit.id = pi.typeId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               pit.name AS poitem_type_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.poitemid = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        LEFT JOIN poitemtype pit ON pit.id = pi.typeId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               pit.name AS poitem_type_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.orderItemId = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        LEFT JOIN poitemtype pit ON pit.id = pi.typeId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               pit.name AS poitem_type_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.orderitemid = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        LEFT JOIN poitemtype pit ON pit.id = pi.typeId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.orderItemId = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.orderitemid = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               pis.name AS poitem_status_name
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        LEFT JOIN poitemstatus pis ON pis.id = pi.statusId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered,
                               (SELECT COALESCE(SUM(ri.qty), 0)
                                FROM receiptitem ri
                                WHERE ri.orderItemId = pi.id) AS receipt_qty_sum
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled,
                               COALESCE(pi.qty, pi.qtyToFulfill + COALESCE(pi.qtyFulfilled, 0)) AS qtyOrdered
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num,
                               COALESCE(pi.qtyFulfilled, 0) AS qtyFulfilled
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                ],
                non_empty=True,
            )
            if rows:
                self._enrich_po_lines_with_poitem_types(conn, po_id, rows)
                return rows
            rows = self._fb_try_queries(
                conn,
                [
                    (
                        """
                        SELECT pi.id AS poitem_id, pi.poId, pi.partId, pi.partNum,
                               pi.description, pi.qtyToFulfill, pi.unitCost, pi.totalCost,
                               pi.poLineItem, p.num AS part_num
                        FROM poitem pi
                        LEFT JOIN part p ON p.id = pi.partId
                        WHERE pi.poId = %s
                        ORDER BY COALESCE(pi.poLineItem, 0), pi.id
                        """,
                        (po_id,),
                    ),
                ],
                non_empty=False,
            )
            if rows:
                self._enrich_po_lines_with_poitem_types(conn, po_id, rows)
            return rows
        finally:
            conn.close()

    @staticmethod
    def fb_po_line_ordered_qty(line):
        """Ordered quantity on the Fishbowl PO line (full line, not remaining).

        Prefer ``totalCost / unitCost`` when both exist so quantities match Fishbowl line totals (avoids
        bad ``qty`` / double-counted ``qtyFulfilled`` + ``qtyToFulfill``). Else ``qtyOrdered`` /
        ``qty``, then fulfilled + to-fulfill.
        """
        u = line.get('unitCost') if line.get('unitCost') is not None else line.get('unit_cost')
        t = line.get('totalCost') if line.get('totalCost') is not None else line.get('total_cost')
        if u is not None and t is not None:
            uu, tt = float(u), float(t)
            if uu > 0 and tt >= 0:
                q = tt / uu
                if q > 0:
                    return q
        for key in ('qtyOrdered', 'qty'):
            if line.get(key) is not None:
                v = float(line[key] or 0)
                if v > 0:
                    return v
        qf = float(line.get('qtyFulfilled') or 0)
        qtf = float(line.get('qtyToFulfill') or 0)
        if 'qtyFulfilled' in line or qf or qtf:
            return qf + qtf
        return float(line.get('qtyToFulfill') or 0)

    @staticmethod
    def fb_po_line_fulfilled_qty(line):
        """Quantity already received in Fishbowl for this poitem.

        Priority:

        1. ``poitemstatus.name`` from ``poitem.statusId`` (e.g. **Fulfilled** = full line received,
           **Entered** = open — combine with ``qtyFulfilled`` / ``receipt_qty_sum``).
        2. ``receipt_qty_sum`` — sum of ``receiptitem.qty`` for ``orderItemId`` = ``poitem.id``.
        3. ``ordered - qtyToFulfill`` merged with ``qtyFulfilled`` / receipt (``qtyToFulfill`` can be
           stale while the status/receipt tables match the UI).
        """
        ordered = FishbowlSyncConfig.fb_po_line_ordered_qty(line)
        if float_compare(ordered, 0, precision_digits=5) <= 0:
            return 0.0

        qf = float(line.get('qtyFulfilled') or 0)
        rq = line.get('receipt_qty_sum')
        rsum = float(rq) if rq is not None else None
        receipt = 0.0 if rsum is None else max(0.0, min(ordered, rsum))
        merged_from_receipt = max(qf, receipt)

        status = line.get('poitem_status_name')
        if status is not None and str(status).strip():
            s = str(status).strip().lower()
            if s in ('fulfilled', 'received'):
                return ordered
            if s in ('entered', 'bid', 'pending', 'open'):
                return max(0.0, min(ordered, merged_from_receipt))
            if s in ('cancelled', 'void'):
                return 0.0

        qtf = line.get('qtyToFulfill')
        if qtf is not None:
            qtf = float(qtf or 0)
            by_remaining = max(0.0, min(ordered, ordered - qtf))
            fulfilled = max(by_remaining, merged_from_receipt)
            if float_compare(fulfilled, ordered, precision_digits=5) > 0:
                fulfilled = ordered
            return fulfilled

        if float_compare(merged_from_receipt, 0, precision_digits=5) > 0:
            return min(ordered, merged_from_receipt)

        for key in ('qtyFulfilled', 'qty_fulfilled', 'qtyVouchered', 'qty_vouchered'):
            if line.get(key) is not None:
                return min(ordered, float(line[key] or 0))
        return 0.0

    @staticmethod
    def fb_po_line_remaining_qty(line):
        """Quantity still to receive in Fishbowl (ordered minus fulfilled)."""
        ordered = FishbowlSyncConfig.fb_po_line_ordered_qty(line)
        fulfilled = FishbowlSyncConfig.fb_po_line_fulfilled_qty(line)
        rem = ordered - fulfilled
        if float_compare(rem, 0, precision_digits=5) <= 0:
            return 0.0
        if float_compare(rem, ordered, precision_digits=5) > 0:
            return ordered
        return rem

    def _fishbowl_po_receipt_moves_for_line(self, pol):
        """Vendor-receipt stock moves for this PO line (search by ``purchase_line_id``, like SO ``line.move_ids``).

        Uses ``search`` so moves are found even when the O2M cache is stale. Includes moves from
        supplier locations (not only ``picking_type_id.code == 'incoming'``).
        """
        self.ensure_one()
        moves = self.env['stock.move'].sudo().search(
            [
                ('purchase_line_id', '=', pol.id),
                ('state', 'not in', ('done', 'cancel')),
            ]
        )
        return moves.filtered(
            lambda m: (m.picking_id and m.picking_id.picking_type_id.code == 'incoming')
            or m.location_id.usage == 'supplier'
        )

    def adjust_fishbowl_po_incoming_moves(self, purchase_order, fb_lines=None):
        """Limit incoming moves to **remaining** qty (ordered − Fishbowl-already-received); cancel when none.

        Uses **Odoo PO line** fields ``product_qty`` and ``fishbowl_prior_received_qty`` (set at import).
        Do **not** rely on re-querying Fishbowl rows here: a missing ``fb_lines`` row previously caused
        ``if not fb: continue`` and skipped adjustment, leaving full demand on the receipt.

        ``fb_lines`` is optional and unused for the main path (kept for API compatibility).
        """
        self.ensure_one()
        po = purchase_order.sudo()
        if po.state != 'purchase':
            return
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        po.invalidate_recordset(['order_line'])
        self.env.flush_all()

        for line in po.order_line.filtered(lambda l: not l.display_type and l.fishbowl_poitem_id):
            ordered = line.product_qty
            prior = line.fishbowl_prior_received_qty or 0.0
            remaining = ordered - prior
            if float_compare(remaining, 0, precision_digits=precision) < 0:
                remaining = 0.0
            if float_compare(remaining, ordered, precision_digits=precision) > 0:
                remaining = ordered
            moves = self._fishbowl_po_receipt_moves_for_line(line).sorted('id')
            if not moves:
                continue
            if float_compare(remaining, 0, precision_digits=precision) <= 0:
                try:
                    moves.sudo()._action_cancel()
                except Exception as e:
                    _logger.warning(
                        'Fishbowl PO import: could not cancel receipt moves for POL %s: %s',
                        line.id,
                        e,
                    )
                continue
            pol_uom = line.product_uom_id
            primary = moves[0]
            extra = moves[1:]
            if extra:
                try:
                    extra.sudo()._action_cancel()
                except Exception as e:
                    _logger.warning(
                        'Fishbowl PO import: could not cancel extra receipt moves for POL %s: %s',
                        line.id,
                        e,
                    )
            qty_move_uom = pol_uom._compute_quantity(remaining, primary.product_uom)
            if float_compare(qty_move_uom, primary.product_uom_qty, precision_digits=precision) != 0:
                primary.write({'product_uom_qty': qty_move_uom})

        po.invalidate_recordset(['picking_ids'])
        pickings = po.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel') and p.picking_type_id.code == 'incoming'
        )
        for picking in pickings:
            try:
                picking.action_assign()
            except Exception as e:
                _logger.warning(
                    'Fishbowl PO import: action_assign failed for %s: %s', picking.name, e
                )
