# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ShippoValidateWarningWizard(models.TransientModel):
    _name = 'shippo.validate.warning.wizard'
    _description = 'Confirm validation without Shippo label'

    picking_id = fields.Many2one('stock.picking', required=True, ondelete='cascade')
    service_name = fields.Char(
        related='picking_id.shippo_selected_service',
        string='Selected Delivery Service',
        readonly=True,
    )

    def action_validate_anyway(self):
        self.ensure_one()
        return self.picking_id.with_context(skip_shippo_label_warning=True).button_validate()
