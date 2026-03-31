# -*- coding: utf-8 -*-

import uuid

from odoo import api, fields, models

from .fishbowl_defaults import default_fishbowl_sync_config


class FishbowlImportMasterWizard(models.TransientModel):
    _name = 'fishbowl.import.master.wizard'
    _description = 'Fishbowl master data import'

    @api.model
    def _default_config_id(self):
        return default_fishbowl_sync_config(self.env)

    config_id = fields.Many2one(
        'fishbowl.sync.config',
        required=True,
        domain=[('active', '=', True)],
        default=_default_config_id,
    )
    import_customers = fields.Boolean(string='Import / update customers', default=True)
    import_vendors = fields.Boolean(string='Import / update vendors', default=True)
    result_message = fields.Text(string='Result', readonly=True)

    def _ctx(self):
        return {
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
            'fishbowl_import': True,
        }

    def action_import(self):
        self.ensure_one()
        config = self.config_id
        batch = uuid.uuid4().hex
        Log = self.env['fishbowl.import.log']
        ctx = self._ctx()
        conn = config._get_connection()
        cust_n = 0
        vend_n = 0
        try:
            with conn.cursor() as cur:
                if self.import_customers:
                    try:
                        cur.execute('SELECT id FROM customer')
                        for r in cur.fetchall():
                            try:
                                config.with_context(**ctx).create_or_get_customer_partner(r['id'])
                                cust_n += 1
                            except Exception:
                                pass
                    except Exception:
                        Log.log_line(
                            'master',
                            'Customer import query failed (check MySQL schema).',
                            level='warning',
                            batch_id=batch,
                        )
                if self.import_vendors:
                    try:
                        cur.execute('SELECT id FROM vendor')
                        for r in cur.fetchall():
                            try:
                                config.with_context(**ctx).create_or_get_vendor_partner(r['id'])
                                vend_n += 1
                            except Exception:
                                pass
                    except Exception:
                        Log.log_line(
                            'master',
                            'Vendor import query failed.',
                            level='warning',
                            batch_id=batch,
                        )
        finally:
            conn.close()
        msg = 'Customers upserted: %s, vendors upserted: %s. Batch: %s' % (cust_n, vend_n, batch)
        self.write({'result_message': msg})
        Log.log_line('master', msg, level='info', batch_id=batch)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
