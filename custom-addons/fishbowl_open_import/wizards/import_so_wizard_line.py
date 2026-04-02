# -*- coding: utf-8 -*-

from odoo import fields, models


class FishbowlImportSoWizardLine(models.TransientModel):
    _name = 'fishbowl.import.so.wizard.line'
    _description = 'Fishbowl SO import wizard preview row'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'fishbowl.import.so.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='Order', default=10)
    fishbowl_so_id = fields.Integer(string='Fishbowl SO id', required=True)
    so_num = fields.Char(string='Fishbowl SO #')
    selected = fields.Boolean(string='Import', default=True)
