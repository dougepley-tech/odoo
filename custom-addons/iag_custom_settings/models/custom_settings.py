# -*- coding: utf-8 -*-

from odoo import api, fields, models


class IagCustomSettings(models.Model):
    _name = "iag.custom.settings"
    _description = "IAG Custom Settings"

    singleton_key = fields.Integer(default=1, required=True)
    name = fields.Char(default="IAG Custom Settings", required=True)
    lock_unlock_group_ids = fields.Many2many(
        "res.groups",
        "iag_custom_settings_lock_unlock_groups_rel",
        "settings_id",
        "group_id",
        string="Groups Allowed to Lock/Unlock Sales Orders",
        help="Users in these groups can use the Lock and Unlock buttons on sales orders (in addition to Sales Managers).",
    )

    iag_us_check_enable_check = fields.Boolean(
        string="Enable US check face layout",
        default=False,
        help="Applies margin adjustments on the printed check (payee address and memo).",
    )
    iag_us_check_enable_stub = fields.Boolean(
        string="Enable US check stub layout",
        default=False,
        help="Adds payee address on the check stub with configurable margin.",
    )
    iag_us_check_margin_payee = fields.Integer(
        string="Check — payee address margin (px)",
        default=30,
    )
    iag_us_check_margin_memo = fields.Integer(
        string="Check — memo margin (px)",
        default=30,
    )
    iag_us_check_margin_stub_payee = fields.Integer(
        string="Stub — payee address margin (px)",
        default=20,
    )

    _sql_constraints = [
        (
            "iag_custom_settings_singleton_key_unique",
            "UNIQUE(singleton_key)",
            "Only one IAG settings record is allowed.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.lock_unlock_group_ids:
                rec._sync_lock_unlock_groups([], rec.lock_unlock_group_ids.ids)
            rec._iag_sync_us_check_views()
        return records

    def write(self, vals):
        old_groups = {rec.id: rec.lock_unlock_group_ids.ids for rec in self}
        res = super().write(vals)
        if "lock_unlock_group_ids" in vals:
            for rec in self:
                self._sync_lock_unlock_groups(
                    old_groups.get(rec.id, []),
                    rec.lock_unlock_group_ids.ids,
                )
        if any(k.startswith("iag_us_check") for k in vals):
            for rec in self:
                rec._iag_sync_us_check_views()
        return res

    @api.model
    def _sync_lock_unlock_groups(self, previous_ids, current_ids):
        """Add/remove implied technical group on res.groups when selection changes."""
        custom_group = self.env.ref("iag_custom_settings.group_sale_order_lock_unlock")
        Groups = self.env["res.groups"].sudo()
        previous = set(previous_ids or [])
        current = set(current_ids or [])
        for gid in previous - current:
            g = Groups.browse(gid).exists()
            if g:
                g.write({"implied_ids": [(3, custom_group.id)]})
        for gid in current - previous:
            g = Groups.browse(gid).exists()
            if g:
                g.write({"implied_ids": [(4, custom_group.id)]})

    def _iag_sync_us_check_views(self):
        self.ensure_one()
        v_check = self.env.ref(
            "iag_custom_settings.ckus_check_inherit_iag", raise_if_not_found=False
        )
        v_stub = self.env.ref(
            "iag_custom_settings.ckus_stub_inherit_iag", raise_if_not_found=False
        )
        if v_check:
            v_check.write(
                {
                    "arch": self._iag_us_check_arch_check(),
                    "active": self.iag_us_check_enable_check,
                }
            )
        if v_stub:
            v_stub.write(
                {
                    "arch": self._iag_us_check_arch_stub(),
                    "active": self.iag_us_check_enable_stub,
                }
            )

    def _iag_us_check_arch_check(self):
        payee = int(self.iag_us_check_margin_payee or 0)
        memo = int(self.iag_us_check_margin_memo or 0)
        return """<data>
  <xpath expr="//div[@class='ckus_payee_addr']" position="attributes">
    <attribute name="style">margin-top: %spx;</attribute>
  </xpath>
  <xpath expr="//div[@class='ckus_memo']" position="attributes">
    <attribute name="style">margin-top: %spx;</attribute>
  </xpath>
</data>""" % (
            payee,
            memo,
        )

    def _iag_us_check_arch_stub(self):
        margin = int(self.iag_us_check_margin_stub_payee or 0)
        return """<data>
  <xpath expr="//div[@class='stub_total_amount']" position="after">
    <div class="ckus_payee_addr" style="margin-top: %spx;" t-out="page['partner_id']" t-options="{&quot;widget&quot;: &quot;contact&quot;, &quot;fields&quot;: [&quot;address&quot;, &quot;name&quot;], &quot;no_marker&quot;: True}"/>
  </xpath>
</data>""" % (
            margin,
        )
