# -*- coding: utf-8 -*-
#
# Fishbowl ship / shipitem / pick queries target typical MySQL schemas (camelCase ids).
# pickitem uses soItemId (capital I), not soitemId — wrong casing breaks queries on case-sensitive MySQL.
# pick often has no soId/orderId; link SO lines via pickitem.soItemId → soitem.id or pickitem.orderId → so.id.
# Pick qty: pickitemstatus whitelist (Picked / Finished / Committed / …) — exclude Started, Short, etc.
# ``Committed`` in Fishbowl means qty is already picked; those lines must not stay on Odoo pick moves.
# Kit/bundle: roll up child pick/ship qty onto parent for *totals* only; the SO line uses the parent
# ``soitem`` id and Odoo explodes BOM moves. Distributing rolled-up sums across moves is wrong — we match
# each move to a child ``soitem`` by product code and set done qty per component + parent kit move.
# ``soitem.kitItemId`` often points at
# ``kititem.id`` (catalog row); parent ``soitem`` is then the line on the same SO whose ``productId``
# equals ``kititem.kitProductId`` (see fetch_soitem_kit_parent_map).
# SO line status (``soitem`` + ``soitemstatus``): UI can show components ``Fulfilled`` while pick/ship
# tables lag; we also mark picked when the Fishbowl line status is fulfilled-like.
# If your DB differs, adjust after: DESCRIBE ship; DESCRIBE shipitem; DESCRIBE pick; DESCRIBE pickitem;

import logging

from markupsafe import Markup, escape

from odoo import models
from odoo.tools.float_utils import float_compare, float_is_zero

_logger = logging.getLogger(__name__)


class FishbowlSyncConfig(models.Model):
    _inherit = 'fishbowl.sync.config'

    def _fb_try_queries(self, conn, queries_with_params, non_empty=False):
        """Run SQL variants until one succeeds. If non_empty, skip successful queries that return no rows."""
        last_err = None
        with conn.cursor() as cur:
            for sql, params in queries_with_params:
                try:
                    cur.execute(sql, params)
                    rows = list(cur.fetchall())
                    if non_empty and not rows:
                        continue
                    return rows
                except Exception as e:
                    last_err = e
                    continue
        if last_err:
            _logger.debug('Fishbowl fulfillment SQL fallbacks exhausted: %s', last_err)
        return []

    def fetch_so_shipment_qty_by_soitem(self, so_fb_id):
        """Sum shipped quantity per Fishbowl soitem id from ship + shipitem.

        Uses ``non_empty`` fallbacks: the first query that *executes* but returns no rows may be
        the wrong column names; we try the next variant until one returns data or all are exhausted.
        """
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        try:
            rows = self._fb_try_queries(
                conn,
                [
                    (
                        """
                        SELECT si.soItemId AS soitem_id, SUM(COALESCE(si.qtyShipped, 0)) AS qty
                        FROM shipitem si
                        INNER JOIN ship s ON s.id = si.shipId
                        WHERE s.soId = %s
                        GROUP BY si.soItemId
                        """,
                        (so_fb_id,),
                    ),
                    (
                        """
                        SELECT COALESCE(si.soItemId, si.itemId) AS soitem_id,
                               SUM(COALESCE(si.qtyShipped, 0)) AS qty
                        FROM shipitem si
                        INNER JOIN ship s ON s.id = si.shipId
                        WHERE s.soId = %s
                          AND COALESCE(si.soItemId, si.itemId) IS NOT NULL
                          AND COALESCE(si.soItemId, si.itemId) > 0
                        GROUP BY COALESCE(si.soItemId, si.itemId)
                        """,
                        (so_fb_id,),
                    ),
                    (
                        """
                        SELECT si.soitemId AS soitem_id, SUM(COALESCE(si.qty, 0)) AS qty
                        FROM shipitem si
                        INNER JOIN ship s ON s.id = si.shipId
                        WHERE s.soId = %s
                        GROUP BY si.soitemId
                        """,
                        (so_fb_id,),
                    ),
                    (
                        """
                        SELECT si.soitemId AS soitem_id, SUM(COALESCE(si.qtyShipped, si.qty, 0)) AS qty
                        FROM shipitem si
                        INNER JOIN ship s ON s.id = si.shipId
                        WHERE s.soId = %s
                        GROUP BY si.soitemId
                        """,
                        (so_fb_id,),
                    ),
                ],
                non_empty=True,
            )
        finally:
            conn.close()
        out = {}
        for r in rows:
            sid = r.get('soitem_id')
            if sid is None:
                continue
            out[int(sid)] = float(r.get('qty') or 0)
        return out

    def fetch_so_ship_date_shipped_exists(self, so_fb_id):
        """True if any ``ship`` row for this SO has ``dateShipped`` set (shipment completed)."""
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 AS ok
                    FROM ship
                    WHERE soId = %s AND dateShipped IS NOT NULL
                    LIMIT 1
                    """,
                    (so_fb_id,),
                )
                return bool(cur.fetchall())
        except Exception:
            return False
        finally:
            conn.close()

    def fetch_soitem_fulfilled_soitem_ids(self, so_fb_id):
        """Fishbowl ``soitem`` ids whose **line** status name is fulfilled-like (UI ``Fulfilled``, etc.)."""
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        _ok = (
            "'fulfilled', 'shipped', 'picked', 'complete', 'closed', 'done', "
            "'delivered', 'partially fulfilled', 'partial'"
        )
        sql_variants = [
            (
                """
                SELECT si.id AS soitem_id
                FROM soitem si
                INNER JOIN soitemstatus st ON st.id = si.statusId
                WHERE si.soId = %s AND LOWER(TRIM(st.name)) IN ({ok})
                """.format(
                    ok=_ok,
                ),
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS soitem_id
                FROM soitem si
                INNER JOIN soitemstatus st ON st.id = si.soItemStatusId
                WHERE si.soId = %s AND LOWER(TRIM(st.name)) IN ({ok})
                """.format(ok=_ok),
                (so_fb_id,),
            ),
        ]
        out = set()
        try:
            with conn.cursor() as cur:
                for sql, params in sql_variants:
                    try:
                        cur.execute(sql, params)
                        for r in cur.fetchall():
                            sid = r.get('soitem_id')
                            if sid is not None:
                                out.add(int(sid))
                    except Exception:
                        continue
        finally:
            conn.close()
        return out

    @staticmethod
    def _effective_fb_line_fulfilled(soitem_id, fulfilled_ids, parent_map):
        """True if this ``soitem`` or an ancestor kit line is in ``fulfilled_ids``."""
        if not fulfilled_ids:
            return False
        cur = int(soitem_id)
        for _dummy in range(256):
            if cur in fulfilled_ids:
                return True
            parent = parent_map.get(cur) if parent_map else None
            if parent is None:
                break
            cur = int(parent)
        return False

    def fetch_soitem_kit_parent_map(self, so_fb_id):
        """Return dict child_soitem_id -> parent_soitem_id for kit/bundle lines (Fishbowl ``soitem``).

        Merges rows from ``parentId`` / ``parentSoItemId`` / ``kitParent*``, and (when ``kititem`` exists)
        ``soitem.kitItemId`` → ``kititem.id`` with ``kititem.kitProductId`` matching the parent ``soitem``
        line on the same SO. Later queries overwrite earlier keys for the same child id.
        """
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        queries = [
            (
                """
                SELECT si.id AS child_id, si.parentId AS parent_id
                FROM soitem si
                WHERE si.soId = %s AND si.parentId IS NOT NULL AND si.parentId > 0 AND si.parentId <> si.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS child_id, si.parentSoItemId AS parent_id
                FROM soitem si
                WHERE si.soId = %s AND si.parentSoItemId IS NOT NULL AND si.parentSoItemId <> si.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS child_id, si.kitParentId AS parent_id
                FROM soitem si
                WHERE si.soId = %s AND si.kitParentId IS NOT NULL AND si.kitParentId > 0 AND si.kitParentId <> si.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS child_id, si.kitParentSoItemId AS parent_id
                FROM soitem si
                WHERE si.soId = %s AND si.kitParentSoItemId IS NOT NULL AND si.kitParentSoItemId <> si.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS child_id, MIN(pl.id) AS parent_id
                FROM soitem si
                INNER JOIN kititem ki ON ki.id = si.kitItemId
                INNER JOIN soitem pl ON pl.soId = si.soId
                    AND pl.productId = ki.kitProductId
                    AND pl.id <> si.id
                WHERE si.soId = %s AND si.kitItemId IS NOT NULL
                GROUP BY si.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT si.id AS child_id, MIN(pl.id) AS parent_id
                FROM soitem si
                INNER JOIN kititem ki ON ki.id = si.kititemId
                INNER JOIN soitem pl ON pl.soId = si.soId
                    AND pl.productId = ki.kitProductId
                    AND pl.id <> si.id
                WHERE si.soId = %s AND si.kititemId IS NOT NULL
                GROUP BY si.id
                """,
                (so_fb_id,),
            ),
        ]
        out = {}
        try:
            with conn.cursor() as cur:
                for sql, params in queries:
                    try:
                        cur.execute(sql, params)
                        for r in cur.fetchall():
                            cid = r.get('child_id')
                            pid = r.get('parent_id')
                            if cid is None or pid is None:
                                continue
                            try:
                                out[int(cid)] = int(pid)
                            except (TypeError, ValueError):
                                continue
                    except Exception:
                        continue
        finally:
            conn.close()
        return out

    @staticmethod
    def _rollup_fb_qty_by_parent_soitem(by_item, parent_map):
        """Add quantities keyed on child soitem ids onto their parent (kit) soitem id."""
        if not by_item or not parent_map:
            return dict(by_item) if by_item else {}
        out = dict(by_item)
        for child_id, parent_id in parent_map.items():
            if not parent_id:
                continue
            q = float(out.get(int(child_id), 0) or 0)
            if abs(q) < 1e-12:
                continue
            pid = int(parent_id)
            out[pid] = float(out.get(pid, 0) or 0) + q
        return out

    @staticmethod
    def _effective_fb_qty_for_soitem(soitem_id, by_item, parent_map):
        """Quantity for this soitem, or any ancestor kit line (Fishbowl may key picks on parent only)."""
        if not by_item:
            return 0.0
        cur = int(soitem_id)
        for _dummy in range(256):
            q = float(by_item.get(cur, 0) or 0)
            if abs(q) > 1e-12:
                return q
            parent = parent_map.get(cur) if parent_map else None
            if parent is None:
                break
            cur = int(parent)
        return 0.0

    def fetch_soitem_product_qty_by_id(self, so_fb_id):
        """Map Fishbowl ``soitem.id`` → ``product_num`` and ``qty_ordered`` for kit move matching."""
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        try:
            rows = self._fb_try_queries(
                conn,
                [
                    (
                        """
                        SELECT si.id AS soitem_id, si.productNum AS product_num, si.qtyOrdered AS qty_ordered
                        FROM soitem si
                        WHERE si.soId = %s
                        """,
                        (so_fb_id,),
                    ),
                    (
                        """
                        SELECT si.id AS soitem_id, si.productNum AS product_num, si.qty AS qty_ordered
                        FROM soitem si
                        WHERE si.soId = %s
                        """,
                        (so_fb_id,),
                    ),
                ],
                non_empty=True,
            )
        finally:
            conn.close()
        out = {}
        for r in rows:
            sid = r.get('soitem_id')
            if sid is None:
                continue
            pn = r.get('product_num')
            if pn is None:
                pn = r.get('productNum')
            pn = (str(pn).strip() if pn else '') or ''
            qo = r.get('qty_ordered')
            if qo is None:
                qo = r.get('qtyOrdered')
            try:
                qf = float(qo or 0)
            except (TypeError, ValueError):
                qf = 0.0
            out[int(sid)] = {'product_num': pn, 'qty_ordered': qf}
        return out

    def _fishbowl_kit_fb_qty_for_child(self, child_id, is_outgoing, shipped_by_item, picked_by_item):
        """Ship qty on outgoing; pick qty on internal (fallback to ship when pick empty)."""
        cid = int(child_id)
        if is_outgoing:
            return float(shipped_by_item.get(cid, 0) or 0)
        if picked_by_item:
            q = float(picked_by_item.get(cid, 0) or 0)
            if not float_is_zero(q, precision_rounding=0.0001):
                return q
        return float(shipped_by_item.get(cid, 0) or 0)

    def _apply_fishbowl_kit_line_moves(
        self,
        line,
        moves,
        outgoing_moves,
        internal_moves,
        shipped_by_item,
        picked_by_item,
        children,
        soitem_detail,
        has_positive_ship,
        sol_rounding,
    ):
        """Set done qty on kit component moves + parent kit move from Fishbowl per-child ship/pick rows."""
        children = [int(c) for c in children]
        code_to_child = {}
        for c in children:
            info = soitem_detail.get(c) or {}
            pn = (info.get('product_num') or '').strip()
            if pn:
                code_to_child[pn.lower()] = c

        def _norm_code(product):
            return (product.default_code or '').strip().lower()

        # Partial SO: other lines have ship rows; this kit has no shipped qty on any child — clear outgoing.
        if has_positive_ship:
            any_child_ship = any(
                not float_is_zero(float(shipped_by_item.get(int(c), 0) or 0), precision_rounding=sol_rounding)
                for c in children
            )
            if not any_child_ship:
                for move in outgoing_moves:
                    if move.state in ('done', 'cancel'):
                        continue
                    move._set_quantity_done(0.0)

        for move in moves.sorted('id'):
            if move.state in ('done', 'cancel'):
                continue
            is_out = move.picking_id.picking_type_id.code == 'outgoing'
            # Parent kit product row on the picking (same product as SO line)
            if move.product_id == line.product_id:
                ok = True
                for c in children:
                    q_req = float((soitem_detail.get(c) or {}).get('qty_ordered') or 0)
                    q_fb = self._fishbowl_kit_fb_qty_for_child(
                        c, is_out, shipped_by_item, picked_by_item
                    )
                    if float_compare(q_fb, q_req, precision_rounding=sol_rounding) < 0:
                        ok = False
                        break
                # Only set done qty when Fishbowl confirms every child; never force 0 (would wipe Odoo).
                if ok:
                    move._set_quantity_done(move.product_uom_qty)
                continue

            child_id = code_to_child.get(_norm_code(move.product_id))
            if not child_id:
                continue
            q_fb = self._fishbowl_kit_fb_qty_for_child(
                child_id, is_out, shipped_by_item, picked_by_item
            )
            # If Fishbowl has no ship/pick row for this child, do not overwrite reserved/done qty with 0.
            if float_is_zero(q_fb, precision_rounding=move.product_uom.rounding):
                continue
            cap = move.product_uom_qty
            take = min(cap, q_fb)
            move._set_quantity_done(take)

    def _set_phantom_kit_parent_move_done_if_components_ready(self, line, moves):
        """Set the phantom kit parent move quantity done when every BOM component move is fully done.

        Used when Fishbowl has no parent→child soitem map, or when Fishbowl kit logic left the parent
        at 0 while component moves in Odoo are already at full demand.
        """
        if 'mrp.bom' not in self.env:
            return
        Bom = self.env['mrp.bom'].sudo()
        bom = Bom._bom_find(line.product_id, company_id=line.company_id.id, bom_type='phantom')[line.product_id]
        if not bom or bom.type != 'phantom':
            return
        comp_moves = moves.filtered(lambda m: m.bom_line_id and m.bom_line_id.bom_id == bom)
        parent_moves = moves.filtered(lambda m: m.product_id == line.product_id and not m.bom_line_id)
        if not comp_moves:
            return
        for m in comp_moves:
            if m.state in ('done', 'cancel'):
                continue
            if float_compare(m.quantity, m.product_uom_qty, precision_rounding=m.product_uom.rounding) < 0:
                return
        for pm in parent_moves:
            if pm.state in ('done', 'cancel'):
                continue
            pm._set_quantity_done(pm.product_uom_qty)

    def fetch_so_pick_qty_by_soitem(self, so_fb_id):
        """Sum picked quantity per soitem from pickitem (+ soitem when needed).

        Runs every SQL variant that succeeds and merges **max** qty per ``soitem_id`` so a strict
        query that returns rows for some lines does not block relaxed/NULL-status variants from
        filling kit component lines.

        Strict: pickitemstatus in a finished whitelist. Relaxed: NULL ``statusId`` or extra names;
        still excludes Short / Started / Cancelled.
        """
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        _pick_ok = """
            AND LOWER(TRIM(COALESCE(pis.name, ''))) IN (
                'picked', 'finished', 'done', 'complete', 'completed', 'packed', 'fulfilled',
                'committed'
            )
        """
        _pick_ok_relaxed = """
            AND (
                pi.statusId IS NULL
                OR LOWER(TRIM(COALESCE(pis.name, ''))) IN (
                    'picked', 'finished', 'done', 'complete', 'completed', 'packed', 'fulfilled',
                    'picked complete', 'pick complete', 'closed', 'committed'
                )
            )
            AND (
                pi.statusId IS NULL
                OR LOWER(TRIM(COALESCE(pis.name, ''))) NOT IN (
                    'short', 'canceled', 'cancelled', 'void', 'started', 'canceled'
                )
            )
        """
        queries = [
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN soitem si ON si.id = pi.soItemId
                INNER JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE si.soId = %s
                """ + _pick_ok + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE pi.orderId = %s AND pi.soItemId IS NOT NULL
                """ + _pick_ok + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soitemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN soitem si ON si.id = pi.soitemId
                INNER JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE si.soId = %s
                """ + _pick_ok + """
                GROUP BY pi.soitemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN pick p ON p.id = pi.pickId
                INNER JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE p.soId = %s
                """ + _pick_ok + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN pick p ON p.id = pi.pickId
                INNER JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE p.orderId = %s
                """ + _pick_ok + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                INNER JOIN soitem si ON si.id = pi.soItemId
                LEFT JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE si.soId = %s
                """ + _pick_ok_relaxed + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT pi.soItemId AS soitem_id, SUM(COALESCE(pi.qty, 0)) AS qty
                FROM pickitem pi
                LEFT JOIN pickitemstatus pis ON pis.id = pi.statusId
                WHERE pi.orderId = %s AND pi.soItemId IS NOT NULL
                """ + _pick_ok_relaxed + """
                GROUP BY pi.soItemId
                """,
                (so_fb_id,),
            ),
        ]
        out = {}
        try:
            with conn.cursor() as cur:
                for sql, params in queries:
                    try:
                        cur.execute(sql, params)
                        for r in cur.fetchall():
                            sid = r.get('soitem_id')
                            if sid is None:
                                continue
                            k = int(sid)
                            q = float(r.get('qty') or 0)
                            out[k] = max(out.get(k, 0.0), q)
                    except Exception:
                        continue
        finally:
            conn.close()
        return out

    def _normalize_fb_tracking_value(self, raw):
        if raw is None:
            return None
        s = str(raw).strip()
        return s or None

    def fetch_so_shipment_tracking_strings(self, so_fb_id):
        """Non-empty tracking strings for this SO (ship, shipcarton, shipment id; order preserved).

        Fishbowl often stores UPS/FedEx numbers on **cartons** (`shipcarton`), not on ``ship``;
        ``ship`` may only expose ``shipmentIdentificationNumber``.
        """
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        queries = [
            (
                """
                SELECT s.id AS ship_id, s.trackingNum AS tr
                FROM ship s
                WHERE s.soId = %s AND s.trackingNum IS NOT NULL AND TRIM(s.trackingNum) <> ''
                ORDER BY s.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT s.id AS ship_id, s.trackingNumber AS tr
                FROM ship s
                WHERE s.soId = %s AND s.trackingNumber IS NOT NULL AND TRIM(s.trackingNumber) <> ''
                ORDER BY s.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT s.id AS ship_id, s.shipmentIdentificationNumber AS tr
                FROM ship s
                WHERE s.soId = %s
                  AND s.shipmentIdentificationNumber IS NOT NULL
                  AND TRIM(s.shipmentIdentificationNumber) <> ''
                ORDER BY s.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT s.id AS ship_id, sc.trackingNum AS tr
                FROM shipcarton sc
                INNER JOIN ship s ON s.id = sc.shipId
                WHERE s.soId = %s AND sc.trackingNum IS NOT NULL AND TRIM(sc.trackingNum) <> ''
                ORDER BY s.id, sc.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT s.id AS ship_id, sc.trackingNumber AS tr
                FROM shipcarton sc
                INNER JOIN ship s ON s.id = sc.shipId
                WHERE s.soId = %s AND sc.trackingNumber IS NOT NULL AND TRIM(sc.trackingNumber) <> ''
                ORDER BY s.id, sc.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT s.id AS ship_id, sc.trackNum AS tr
                FROM shipcarton sc
                INNER JOIN ship s ON s.id = sc.shipId
                WHERE s.soId = %s AND sc.trackNum IS NOT NULL AND TRIM(sc.trackNum) <> ''
                ORDER BY s.id, sc.id
                """,
                (so_fb_id,),
            ),
        ]
        seen = set()
        ordered = []
        try:
            with conn.cursor() as cur:
                for sql, params in queries:
                    try:
                        cur.execute(sql, params)
                        for r in cur.fetchall():
                            tr = self._normalize_fb_tracking_value(r.get('tr'))
                            if tr and tr not in seen:
                                seen.add(tr)
                                ordered.append(tr)
                    except Exception:
                        continue
        finally:
            conn.close()
        return ordered

    def post_fishbowl_shipment_tracking_to_chatter(self, sale_order, tracking_strings):
        """Log note on ``sale.order`` with Fishbowl tracking numbers (internal note)."""
        self.ensure_one()
        if not tracking_strings:
            return
        so = sale_order.sudo()
        body = Markup('<p><strong>Fishbowl shipment tracking</strong></p>')
        for tr in tracking_strings:
            body += Markup('<p>%s</p>') % escape(str(tr))
        so.with_context(
            mail_create_nolog=False,
            mail_post_autofollow=False,
        ).message_post(
            body=body,
            subtype_xmlid='mail.mt_note',
            message_type='comment',
        )

    def fishbowl_sales_line_fully_shipped_for_import(
        self,
        so_fb_id,
        fb_line,
        qty_ordered,
        shipped_by_item,
        parent_map,
        shipped_by_item_raw=None,
        parent_to_children=None,
        soitem_detail=None,
        fulfilled_soitem_ids=None,
    ):
        """True if Fishbowl shows this line fully shipped/delivered (ship rows and/or line status).

        Used at import so Odoo can skip stock moves and mark delivered qty manually (no inventory
        impact). Partially shipped lines return False here so normal stock moves remain.

        For **kit** SO lines (Fishbowl parent soitem with component children), rolled-up ship totals on
        the parent are not comparable to ordered kit qty (1); we require each child soitem's shipped
        qty >= that line's ``qtyOrdered`` using **raw** per-soitem ship quantities.

        Also treats Fishbowl **soitem** line status as delivered: ``fulfilled_soitem_ids`` from
        ``soitem`` + ``soitemstatus`` (and kit parents when **every** child soitem is in that set,
        which matches Fishbowl often marking component lines delivered but not the kit parent row).
        """
        self.ensure_one()
        try:
            qty_ordered = float(qty_ordered or 0)
        except (TypeError, ValueError):
            qty_ordered = 0.0
        if qty_ordered <= 0:
            return False
        sid = int(fb_line.get('soitem_id') or 0)
        if not sid:
            return False
        if fulfilled_soitem_ids:
            if self._effective_fb_line_fulfilled(int(sid), fulfilled_soitem_ids, parent_map):
                return True
            ch = (parent_to_children or {}).get(int(sid), [])
            if ch and all(int(c) in fulfilled_soitem_ids for c in ch):
                return True
        children = (parent_to_children or {}).get(sid, [])
        if children and shipped_by_item_raw is not None and soitem_detail:
            kit_verified = True
            for cid in children:
                info = soitem_detail.get(int(cid))
                if not info:
                    kit_verified = False
                    break
                q_req = float(info.get('qty_ordered') or 0)
                q_fb = float(shipped_by_item_raw.get(int(cid), 0) or 0)
                rounding = max(0.0001, (q_req or 0.01) * 1e-6)
                if float_compare(q_fb, q_req, precision_rounding=rounding) < 0:
                    return False
            if kit_verified:
                return True
        if not shipped_by_item:
            return False
        ship_alloc = float(shipped_by_item.get(sid, 0) or 0)
        ship_eff = self._effective_fb_qty_for_soitem(sid, shipped_by_item, parent_map)
        qty_ship = max(ship_alloc, ship_eff)
        rounding = max(0.0001, (qty_ordered or 0.01) * 1e-6)
        return float_compare(qty_ship, qty_ordered, precision_rounding=rounding) >= 0

    def fishbowl_sales_line_fully_picked_for_import(
        self,
        so_fb_id,
        fb_line,
        qty_ordered,
        picked_by_item,
        parent_map,
        picked_by_item_raw=None,
        parent_to_children=None,
        soitem_detail=None,
    ):
        """True if Fishbowl shows this line fully picked (pickitem qty incl. *Committed* status).

        Used with ``fishbowl_ship_import_without_stock`` so Odoo does not leave open pick moves for
        lines Fishbowl already picked (Committed / Picked / … on pickitem).
        """
        self.ensure_one()
        try:
            qty_ordered = float(qty_ordered or 0)
        except (TypeError, ValueError):
            qty_ordered = 0.0
        if qty_ordered <= 0:
            return False
        sid = int(fb_line.get('soitem_id') or 0)
        if not sid:
            return False
        children = (parent_to_children or {}).get(sid, [])
        if children and picked_by_item_raw is not None and soitem_detail:
            kit_verified = True
            for cid in children:
                info = soitem_detail.get(int(cid))
                if not info:
                    kit_verified = False
                    break
                q_req = float(info.get('qty_ordered') or 0)
                q_fb = float(picked_by_item_raw.get(int(cid), 0) or 0)
                rounding = max(0.0001, (q_req or 0.01) * 1e-6)
                if float_compare(q_fb, q_req, precision_rounding=rounding) < 0:
                    return False
            if kit_verified:
                return True
        if not picked_by_item:
            return False
        pick_alloc = float(picked_by_item.get(sid, 0) or 0)
        pick_eff = self._effective_fb_qty_for_soitem(sid, picked_by_item, parent_map)
        qty_pick = max(pick_alloc, pick_eff)
        rounding = max(0.0001, (qty_ordered or 0.01) * 1e-6)
        return float_compare(qty_pick, qty_ordered, precision_rounding=rounding) >= 0

    def fishbowl_apply_skip_shipped_lines_after_confirm(self, sale_order):
        """After SO confirm: cancel stock moves and set ``fishbowl_skip_procurement`` when Fishbowl shows shipped.

        When ``fishbowl_ship_import_without_stock`` is enabled, the same check runs on each SOL at
        create — but kit parent/child detection or timing can miss. Re-running here removes open
        pick lines that would stay *Not Available* in Odoo when there is no local stock even though
        Fishbowl already shipped, and when Fishbowl pickitem shows full qty picked (including Committed).
        """
        self.ensure_one()
        so = sale_order
        if so.state != 'sale':
            return
        so_fb_id = so.fishbowl_so_id
        if not so_fb_id:
            return
        try:
            adj_prod = self.env.ref(
                'fishbowl_open_import.product_template_fishbowl_so_adjustment'
            ).product_variant_id
        except ValueError:
            adj_prod = None
        shipped_by_item_raw = self.fetch_so_shipment_qty_by_soitem(so_fb_id)
        picked_by_item_raw = self.fetch_so_pick_qty_by_soitem(so_fb_id)
        parent_map = self.fetch_soitem_kit_parent_map(so_fb_id)
        shipped_by_item = self._rollup_fb_qty_by_parent_soitem(shipped_by_item_raw, parent_map)
        picked_by_item = self._rollup_fb_qty_by_parent_soitem(picked_by_item_raw, parent_map)
        parent_to_children = {}
        for cid, pid in parent_map.items():
            if pid:
                parent_to_children.setdefault(int(pid), []).append(int(cid))
        soitem_detail = self.fetch_soitem_product_qty_by_id(so_fb_id)
        fulfilled_soitem_ids = self.fetch_soitem_fulfilled_soitem_ids(so_fb_id)
        for line in so.order_line:
            if line.fishbowl_skip_procurement:
                continue
            if adj_prod and line.product_id == adj_prod:
                continue
            sid = line.fishbowl_soitem_id
            if not sid:
                continue
            qty = line.product_uom_qty
            fb_line = {'soitem_id': sid}
            if not self.fishbowl_sales_line_fully_shipped_for_import(
                so_fb_id,
                fb_line,
                qty,
                shipped_by_item,
                parent_map,
                shipped_by_item_raw=shipped_by_item_raw,
                parent_to_children=parent_to_children,
                soitem_detail=soitem_detail,
                fulfilled_soitem_ids=fulfilled_soitem_ids,
            ) and not self.fishbowl_sales_line_fully_picked_for_import(
                so_fb_id,
                fb_line,
                qty,
                picked_by_item,
                parent_map,
                picked_by_item_raw=picked_by_item_raw,
                parent_to_children=parent_to_children,
                soitem_detail=soitem_detail,
            ):
                continue
            moves = line.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
            if moves:
                try:
                    moves.sudo()._action_cancel()
                except Exception as e:
                    _logger.warning(
                        'Fishbowl post-confirm skip-stock: could not cancel moves for SOL %s: %s',
                        line.id,
                        e,
                    )
                    continue
            line.sudo().write({'fishbowl_skip_procurement': True})

    def _fishbowl_kit_fully_shipped_by_fishbowl_shipment(
        self, children, shipped_by_item_raw, soitem_detail
    ):
        """True when every Fishbowl kit child soitem has ship qty >= ordered for that component."""
        if not children or not shipped_by_item_raw or not soitem_detail:
            return False
        for cid in children:
            info = soitem_detail.get(int(cid))
            if not info:
                return False
            q_req = float(info.get('qty_ordered') or 0)
            q_fb = float(shipped_by_item_raw.get(int(cid), 0) or 0)
            rounding = max(0.0001, (q_req or 0.01) * 1e-6)
            if float_compare(q_fb, q_req, precision_rounding=rounding) < 0:
                return False
        return True

    def _fishbowl_cancel_open_moves_and_mark_skipped(self, line):
        """Cancel undelivered stock moves for this SOL and mark line as Fishbowl skip (manual delivered).

        Used when Fishbowl already shipped every kit component: removes component lines from pickings
        instead of leaving phantom BOM moves open.
        """
        self.ensure_one()
        line.ensure_one()
        moves = line.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        if moves:
            try:
                moves.sudo()._action_cancel()
            except Exception as e:
                _logger.warning(
                    'Fishbowl fulfillment: could not cancel moves for SOL %s (kit already shipped): %s',
                    line.id,
                    e,
                )
                return
        line.sudo().write({'fishbowl_skip_procurement': True})

    def _fishbowl_phantom_kit_all_components_at_full_demand(self, line):
        """True when this SOL is a phantom kit and every stock BOM line has moves at full demand (or done).

        Used when Fishbowl parent/child soitem mapping or ship rows do not trigger the SQL-based
        cleanup, but Odoo already shows full reserved/done qty on every component move (SO delivered).
        """
        self.ensure_one()
        line.ensure_one()
        if line.fishbowl_skip_procurement:
            return False
        if 'mrp.bom' not in self.env:
            return False
        Bom = self.env['mrp.bom'].sudo()
        bom = Bom._bom_find(line.product_id, company_id=line.company_id.id, bom_type='phantom')[line.product_id]
        if not bom or bom.type != 'phantom':
            return False
        for bom_line in bom.bom_line_ids:
            if bom_line.product_id.type == 'service':
                continue
            line_moves = line.move_ids.filtered(lambda m, bl=bom_line: m.bom_line_id == bl)
            if not line_moves:
                return False
            open_moves = line_moves.filtered(lambda m: m.state not in ('done', 'cancel'))
            if not open_moves:
                continue
            for m in open_moves:
                if float_compare(m.quantity, m.product_uom_qty, precision_rounding=m.product_uom.rounding) < 0:
                    return False
        return True

    def apply_fishbowl_fulfillment_to_sale_order(self, sale_order, log=None, batch_id=None, fishbowl_ref=None):
        """Best-effort: align outgoing deliveries with Fishbowl ship/pick data; never raises."""
        self.ensure_one()
        try:
            self._apply_fishbowl_fulfillment_to_sale_order_impl(sale_order)
        except Exception as e:
            _logger.exception('Fishbowl fulfillment sync failed')
            if log:
                log.log_line(
                    'picking',
                    'Fulfillment sync failed: %s' % e,
                    level='warning',
                    fishbowl_ref=fishbowl_ref,
                    batch_id=batch_id,
                    sale_order=sale_order,
                )

    def _apply_fishbowl_fulfillment_to_sale_order_impl(self, sale_order):
        self.ensure_one()
        so = sale_order
        if so.state != 'sale':
            return
        so_fb_id = so.fishbowl_so_id
        if not so_fb_id:
            return

        shipped_by_item_raw = self.fetch_so_shipment_qty_by_soitem(so_fb_id)
        picked_by_item_raw = self.fetch_so_pick_qty_by_soitem(so_fb_id)
        parent_map = self.fetch_soitem_kit_parent_map(so_fb_id)
        fulfilled_soitem_ids = self.fetch_soitem_fulfilled_soitem_ids(so_fb_id)
        shipped_by_item = self._rollup_fb_qty_by_parent_soitem(shipped_by_item_raw, parent_map)
        picked_by_item = self._rollup_fb_qty_by_parent_soitem(picked_by_item_raw, parent_map)
        tracking_parts = self.fetch_so_shipment_tracking_strings(so_fb_id)
        ship_date_shipped = self.fetch_so_ship_date_shipped_exists(so_fb_id)
        posted_tracking_chatter = False

        def _post_tracking_chatter_once():
            nonlocal posted_tracking_chatter
            if posted_tracking_chatter or not tracking_parts:
                return
            self.post_fishbowl_shipment_tracking_to_chatter(so, tracking_parts)
            posted_tracking_chatter = True

        # Pick / Pack steps use operation type "internal"; only the ship step is "outgoing".
        # Filtering to outgoing only skipped the actual pick transfer (e.g. WWH/PICK/…).
        pickings = so.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel')
            and p.picking_type_id.code in ('outgoing', 'internal')
        )
        if not pickings:
            _post_tracking_chatter_once()
            return

        delivery_pickings = pickings.filtered(lambda p: p.picking_type_id.code == 'outgoing')
        if tracking_parts and delivery_pickings:
            ref = ', '.join(dict.fromkeys(tracking_parts))
            delivery_pickings.write({'carrier_tracking_ref': ref})

        has_positive_ship = any(q > 0 for q in shipped_by_item.values())
        has_positive_pick = any(q > 0 for q in picked_by_item.values())
        has_fulfilled_line = bool(fulfilled_soitem_ids)
        # Shipment exists in Fishbowl (shipitem totals, tracking, or ship.dateShipped) even when
        # per-soitem aggregation is empty — needed to complete Odoo deliveries and ``qty_delivered``.
        ship_evidence = has_positive_ship or bool(tracking_parts) or ship_date_shipped
        # Fishbowl often has picks completed before any ship rows exist; the old logic
        # returned here and skipped lines with ship_qty == 0, so Odoo never got picked flags.
        # SO line status ``Fulfilled`` (soitem + soitemstatus) can apply when pick/ship rows do not.
        if not has_positive_ship and not has_positive_pick and not has_fulfilled_line:
            _post_tracking_chatter_once()
            return

        pickings.action_assign()

        parent_to_children = {}
        for cid, pid in parent_map.items():
            if pid:
                parent_to_children.setdefault(int(pid), []).append(int(cid))
        soitem_detail = self.fetch_soitem_product_qty_by_id(so_fb_id)
        # Kit move matching must use per-child ship/pick keys only; rolled-up parent totals are wrong
        # for comparing each component line to its ordered qty.

        for line in so.order_line:
            sid = line.fishbowl_soitem_id
            if not sid:
                continue
            # Odoo 19 sale.order.line uses product_uom_id (uom.uom), not product_uom.
            sol_uom = line.product_uom_id or line.product_id.uom_id
            sol_rounding = sol_uom.rounding if sol_uom else 0.01

            children = parent_to_children.get(int(sid), [])
            if (
                children
                and not line.fishbowl_skip_procurement
                and self._fishbowl_kit_fully_shipped_by_fishbowl_shipment(
                    children, shipped_by_item_raw, soitem_detail
                )
            ):
                self._fishbowl_cancel_open_moves_and_mark_skipped(line)
                continue

            # Direct qty: Fishbowl row for this soitem only (component UoM; do not use parent kit totals).
            ship_alloc = float(shipped_by_item.get(int(sid), 0) or 0)
            if picked_by_item:
                pick_alloc = float(picked_by_item.get(int(sid), 0) or 0)
            else:
                pick_alloc = ship_alloc
            # Effective qty: walk up kit parents so picks keyed only on the parent line still apply.
            ship_eff = self._effective_fb_qty_for_soitem(int(sid), shipped_by_item, parent_map)
            if picked_by_item:
                pick_eff = self._effective_fb_qty_for_soitem(int(sid), picked_by_item, parent_map)
            else:
                pick_eff = ship_eff

            line_fb_fulfilled = self._effective_fb_line_fulfilled(
                int(sid), fulfilled_soitem_ids, parent_map
            )

            if (
                float_is_zero(ship_alloc, precision_rounding=sol_rounding)
                and float_is_zero(pick_alloc, precision_rounding=sol_rounding)
                and float_is_zero(ship_eff, precision_rounding=sol_rounding)
                and float_is_zero(pick_eff, precision_rounding=sol_rounding)
                and not line_fb_fulfilled
            ):
                continue

            mark_picked = max(ship_eff, pick_eff) > 0 or line_fb_fulfilled

            moves = line.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
                and m.picking_id
                and m.picking_id.picking_type_id.code in ('outgoing', 'internal')
            ).sorted('id')
            outgoing_moves = moves.filtered(
                lambda m: m.picking_id.picking_type_id.code == 'outgoing'
            )
            internal_moves = moves.filtered(
                lambda m: m.picking_id.picking_type_id.code == 'internal'
            )

            if children:
                # Kit SO line: match each stock move to a Fishbowl child soitem by product code; set the
                # parent kit move done when every child meets ship/pick vs ordered (not rolled-up sums).
                self._apply_fishbowl_kit_line_moves(
                    line,
                    moves,
                    outgoing_moves,
                    internal_moves,
                    shipped_by_item_raw,
                    picked_by_item_raw,
                    children,
                    soitem_detail,
                    has_positive_ship,
                    sol_rounding,
                )
            else:
                # Partial SO: shipitem data exists for at least one line — this soitem has no shipped qty.
                # Do not mark outgoing delivery done; pick qty (if any) applies to internal moves only.
                if has_positive_ship and float_is_zero(
                    ship_alloc, precision_rounding=sol_rounding
                ) and float_is_zero(ship_eff, precision_rounding=sol_rounding):
                    for move in outgoing_moves:
                        if move.state in ('done', 'cancel'):
                            continue
                        move._set_quantity_done(0.0)

                # Only push Fishbowl numbers into done qty when this soitem has its own FB rows.
                # If Fishbowl only has parent kit qty, skip _set_quantity_done so we do not wipe component done.
                has_direct_fb_qty = (not float_is_zero(ship_alloc, precision_rounding=sol_rounding)) or (
                    not float_is_zero(pick_alloc, precision_rounding=sol_rounding)
                )
                if has_direct_fb_qty:
                    if has_positive_ship:
                        # Ship totals apply to outgoing; pick totals to internal — avoids putting pick qty on
                        # outgoing when another line is partially shipped on the same SO.
                        if not float_is_zero(ship_alloc, precision_rounding=sol_rounding):
                            remaining = ship_alloc
                            for move in outgoing_moves.sorted('id'):
                                if float_is_zero(remaining, precision_rounding=move.product_uom.rounding):
                                    move._set_quantity_done(0.0)
                                    continue
                                cap = move.product_uom_qty
                                take = min(remaining, cap)
                                move._set_quantity_done(take)
                                remaining -= take
                        if not float_is_zero(pick_alloc, precision_rounding=sol_rounding):
                            remaining = pick_alloc
                            pick_moves = internal_moves if internal_moves else outgoing_moves
                            for move in pick_moves.sorted('id'):
                                if float_is_zero(remaining, precision_rounding=move.product_uom.rounding):
                                    move._set_quantity_done(0.0)
                                    continue
                                cap = move.product_uom_qty
                                take = min(remaining, cap)
                                move._set_quantity_done(take)
                                remaining -= take
                    else:
                        if not float_is_zero(ship_alloc, precision_rounding=sol_rounding):
                            remaining = ship_alloc
                        else:
                            remaining = pick_alloc
                        for move in moves:
                            if float_is_zero(remaining, precision_rounding=move.product_uom.rounding):
                                move._set_quantity_done(0.0)
                                continue
                            cap = move.product_uom_qty
                            take = min(remaining, cap)
                            move._set_quantity_done(take)
                            remaining -= take
                elif line_fb_fulfilled and ship_evidence and not has_direct_fb_qty and not has_positive_ship:
                    # Shipitem rows did not aggregate per soitem at all (schema); not used when any line has
                    # shipitem totals — partial shipments must use ship_alloc only.
                    for move in moves:
                        if move.state in ('done', 'cancel'):
                            continue
                        move._set_quantity_done(move.product_uom_qty)

            if not children:
                self._set_phantom_kit_parent_move_done_if_components_ready(line, moves)
            else:
                parent_moves = moves.filtered(
                    lambda m: m.product_id == line.product_id and not m.bom_line_id
                )
                if parent_moves:
                    pm0 = parent_moves.sorted('id')[0]
                    if float_is_zero(pm0.quantity, precision_rounding=pm0.product_uom.rounding):
                        self._set_phantom_kit_parent_move_done_if_components_ready(line, moves)

            # Set picked on the move (inverse updates move lines). Use move.quantity (Odoo done qty) so
            # kit splits stay consistent with the operations list.
            for move in moves:
                done_qty = move.quantity
                move.picked = bool(
                    mark_picked
                    and not float_is_zero(done_qty, precision_rounding=move.product_uom.rounding)
                )

        # Odoo-only cleanup: lines with no Fishbowl ship rows may ``continue`` early above; still clear
        # phantom kit picks when every component move is already at full demand (SO delivered in Odoo).
        for line in so.order_line:
            if not line.fishbowl_soitem_id:
                continue
            if (
                not line.fishbowl_skip_procurement
                and self._fishbowl_phantom_kit_all_components_at_full_demand(line)
            ):
                self._fishbowl_cancel_open_moves_and_mark_skipped(line)

        # Validate outgoing when shipitem shows shipped qty, or when lines are fulfilled in Fishbowl
        # and we have shipment evidence (tracking / dateShipped). Pick-only (no ship evidence) must
        # not complete outgoing transfers that are still waiting to ship.
        can_validate_outgoing = has_positive_ship or (has_fulfilled_line and ship_evidence)
        if not can_validate_outgoing:
            _post_tracking_chatter_once()
            return

        validate_pickings = pickings.filtered(lambda p: p.picking_type_id.code == 'outgoing')
        if not validate_pickings:
            _post_tracking_chatter_once()
            return

        moves_to_validate = validate_pickings.move_ids.filtered(
            lambda m: m.state not in ('done', 'cancel')
        )
        if not any(
            float_compare(m.quantity, 0, precision_rounding=m.product_uom.rounding) > 0
            for m in moves_to_validate
        ):
            _post_tracking_chatter_once()
            return

        res = validate_pickings.with_context(
            fishbowl_import=True,
            skip_backorder=True,
        ).button_validate()
        if res is not True:
            _logger.warning(
                'Fishbowl fulfillment: button_validate returned non-True (action may need UI): %s', res
            )
        _post_tracking_chatter_once()
