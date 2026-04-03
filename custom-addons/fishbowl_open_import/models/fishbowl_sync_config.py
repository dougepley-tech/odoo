# -*- coding: utf-8 -*-

import logging
import re
from collections import defaultdict
from decimal import Decimal

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

_logger = logging.getLogger(__name__)


def _norm_phone(phone_str):
    if not phone_str or not isinstance(phone_str, str):
        return (phone_str or '').strip() or False
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


class FishbowlSyncConfig(models.Model):
    _name = 'fishbowl.sync.config'
    _description = 'Fishbowl MySQL Sync'
    _rec_name = 'host'

    host = fields.Char(required=True, default='localhost')
    port = fields.Integer(default=3306)
    database = fields.Char(string='Database', required=True)
    user = fields.Char(required=True)
    password = fields.Char()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        required=True,
        ondelete='cascade',
    )
    closed_so_status_names = fields.Text(
        string='Closed SO statuses (Fishbowl sostatus.name)',
        default='Fulfilled\nVoid\nCancelled\nExpired\nHistorical',
        help='Legacy / reference only. Open SO import uses “SO import statuses” instead.',
    )
    import_so_status_names = fields.Text(
        string='SO import statuses (Fishbowl sostatus.name)',
        default='Issued\nIn progress',
        help='Only sales orders whose status name matches one of these lines (case-insensitive) are imported. '
        'One name per line.',
    )
    closed_po_status_names = fields.Text(
        string='Closed PO statuses (Fishbowl postatus.name)',
        default='Closed\nCancelled\nVoid',
        help='One name per line. Used only when “PO import statuses” is empty (legacy exclusion mode).',
    )
    import_po_status_names = fields.Text(
        string='PO import statuses (Fishbowl postatus.name)',
        default='Bid Request\nIssued\nPartial',
        help='Only purchase orders whose status name matches one of these lines (case-insensitive) are '
        'imported. One name per line. '
        'Leave empty to use closed-status exclusion instead.',
    )
    inventory_location_id = fields.Many2one(
        'stock.location',
        string='Default inventory sync location',
        domain="[('usage', 'in', ['internal', 'transit'])]",
        check_company=True,
        help='Used when no fishbowl.location.map matches.',
    )
    fishbowl_memo_so_table_id = fields.Integer(
        string='Fishbowl memo tableId (Sales Order)',
        copy=False,
        help='Optional. In the Fishbowl ``memo`` table, SO memos use a fixed ``tableId`` (e.g. 1012013120). '
        'Set this if memo import returns no rows. Discover with: '
        '``SELECT DISTINCT m.tableId FROM memo m INNER JOIN so ON so.id = m.recordId WHERE so.num = \'…\';``',
    )
    fishbowl_memo_po_table_id = fields.Integer(
        string='Fishbowl memo tableId (Purchase Order)',
        copy=False,
        help='Optional. In the ``memo`` table, PO memos often use a fixed ``tableId`` per database. '
        'Discover with: '
        '``SELECT DISTINCT m.tableId FROM memo m INNER JOIN po ON po.id = m.recordId WHERE po.num = \'…\';``',
    )
    create_credit_return_receipts = fields.Boolean(
        string='Create Odoo incoming receipt for credit returns',
        default=True,
        help='When a Fishbowl SO has Credit Return lines that are not fully received on ``receiptitem``, '
        'create a draft incoming transfer (customer → stock) during SO import. Stored on this config so '
        'the option is not lost when the import wizard moves to the confirm step (OWL drops hidden booleans).',
    )
    credit_return_receipt_when_fishbowl_fully_received = fields.Boolean(
        string='Receipt when Fishbowl shows return fully received',
        default=False,
        help='If Fishbowl ``receiptitem`` already sums to the full ordered qty on each credit return line, '
        'remaining receive qty is zero and no receipt is created by default. Enable this to still create '
        'an Odoo incoming transfer using the **ordered** return qty (for RMA / Odoo-only paperwork). '
        '**SO import** already does this automatically when Credit Return lines are skipped from the order. '
        'Only use when you accept possible double-count risk if goods were already received in Fishbowl.',
    )
    zero_balance_when_fishbowl_paid = fields.Boolean(
        string='Match Odoo total to Fishbowl (paid / header total)',
        default=True,
        help='When enabled, add adjustment line(s) so the Odoo order total matches Fishbowl: '
        '**(1)** paid in full → zero balance in Odoo; **(2)** partial payment → Odoo total reflects '
        'amount still due (header total minus payments from Fishbowl); **(3)** Fishbowl ``so.total`` '
        'when it differs from imported lines (e.g. net **$0** RMA orders). '
        'Stored on this config so the option is not lost when the import wizard moves to the confirm step.',
    )
    fishbowl_paid_when_sopayment_row_exists = fields.Boolean(
        string='Treat SO as paid if any sopayment row exists',
        default=False,
        help='Last resort when MySQL does not expose payment amounts in standard columns. If **any** row '
        'exists in Fishbowl ``sopayment`` / payment views for this SO **and** balance due is zero (or '
        'missing), the import assumes **paid in full**. If Fishbowl exposes a positive balance due, '
        'partial payments are **not** treated as paid in full.',
    )

    _company_uniq = models.Constraint(
        'UNIQUE(company_id)',
        'Only one Fishbowl sync config per company.',
    )

    def _parse_status_lines(self, text):
        if not text:
            return []
        return [ln.strip() for ln in text.replace(',', '\n').splitlines() if ln.strip()]

    def sanitize_import_po_status_names(self):
        """Remove ``Big Request`` lines from the PO import whitelist (legacy typo; not Fishbowl ``postatus``)."""
        default = 'Bid Request\nIssued\nPartial'
        for rec in self:
            txt = (rec.import_po_status_names or '').strip()
            if not txt:
                rec.import_po_status_names = default
                continue
            lines = rec._parse_status_lines(rec.import_po_status_names)
            new_lines = [
                ln
                for ln in lines
                if ' '.join(ln.split()).lower() != 'big request'
            ]
            if new_lines != lines:
                rec.import_po_status_names = '\n'.join(new_lines) if new_lines else default

    def _get_connection(self):
        import pymysql
        return pymysql.connect(
            host=self.host,
            port=self.port or 3306,
            user=self.user,
            password=self.password or '',
            database=self.database,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
        )

    def action_test_connection(self):
        self.ensure_one()
        try:
            conn = self._get_connection()
            conn.ping(reconnect=False)
            conn.close()
        except Exception as e:
            raise UserError(
                'Connection failed: %s\n\nCheck host, port, database, user, and password.' % (e,)
            ) from e
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Connection successful',
                'message': 'Connected to %s@%s:%s/%s.'
                % (self.user, self.host, self.port or 3306, self.database),
                'type': 'success',
                'sticky': False,
            },
        }

    def _fishbowl_header_to_odoo_datetime(self, hdr):
        """Prefer Fishbowl dateIssued, else dateCreated. Returns naive UTC datetime for storage."""
        self.ensure_one()
        if not hdr:
            return None
        raw = hdr.get('dateIssued')
        if raw is None:
            raw = hdr.get('dateCreated')
        if raw is None:
            for key, val in hdr.items():
                if isinstance(key, str) and key.lower() == 'dateissued' and val is not None:
                    raw = val
                    break
        if raw is None:
            for key, val in hdr.items():
                if isinstance(key, str) and key.lower() == 'datecreated' and val is not None:
                    raw = val
                    break
        if raw is None:
            return None
        try:
            return fields.Datetime.to_datetime(raw)
        except (TypeError, ValueError):
            return None

    def apply_odoo_order_dates_from_fishbowl_header(self, order, hdr):
        """Align Order date and Creation date with Fishbowl (ORM blocks create_date on create)."""
        self.ensure_one()
        if not order or not order.id:
            return
        if order._name not in ('sale.order', 'purchase.order'):
            return
        dt = self._fishbowl_header_to_odoo_datetime(hdr)
        if not dt:
            return
        if order._name == 'sale.order':
            self.env.cr.execute(
                """
                UPDATE sale_order
                SET date_order = %s, create_date = %s, write_date = %s
                WHERE id = %s
                """,
                (dt, dt, dt, order.id),
            )
        else:
            self.env.cr.execute(
                """
                UPDATE purchase_order
                SET date_order = %s, create_date = %s, write_date = %s
                WHERE id = %s
                """,
                (dt, dt, dt, order.id),
            )
        order.invalidate_recordset(['date_order', 'create_date', 'write_date'])

    def _closed_so_names_tuple(self):
        names = self._parse_status_lines(self.closed_so_status_names)
        return tuple(names) if names else ('__none__',)

    def _so_import_allowed_names_tuple(self):
        """Normalized (lowercase) Fishbowl sostatus.name values allowed for SO import."""
        names = self._parse_status_lines(self.import_so_status_names)
        return tuple(n.lower() for n in names if n.strip())

    def _closed_po_names_tuple(self):
        names = self._parse_status_lines(self.closed_po_status_names)
        return tuple(names) if names else ('__none__',)

    def _po_import_allowed_names_tuple(self):
        """Normalized (lowercase) Fishbowl postatus.name values allowed for PO import."""
        names = self._parse_status_lines(self.import_po_status_names)
        return tuple(n.lower() for n in names if n.strip())

    @staticmethod
    def _fb_so_hdr_get(hdr, *names):
        """Case-insensitive get from a Fishbowl row dict (PyMySQL)."""
        if not hdr or not names:
            return None
        want = {n.lower() for n in names if n}
        for k, v in hdr.items():
            if isinstance(k, str) and k.lower() in want:
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
                return v
        return None

    def find_odoo_salesperson_user(self, hdr):
        """Map Fishbowl ``sysuser`` (first/last on the SO header) to ``res.users`` (Salesperson).

        Matches primarily on **first name + last name** against ``res.users`` ``name`` (exact
        ilike, ``Last, First``, then both parts substring). Company: config company or no companies.
        """
        self.ensure_one()
        Users = self.env['res.users'].sudo()
        company = self.company_id
        comp_domain = ['|', ('company_ids', 'in', company.id), ('company_ids', '=', False)]
        if not hdr:
            return Users.browse()
        fn = self._fb_so_hdr_get(hdr, 'sales_rep_first_name', 'firstname', 'first_name')
        ln = self._fb_so_hdr_get(hdr, 'sales_rep_last_name', 'lastname', 'last_name')
        fn = ' '.join(str(fn).split()).strip() if fn else ''
        ln = ' '.join(str(ln).split()).strip() if ln else ''
        if not fn and not ln:
            return Users.browse()
        if fn and ln:
            full = '%s %s' % (fn, ln)
            u = Users.search([('name', '=ilike', full)] + comp_domain, limit=1)
            if u:
                return u
            u = Users.search([('name', '=ilike', '%s, %s' % (ln, fn))] + comp_domain, limit=1)
            if u:
                return u
            u = Users.search(
                [('name', 'ilike', fn), ('name', 'ilike', ln)] + comp_domain,
                limit=1,
            )
            if u:
                return u
        if fn and not ln:
            u = Users.search([('name', '=ilike', fn)] + comp_domain, limit=1)
            if u:
                return u
        if ln and not fn:
            u = Users.search([('name', '=ilike', ln)] + comp_domain, limit=1)
            if u:
                return u
        return Users.browse()

    def find_odoo_salesperson_user_by_fishbowl_login(self, hdr):
        """Map Fishbowl ``sysuser.userName`` (login) to ``res.users.login``.

        Fishbowl often stores the rep as a single login string (e.g. ``jackie.whetzler``) while
        ``firstName``/``lastName`` may be empty or formatted differently than ``res.users.name``.
        """
        self.ensure_one()
        Users = self.env['res.users'].sudo()
        company = self.company_id
        comp_domain = ['|', ('company_ids', 'in', company.id), ('company_ids', '=', False)]
        if not hdr:
            return Users.browse()
        raw = self._fb_so_hdr_get(hdr, 'sales_rep_username', 'username', 'userName')
        if not raw:
            return Users.browse()
        login = str(raw).strip()
        if not login:
            return Users.browse()
        u = Users.search([('login', '=', login)] + comp_domain, limit=1)
        if u:
            return u
        u = Users.search([('login', '=ilike', login)] + comp_domain, limit=1)
        if u:
            return u
        # Local part if Fishbowl stores an email in userName
        if '@' in login:
            local = login.split('@', 1)[0].strip()
            if local:
                u = Users.search([('login', '=ilike', local)] + comp_domain, limit=1)
                if u:
                    return u
        return Users.browse()

    def _fishbowl_salesman_login_key(self, hdr):
        """Normalize Fishbowl ``sysuser`` login for channel rules (uppercase, no spaces)."""
        if not hdr:
            return None
        raw = self._fb_so_hdr_get(hdr, 'sales_rep_username', 'username', 'userName')
        if not raw:
            return None
        return ''.join(str(raw).split()).upper() or None

    def apply_odoo_sales_team_and_user_from_fishbowl_header(self, sale_order, hdr):
        """Set ``team_id`` and ``user_id`` on ``sale.order`` from Fishbowl header.

        Channel-only Fishbowl sales reps (by ``sysuser`` login) clear the Odoo salesperson
        and assign the matching ``crm.team`` by name when it exists. Other reps map by
        first/last name to ``res.users`` as in ``find_odoo_salesperson_user``.
        """
        self.ensure_one()
        key = self._fishbowl_salesman_login_key(hdr)
        if key in ('BIGC', 'IAGOFFROAD', 'AMAZON', 'AMAZONFBA'):
            team_name = {
                'BIGC': 'BigCommerce Performance',
                'IAGOFFROAD': 'BigCommerce Off-Road',
                'AMAZON': 'Amazon',
                'AMAZONFBA': 'Amazon',
            }[key]
            Team = self.env['crm.team'].sudo()
            company = self.company_id
            team = Team.search(
                [
                    ('name', '=ilike', team_name),
                    '|',
                    ('company_id', '=', company.id),
                    ('company_id', '=', False),
                ],
                limit=1,
            )
            vals = {'user_id': False}
            if team:
                vals['team_id'] = team.id
            else:
                vals['team_id'] = False
            sale_order.write(vals)
            return
        sp = self.find_odoo_salesperson_user_by_fishbowl_login(hdr)
        if not sp:
            sp = self.find_odoo_salesperson_user(hdr)
        if sp:
            sale_order.write({'user_id': sp.id})
        else:
            # Avoid leaving the import user as salesperson when Fishbowl rep does not map.
            sale_order.write({'user_id': False})

    def fetch_open_sales_orders(self, allowed_status_names=None):
        """Return list of SO header dicts from Fishbowl (status whitelist only).

        When supported by the schema, includes ``sales_rep_first_name``, ``sales_rep_last_name``,
        and ``sales_rep_username`` from ``sysuser`` (``so.salesRepId`` or ``so.salesmanId``).

        :param allowed_status_names: optional tuple of normalized (lowercase) sostatus.name values.
            If None, use ``import_so_status_names`` on this config.
        """
        self.ensure_one()
        if allowed_status_names is None:
            allowed = self._so_import_allowed_names_tuple()
        else:
            allowed = allowed_status_names
        if not allowed:
            return []
        placeholders = ','.join(['%s'] * len(allowed))
        conn = self._get_connection()
        base_cols = """so.id, so.num, so.customerId, so.statusId, ss.name AS status_name,
                           so.dateIssued, so.dateCreated, so.billToName, so.currencyId,
                           so.customerPO, so.note"""
        # Each entry must be a str (not a 1-tuple) or cur.execute fails and we swallow the error.
        # Try userName then username column names (Fishbowl schema varies).
        rep_u = """
                       u.firstName AS sales_rep_first_name,
                       u.lastName AS sales_rep_last_name,
                       u.userName AS sales_rep_username
"""
        rep_l = """
                       u.firstName AS sales_rep_first_name,
                       u.lastName AS sales_rep_last_name,
                       u.username AS sales_rep_username
"""
        sql_variants = [
            """
                SELECT {cols},
{rep}
                FROM so so
                INNER JOIN sostatus ss ON so.statusId = ss.id
                LEFT JOIN sysuser u ON u.id = so.salesRepId
                WHERE LOWER(TRIM(ss.name)) IN ({ph})
                ORDER BY so.dateCreated, so.id
                """.format(cols=base_cols, rep=rep_u, ph=placeholders),
            """
                SELECT {cols},
{rep}
                FROM so so
                INNER JOIN sostatus ss ON so.statusId = ss.id
                LEFT JOIN sysuser u ON u.id = so.salesmanId
                WHERE LOWER(TRIM(ss.name)) IN ({ph})
                ORDER BY so.dateCreated, so.id
                """.format(cols=base_cols, rep=rep_u, ph=placeholders),
            """
                SELECT {cols},
{rep}
                FROM so so
                INNER JOIN sostatus ss ON so.statusId = ss.id
                LEFT JOIN sysuser u ON u.id = so.salesRepId
                WHERE LOWER(TRIM(ss.name)) IN ({ph})
                ORDER BY so.dateCreated, so.id
                """.format(cols=base_cols, rep=rep_l, ph=placeholders),
            """
                SELECT {cols},
{rep}
                FROM so so
                INNER JOIN sostatus ss ON so.statusId = ss.id
                LEFT JOIN sysuser u ON u.id = so.salesmanId
                WHERE LOWER(TRIM(ss.name)) IN ({ph})
                ORDER BY so.dateCreated, so.id
                """.format(cols=base_cols, rep=rep_l, ph=placeholders),
            """
                SELECT {cols}
                FROM so so
                INNER JOIN sostatus ss ON so.statusId = ss.id
                WHERE LOWER(TRIM(ss.name)) IN ({ph})
                ORDER BY so.dateCreated, so.id
                """.format(cols=base_cols, ph=placeholders),
        ]
        rows = []
        try:
            with conn.cursor() as cur:
                for sql in sql_variants:
                    try:
                        cur.execute(sql, allowed)
                        rows = list(cur.fetchall())
                        break
                    except Exception:
                        continue
        finally:
            conn.close()
        return rows

    def _fetch_so_line_total_sum_by_so_ids(self, so_ids):
        """Sum of ``soitem.totalPrice`` per SO (Fishbowl header ``so.total`` can differ from line sum)."""
        if not so_ids:
            return {}
        ph = ','.join(['%s'] * len(so_ids))
        conn = self._get_connection()
        out = {}
        try:
            with conn.cursor() as cur:
                for sql in (
                    f'SELECT soId, SUM(totalPrice) AS line_sum FROM soitem WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, SUM(totalPrice) AS line_sum FROM soitem WHERE soid IN ({ph}) GROUP BY soid',
                ):
                    try:
                        cur.execute(sql, tuple(so_ids))
                        for row in cur.fetchall():
                            sid = None
                            ls = None
                            for k, v in (row or {}).items():
                                lk = str(k).lower()
                                if lk == 'soid':
                                    sid = int(v) if v is not None else None
                                elif lk == 'line_sum':
                                    ls = v
                            if sid is not None:
                                try:
                                    out[sid] = float(ls or 0)
                                except (TypeError, ValueError):
                                    out[sid] = 0.0
                        if out:
                            break
                    except Exception:
                        out = {}
                        continue
        finally:
            conn.close()
        return out

    def _fetch_so_payment_total_sum_by_so_ids(self, so_ids):
        """Best-effort sum of payments per SO from Fishbowl payment storage.

        When ``totalpaidview`` exposes ``soId`` + ``amount`` (typical Fishbowl schema), that aggregate
        is **authoritative** for those SO ids: we do not merge higher values from other queries
        (JOIN-by-soNum variants could inflate totals vs partial payments).

        For SO ids without a ``totalpaidview`` row, we still merge the maximum across other strategies
        (``payment`` / ``sopayment`` / ``paymentview`` by ``soNum``, etc.).
        """
        if not so_ids:
            return {}
        ph = ','.join(['%s'] * len(so_ids))
        tpl_ids = tuple(int(x) for x in so_ids)
        conn = self._get_connection()
        out = {}
        tpv_locked = set()
        try:
            with conn.cursor() as cur:
                for sql in (
                    f'SELECT soId, COALESCE(SUM(amount), 0) AS paid FROM totalpaidview WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, COALESCE(SUM(amount), 0) AS paid FROM totalpaidview WHERE soid IN ({ph}) GROUP BY soid',
                    # Some Fishbowl builds key the view by order id under ``orderId`` (same value as ``so.id``).
                    f'SELECT orderId AS soId, COALESCE(SUM(amount), 0) AS paid FROM totalpaidview WHERE orderId IN ({ph}) GROUP BY orderId',
                    f'SELECT orderid AS soId, COALESCE(SUM(amount), 0) AS paid FROM totalpaidview WHERE orderid IN ({ph}) GROUP BY orderid',
                ):
                    try:
                        cur.execute(sql, tpl_ids)
                        rows = cur.fetchall()
                    except Exception:
                        continue
                    if not rows:
                        continue
                    for row in rows or []:
                        d = dict(row or {})
                        sid = None
                        pv = None
                        for k, v in d.items():
                            lk = str(k).lower()
                            if lk == 'soid':
                                sid = int(v) if v is not None else None
                            elif lk == 'paid':
                                pv = v
                        if sid is None:
                            continue
                        if pv is None:
                            for k, v in d.items():
                                if str(k).lower() == 'soid':
                                    continue
                                try:
                                    pv = float(v)
                                    break
                                except (TypeError, ValueError):
                                    continue
                        try:
                            val = float(pv or 0)
                        except (TypeError, ValueError):
                            val = 0.0
                        out[sid] = val
                        tpv_locked.add(sid)
                    break
            with conn.cursor() as cur:
                for sql in (
                    # Fishbowl ``paymentview`` often links by ``soNum`` (varchar), not ``soId``.
                    f'SELECT so.id AS soId, COALESCE(SUM(pv.amount), 0) AS paid FROM so '
                    f'INNER JOIN paymentview pv ON TRIM(pv.soNum) = TRIM(so.num) WHERE so.id IN ({ph}) '
                    f'GROUP BY so.id',
                    f'SELECT soId, COALESCE(SUM(amount), 0) AS paid FROM payment WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, COALESCE(SUM(amount), 0) AS paid FROM payment WHERE soid IN ({ph}) GROUP BY soid',
                    f'SELECT soId, COALESCE(SUM(totalAmount), 0) AS paid FROM payment WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, COALESCE(SUM(totalAmount), 0) AS paid FROM payment WHERE soid IN ({ph}) GROUP BY soid',
                    f'SELECT soId, COALESCE(SUM(amount), 0) AS paid FROM sopayment WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, COALESCE(SUM(amount), 0) AS paid FROM sopayment WHERE soid IN ({ph}) GROUP BY soid',
                    f'SELECT soId, COALESCE(SUM(totalAmount), 0) AS paid FROM sopayment WHERE soId IN ({ph}) GROUP BY soId',
                    f'SELECT soid, COALESCE(SUM(totalAmount), 0) AS paid FROM sopayment WHERE soid IN ({ph}) GROUP BY soid',
                    # Amount often lives on ``payment``; ``sopayment`` only links SO ↔ payment (BigCommerce / card).
                    f'SELECT sp.soId, COALESCE(SUM(p.amount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.paymentId WHERE sp.soId IN ({ph}) GROUP BY sp.soId',
                    f'SELECT sp.soid, COALESCE(SUM(p.amount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.paymentId WHERE sp.soid IN ({ph}) GROUP BY sp.soid',
                    f'SELECT sp.soId, COALESCE(SUM(p.amount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.paymentid WHERE sp.soId IN ({ph}) GROUP BY sp.soId',
                    f'SELECT sp.soId, COALESCE(SUM(p.amount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.payment_id WHERE sp.soId IN ({ph}) GROUP BY sp.soId',
                    f'SELECT sp.soId, COALESCE(SUM(p.totalAmount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.paymentId WHERE sp.soId IN ({ph}) GROUP BY sp.soId',
                    f'SELECT sp.soId, COALESCE(SUM(p.totalAmount), 0) AS paid FROM sopayment sp '
                    f'INNER JOIN payment p ON p.id = sp.paymentid WHERE sp.soId IN ({ph}) GROUP BY sp.soId',
                ):
                    try:
                        cur.execute(sql, tuple(so_ids))
                        for row in cur.fetchall():
                            sid = None
                            pv = None
                            for k, v in (row or {}).items():
                                lk = str(k).lower()
                                if lk == 'soid':
                                    sid = int(v) if v is not None else None
                                elif lk == 'paid':
                                    pv = v
                            if sid is None:
                                continue
                            if sid in tpv_locked:
                                continue
                            try:
                                val = float(pv or 0)
                            except (TypeError, ValueError):
                                val = 0.0
                            prev = float(out.get(sid, 0.0))
                            if val > prev:
                                out[sid] = val
                    except Exception:
                        continue
        finally:
            conn.close()
        # Row-level sums: GROUP BY can miss amounts when Fishbowl uses a non-standard column name
        # (e.g. BigCommerce card rows in ``sopayment``). Same pattern as scanning credit-return lines.
        raw = self._fetch_so_payment_total_sum_by_raw_rows(so_ids)
        for sid, val in raw.items():
            if int(sid) in tpv_locked:
                continue
            try:
                valf = float(val or 0)
            except (TypeError, ValueError):
                continue
            prev = float(out.get(int(sid), 0.0))
            if valf > prev:
                out[int(sid)] = valf
        # Brute ``SELECT *`` on physical tables only. ``paymentview`` / ``totalpaidview`` are already
        # covered by JOINs above and raw rows; re-scanning them tripled work per import.
        for tbl in ('sopayment', 'payment'):
            brute = self._fetch_so_payment_brute_currency_sums_from_table(tbl, so_ids)
            for sid, val in brute.items():
                if int(sid) in tpv_locked:
                    continue
                try:
                    valf = float(val or 0)
                except (TypeError, ValueError):
                    continue
                prev = float(out.get(int(sid), 0.0))
                if valf > prev:
                    out[int(sid)] = valf
        return out

    _FB_PAYMENT_TABLE_WHITELIST = frozenset(
        {'sopayment', 'totalpaidview', 'paymentview', 'payment'}
    )

    def _fishbowl_so_id_to_num_map(self, so_ids):
        """Map Fishbowl ``so.id`` → ``so.num`` (for views keyed by ``soNum``)."""
        if not so_ids:
            return {}
        ph = ','.join(['%s'] * len(so_ids))
        conn = self._get_connection()
        out = {}
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT id, num FROM so WHERE id IN ({ph})', tuple(int(x) for x in so_ids))
                for row in cur.fetchall() or []:
                    rid = row.get('id')
                    if rid is not None:
                        out[int(rid)] = (row.get('num') or '').strip()
        except Exception as e:
            _logger.debug('Fishbowl so id→num: %s', e)
        finally:
            conn.close()
        return out

    def _fetch_so_payment_brute_currency_sums_from_table(self, table, so_ids):
        """Sum payments by taking, per row, the largest plausible currency float from ``table``.

        Fishbowl builds differ: some use ``sopayment``/``payment`` tables; others only expose
        ``totalpaidview`` / ``paymentview`` (no ``sopayment`` in MySQL). Column names vary, so we scan.

        ``paymentview`` / some builds of ``totalpaidview`` use ``soNum`` (matches ``so.num``), not ``soId``.
        """
        if not so_ids or table not in self._FB_PAYMENT_TABLE_WHITELIST:
            return {}
        if table in ('paymentview', 'totalpaidview'):
            return self._fetch_so_payment_brute_from_sonum_view(table, so_ids)
        ph = ','.join(['%s'] * len(so_ids))
        tpl = tuple(int(x) for x in so_ids)
        sums = defaultdict(float)
        skip_substrings = ('date', 'time')
        skip_exact = {'soid', 'id'}
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                for sql in (
                    f'SELECT * FROM {table} WHERE soId IN ({ph})',
                    f'SELECT * FROM {table} WHERE soid IN ({ph})',
                ):
                    try:
                        cur.execute(sql, tpl)
                        rows = cur.fetchall()
                    except Exception:
                        continue
                    if not rows:
                        continue
                    for row in rows:
                        d = dict(row or {})
                        sid = None
                        for k, v in d.items():
                            if k and str(k).lower() == 'soid' and v is not None:
                                try:
                                    sid = int(v)
                                except (TypeError, ValueError):
                                    sid = None
                                break
                        if sid is None:
                            continue
                        row_max = 0.0
                        for k, v in d.items():
                            if not k or v is None:
                                continue
                            kl = str(k).lower()
                            if kl in skip_exact:
                                continue
                            if any(s in kl for s in skip_substrings):
                                continue
                            if kl == 'id' or kl.endswith('id'):
                                continue
                            try:
                                fv = float(v)
                            except (TypeError, ValueError):
                                continue
                            if fv <= 0 or fv > 1e8:
                                continue
                            if fv > row_max:
                                row_max = fv
                        if row_max > 0:
                            sums[sid] += row_max
                    break
        finally:
            conn.close()
        return dict(sums)

    def _fetch_so_payment_brute_from_sonum_view(self, table, so_ids):
        """Sum ``amount`` from ``paymentview`` / ``totalpaidview`` rows matched by ``soNum`` = ``so.num``."""
        id_to_num = self._fishbowl_so_id_to_num_map(so_ids)
        num_to_id = {}
        for i in so_ids:
            n = id_to_num.get(int(i))
            if n:
                num_to_id[n] = int(i)
        nums = sorted(num_to_id.keys())
        if not nums:
            return {}
        ph = ','.join(['%s'] * len(nums))
        tpl = tuple(nums)
        sums = defaultdict(float)
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(f'SELECT * FROM {table} WHERE soNum IN ({ph})', tpl)
                    rows = cur.fetchall()
                except Exception:
                    return {}
                for row in rows or []:
                    d = dict(row)
                    sn = (d.get('soNum') or '').strip()
                    sid = num_to_id.get(sn)
                    if sid is None:
                        continue
                    amt = d.get('amount')
                    if amt is not None:
                        try:
                            sums[sid] += float(amt)
                        except (TypeError, ValueError):
                            continue
        finally:
            conn.close()
        return dict(sums)

    def _fetch_so_payment_total_sum_by_raw_rows(self, so_ids):
        """Sum ``sopayment`` / ``payment`` rows per SO using ``SELECT *`` + amount heuristics.

        Aggregates with fixed column names can return 0 while individual rows hold the real amounts.
        We take **max(sopayment sum, payment sum)** per SO so duplicate linkage across tables does not
        double-count the same payment.
        """
        if not so_ids:
            return {}
        ph = ','.join(['%s'] * len(so_ids))
        tpl = tuple(int(x) for x in so_ids)

        def _soid_from_row(row):
            d = dict(row or {})
            for k, v in d.items():
                if k and str(k).lower() == 'soid' and v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return None
            return None

        def _amount_from_row(d):
            preferred = (
                'amount',
                'totalAmount',
                'total_amount',
                'paymentAmount',
                'paymentamount',
                'amountPaid',
                'amountpaid',
                'paidTotal',
                'paidtotal',
            )
            for name in preferred:
                for k, v in d.items():
                    if k is None or v is None:
                        continue
                    if str(k).lower() == name.lower():
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
            for k, v in d.items():
                if k is None or v is None:
                    continue
                kl = str(k).lower()
                if 'amount' in kl and 'tax' not in kl and 'discount' not in kl:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            for k, v in d.items():
                if k is None or v is None:
                    continue
                if str(k).lower() == 'total':
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None
            return None

        def _accumulate(table_variants):
            acc = defaultdict(float)
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    for sql in table_variants:
                        try:
                            cur.execute(sql, tpl)
                            rows = cur.fetchall()
                        except Exception:
                            continue
                        if not rows:
                            continue
                        for row in rows:
                            sid = _soid_from_row(row)
                            if sid is None:
                                continue
                            amt = _amount_from_row(dict(row))
                            if amt is not None:
                                acc[sid] += amt
                        break
            finally:
                conn.close()
            return acc

        sums_sp = _accumulate(
            (
                f'SELECT * FROM sopayment WHERE soId IN ({ph})',
                f'SELECT * FROM sopayment WHERE soid IN ({ph})',
            )
        )
        sums_py = _accumulate(
            (
                f'SELECT * FROM payment WHERE soId IN ({ph})',
                f'SELECT * FROM payment WHERE soid IN ({ph})',
            )
        )
        out = {}
        for sid in so_ids:
            i = int(sid)
            m = max(
                float(sums_sp.get(i, 0.0)),
                float(sums_py.get(i, 0.0)),
            )
            if m > 0:
                out[i] = m
        return out

    def _fishbowl_so_row_order_total(self, row):
        """Best-effort order total from a Fishbowl ``so`` row (column names differ by version).

        **Signed** final totals (e.g. ``so.total``) must be returned even when **negative** (misc
        credits / net credit balance). Older code only accepted ``f >= 0`` on ``total``, which
        wrongly fell through to ``subTotal`` / ``totalPrice`` and inflated alignment targets.

        Many Fishbowl databases use **only** ``totalPrice`` / ``subTotal`` on ``so`` (no ``total``
        column). Some builds omit header totals entirely—then callers fall back to ``soitem`` sums.
        """
        if not row:
            return None
        # Final order total columns: return first match including **negative** (credit) balances.
        signed_final = (
            'total',
            'grandTotal',
            'orderTotal',
            'soTotal',
            'totalAmount',
        )
        for name in signed_final:
            for k, v in row.items():
                if k is None or v is None:
                    continue
                if str(k).lower() == name.lower():
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
        # Mid-pipeline / gross columns: only use when non-negative (avoid flipping sign).
        preferred_positive = (
            'totalPrice',
            'subTotal',
            'totalMoney',
        )
        for name in preferred_positive:
            for k, v in row.items():
                if k is None or v is None:
                    continue
                if str(k).lower() == name.lower():
                    try:
                        f = float(v)
                        if f >= 0:
                            return f
                    except (TypeError, ValueError):
                        continue
        best = None
        for k, v in row.items():
            if k is None or v is None:
                continue
            kl = str(k).lower()
            if 'total' not in kl:
                continue
            if 'tax' in kl or 'discount' in kl:
                continue
            if kl == 'totalcost':
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f <= 0:
                continue
            if best is None or f > best:
                best = f
        return best

    def _fishbowl_so_row_payment_total_like(self, row):
        """Best-effort payment amount from ``so`` row when such columns exist.

        Many Fishbowl ``so`` tables have **no** payment columns at all (only ``subTotal`` /
        ``totalPrice``). Paid-in-full is then inferred from ``sopayment`` / ``payment`` / line sums only.
        """
        if not row:
            return None
        for name in (
            'paymentTotal',
            'paymenttotal',
            'amountPaid',
            'amountpaid',
            'paidTotal',
            'paidtotal',
            'paidAmount',
            'paidamount',
            'paid',
        ):
            for k, v in row.items():
                if k is None or v is None:
                    continue
                if str(k).lower() == name.lower():
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
        for k, v in row.items():
            if k is None or v is None:
                continue
            kl = str(k).lower()
            if 'payment' in kl and 'total' in kl.replace('_', ''):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    def enrich_so_headers_payment_flags(self, headers):
        """Set ``fishbowl_paid_in_full`` and ``fishbowl_amount_paid`` on each header dict.

        Uses header payment fields vs order total when present (rounded, 2¢ tolerance). Order total
        comes from ``so.totalPrice`` / ``subTotal`` / etc., or **sum of** ``soitem.totalPrice``.
        Many Fishbowl schemas have **no** payment or balance columns on ``so``—then only
        ``sopayment`` / ``payment`` / ``totalpaidview`` rows can show paid-in-full. If the SO query
        fails, every header is still set to ``fishbowl_paid_in_full = False`` and amount paid from MySQL
        sums only.
        """
        self.ensure_one()
        for h in headers or []:
            h['fishbowl_paid_in_full'] = False
            h['fishbowl_header_total'] = None
            h['fishbowl_amount_paid'] = 0.0
        if not headers:
            return
        ids = []
        for h in headers:
            hid = h.get('id')
            if hid is not None:
                ids.append(int(hid))
        if not ids:
            return
        ph = ','.join(['%s'] * len(ids))
        conn = self._get_connection()
        rows_by_id = {}
        try:
            with conn.cursor() as cur:
                # ``SELECT *`` avoids Error 1054 when ``total`` / ``paymentTotal`` names differ by version.
                sql_variants = (
                    f'SELECT * FROM so WHERE id IN ({ph})',
                    f"SELECT id, paymentTotal, total, balanceDue, amountPaid, paidTotal FROM so WHERE id IN ({ph})",
                    f"SELECT id, paymentTotal, total, balanceDue, amountPaid FROM so WHERE id IN ({ph})",
                    f"SELECT id, paymentTotal, total, balanceDue FROM so WHERE id IN ({ph})",
                    f"SELECT id, paymentTotal, total FROM so WHERE id IN ({ph})",
                )
                for sql in sql_variants:
                    try:
                        cur.execute(sql, tuple(ids))
                        for row in cur.fetchall():
                            rid = row.get('id')
                            if rid is not None:
                                rows_by_id[int(rid)] = row
                        if rows_by_id:
                            break
                    except Exception:
                        rows_by_id = {}
                        continue
        finally:
            conn.close()

        line_sums = self._fetch_so_line_total_sum_by_so_ids(ids)
        payment_sums = self._fetch_so_payment_total_sum_by_so_ids(ids)

        def _apply_fishbowl_amount_paid_to_headers():
            for h in headers or []:
                hid = h.get('id')
                if hid is None:
                    continue
                v = payment_sums.get(int(hid))
                h['fishbowl_amount_paid'] = float(v) if v is not None else 0.0

        if not rows_by_id:
            _apply_fishbowl_amount_paid_to_headers()
            return

        def _col(row, *names):
            for n in names:
                for k, v in row.items():
                    if k and str(k).lower() == n.lower():
                        return v
            return None

        def _col_first(row, *names):
            for n in names:
                v = _col(row, n)
                if v is not None:
                    return v
            return None

        def _payment_covers(pay, need):
            """True when ``pay`` covers ``need`` after currency rounding to cents."""
            try:
                pf = float_round(float(pay or 0), precision_rounding=0.01)
                nf = float_round(float(need or 0), precision_rounding=0.01)
            except (TypeError, ValueError):
                return False
            if nf <= 0:
                return False
            return float_compare(pf, nf, precision_rounding=0.01) >= 0

        prec = 0.01

        for h in headers:
            hid = h.get('id')
            if hid is None:
                continue
            row = rows_by_id.get(int(hid))
            if not row:
                continue
            totf = self._fishbowl_so_row_order_total(row)
            if totf is None:
                ls = line_sums.get(int(hid))
                if ls is not None and ls > 0:
                    totf = float(ls)
                else:
                    continue
            try:
                totf = float(totf)
            except (TypeError, ValueError):
                continue
            h['fishbowl_header_total'] = float_round(totf, precision_rounding=0.01)
            # Net-zero / credit header (e.g. Sale + Credit Return): no payment to compare; alignment
            # uses ``fishbowl_header_total`` so Odoo can still match $0.
            if totf <= 0:
                continue
            pt = self._fishbowl_so_row_payment_total_like(row)
            if pt is None:
                pt = _col(row, 'paymentTotal', 'paymenttotal')
            try:
                ptf = float(pt or 0)
            except (TypeError, ValueError):
                ptf = 0.0
            line_sum = line_sums.get(int(hid))
            paid = _payment_covers(ptf, totf)
            if not paid and line_sum is not None and line_sum > 0:
                # Only compare to line sum when it matches header total (tax/rounding); not when they
                # diverge materially (partial payment vs understated lines).
                if self._fishbowl_line_total_matches_header_total(totf, line_sum, prec):
                    paid = _payment_covers(ptf, line_sum)
            if not paid:
                for ap_name in (
                    'amountPaid',
                    'amountpaid',
                    'paidTotal',
                    'paidtotal',
                    'paid',
                    'paidAmount',
                    'paidamount',
                ):
                    apv = _col(row, ap_name)
                    if apv is None:
                        continue
                    try:
                        apf = float(apv)
                    except (TypeError, ValueError):
                        continue
                    line_ok = (
                        line_sum is not None
                        and line_sum > 0
                        and self._fishbowl_line_total_matches_header_total(totf, line_sum, prec)
                        and _payment_covers(apf, line_sum)
                    )
                    if _payment_covers(apf, totf) or line_ok:
                        paid = True
                        break
            if not paid:
                bd = _col_first(
                    row,
                    'balanceDue',
                    'balancedue',
                    'balance',
                    'remainingAmount',
                    'remainingamount',
                    'amountDue',
                    'amountdue',
                )
                try:
                    bdf = float(bd) if bd is not None else None
                except (TypeError, ValueError):
                    bdf = None
                if bdf is not None and float_compare(
                    float_round(bdf, precision_rounding=0.01),
                    0.02,
                    precision_rounding=0.01,
                ) <= 0:
                    paid = True
            if not paid:
                ps = payment_sums.get(int(hid))
                if ps is not None:
                    if _payment_covers(ps, totf):
                        paid = True
                    elif (
                        line_sum is not None
                        and line_sum > 0
                        and self._fishbowl_line_total_matches_header_total(totf, line_sum, prec)
                        and _payment_covers(ps, line_sum)
                    ):
                        paid = True
            if not paid:
                paid = self._so_row_heuristic_paid_in_full(row, totf)
            if not paid and getattr(self, 'fishbowl_paid_when_sopayment_row_exists', False):
                if self._so_has_any_sopayment_row(int(hid)):
                    paid = False
                    bd = _col_first(
                        row,
                        'balanceDue',
                        'balancedue',
                        'balance',
                        'remainingAmount',
                        'remainingamount',
                        'amountDue',
                        'amountdue',
                    )
                    try:
                        bdf = float(bd) if bd is not None else None
                    except (TypeError, ValueError):
                        bdf = None
                    if bdf is None:
                        paid = True
                    elif float_compare(
                        float_round(bdf, precision_rounding=prec),
                        0.02,
                        precision_rounding=prec,
                    ) <= 0:
                        paid = True
            h['fishbowl_paid_in_full'] = paid

        _apply_fishbowl_amount_paid_to_headers()

    def _fishbowl_line_total_matches_header_total(self, totf, line_sum, prec=0.01):
        """Whether ``soitem`` line sum is close enough to header total to compare payment against it.

        When header total and line sum differ a lot (partial payments, incomplete line data, etc.),
        comparing paid amount only to ``line_sum`` can wrongly mark the order paid in full (e.g. $60k
        paid vs $60k lines while the Fishbowl header total is still $89k).
        """
        if line_sum is None or totf is None:
            return False
        try:
            t = float(totf)
            ls = float(line_sum)
        except (TypeError, ValueError):
            return False
        if t <= 0 or ls <= 0:
            return False
        diff = abs(float_round(t - ls, precision_rounding=prec))
        # Allow tax / rounding drift: 1% of order or $0.02, whichever is larger (capped at $500).
        tol = min(500.0, max(0.02, float_round(t * 0.01, precision_rounding=prec)))
        return float_compare(diff, tol, precision_rounding=prec) <= 0

    def _so_row_heuristic_paid_in_full(self, full, totf):
        """True when ``so`` has a payment-like float column covering ``totf`` (non-standard column names)."""
        if not full or totf <= 0:
            return False
        prec = 0.01
        nt = float_round(totf, precision_rounding=prec)
        for k, v in full.items():
            if k is None or v is None:
                continue
            kl = str(k).lower()
            if 'date' in kl or 'time' in kl or 'memo' in kl:
                continue
            if kl in ('id', 'soid', 'customerid', 'statusid', 'currencyid', 'taxrateid', 'paymentmethodid'):
                continue
            if 'unpaid' in kl:
                continue
            if 'balance' in kl and 'due' not in kl and kl != 'balancedue':
                continue
            if 'paid' in kl:
                pass
            elif 'payment' in kl and 'total' in kl.replace('_', ''):
                pass
            elif kl in ('amountpaid', 'paidtotal'):
                pass
            else:
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                continue
            if vf <= 0 or vf > totf * 3 + 50.0:
                continue
            if float_compare(float_round(vf, precision_rounding=prec), nt, precision_rounding=prec) >= 0:
                return True
        return False

    def _so_has_any_sopayment_row(self, so_id):
        """True if any payment-related row exists for this SO (``sopayment`` or Fishbowl payment views)."""
        sid = int(so_id)
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                for table in ('sopayment',):
                    for sql in (
                        f'SELECT 1 FROM {table} WHERE soId = %s LIMIT 1',
                        f'SELECT 1 FROM {table} WHERE soid = %s LIMIT 1',
                    ):
                        try:
                            cur.execute(sql, (sid,))
                            if cur.fetchone():
                                return True
                        except Exception:
                            continue
                num = self._fishbowl_so_id_to_num_map([sid]).get(sid)
                if num:
                    for table in ('paymentview', 'totalpaidview'):
                        if table not in self._FB_PAYMENT_TABLE_WHITELIST:
                            continue
                        try:
                            cur.execute(f'SELECT 1 FROM {table} WHERE soNum = %s LIMIT 1', (num,))
                            if cur.fetchone():
                                return True
                        except Exception:
                            continue
        finally:
            conn.close()
        return False

    def refresh_so_header_payment_flags_from_mysql(self, hdr):
        """Re-read one ``so`` row with ``SELECT *`` and refresh payment flags on ``hdr``.

        Fishbowl versions use different column names; batch queries only select a few columns.
        This runs at SO import **apply** time so paid-in-full detection matches the live DB row.
        """
        self.ensure_one()
        so_id = hdr.get('id')
        if so_id is None:
            return
        conn = self._get_connection()
        full = {}
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM so WHERE id = %s', (int(so_id),))
                row = cur.fetchone()
                if row:
                    full = dict(row)
        except Exception as e:
            _logger.debug('Fishbowl SELECT * so id=%s: %s', so_id, e)
        finally:
            conn.close()
        if not full:
            return

        def _flt(row, *keys):
            for key in keys:
                for k, v in row.items():
                    if k is None:
                        continue
                    if str(k).lower() == key.lower():
                        if v is None:
                            break
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            break
            return None

        line_sum_cached = self._fetch_so_line_total_sum_by_so_ids([int(so_id)]).get(int(so_id))
        totf = self._fishbowl_so_row_order_total(full)
        # Do not replace a **negative** header total with a positive soitem sum (credit / misc lines).
        if totf is None:
            if line_sum_cached is not None and float(line_sum_cached) > 0:
                totf = float(line_sum_cached)
        elif float_compare(totf, 0.0, precision_rounding=0.01) == 0:
            if line_sum_cached is not None and float(line_sum_cached) > 0:
                totf = float(line_sum_cached)
        totf = float(totf or 0)
        if totf:
            hdr['fishbowl_header_total'] = float_round(totf, precision_rounding=0.01)

        ptf = self._fishbowl_so_row_payment_total_like(full)
        if ptf is None:
            ptf = _flt(
                full,
                'paymenttotal',
                'paymentTotal',
                'payment_total',
                'amountpaid',
                'amountPaid',
                'paidtotal',
                'paidTotal',
                'paidamount',
                'paidAmount',
            )
        if ptf is None:
            for k, v in full.items():
                if v is None or k is None:
                    continue
                kl = str(k).lower()
                if 'payment' in kl and 'total' in kl.replace('_', ''):
                    try:
                        ptf = float(v)
                        break
                    except (TypeError, ValueError):
                        continue
        # Some Fishbowl builds expose the paid amount in a column literally named ``paid``.
        if ptf is None:
            pv = _flt(full, 'paid')
            if pv is not None:
                ptf = pv

        bdf = _flt(
            full,
            'balancedue',
            'balanceDue',
            'balance_due',
            'remainingamount',
            'remainingAmount',
            'amountdue',
            'amountDue',
            'balance',
        )

        prec = 0.01
        paid = False
        if totf > 0:
            if ptf is not None and float_compare(
                float_round(ptf, precision_rounding=prec),
                float_round(totf, precision_rounding=prec),
                precision_rounding=prec,
            ) >= 0:
                paid = True
            elif bdf is not None and float_compare(
                float_round(bdf, precision_rounding=prec),
                0.02,
                precision_rounding=prec,
            ) <= 0:
                paid = True

        ps = None
        if not paid and totf > 0:
            ps = self._fetch_so_payment_total_sum_by_so_ids([int(so_id)]).get(int(so_id))
            if ps is not None and float_compare(
                float_round(float(ps), precision_rounding=prec),
                float_round(totf, precision_rounding=prec),
                precision_rounding=prec,
            ) >= 0:
                paid = True
            if not paid and ps is not None:
                if line_sum_cached is None:
                    line_sum_cached = self._fetch_so_line_total_sum_by_so_ids([int(so_id)]).get(
                        int(so_id)
                    )
                if line_sum_cached is not None and float(line_sum_cached) > 0:
                    if self._fishbowl_line_total_matches_header_total(totf, line_sum_cached, prec):
                        if float_compare(
                            float_round(float(ps), precision_rounding=prec),
                            float_round(float(line_sum_cached), precision_rounding=prec),
                            precision_rounding=prec,
                        ) >= 0:
                            paid = True

        if not paid and totf > 0:
            paid = self._so_row_heuristic_paid_in_full(full, totf)

        if not paid and totf > 0 and getattr(self, 'fishbowl_paid_when_sopayment_row_exists', False):
            if self._so_has_any_sopayment_row(int(so_id)):
                paid = False
                if bdf is None:
                    paid = True
                elif float_compare(
                    float_round(float(bdf), precision_rounding=prec),
                    0.02,
                    precision_rounding=prec,
                ) <= 0:
                    paid = True
                if paid:
                    _logger.info(
                        'Fishbowl SO id=%s: paid_in_full=True (config fishbowl_paid_when_sopayment_row_exists)',
                        so_id,
                    )

        if paid:
            hdr['fishbowl_paid_in_full'] = True
        elif totf > 0:
            if ps is None:
                ps = self._fetch_so_payment_total_sum_by_so_ids([int(so_id)]).get(int(so_id))
            ps_dbg = ps
            hint_keys = [
                k
                for k in full.keys()
                if k
                and (
                    'pay' in str(k).lower()
                    or 'bal' in str(k).lower()
                    or str(k).lower() == 'total'
                )
            ][:30]
            _logger.info(
                'Fishbowl SO id=%s: paid not detected — total=%s payment_col=%s balance=%s '
                'payment_sum=%s; sample column names=%s',
                so_id,
                totf,
                ptf,
                bdf,
                ps_dbg,
                hint_keys,
            )

    def enrich_so_headers_payment_memo_hints(self, headers):
        """Memos are not used to set ``fishbowl_paid_in_full`` (text is unreliable vs ``totalpaidview`` / balance).

        Paid-in-full is determined from MySQL amounts in :meth:`enrich_so_headers_payment_flags` only.
        """
        self.ensure_one()
        return

    def apply_odoo_sale_order_total_to_fishbowl_header(self, sale_order, hdr, ctx):
        """Add one adjustment line so Odoo ``amount_total`` matches Fishbowl.

        * If Fishbowl shows **paid in full** (``fishbowl_paid_in_full``): target Odoo total is **$0**.
        * Else if **Fishbowl** ``so.total`` is known (``fishbowl_header_total`` ``T``): target is
          ``T - P`` (``P`` = ``fishbowl_amount_paid``). For **positive** ``T``, clamp to ``>= 0``
          (amount due after partial pay). For **negative** ``T`` (net credit / misc credits), use the
          signed net so alignment does not add a bogus positive line.

        When **no payments** are recorded (``P == 0``) and the order is **not** paid in full, we **do
        not** add a generic alignment line: the imported SO lines (including misc credits) are
        expected to match Fishbowl. Alignment is only used when ``P > 0`` (partial payment), **or**
        when ``T`` is **net zero** (Fishbowl header total ~$0; credit-return / RMA lines not on Odoo).

        Returns whether a line was created.
        """
        self.ensure_one()
        if not getattr(self, 'zero_balance_when_fishbowl_paid', True):
            return False
        # Batch :meth:`enrich_so_headers_payment_flags` already ran on this ``hdr`` during import.
        # Re-querying MySQL here (per SO) duplicated ~15+ payment SQL variants + raw/brute
        # scans for every order. Only refresh when we still have nothing to align to.
        needs_mysql_refresh = hdr.get('fishbowl_paid_in_full') is not True and hdr.get(
            'fishbowl_header_total'
        ) is None
        if needs_mysql_refresh:
            self.refresh_so_header_payment_flags_from_mysql(hdr)
        so = sale_order.sudo()
        prec = so.currency_id.rounding or 0.01
        target = None
        if hdr.get('fishbowl_paid_in_full'):
            target = 0.0
        elif hdr.get('fishbowl_header_total') is not None:
            try:
                T = float(hdr['fishbowl_header_total'])
            except (TypeError, ValueError):
                return False
            try:
                P = float(hdr.get('fishbowl_amount_paid') or 0.0)
            except (TypeError, ValueError):
                P = 0.0
            T = float_round(T, precision_rounding=prec)
            # No recorded payments: do not add a generic header alignment (lines include misc credits).
            # Still align when T ~ 0 (net Fishbowl total not represented by imported lines).
            if float_compare(P, 0.0, precision_rounding=prec) == 0:
                if float_compare(T, 0.0, precision_rounding=prec) != 0:
                    return False
            net = float_round(T - P, precision_rounding=prec)
            if float_compare(T, 0.0, precision_rounding=prec) < 0:
                target = net
            else:
                target = max(0.0, net)
        else:
            return False
        so.env.flush_all()
        so.invalidate_recordset()
        cur = float_round(so.amount_total, precision_rounding=prec)
        target = float_round(float(target), precision_rounding=prec)
        if float_compare(cur, target, precision_rounding=prec) == 0:
            return False
        delta = float_round(target - cur, precision_rounding=prec)
        if float_compare(abs(delta), 0.0, precision_rounding=prec) == 0:
            return False
        tmpl = self.env.ref(
            'fishbowl_open_import.product_template_fishbowl_so_adjustment',
            raise_if_not_found=False,
        )
        if not tmpl:
            return False
        adj = tmpl.product_variant_id
        if hdr.get('fishbowl_paid_in_full'):
            label = 'Fishbowl: paid in full (import adjustment to zero balance)'
        elif float_compare(target, 0.0, precision_rounding=prec) == 0:
            label = (
                'Fishbowl: net order total $0 (import adjustment; Credit Return / offset lines '
                'not on SO)'
            )
        elif float_compare(target, 0.0, precision_rounding=prec) < 0:
            label = 'Fishbowl: net order total (credit balance; import alignment)'
        else:
            try:
                p_lbl = float(hdr.get('fishbowl_amount_paid') or 0.0)
            except (TypeError, ValueError):
                p_lbl = 0.0
            if p_lbl > 0.0:
                label = 'Fishbowl: order total after payment applied (import alignment)'
            else:
                label = 'Fishbowl: order total alignment (import)'
        line_vals = {
            'order_id': so.id,
            'product_id': adj.id,
            'product_uom_qty': 1.0,
            'price_unit': delta,
            'technical_price_unit': delta,
            'fishbowl_line_label': label,
        }
        # Avoid category/default product taxes so the line hits exactly ``target`` (usually $0).
        if 'tax_ids' in self.env['sale.order.line']._fields:
            line_vals['tax_ids'] = [(6, 0, [])]
        self.env['sale.order.line'].with_context(**ctx).create(line_vals)
        return True

    def fetch_so_lines(self, so_id):
        """Return soitem rows for a Fishbowl SO. Includes ``line_type_name`` when soitemtype joins."""
        self.ensure_one()
        conn = self._get_connection()
        sql_with_type = """
            SELECT si.id AS soitem_id, si.soId, si.productId, si.productNum,
                   si.description, si.qtyOrdered, si.unitPrice, si.totalPrice,
                   si.soLineItem, p.num AS part_num,
                   sit.name AS line_type_name
            FROM soitem si
            LEFT JOIN product p ON p.id = si.productId
            LEFT JOIN soitemtype sit ON sit.id = si.typeId
            WHERE si.soId = %s
            ORDER BY COALESCE(si.soLineItem, 0), si.id
            """
        sql_basic = """
            SELECT si.id AS soitem_id, si.soId, si.productId, si.productNum,
                   si.description, si.qtyOrdered, si.unitPrice, si.totalPrice,
                   si.soLineItem, p.num AS part_num,
                   NULL AS line_type_name
            FROM soitem si
            LEFT JOIN product p ON p.id = si.productId
            WHERE si.soId = %s
            ORDER BY COALESCE(si.soLineItem, 0), si.id
            """
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql_with_type, (so_id,))
                except Exception:
                    cur.execute(sql_basic, (so_id,))
                return list(cur.fetchall())
        finally:
            conn.close()

    def fetch_open_purchase_orders(self, allowed_status_names=None):
        """Return PO headers from Fishbowl.

        :param allowed_status_names: only these normalized (lowercase) ``postatus.name`` values
            (whitelist). If ``None``, use ``import_po_status_names`` on this config when non-empty;
            otherwise legacy ``closed_po_status_names`` exclusion (NOT IN).
        """
        self.ensure_one()
        if allowed_status_names is None:
            allowed_status_names = self._po_import_allowed_names_tuple()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                if allowed_status_names:
                    ph = ','.join(['%s'] * len(allowed_status_names))
                    cur.execute(
                        """
                        SELECT po.id, po.num, po.vendorId, po.statusId, ps.name AS status_name,
                               po.dateIssued, po.dateCreated, po.currencyId, po.note
                        FROM po po
                        INNER JOIN postatus ps ON po.statusId = ps.id
                        WHERE LOWER(TRIM(ps.name)) IN ({ph})
                        ORDER BY po.dateCreated, po.id
                        """.format(ph=ph),
                        tuple(allowed_status_names),
                    )
                else:
                    closed = self._closed_po_names_tuple()
                    placeholders = ','.join(['%s'] * len(closed))
                    cur.execute(
                        """
                        SELECT po.id, po.num, po.vendorId, po.statusId, ps.name AS status_name,
                               po.dateIssued, po.dateCreated, po.currencyId, po.note
                        FROM po po
                        INNER JOIN postatus ps ON po.statusId = ps.id
                        WHERE ps.name NOT IN ({ph})
                        ORDER BY po.dateCreated, po.id
                        """.format(ph=placeholders),
                        closed,
                    )
                return list(cur.fetchall())
        finally:
            conn.close()

    def fetch_part(self, part_id):
        """Return part row dict or None.

        Uses ``SELECT *`` so we do not fail when ``part`` column names differ by Fishbowl
        version (e.g. default vendor) or when ``part`` is implemented as a view.
        """
        if not part_id:
            return None
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM part WHERE id = %s', (part_id,))
                return cur.fetchone()
        finally:
            conn.close()

    def fetch_part_by_product_id(self, product_id):
        """Resolve Fishbowl product.id → part row (schema varies by version)."""
        if not product_id:
            return None
        self.ensure_one()
        conn = self._get_connection()
        queries = [
            """
            SELECT p.* FROM part p
            INNER JOIN product pr ON pr.partId = p.id
            WHERE pr.id = %s
            """,
            """
            SELECT p.* FROM part p
            INNER JOIN product pr ON p.id = pr.partId
            WHERE pr.id = %s
            """,
            'SELECT * FROM part WHERE id = %s',
        ]
        try:
            with conn.cursor() as cur:
                for q in queries[:2]:
                    try:
                        cur.execute(q, (product_id,))
                        row = cur.fetchone()
                        if row:
                            return row
                    except Exception:
                        continue
                cur.execute(
                    'SELECT partId FROM product WHERE id = %s', (product_id,)
                )
                r = cur.fetchone()
                if r and r.get('partId'):
                    return self.fetch_part(r['partId'])
        finally:
            conn.close()
        return None

    def fetch_part_by_num(self, part_num):
        if not part_num:
            return None
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                n = part_num.strip()
                cur.execute('SELECT * FROM part WHERE num = %s LIMIT 1', (n,))
                row = cur.fetchone()
                if row:
                    return row
                cur.execute('SELECT * FROM part WHERE TRIM(num) = %s LIMIT 1', (n,))
                return cur.fetchone()
        finally:
            conn.close()

    def fetch_part_by_poitem_id(self, poitem_id):
        """Resolve a part row from Fishbowl ``poitem.id`` (authoritative for PO import lines).

        Use when the row dict has a stale ``partId`` or when ``part.num`` does not match
        ``poitem``/``part`` as returned by the list query.
        """
        if not poitem_id:
            return None
        try:
            pid = int(poitem_id)
        except (TypeError, ValueError):
            return None
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT p.* FROM poitem pi
                        INNER JOIN part p ON p.id = pi.partId
                        WHERE pi.id = %s
                        """,
                        (pid,),
                    )
                    row = cur.fetchone()
                    if row:
                        return row
                except Exception:
                    pass
                cur.execute(
                    'SELECT partId, partNum FROM poitem WHERE id = %s',
                    (pid,),
                )
                pi = cur.fetchone()
                if not pi:
                    return None
                if pi.get('partId'):
                    part = self.fetch_part(pi['partId'])
                    if part:
                        return part
                pn = (pi.get('partNum') or '').strip()
                if pn:
                    return self.fetch_part_by_num(pn)
        finally:
            conn.close()
        return None

    def fetch_customer_row(self, customer_id):
        if not customer_id:
            return None
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.name, c.accountId, c.dateLastModified
                    FROM customer c
                    WHERE c.id = %s
                    """,
                    (customer_id,),
                )
                return cur.fetchone()
        finally:
            conn.close()

    def fetch_vendor_row(self, vendor_id):
        if not vendor_id:
            return None
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT v.id, v.name, v.accountId
                    FROM vendor v
                    WHERE v.id = %s
                    """,
                    (vendor_id,),
                )
                return cur.fetchone()
        finally:
            conn.close()

    def fetch_customer_email_phone(self, account_id):
        """Best-effort email and phone from contact/address."""
        if not account_id:
            return False, False
        self.ensure_one()
        conn = self._get_connection()
        email, phone = False, False
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT c.datus FROM contact c
                        INNER JOIN contacttype t ON t.id = c.typeId
                        WHERE c.accountId = %s AND c.datus LIKE %s
                        ORDER BY c.defaultFlag DESC, c.id LIMIT 1
                        """,
                        (account_id, '%@%'),
                    )
                    row = cur.fetchone()
                    if row and row.get('datus'):
                        email = row['datus'].strip()
                except Exception:
                    pass
                try:
                    cur.execute(
                        """
                        SELECT c.datus FROM contact c
                        INNER JOIN contacttype t ON t.id = c.typeId
                        WHERE c.accountId = %s
                          AND c.datus IS NOT NULL AND TRIM(c.datus) != ''
                          AND c.datus NOT LIKE %s
                        ORDER BY c.defaultFlag DESC, c.id LIMIT 1
                        """,
                        (account_id, '%@%'),
                    )
                    row = cur.fetchone()
                    if row and row.get('datus'):
                        phone = row['datus'].strip()
                except Exception:
                    pass
        finally:
            conn.close()
        return email, phone

    def fetch_address_for_account(self, account_id):
        """Return dict street, street2, city, zip, state_id (name), country_id (name) from first address."""
        if not account_id:
            return {}
        self.ensure_one()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT a.name, a.address, a.city, a.zip, a.stateId, a.countryId
                        FROM address a
                        INNER JOIN contact c ON c.addressId = a.id
                        WHERE c.accountId = %s
                        ORDER BY c.defaultFlag DESC, c.id
                        LIMIT 1
                        """,
                        (account_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return {
                            'street': row.get('address') or '',
                            'city': row.get('city') or '',
                            'zip': row.get('zip') or '',
                        }
                except Exception:
                    pass
        finally:
            conn.close()
        return {}

    def customer_is_company(self, customer_id):
        """Heuristic: Fishbowl may not expose type; default True for business customers."""
        row = self.fetch_customer_row(customer_id)
        if not row:
            return True
        name = (row.get('name') or '').strip()
        return True if name else True

    def map_so_state(self, status_id):
        self.ensure_one()
        m = self.env['fishbowl.status.map'].search(
            [
                ('company_id', '=', self.company_id.id),
                ('direction', '=', 'so'),
                ('fishbowl_status_id', '=', int(status_id)),
                ('active', '=', True),
            ],
            limit=1,
        )
        if m and m.odoo_so_state:
            return m.odoo_so_state
        return 'draft'

    def map_po_state(self, status_id, status_name=None):
        """Map Fishbowl PO status → Odoo ``purchase.order`` state.

        * Bid Request → ``draft`` (RFQ)
        * Issued / Partial → ``purchase`` (confirmed PO; receipts from import wizard)

        Known ``postatus.name`` values are applied **before** ``fishbowl.status.map`` so a misconfigured
        map cannot force Issued/Partial to RFQ.
        """
        self.ensure_one()
        raw_name = (status_name or '').strip()
        name = raw_name.lower()
        if name in ('bid request', 'big request'):
            return 'draft'
        if name in ('issued', 'partial'):
            return 'purchase'
        StatusMap = self.env['fishbowl.status.map']
        company_domain = [
            ('company_id', '=', self.company_id.id),
            ('direction', '=', 'po'),
            ('active', '=', True),
        ]
        if status_id is not None and status_id != '':
            try:
                sid = int(status_id)
            except (TypeError, ValueError):
                sid = None
            if sid is not None:
                m = StatusMap.search(
                    company_domain + [('fishbowl_status_id', '=', sid)],
                    limit=1,
                )
                if m and m.odoo_po_state:
                    return m.odoo_po_state
        if raw_name:
            m = StatusMap.search(
                company_domain + [('fishbowl_status_name', '=ilike', raw_name)],
                limit=1,
            )
            if m and m.odoo_po_state:
                return m.odoo_po_state
        return 'draft'

    def get_currency(self, currency_id):
        """Map Fishbowl currency id to res.currency via name code if possible."""
        self.ensure_one()
        if not currency_id:
            return self.company_id.currency_id
        conn = self._get_connection()
        code = None
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT code, name FROM currency WHERE id = %s', (currency_id,))
                row = cur.fetchone()
                if row:
                    code = row.get('code') or row.get('name')
        except Exception:
            pass
        finally:
            conn.close()
        if code:
            cur = self.env['res.currency'].search([('name', '=', str(code)[:3].upper())], limit=1)
            if cur:
                return cur
        return self.company_id.currency_id

    @staticmethod
    def _aggregate_fishbowl_qty_rows(raw_rows):
        """Merge rows by (part_id, part_num, group_name, type_name, location_name) summing qty."""
        buckets = defaultdict(float)

        def _pick(row, *names):
            for n in names:
                for k, v in row.items():
                    if k.lower() == n.lower():
                        return v
            return None

        for r in raw_rows:
            pnv = _pick(r, 'part_num')
            pn = (pnv or '').strip() if isinstance(pnv, str) else str(pnv or '').strip()
            if not pn:
                continue
            part_id_v = _pick(r, 'part_id')
            try:
                part_id = int(part_id_v) if part_id_v is not None else None
            except (TypeError, ValueError):
                part_id = None
            gv = _pick(r, 'group_name')
            tv = _pick(r, 'type_name')
            lv = _pick(r, 'location_name')
            g = (gv or '').strip() if gv else None
            t = (tv or '').strip() if tv else None
            locn = (lv or '').strip() if lv else None
            if g == '':
                g = None
            if t == '':
                t = None
            if locn == '':
                locn = None
            qv = _pick(r, 'qty')
            if qv is None:
                for k, v in r.items():
                    if k and ('qty' in k.lower() or k.lower().startswith('sum(')):
                        qv = v
                        break
            try:
                qf = float(qv) if qv is not None else 0.0
            except (TypeError, ValueError):
                qf = 0.0
            buckets[(part_id, pn, g, t, locn)] += qf
        return [
            {
                'part_id': a,
                'part_num': b,
                'qty': q,
                'group_name': c,
                'type_name': d,
                'location_name': e,
            }
            for (a, b, c, d, e), q in buckets.items()
        ]

    @staticmethod
    def _fishbowl_num_matches_code(fb_num, code):
        """True if Fishbowl ``part.num`` matches Odoo code (same rules as ``_fishbowl_part_ids_matching_num`` triple OR)."""
        if fb_num is None:
            fb_num = ''
        fb_num = str(fb_num).strip()
        code = (code or '').strip()
        if not code:
            return False
        return (
            fb_num == code
            or fb_num.replace(' ', '-') == code.replace(' ', '-')
            or fb_num.lower() == code.lower()
        )

    def _fishbowl_part_ids_for_nums_batched(self, conn, part_nums, chunk_size=200):
        """
        Resolve Fishbowl ``part.id`` values for many Odoo part codes in few round-trips.

        Uses the same matching rules as repeated ``_fishbowl_part_ids_matching_num`` (triple OR per code,
        then LIKE fallback only for codes with no triple match in the batch result).

        Returns ``(merged_ids, code_map)`` where ``code_map`` maps each Odoo internal reference (stripped)
        to the list of Fishbowl ``part.id`` values that resolve to that code.
        """
        seen_codes = []
        seen = set()
        for raw in part_nums:
            code = (raw or '').strip()
            if not code or code in seen:
                continue
            seen.add(code)
            seen_codes.append(code)
        if not seen_codes:
            return [], {}

        merged = []
        merged_seen = set()
        code_map = {}

        def add_ids(pid_list):
            for pid in pid_list:
                if pid not in merged_seen:
                    merged_seen.add(pid)
                    merged.append(pid)

        for i in range(0, len(seen_codes), chunk_size):
            chunk = seen_codes[i : i + chunk_size]
            or_parts = []
            params = []
            for c in chunk:
                or_parts.append(
                    '(TRIM(num) = %s OR REPLACE(num, \' \', \'-\') = REPLACE(TRIM(%s), \' \', \'-\') '
                    'OR LOWER(TRIM(num)) = LOWER(TRIM(%s)))'
                )
                params.extend([c, c, c])
            sql = 'SELECT id, num FROM part WHERE ' + ' OR '.join(or_parts)
            rows = []
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = list(cur.fetchall())
            except Exception as exc:
                _logger.debug('Fishbowl batch part lookup: %s', exc)
                rows = []

            matched_codes = set()
            for row in rows:
                rnum = row.get('num')
                if rnum is None:
                    rnum = row.get('NUM')
                for c in chunk:
                    if self._fishbowl_num_matches_code(rnum, c):
                        matched_codes.add(c)

            for c in chunk:
                if c in matched_codes:
                    pids_c = []
                    for row in rows:
                        rid = row.get('id')
                        rnum = row.get('num') or row.get('NUM')
                        if rid is not None and self._fishbowl_num_matches_code(rnum, c):
                            pids_c.append(int(rid))
                    pids_c = list(dict.fromkeys(pids_c))
                else:
                    pids_c = self._fishbowl_part_ids_matching_num(conn, c)
                code_map[c] = pids_c
                add_ids(pids_c)
        return merged, code_map

    def _fishbowl_part_ids_matching_num(self, conn, pn):
        """Resolve ``part.id`` values for a part number string (handles space/dash variants)."""
        if not pn:
            return []
        ids = []
        sqls = (
            """
            SELECT id FROM part
            WHERE TRIM(num) = %s
               OR REPLACE(num, ' ', '-') = REPLACE(TRIM(%s), ' ', '-')
               OR LOWER(TRIM(num)) = LOWER(TRIM(%s))
            """,
            """
            SELECT id FROM part WHERE num LIKE %s LIMIT 40
            """,
        )
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sqls[0], (pn, pn, pn))
                    for row in cur.fetchall():
                        rid = row.get('id')
                        if rid is not None:
                            ids.append(int(rid))
                except Exception:
                    pass
                if not ids:
                    cur.execute(
                        sqls[1],
                        ('%' + pn.replace('-', '%').replace(' ', '%') + '%',),
                    )
                    for row in cur.fetchall():
                        rid = row.get('id')
                        if rid is not None:
                            ids.append(int(rid))
        except Exception as exc:
            _logger.debug('Fishbowl part lookup for inventory: %s', exc)
        return list(dict.fromkeys(ids))

    def fetch_qty_on_hand_rows(self, part_num_filter=None, part_nums_filter=None, return_code_map=False):
        """
        Return list of dicts with ``part_id``, ``part_num``, ``qty``, ``group_name``, ``type_name``,
        ``location_name`` for inventory sync.

        Tries several Fishbowl MySQL layouts: ``qtyonhand``, ``qohview`` (common for exports),
        alternate column casing, and raw rows aggregated in Python (no ``GROUP BY``).
        ``location_name`` comes from Fishbowl ``location.name`` (bin) when the join is available.
        Rows tied to an inactive Fishbowl ``location`` (``location.activeFlag``) or inactive
        ``locationgroup`` (``locationgroup.activeFlag``) are omitted.

        :param part_num_filter: optional Fishbowl part number (or Odoo-style code). When set, only
            rows for that part are returned (matches ``p.num`` exactly or with space/dash normalization).
            Takes precedence over ``part_nums_filter``.
        :param part_nums_filter: optional iterable of part numbers (e.g. Odoo ``default_code`` values).
            When set (and single filter is not), Fishbowl rows are restricted to those parts only.
            Use this for a full import driven by the Odoo product list instead of scanning all Fishbowl
            parts; part ids are resolved with batched SQL (chunked OR) plus per-code LIKE fallback when
            needed. Pass ``[]`` to fetch no rows. When ``None``, no part restriction is applied (entire
            Fishbowl qty snapshot).
        :param return_code_map: if True, return ``(rows, odoo_code_to_fishbowl_part_ids)`` so the import
            can match Fishbowl rows to Odoo products by ``part.id`` (not Fishbowl ``p.num`` string).
        """
        self.ensure_one()
        conn = self._get_connection()
        pn = (part_num_filter or '').strip()
        # Prefer matching by resolved part.id(s). Using both text + id ANDed can return 0 rows when
        # the user types an Odoo-style code that does not exactly match Fishbowl ``part.num`` even
        # though ``_fishbowl_part_ids_matching_num`` found the row (e.g. LIKE / case).
        part_clause = ''
        part_params = ()
        pids = []
        odoo_code_to_fb_part_ids = {}
        if pn:
            pids = self._fishbowl_part_ids_matching_num(conn, pn)
            odoo_code_to_fb_part_ids = {pn: list(pids)}
            if pids:
                placeholders = ','.join(['%s'] * len(pids))
                part_clause = ' AND p.id IN (%s)' % placeholders
                part_params = tuple(pids)
            else:
                part_clause = (
                    ' AND (TRIM(p.num) = %s OR REPLACE(p.num, \' \', \'-\') = REPLACE(TRIM(%s), \' \', \'-\'))'
                )
                part_params = (pn, pn)
        elif part_nums_filter is not None:
            pids, odoo_code_to_fb_part_ids = self._fishbowl_part_ids_for_nums_batched(conn, part_nums_filter)
            if pids:
                placeholders = ','.join(['%s'] * len(pids))
                part_clause = ' AND p.id IN (%s)' % placeholders
                part_params = tuple(pids)
            else:
                part_clause = ' AND 1=0'
                part_params = ()

        def _run_queries(queries, params):
            last_err = None
            with conn.cursor() as cur:
                for sql in queries:
                    try:
                        cur.execute(sql, params)
                        raw = list(cur.fetchall())
                    except Exception as exc:
                        last_err = exc
                        _logger.debug('Fishbowl inventory SQL skipped: %s', exc)
                        continue
                    if raw:
                        agg = self._aggregate_fishbowl_qty_rows(raw)
                        if agg:
                            return agg
            if last_err:
                _logger.warning(
                    'Fishbowl inventory: no rows from any query variant; last DB error: %s',
                    last_err,
                )
            return []

        # Fishbowl schema: ``location.activeFlag`` / ``locationgroup.activeFlag`` (bit), not ``active``.
        fb_loc_active_sql = (
            " AND (l.id IS NULL OR CAST(l.activeFlag AS UNSIGNED) = 1)"
            " AND (lg.id IS NULL OR CAST(lg.activeFlag AS UNSIGNED) = 1)"
        )

        # --- Grouped SQL (fast path); include ``location.name`` for path rules (bin-level stock). ---
        grouped_queries = [
            """
            SELECT p.id AS part_id, p.num AS part_num, SUM(q.qty) AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qtyonhand q
            INNER JOIN part p ON p.id = q.partId
            LEFT JOIN location l ON l.id = q.locationId
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql
            + """
            GROUP BY p.id, p.num, lg.name, lt.name, l.name
            """,
            """
            SELECT p.id AS part_id, p.num AS part_num, SUM(q.qty) AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qtyonhand q
            INNER JOIN part p ON p.id = q.partid
            LEFT JOIN location l ON l.id = q.locationid
            LEFT JOIN locationgroup lg ON lg.id = l.locationgroupid
            LEFT JOIN locationtype lt ON lt.id = l.typeid
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql
            + """
            GROUP BY p.id, p.num, lg.name, lt.name, l.name
            """,
            """
            SELECT p.id AS part_id, p.num AS part_num, SUM(qoh.QTY) AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.PARTID
            LEFT JOIN location l ON l.id = qoh.LOCATIONID
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql
            + """
            GROUP BY p.id, p.num, lg.name, lt.name, l.name
            """,
            """
            SELECT p.id AS part_id, p.num AS part_num, SUM(qoh.qty) AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.partId
            LEFT JOIN location l ON l.id = qoh.locationId
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql
            + """
            GROUP BY p.id, p.num, lg.name, lt.name, l.name
            """,
        ]

        # --- Raw rows (no GROUP BY; aggregate in Python) — works when GROUP BY / joins differ ---
        raw_queries = [
            """
            SELECT p.id AS part_id, p.num AS part_num, q.qty AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qtyonhand q
            INNER JOIN part p ON p.id = q.partId
            LEFT JOIN location l ON l.id = q.locationId
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql,
            """
            SELECT p.id AS part_id, p.num AS part_num, qoh.QTY AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.PARTID
            LEFT JOIN location l ON l.id = qoh.LOCATIONID
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql,
            """
            SELECT p.id AS part_id, p.num AS part_num, qoh.qty AS qty, lg.name AS group_name, lt.name AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.partId
            LEFT JOIN location l ON l.id = qoh.locationId
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            LEFT JOIN locationtype lt ON lt.id = l.typeId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql,
            """
            SELECT p.id AS part_id, p.num AS part_num, qoh.QTY AS qty, NULL AS group_name, NULL AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.PARTID
            LEFT JOIN location l ON l.id = qoh.LOCATIONID
            LEFT JOIN locationgroup lg ON lg.id = l.locationGroupId
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql,
            """
            SELECT p.id AS part_id, p.num AS part_num, qoh.qty AS qty, NULL AS group_name, NULL AS type_name,
                   l.name AS location_name
            FROM qohview qoh
            INNER JOIN part p ON p.id = qoh.partId
            LEFT JOIN location l ON l.id = qoh.locationId
            LEFT JOIN locationgroup lg ON lg.id = l.locationgroupid
            WHERE 1=1
            """
            + part_clause
            + fb_loc_active_sql,
        ]

        try:
            params = part_params if part_params else ()

            rows = _run_queries(grouped_queries, params)
            if rows:
                if return_code_map:
                    return rows, odoo_code_to_fb_part_ids
                return rows
            rows = _run_queries(raw_queries, params)
            if rows:
                if return_code_map:
                    return rows, odoo_code_to_fb_part_ids
                return rows

            if return_code_map:
                return [], odoo_code_to_fb_part_ids
            return []
        finally:
            conn.close()

    def _normalize_odoo_location_path_string(self, path):
        if not path:
            return ''
        return '/'.join(
            x.strip()
            for x in str(path).replace(' / ', '/').replace('\\', '/').split('/')
            if x.strip()
        )

    def find_stock_location_by_fishbowl_path(self, company, path_str):
        """Match a slash path from ``build_fishbowl_odoo_location_path`` to ``stock.location``."""
        self.ensure_one()
        path_norm = self._normalize_odoo_location_path_string(path_str)
        if not path_norm:
            return self.env['stock.location']
        Location = self.env['stock.location'].sudo()
        domain = [
            ('company_id', 'in', [False, company.id]),
            ('usage', 'in', ('internal', 'transit')),
        ]
        for loc in Location.search(domain):
            cn = self._normalize_odoo_location_path_string(loc.complete_name or '')
            if cn == path_norm:
                return loc
        for loc in Location.search(domain):
            cn = self._normalize_odoo_location_path_string(loc.complete_name or '')
            if cn.endswith(path_norm) or path_norm.endswith(cn):
                return loc
        return self.env['stock.location']

    def resolve_inventory_location_for_import(self, company, group_name, type_name, location_name, default_loc):
        """Resolve Odoo stock location: built-in path rules first, then ``fishbowl.location.map``, then default."""
        self.ensure_one()
        from .fishbowl_location_path import build_fishbowl_odoo_location_path

        path = build_fishbowl_odoo_location_path(group_name, type_name, location_name)
        loc = self.find_stock_location_by_fishbowl_path(company, path)
        if loc:
            return loc, 'path', path
        loc = self.env['fishbowl.location.map'].resolve_location(
            company, group_name, type_name, location_name
        )
        if loc:
            return loc, 'map', path
        return default_loc, 'default', path

    def fetch_so_memos(self, so_fb_id):
        """Return Fishbowl Sales Order Memo tab rows for chatter import.

        Fishbowl stores memos in either:

        * Generic ``memo``: ``memo.recordId`` = Fishbowl ``so.id``; all SO memos share one ``tableId``
          per database (set **Fishbowl memo tableId (Sales Order)** on the config when needed).
        * On many MySQL exports there is **no** catalog table named ``table``; use ``JOIN so`` or
          ``WHERE recordId = so.id`` instead of metadata joins.
        * Optional catalog ``table`` (``name`` = ``SO``) when that table exists.
        * Legacy ``somemo`` (``soId``), on some databases.

        Each dict may include: ``memo_date`` (datetime), ``memo_text`` (str), ``user_name`` (str|None).
        Keys are normalized to lowercase for PyMySQL / MySQL column name variants.
        """
        self.ensure_one()
        so_fb_id = int(so_fb_id)
        conn = self._get_connection()
        memo_tid = self.fishbowl_memo_so_table_id
        # When set, match Fishbowl UI: memo rows for an SO use recordId = so.id and a constant tableId.
        queries = []
        if memo_tid:
            queries.append(
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    WHERE m.recordId = %s AND m.tableId = %s
                    ORDER BY m.dateCreated, m.id
                    """,
                    (so_fb_id, int(memo_tid)),
                ),
            )
        # Order: ``memo`` + ``so`` (MySQL often has no ``table`` catalog). Then ``table``/``sysmodule``/``somemo``.
        queries.extend(
            [
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN so ON so.id = m.recordId
                WHERE so.id = %s
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN so ON so.id = %s
                  AND TRIM(CAST(m.recordId AS CHAR(64))) = TRIM(CAST(so.num AS CHAR(64)))
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN sysmodule sm ON sm.id = m.tableId AND sm.name = 'SO'
                WHERE m.recordId = %s
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN sysmodule sm ON sm.id = m.tableId
                WHERE m.recordId = %s
                  AND LOWER(TRIM(sm.name)) IN ('so', 'salesorder')
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN `table` tb ON tb.id = m.tableId AND tb.name = 'SO'
                WHERE m.recordId = %s
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN `table` tb ON tb.id = m.tableId
                WHERE m.recordId = %s
                  AND LOWER(TRIM(tb.name)) IN ('so', 'salesorder')
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                WHERE m.recordId = %s
                  AND m.tableId = (SELECT id FROM `table` WHERE name = 'SO' LIMIT 1)
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                FROM memo m
                INNER JOIN `table` tb ON tb.id = m.tableId
                WHERE m.recordId = %s
                  AND tb.name IN ('SO', 'So', 'SalesOrder', 'Sales Order')
                ORDER BY m.dateCreated, m.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT sm.dateCreated AS memo_date, sm.memo AS memo_text, u.username AS user_name
                FROM somemo sm
                LEFT JOIN sysuser u ON u.id = sm.userId
                WHERE sm.soId = %s
                ORDER BY sm.dateCreated, sm.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT sm.dateCreated AS memo_date, sm.memo AS memo_text, u.userName AS user_name
                FROM somemo sm
                LEFT JOIN sysuser u ON u.id = sm.userId
                WHERE sm.soId = %s
                ORDER BY sm.dateCreated, sm.id
                """,
                (so_fb_id,),
            ),
            (
                """
                SELECT sm.dateCreated AS memo_date, sm.memo AS memo_text, NULL AS user_name
                FROM somemo sm
                WHERE sm.soId = %s
                ORDER BY sm.dateCreated, sm.id
                """,
                (so_fb_id,),
            ),
            ]
        )
        rows_out = []
        try:
            with conn.cursor() as cur:
                for sql, params in queries:
                    try:
                        cur.execute(sql, params)
                        rows_out = list(cur.fetchall())
                        # Do not stop on the first query that succeeds but returns 0 rows (e.g. empty
                        # ``somemo``) — try ``memo`` + ``table`` and other variants.
                        if rows_out:
                            break
                    except Exception:
                        continue
        finally:
            conn.close()
        if not rows_out:
            _logger.debug(
                'Fishbowl SO memos: no rows for so id %s (tried memo+so, sysmodule, `table`, somemo)',
                so_fb_id,
            )
            return []
        # Normalize dict keys (MySQL may return mixed-case column names).
        norm = []
        for row in rows_out:
            norm.append({(k.lower() if isinstance(k, str) else k): v for k, v in row.items()})
        return norm

    def fetch_po_memos(self, po_fb_id):
        """Return Fishbowl Purchase Order Memo tab rows for chatter import (same patterns as ``fetch_so_memos``).

        Uses ``memo`` (``recordId`` = ``po.id``), optional ``table`` / ``sysmodule`` name PO, or ``pomemo``.
        """
        self.ensure_one()
        po_fb_id = int(po_fb_id)
        conn = self._get_connection()
        memo_tid = self.fishbowl_memo_po_table_id
        queries = []
        if memo_tid:
            queries.append(
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    WHERE m.recordId = %s AND m.tableId = %s
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id, int(memo_tid)),
                ),
            )
        queries.extend(
            [
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN po ON po.id = m.recordId
                    WHERE po.id = %s
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN sysmodule sm ON sm.id = m.tableId AND sm.name = 'PO'
                    WHERE m.recordId = %s
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN sysmodule sm ON sm.id = m.tableId
                    WHERE m.recordId = %s
                      AND LOWER(TRIM(sm.name)) IN ('po', 'purchaseorder', 'purchase order')
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN `table` tb ON tb.id = m.tableId AND tb.name = 'PO'
                    WHERE m.recordId = %s
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN `table` tb ON tb.id = m.tableId
                    WHERE m.recordId = %s
                      AND LOWER(TRIM(tb.name)) IN ('po', 'purchaseorder', 'purchase order')
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    WHERE m.recordId = %s
                      AND m.tableId = (SELECT id FROM `table` WHERE name = 'PO' LIMIT 1)
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT m.dateCreated AS memo_date, m.memo AS memo_text, m.username AS user_name
                    FROM memo m
                    INNER JOIN `table` tb ON tb.id = m.tableId
                    WHERE m.recordId = %s
                      AND tb.name IN ('PO', 'Po', 'PurchaseOrder', 'Purchase Order')
                    ORDER BY m.dateCreated, m.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT pm.dateCreated AS memo_date, pm.memo AS memo_text, u.username AS user_name
                    FROM pomemo pm
                    LEFT JOIN sysuser u ON u.id = pm.userId
                    WHERE pm.poId = %s
                    ORDER BY pm.dateCreated, pm.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT pm.dateCreated AS memo_date, pm.memo AS memo_text, u.userName AS user_name
                    FROM pomemo pm
                    LEFT JOIN sysuser u ON u.id = pm.userId
                    WHERE pm.poId = %s
                    ORDER BY pm.dateCreated, pm.id
                    """,
                    (po_fb_id,),
                ),
                (
                    """
                    SELECT pm.dateCreated AS memo_date, pm.memo AS memo_text, NULL AS user_name
                    FROM pomemo pm
                    WHERE pm.poId = %s
                    ORDER BY pm.dateCreated, pm.id
                    """,
                    (po_fb_id,),
                ),
            ]
        )
        rows_out = []
        try:
            with conn.cursor() as cur:
                for sql, params in queries:
                    try:
                        cur.execute(sql, params)
                        rows_out = list(cur.fetchall())
                        if rows_out:
                            break
                    except Exception:
                        continue
        finally:
            conn.close()
        if not rows_out:
            _logger.debug(
                'Fishbowl PO memos: no rows for po id %s (tried memo+po, sysmodule, `table`, pomemo)',
                po_fb_id,
            )
            return []
        norm = []
        for row in rows_out:
            norm.append({(k.lower() if isinstance(k, str) else k): v for k, v in row.items()})
        return norm
