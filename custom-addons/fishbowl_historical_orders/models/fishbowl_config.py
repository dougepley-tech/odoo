# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.exceptions import UserError


class FishbowlConfig(models.Model):
    _name = 'fishbowl.config'
    _description = 'Fishbowl MySQL Connection'
    _rec_name = 'host'

    host = fields.Char(string='Host', required=True, default='localhost')
    port = fields.Integer(string='Port', default=3306)
    database = fields.Char(string='Database', required=True)
    user = fields.Char(string='User', required=True)
    password = fields.Char(string='Password')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        ondelete='cascade',
    )

    _company_uniq = models.Constraint(
        'UNIQUE(company_id)',
        'Only one Fishbowl config per company.',
    )

    def action_test_connection(self):
        """Test the MySQL connection and show success or failure."""
        self.ensure_one()
        try:
            conn = self._get_connection()
            conn.ping(reconnect=False)
            conn.close()
        except Exception as e:
            raise UserError(
                'Connection failed: %s\n\nCheck host, port, database, user, and password.'
                % (e,)
            ) from e
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Connection successful',
                'message': 'Successfully connected to %s@%s:%s/%s.'
                % (self.user, self.host, self.port or 3306, self.database),
                'type': 'success',
                'sticky': False,
            },
        }

    def _get_connection(self):
        """Return a PyMySQL connection to the Fishbowl database."""
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

    def _get_customer_ids_by_email(self, email):
        """Return list of Fishbowl customer IDs whose contact email matches (exact or LIKE)."""
        if not email or not (email or '').strip():
            return []
        email = (email or '').strip()
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT c.id FROM customer c
                        INNER JOIN contact ct ON ct.accountId = c.accountId
                        WHERE ct.datus = %s OR ct.datus LIKE %s
                        """,
                        (email, '%' + email + '%'),
                    )
                    return [row['id'] for row in cur.fetchall()]
                except Exception:
                    pass
                try:
                    cur.execute(
                        "SELECT id FROM customer WHERE email = %s", (email,)
                    )
                    return [row['id'] for row in cur.fetchall()]
                except Exception:
                    return []
        finally:
            conn.close()

    def _get_customer_ids_by_name(self, name):
        """Return list of Fishbowl customer IDs by customer name or SO billToName."""
        if not name or not (name or '').strip():
            return []
        name = (name or '').strip()
        pattern = '%' + name + '%'
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM customer WHERE name LIKE %s", (pattern,)
                )
                ids = [row['id'] for row in cur.fetchall()]
                cur.execute(
                    "SELECT DISTINCT customerId FROM SO WHERE billToName LIKE %s",
                    (pattern,),
                )
                for row in cur.fetchall():
                    cid = row.get('customerId')
                    if cid and cid not in ids:
                        ids.append(cid)
                return ids
        finally:
            conn.close()

    def _get_customer_ids_by_phone(self, phone):
        """Return list of Fishbowl customer IDs whose contact phone (datus) matches."""
        if not phone or not (phone or '').strip():
            return []
        import re
        digits = re.sub(r'\D', '', (phone or '').strip())
        if not digits:
            return []
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT c.id FROM customer c
                    INNER JOIN contact ct ON ct.accountId = c.accountId
                    WHERE ct.datus LIKE %s
                    """,
                    ('%' + digits + '%',),
                )
                return [row['id'] for row in cur.fetchall()]
        finally:
            conn.close()

    def _fetch_orders(
        self,
        date_from=None,
        date_to=None,
        limit=None,
        offset=0,
        so_num=None,
        customer_po=None,
        customer_ids=None,
    ):
        """
        Fetch SO headers from Fishbowl. Optional filters: so_num, customer_po, customer_ids.
        When so_num or customer_po or customer_ids are set, date range is optional.
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                for email_col in ('email', 'billToEmail', 'customerEmail', None):
                    if email_col:
                        q = """
                            SELECT so.id, so.num, so.customerId, so.customerPO,
                                   so.dateCreated AS order_date, so.dateCompleted AS date_completed,
                                   so.statusId AS status, so.note, so.billToName,
                                   so.`%s` AS customer_email
                            FROM SO so
                            WHERE 1=1
                        """ % email_col
                    else:
                        q = """
                            SELECT so.id, so.num, so.customerId, so.customerPO,
                                   so.dateCreated AS order_date, so.dateCompleted AS date_completed,
                                   so.statusId AS status, so.note, so.billToName
                            FROM SO so
                            WHERE 1=1
                        """
                    params = []
                    if so_num:
                        q += " AND so.num = %s"
                        params.append(so_num.strip())
                    if customer_po:
                        q += " AND so.customerPO = %s"
                        params.append(customer_po.strip())
                    if customer_ids is not None:
                        if not customer_ids:
                            return []
                        q += " AND so.customerId IN (%s)" % ",".join(["%s"] * len(customer_ids))
                        params.extend(customer_ids)
                    if date_from:
                        q += " AND so.dateCreated >= %s"
                        params.append(date_from)
                    if date_to:
                        q += " AND so.dateCreated <= %s"
                        params.append(date_to)
                    q += " ORDER BY so.dateCreated ASC, so.id ASC"
                    if limit:
                        q += " LIMIT %s"
                        params.append(limit)
                    if offset:
                        q += " OFFSET %s"
                        params.append(offset)
                    try:
                        cur.execute(q, params or None)
                        rows = cur.fetchall()
                        for r in rows:
                            r.setdefault('status', None)
                            r.setdefault('note', None)
                            r.setdefault('billToName', None)
                            r.setdefault('customer_email', None)
                        return rows
                    except Exception as e:
                        if email_col and 'Unknown column' in str(e):
                            continue
                        raise
        finally:
            conn.close()

    def _fetch_customer_email(self, customer_id):
        """
        Get customer email from Fishbowl CUSTOMER, or from contact/address.
        Contact table (https://fishbowlhelp.com/files/database/tables/contact.html) uses
        accountId (not customerId); contact has addressId -> email may be on ADDRESS.
        """
        if not customer_id:
            return None
        conn = self._get_connection()
        try:
            for table in ('CUSTOMER', 'Customer', 'customer'):
                for col in ('email', 'primaryEmail', 'Email', 'emailAddress'):
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT `%s` FROM `%s` WHERE id = %%s" % (col, table),
                                (customer_id,),
                            )
                            row = cur.fetchone()
                            if row and row.get(col):
                                return row[col]
                    except Exception:
                        continue
            account_id = None
            for acc_col in ('accountId', 'account_id'):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT `%s` FROM CUSTOMER WHERE id = %%s" % acc_col,
                            (customer_id,),
                        )
                        row = cur.fetchone()
                        if row and row.get(acc_col):
                            account_id = row[acc_col]
                            break
                except Exception:
                    continue
            if not account_id:
                account_id = customer_id
            for contact_table in ('contact', 'CONTACT', 'Contact'):
                for addr_table in ('address', 'ADDRESS', 'Address'):
                    for fk in ('accountId', 'account_id'):
                        for email_col in ('email', 'emailAddress', 'Email'):
                            try:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "SELECT a.`%s` FROM `%s` c "
                                        "INNER JOIN `%s` a ON a.id = c.addressId "
                                        "WHERE c.`%s` = %%s AND a.`%s` IS NOT NULL AND TRIM(a.`%s`) != '' "
                                        "ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                        % (email_col, contact_table, addr_table, fk, email_col, email_col),
                                        (account_id,),
                                    )
                                    row = cur.fetchone()
                                    if row and row.get(email_col):
                                        return row[email_col]
                            except Exception:
                                continue
        except Exception:
            pass
        finally:
            conn.close()
        return None

    def _fetch_customer_phone(self, customer_id):
        """
        Get customer phone from Fishbowl via contact table (and address).
        Schema: customer.accountId -> contact.accountId; contact.typeId -> contacttype.id;
        contact.datus holds the value; contacttype.name is Main, Home, Work, Mobile, Fax, etc.
        """
        if not customer_id:
            return None
        conn = self._get_connection()
        try:
            for table in ('CUSTOMER', 'Customer', 'customer'):
                for col in ('phone', 'primaryPhone', 'Phone', 'phoneNumber', 'phone1', 'mainPhone', 'main', 'Main'):
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT `%s` FROM `%s` WHERE id = %%s" % (col, table),
                                (customer_id,),
                            )
                            row = cur.fetchone()
                            if row and row.get(col):
                                return row[col]
                    except Exception:
                        continue
            account_id = None
            for cust_table in ('customer', 'CUSTOMER', 'Customer'):
                for acc_col in ('accountId', 'account_id'):
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT `%s` FROM `%s` WHERE id = %%s" % (acc_col, cust_table),
                                (customer_id,),
                            )
                            row = cur.fetchone()
                            if row and row.get(acc_col):
                                account_id = row[acc_col]
                                break
                    except Exception:
                        continue
                if account_id is not None:
                    break
            if not account_id:
                account_id = customer_id
            # Direct path: contact.datus + contacttype (schema: contact.accountId, contact.typeId, contact.datus; contacttype.name)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT c.datus
                        FROM contact c
                        INNER JOIN contacttype t ON t.id = c.typeId
                        WHERE c.accountId = %s
                          AND t.name IN ('Main', 'Home', 'Work', 'Mobile', 'Fax', 'Pager', 'Phone', 'Main Phone', 'Work Phone', 'Mobile Phone')
                          AND c.datus IS NOT NULL AND TRIM(COALESCE(c.datus, '')) != ''
                          AND c.datus NOT LIKE '%%@%%'
                        ORDER BY c.defaultFlag DESC, c.id
                        LIMIT 1
                        """,
                        (account_id,),
                    )
                    row = cur.fetchone()
                    if row and row.get('datus'):
                        return row['datus']
            except Exception:
                pass
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT c.datus FROM contact c
                        WHERE c.accountId = %s
                          AND c.datus IS NOT NULL AND TRIM(COALESCE(c.datus, '')) != ''
                          AND c.datus NOT LIKE '%%@%%'
                        ORDER BY c.defaultFlag DESC, c.id
                        LIMIT 1
                        """,
                        (account_id,),
                    )
                    row = cur.fetchone()
                    if row and row.get('datus'):
                        return row['datus']
            except Exception:
                pass
            phone_type_names = ("Main", "Home", "Work", "Mobile", "Fax", "Pager", "main", "home", "work", "mobile", "fax", "pager", "Phone")
            data_cols = ('datus', 'data', 'Data', 'value', 'Value')
            for contact_table in ('contact', 'CONTACT', 'Contact'):
                for type_table in ('contacttype', 'ContactType', 'CONTACTTYPE', 'contact_type'):
                    for type_name_col in ('name', 'Name', 'type', 'Type', 'description'):
                        for data_col in data_cols:
                            for fk in ('accountId', 'account_id'):
                                try:
                                    with conn.cursor() as cur:
                                        placeholders = ','.join(['%s'] * len(phone_type_names))
                                        cur.execute(
                                            "SELECT c.`%s` FROM `%s` c "
                                            "INNER JOIN `%s` t ON t.id = c.typeId "
                                            "WHERE c.`%s` = %%s AND t.`%s` IN (%s) "
                                            "AND c.`%s` IS NOT NULL AND TRIM(COALESCE(c.`%s`, '')) != '' "
                                            "AND c.`%s` NOT LIKE '%%@%%' "
                                            "ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                            % (data_col, contact_table, type_table, fk, type_name_col, placeholders, data_col, data_col, data_col),
                                            (account_id,) + tuple(phone_type_names),
                                        )
                                        row = cur.fetchone()
                                        if row and row.get(data_col):
                                            return row[data_col]
                                except Exception:
                                    continue
            phone_cols = (
                'phone', 'phoneNumber', 'Phone', 'phone1', 'primaryPhone',
                'main', 'Main', 'home', 'Home', 'work', 'Work', 'mobile', 'Mobile',
                'fax', 'Fax', 'pager', 'Pager', 'other', 'Other', 'contactPhone', 'phoneNum',
            )
            for contact_table in ('contact', 'CONTACT', 'Contact'):
                for fk in ('accountId', 'account_id'):
                    for data_col in data_cols:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT c.`%s` FROM `%s` c "
                                    "WHERE c.`%s` = %%s AND c.`%s` IS NOT NULL AND TRIM(COALESCE(c.`%s`, '')) != '' "
                                    "AND c.`%s` NOT LIKE '%%@%%' "
                                    "ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                    % (data_col, contact_table, fk, data_col, data_col, data_col),
                                    (account_id,),
                                )
                                row = cur.fetchone()
                                if row and row.get(data_col):
                                    return row[data_col]
                        except Exception:
                            continue
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT c.datus FROM `%s` c WHERE c.`%s` = %%s AND c.datus IS NOT NULL AND TRIM(COALESCE(c.datus, '')) != '' AND c.datus NOT LIKE '%%@%%' ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                % (contact_table, fk),
                                (account_id,),
                            )
                            row = cur.fetchone()
                            if row and row.get('datus'):
                                return row['datus']
                    except Exception:
                        pass
                    for phone_col in phone_cols:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT c.`%s` FROM `%s` c "
                                    "WHERE c.`%s` = %%s AND c.`%s` IS NOT NULL AND TRIM(COALESCE(c.`%s`, '')) != '' "
                                    "ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                    % (phone_col, contact_table, fk, phone_col, phone_col),
                                    (account_id,),
                                )
                                row = cur.fetchone()
                                if row and row.get(phone_col):
                                    return row[phone_col]
                        except Exception:
                            continue
                for addr_table in ('address', 'ADDRESS', 'Address'):
                    for fk2 in ('accountId', 'account_id'):
                        for phone_col in phone_cols:
                            try:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "SELECT a.`%s` FROM `%s` c "
                                        "INNER JOIN `%s` a ON a.id = c.addressId "
                                        "WHERE c.`%s` = %%s AND a.`%s` IS NOT NULL AND TRIM(COALESCE(a.`%s`, '')) != '' "
                                        "ORDER BY c.defaultFlag DESC, c.id LIMIT 1"
                                        % (phone_col, contact_table, addr_table, fk2, phone_col, phone_col),
                                        (account_id,),
                                    )
                                    row = cur.fetchone()
                                    if row and row.get(phone_col):
                                        return row[phone_col]
                            except Exception:
                                continue
            for contact_table in ('contact', 'CONTACT', 'Contact'):
                for fk in ('customerId', 'customer_id', 'accountId', 'account_id'):
                    for phone_col in phone_cols:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT c.`%s` FROM `%s` c WHERE c.`%s` = %%s AND c.`%s` IS NOT NULL AND TRIM(COALESCE(c.`%s`, '')) != '' ORDER BY c.id LIMIT 1"
                                    % (phone_col, contact_table, fk, phone_col, phone_col),
                                    (customer_id if 'customer' in fk.lower() else account_id,),
                                )
                                row = cur.fetchone()
                                if row and row.get(phone_col):
                                    return row[phone_col]
                        except Exception:
                            continue
        except Exception:
            pass
        finally:
            conn.close()
        return None

    def _fetch_customer_name(self, customer_id):
        """Get customer name from Fishbowl CUSTOMER table."""
        if not customer_id:
            return None
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM CUSTOMER WHERE id = %s",
                    (customer_id,),
                )
                row = cur.fetchone()
                return row['name'] if row else None
        finally:
            conn.close()

    def _fetch_order_lines(self, so_id):
        """
        Fetch SOITEM lines. Product number from product.num (soitem.productId -> product.id).
        Schema: SOITEM has soId, productId, productNum, qtyOrdered, unitPrice, totalPrice, soLineItem (no partId/sortId/productPrice).
        """
        conn = self._get_connection()
        try:
            for soitem_table in ('SOITEM', 'soitem', 'Soitem'):
                for product_table in ('product', 'Product', 'PRODUCT'):
                    try:
                        with conn.cursor() as cur:
                            cur.execute("""
                                SELECT si.soId, si.productId, si.productNum, si.description,
                                       si.qtyOrdered AS qty, si.unitPrice AS unit_price,
                                       si.totalPrice AS total_price, si.soLineItem AS sequence,
                                       p.num AS product_num
                                FROM %s si
                                LEFT JOIN %s p ON p.id = si.productId
                                WHERE si.soId = %%s
                                ORDER BY COALESCE(si.soLineItem, 0), si.id
                            """ % (soitem_table, product_table), (so_id,))
                            rows = cur.fetchall()
                            for r in rows:
                                r.setdefault('product_num', r.get('productNum') or r.get('product_num'))
                            return rows
                    except Exception:
                        continue
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT soId, productId, productNum, description, qtyOrdered AS qty,
                           unitPrice AS unit_price, totalPrice AS total_price, soLineItem AS sequence,
                           productNum AS product_num
                    FROM SOITEM
                    WHERE soId = %s
                    ORDER BY COALESCE(soLineItem, 0), id
                """, (so_id,))
                return cur.fetchall()
        except Exception:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM SOITEM WHERE soId = %s ORDER BY id", (so_id,))
                rows = cur.fetchall()
                result = []
                for r in rows:
                    product_num_val = (
                        next((r[k] for k in r if k and k.lower() == 'productnum'), None)
                        or next((r[k] for k in r if k and k.lower() == 'product_num'), None)
                        or next((r[k] for k in r if k and 'num' in (k or '').lower()), None)
                    )
                    result.append({
                        'soId': r.get('soId'),
                        'partId': r.get('productId'),
                        'partNum': product_num_val,
                        'description': r.get('description'),
                        'qty': r.get('qtyOrdered') or r.get('qty') or r.get('quantity', 1),
                        'unit_price': r.get('unitPrice') or r.get('productPrice') or r.get('unit_price') or 0,
                        'total_price': r.get('totalPrice') or r.get('total_price') or 0,
                        'sequence': r.get('soLineItem') or r.get('sortId') or r.get('sequence', 0),
                        'product_num': product_num_val,
                    })
                return result
        finally:
            conn.close()
