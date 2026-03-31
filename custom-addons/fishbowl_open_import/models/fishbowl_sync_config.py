# -*- coding: utf-8 -*-

import logging
import re
from collections import defaultdict
from decimal import Decimal

from odoo import api, fields, models
from odoo.exceptions import UserError

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
        sp = self.find_odoo_salesperson_user(hdr)
        if sp:
            sale_order.write({'user_id': sp.id})

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
