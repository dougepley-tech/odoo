# -*- coding: utf-8 -*-

import logging
import random
import time
import uuid
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero

_logger = logging.getLogger(__name__)

from .fishbowl_defaults import default_fishbowl_sync_config


class FishbowlImportInventoryWizard(models.TransientModel):
    _name = 'fishbowl.import.inventory.wizard'
    _description = 'Sync Fishbowl on-hand to Odoo'

    @api.model
    def _default_config_id(self):
        return default_fishbowl_sync_config(self.env)

    config_id = fields.Many2one(
        'fishbowl.sync.config',
        required=True,
        domain=[('active', '=', True)],
        default=_default_config_id,
    )
    part_number = fields.Char(
        string='Part number',
        help='Fishbowl part number or Odoo internal reference (default_code). '
        'Leave empty to sync every storable Odoo product that has an internal reference: '
        'the import walks Odoo products first and loads Fishbowl quantities only for matching parts '
        '(not the full Fishbowl catalog). '
        'Spaces and hyphens are matched like the inventory export script. '
        'Products whose internal reference starts with ds_ (case-insensitive) are never imported.',
    )
    reconcile_zero_extra = fields.Boolean(
        string='Zero Odoo locations missing from Fishbowl',
        default=True,
        help='For each product updated from this import, set internal stock to 0 at Odoo locations '
        'that are not part of this Fishbowl snapshot (so Odoo matches Fishbowl exactly for those products). '
        'Disable if you only want to add/update quantities without clearing other bins.',
    )
    dry_run = fields.Boolean(string='Dry run')
    result_message = fields.Text(string='Result', readonly=True)

    def _ctx(self):
        return {
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
            'fishbowl_import': True,
        }

    @staticmethod
    def _inventory_skip_internal_reference(default_code):
        """Skip inventory sync for Odoo internal references that start with ``ds_`` (case-insensitive)."""
        code = (default_code or '').strip()
        return bool(code) and code.lower().startswith('ds_')

    @staticmethod
    def _is_pg_serialization_failure(exc):
        """True for PostgreSQL ``could not serialize access due to concurrent update`` (and variants)."""
        e = exc
        while e is not None:
            if type(e).__name__ == 'SerializationFailure':
                return True
            if getattr(e, 'pgcode', None) in ('40001', 40001):
                return True
            e = getattr(e, '__cause__', None) or getattr(e, 'orig', None)
        return False

    def _apply_fishbowl_inventory_qty(self, prod, loc, qty):
        """Set counted qty on ``stock.quant`` for (product, location); retries on serialization conflicts."""
        self.ensure_one()
        max_retries = 8
        for attempt in range(max_retries):
            try:
                with self.env.cr.savepoint():
                    Quant = self.env['stock.quant'].with_context(**self._ctx(), inventory_mode=True)
                    q = Quant.search(
                        [
                            ('product_id', '=', prod.id),
                            ('location_id', '=', loc.id),
                        ],
                        limit=1,
                    )
                    if q:
                        q.inventory_quantity = qty
                        q.action_apply_inventory()
                    else:
                        Quant.create(
                            {
                                'product_id': prod.id,
                                'location_id': loc.id,
                                'inventory_quantity': qty,
                            }
                        ).action_apply_inventory()
                return
            except Exception as e:
                if self._is_pg_serialization_failure(e) and attempt < max_retries - 1:
                    time.sleep(0.05 * (2**attempt) + random.random() * 0.15)
                    continue
                raise

    def _log_inventory_line(self, Log, message, level, fishbowl_ref, batch_id):
        """Write import log; if the main transaction is aborted, log on a fresh cursor."""
        try:
            Log.log_line(
                'inventory',
                message,
                level=level,
                fishbowl_ref=fishbowl_ref or '',
                batch_id=batch_id,
            )
        except Exception:
            try:
                with self.env.registry.cursor() as cr:
                    env = api.Environment(cr, self.env.uid, dict(self.env.context))
                    env['fishbowl.import.log'].sudo().log_line(
                        'inventory',
                        message,
                        level=level,
                        fishbowl_ref=fishbowl_ref or '',
                        batch_id=batch_id,
                    )
                    cr.commit()
            except Exception as e2:
                _logger.error(
                    'Fishbowl inventory import: could not persist log line: %s (message was: %s)',
                    e2,
                    message[:500],
                )

    def _zero_quant_inventory_apply(self, quant):
        """Set quant on-hand to 0 via inventory mode; retries on serialization conflicts."""
        self.ensure_one()
        qid = quant.id
        max_retries = 8
        for attempt in range(max_retries):
            try:
                with self.env.cr.savepoint():
                    q = self.env['stock.quant'].with_context(
                        **self._ctx(), inventory_mode=True
                    ).browse(qid)
                    q.inventory_quantity = 0.0
                    q.action_apply_inventory()
                return
            except Exception as e:
                if self._is_pg_serialization_failure(e) and attempt < max_retries - 1:
                    time.sleep(0.05 * (2**attempt) + random.random() * 0.15)
                    continue
                raise

    def _reconcile_zero_internal_quants_not_in_sync(
        self,
        config,
        company,
        product_ids,
        synced_product_location_pairs,
        batch,
    ):
        """Set inventory quantity to 0 on internal stock quants not in the Fishbowl sync set."""
        self.ensure_one()
        if not product_ids or not self.reconcile_zero_extra:
            return 0
        Location = self.env['stock.location'].sudo()
        internal_locs = Location.search(
            [
                ('usage', '=', 'internal'),
                ('company_id', 'in', [False, company.id]),
            ]
        )
        if not internal_locs:
            return 0
        Quant = self.env['stock.quant'].with_context(**self._ctx(), inventory_mode=True)
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        zeroed = 0
        pid_list = list(product_ids)
        quants = Quant.search(
            [
                ('product_id', 'in', pid_list),
                ('location_id', 'in', internal_locs.ids),
            ]
        )
        for q in quants:
            pid = q.product_id.id
            if (pid, q.location_id.id) in synced_product_location_pairs:
                continue
            on_hand = float(q.quantity or 0.0)
            if float_is_zero(on_hand, precision_digits=precision):
                continue
            if self.dry_run:
                zeroed += 1
                continue
            self._zero_quant_inventory_apply(q)
            zeroed += 1
        return zeroed

    def action_import(self):
        self.ensure_one()
        config = self.config_id
        batch = uuid.uuid4().hex
        Log = self.env['fishbowl.import.log']
        company = config.company_id
        wh = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        default_loc = config.inventory_location_id
        if not default_loc and wh:
            default_loc = wh.lot_stock_id
        if not default_loc:
            raise UserError('Set default inventory location on Fishbowl config or configure a warehouse.')
        part_filter = (self.part_number or '').strip() or None
        Product = self.env['product.product']
        if part_filter:
            if self._inventory_skip_internal_reference(part_filter):
                rows = []
                code_map_fb = {}
                odoo_products_in_scope = Product.browse()
            else:
                rows, code_map_fb = config.fetch_qty_on_hand_rows(
                    part_num_filter=part_filter, return_code_map=True
                )
                odoo_products_in_scope = Product.search(
                    [
                        ('active', '=', True),
                        ('product_tmpl_id.is_storable', '=', True),
                        ('company_id', 'in', [False, company.id]),
                        ('default_code', '=', part_filter),
                    ]
                )
        else:
            odoo_products_in_scope = Product.search(
                [
                    ('active', '=', True),
                    ('product_tmpl_id.is_storable', '=', True),
                    ('company_id', 'in', [False, company.id]),
                    ('default_code', '!=', False),
                ]
            )
            odoo_products_in_scope = odoo_products_in_scope.filtered(
                lambda p: (p.default_code or '').strip()
                and not self._inventory_skip_internal_reference(p.default_code)
            )
            codes = list(
                dict.fromkeys((p.default_code or '').strip() for p in odoo_products_in_scope)
            )
            rows, code_map_fb = config.fetch_qty_on_hand_rows(
                part_nums_filter=codes, return_code_map=True
            )
        row_count = len(rows)
        odoo_product_count = len(odoo_products_in_scope)
        updated = 0
        skipped_no_odoo_product = 0
        skipped_non_storable = 0
        skipped_ds_prefix = 0
        default_location_rows = 0
        default_loc_details = set()
        errors = 0
        synced_pairs = set()
        affected_product_ids = set(odoo_products_in_scope.ids)
        rows_by_fb_part_id = defaultdict(list)
        for row in rows:
            pid = row.get('part_id')
            if pid is None:
                continue
            try:
                ipid = int(pid)
            except (TypeError, ValueError):
                continue
            rows_by_fb_part_id[ipid].append(row)

        for prod in odoo_products_in_scope:
            code = (prod.default_code or '').strip()
            if self._inventory_skip_internal_reference(code):
                skipped_ds_prefix += 1
                continue
            if not prod.product_tmpl_id.is_storable:
                skipped_non_storable += 1
                continue
            for fb_pid in code_map_fb.get(code, []):
                fb_pid = int(fb_pid)
                for row in rows_by_fb_part_id.get(fb_pid, []):
                    part_num = (row.get('part_num') or '').strip()
                    if not part_num:
                        continue
                    try:
                        qty = float(row.get('qty') or 0)
                        gname = row.get('group_name')
                        tname = row.get('type_name')
                        lname = row.get('location_name')
                        loc, loc_src, built_path = config.resolve_inventory_location_for_import(
                            company, gname, tname, lname, default_loc
                        )
                        used_default_loc = loc_src == 'default'
                        synced_pairs.add((prod.id, loc.id))
                        if used_default_loc:
                            default_location_rows += 1
                            fb_loc = '%s / %s / %s' % (
                                (gname or '—'),
                                (tname or '—'),
                                (lname or '—'),
                            )
                            default_loc_details.add(
                                (code, fb_loc, (built_path or '').strip() or '—')
                            )
                        if self.dry_run:
                            updated += 1
                            continue
                        self._apply_fishbowl_inventory_qty(prod, loc, qty)
                        updated += 1
                    except Exception as e:
                        errors += 1
                        self._log_inventory_line(
                            Log,
                            'Part %s: %s' % (part_num, e),
                            'error',
                            part_num,
                            batch,
                        )

        zeroed_extra = self._reconcile_zero_internal_quants_not_in_sync(
            config,
            company,
            affected_product_ids,
            synced_pairs,
            batch,
        )

        scope = 'part %s' % part_filter if part_filter else 'all Odoo products with internal reference'
        msg = (
            'Inventory import finished. Scope: %s. Odoo products in scope: %s. Fishbowl rows: %s. '
            'Applied: %s. Skipped (no Odoo product): %s. Skipped (not storable): %s. '
            'Skipped (internal ref ds_*): %s. Errors: %s. '
            'Extra Odoo locations zeroed: %s. Dry run: %s. Batch: %s.'
        ) % (
            scope,
            odoo_product_count,
            row_count,
            updated,
            skipped_no_odoo_product,
            skipped_non_storable,
            skipped_ds_prefix,
            errors,
            zeroed_extra,
            self.dry_run,
            batch,
        )
        if default_location_rows:
            detail_lines = sorted(
                '• %s | Fishbowl location: %s | Built path (no Odoo match): %s'
                % (c, fb, bp)
                for c, fb, bp in default_loc_details
            )
            max_lines = 500
            omitted = 0
            if len(detail_lines) > max_lines:
                omitted = len(detail_lines) - max_lines
                detail_lines = detail_lines[:max_lines]
            detail_body = '\n'.join(detail_lines)
            if omitted:
                detail_body += (
                    '\n\n… %s more unique product/location line(s) omitted; narrow scope or fix paths and re-run.'
                    % omitted
                )
            warn_msg = (
                'Warning: %s Fishbowl row(s) used the default Odoo inventory location '
                '(no matching Odoo path and no fishbowl.location.map). '
                '%s unique product / Fishbowl location combination(s) below — '
                'create matching stock locations under that path or add fishbowl.location.map rows.\n\n%s'
                % (default_location_rows, len(default_loc_details), detail_body)
            )
            self._log_inventory_line(
                Log,
                warn_msg,
                'warning',
                part_filter or '',
                batch,
            )
            msg += (
                '\n\nDefault location used on %s row(s); %s unique product/location mapping(s) — '
                'see the warning line in Fishbowl import log for the full list.'
                % (default_location_rows, len(default_loc_details))
            )
        self._log_inventory_line(
            Log,
            msg,
            'info',
            part_filter or '',
            batch,
        )
        self.write({'result_message': msg})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
