# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class BigCommerceMappingDeduplicateLine(models.TransientModel):
    _name = 'bigcommerce.mapping.deduplicate.line'
    _description = 'Duplicate Mapping Line (choose which product to keep)'

    wizard_id = fields.Many2one(
        'bigcommerce.mapping.deduplicate.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    mapping_id = fields.Many2one(
        'bigcommerce.product.mapping',
        string='Mapping',
        required=True,
        ondelete='cascade',
    )
    config_id = fields.Many2one(
        'bigcommerce.config',
        related='mapping_id.config_id',
        string='Configuration',
        readonly=True,
    )
    bigcommerce_id = fields.Integer(
        related='mapping_id.bigcommerce_id',
        string='BigCommerce Product ID',
        readonly=True,
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        related='mapping_id.product_tmpl_id',
        string='Product',
        readonly=True,
    )
    product_name = fields.Char(
        related='mapping_id.product_tmpl_id.name',
        string='Product Name',
        readonly=True,
    )
    default_code = fields.Char(
        related='mapping_id.product_tmpl_id.default_code',
        string='Internal Reference',
        readonly=True,
    )
    keep_this = fields.Boolean(
        string='Keep this product',
        default=False,
        help='Check exactly one product per duplicate group to keep; others will be removed.',
    )
    # BOM info (when Manufacturing / mrp is installed)
    bom_count = fields.Integer(
        string='Has BOM',
        compute='_compute_bom_info',
        help='Number of Bills of Materials where this product is the main product.',
    )
    used_in_bom_count = fields.Integer(
        string='Used in BOMs',
        compute='_compute_bom_info',
        help='Number of BOMs where this product is used as a component.',
    )

    @api.depends('product_tmpl_id')
    def _compute_bom_info(self):
        """Compute BOM counts when mrp module is installed (no dependency added)."""
        for line in self:
            bom_count = 0
            used_in_bom_count = 0
            if line.product_tmpl_id and 'mrp.bom' in self.env:
                try:
                    bom_count = self.env['mrp.bom'].search_count([
                        ('product_tmpl_id', '=', line.product_tmpl_id.id),
                    ])
                except Exception:
                    pass
                try:
                    if 'mrp.bom.line' in self.env:
                        variant_ids = line.product_tmpl_id.product_variant_ids.ids
                        if variant_ids:
                            used_in_bom_count = self.env['mrp.bom.line'].search_count([
                                ('product_id', 'in', variant_ids),
                            ])
                except Exception:
                    pass
            line.bom_count = bom_count
            line.used_in_bom_count = used_in_bom_count

    def action_open_product(self):
        """Open the product form so the user can see full details (BOM, etc.) before deciding."""
        self.ensure_one()
        if not self.product_tmpl_id:
            return
        # Open product in main window so user can use browser Back to return to duplicate list
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.product_tmpl_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class BigCommerceVariantDeduplicateLine(models.TransientModel):
    _name = 'bigcommerce.variant.deduplicate.line'
    _description = 'Duplicate Variant Mapping Line (choose which variant to keep)'

    wizard_id = fields.Many2one(
        'bigcommerce.mapping.deduplicate.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    variant_mapping_id = fields.Many2one(
        'bigcommerce.variant.mapping',
        string='Variant Mapping',
        required=True,
        ondelete='cascade',
    )
    config_id = fields.Many2one(
        'bigcommerce.config',
        related='variant_mapping_id.config_id',
        string='Configuration',
        readonly=True,
    )
    bigcommerce_variant_id = fields.Integer(
        related='variant_mapping_id.bigcommerce_variant_id',
        string='BigCommerce Variant ID',
        readonly=True,
    )
    product_variant_id = fields.Many2one(
        'product.product',
        related='variant_mapping_id.product_variant_id',
        string='Variant',
        readonly=True,
    )
    variant_name = fields.Char(
        related='variant_mapping_id.product_variant_id.display_name',
        string='Variant Name',
        readonly=True,
    )
    default_code = fields.Char(
        related='variant_mapping_id.product_variant_id.default_code',
        string='SKU / Internal Ref',
        readonly=True,
    )
    keep_this = fields.Boolean(
        string='Keep this variant',
        default=False,
        help='Check exactly one variant per duplicate group to keep; others will have their mapping removed.',
    )

    def action_open_variant(self):
        """Open the product (template) form so the user can see the variant in context."""
        self.ensure_one()
        if not self.product_variant_id:
            return
        # Open the variant's product template in form view so user can see the variant
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.product_variant_id.product_tmpl_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class BigCommerceMappingDeduplicateWizard(models.TransientModel):
    _name = 'bigcommerce.mapping.deduplicate.wizard'
    _description = 'Deduplicate BigCommerce Product Mappings'

    line_ids = fields.One2many(
        'bigcommerce.mapping.deduplicate.line',
        'wizard_id',
        string='Duplicate product mappings',
        readonly=True,
    )
    variant_line_ids = fields.One2many(
        'bigcommerce.variant.deduplicate.line',
        'wizard_id',
        string='Duplicate variant mappings',
        readonly=True,
    )
    duplicate_count = fields.Integer(
        string='Duplicate product groups',
        default=0,
        readonly=True,
    )
    line_count = fields.Integer(
        string='Product mappings to review',
        default=0,
        readonly=True,
    )
    variant_duplicate_count = fields.Integer(
        string='Duplicate variant groups',
        default=0,
        readonly=True,
    )
    variant_line_count = fields.Integer(
        string='Variant mappings to review',
        default=0,
        readonly=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('ready', 'Ready'),
            ('done', 'Done'),
        ],
        string='State',
        default='draft',
        readonly=True,
    )
    result_message = fields.Char(
        string='Result',
        readonly=True,
    )

    def action_find_duplicates(self):
        """Find duplicate product and variant mappings (active products only) and load lines."""
        self.ensure_one()
        Mapping = self.env['bigcommerce.product.mapping']
        VariantMapping = self.env['bigcommerce.variant.mapping']
        # --- Product duplicates: (config_id, bigcommerce_id) with more than one mapping (active products only)
        self.env.cr.execute("""
            SELECT m.config_id, m.bigcommerce_id, COUNT(*) AS cnt
            FROM bigcommerce_product_mapping m
            JOIN product_template pt ON pt.id = m.product_tmpl_id AND pt.active = true
            GROUP BY m.config_id, m.bigcommerce_id
            HAVING COUNT(*) > 1
        """)
        product_rows = self.env.cr.fetchall()
        line_vals = []
        for (config_id, bigcommerce_id, _cnt) in product_rows:
            mappings = Mapping.search([
                ('config_id', '=', config_id),
                ('bigcommerce_id', '=', bigcommerce_id),
                ('product_tmpl_id.active', '=', True),
            ], order='id')
            for idx, mapping in enumerate(mappings):
                line_vals.append({
                    'wizard_id': self.id,
                    'mapping_id': mapping.id,
                    'keep_this': idx == 0,
                })
        self.env['bigcommerce.mapping.deduplicate.line'].create(line_vals)
        # --- Variant duplicates: (config_id, bigcommerce_variant_id) with more than one mapping (active products only)
        self.env.cr.execute("""
            SELECT vm.config_id, vm.bigcommerce_variant_id, COUNT(*) AS cnt
            FROM bigcommerce_variant_mapping vm
            JOIN product_product pp ON pp.id = vm.product_variant_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id AND pt.active = true
            GROUP BY vm.config_id, vm.bigcommerce_variant_id
            HAVING COUNT(*) > 1
        """)
        variant_rows = self.env.cr.fetchall()
        variant_line_vals = []
        for (config_id, bigcommerce_variant_id, _cnt) in variant_rows:
            variant_mappings = VariantMapping.search([
                ('config_id', '=', config_id),
                ('bigcommerce_variant_id', '=', bigcommerce_variant_id),
                ('product_variant_id.product_tmpl_id.active', '=', True),
            ], order='id')
            for idx, vm in enumerate(variant_mappings):
                variant_line_vals.append({
                    'wizard_id': self.id,
                    'variant_mapping_id': vm.id,
                    'keep_this': idx == 0,
                })
        self.env['bigcommerce.variant.deduplicate.line'].create(variant_line_vals)
        if not product_rows and not variant_rows:
            raise UserError(
                'No duplicate product or variant mappings found among active products. '
                'Each (Configuration, BigCommerce Product/Variant ID) has at most one active mapping.'
            )
        self.write({
            'state': 'ready',
            'duplicate_count': len(product_rows),
            'line_count': len(line_vals),
            'variant_duplicate_count': len(variant_rows),
            'variant_line_count': len(variant_line_vals),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_deduplicate(self):
        """Archive duplicate products (keep mappings) and remove duplicate variant mappings. Exactly one per group must be kept."""
        self.ensure_one()
        if self.state != 'ready':
            raise UserError('Please run "Find Duplicates" first.')
        # --- Product duplicate groups: archive templates not kept
        groups = {}
        for line in self.line_ids:
            key = (line.config_id.id, line.bigcommerce_id)
            if key not in groups:
                groups[key] = []
            groups[key].append(line)
        products_to_archive = self.env['product.template']
        for key, lines in groups.items():
            kept = [l for l in lines if l.keep_this]
            if len(kept) != 1:
                config_name = lines[0].config_id.name if lines else '?'
                raise ValidationError(
                    f'Product: Configuration "{config_name}" and BigCommerce Product ID {key[1]}: '
                    'please select exactly one product to keep (one "Keep this product" checked).'
                )
            for line in lines:
                if not line.keep_this and line.product_tmpl_id:
                    products_to_archive |= line.product_tmpl_id
        if products_to_archive:
            products_to_archive.write({'active': False})
            _logger.info('Deduplicate mappings: archived %s product(s), mappings kept', len(products_to_archive))
        # --- Variant duplicate groups: unlink variant mappings not kept
        v_groups = {}
        for line in self.variant_line_ids:
            key = (line.config_id.id, line.bigcommerce_variant_id)
            if key not in v_groups:
                v_groups[key] = []
            v_groups[key].append(line)
        variant_mappings_to_unlink = self.env['bigcommerce.variant.mapping']
        for key, lines in v_groups.items():
            kept = [l for l in lines if l.keep_this]
            if len(kept) != 1:
                config_name = lines[0].config_id.name if lines else '?'
                raise ValidationError(
                    f'Variant: Configuration "{config_name}" and BigCommerce Variant ID {key[1]}: '
                    'please select exactly one variant to keep (one "Keep this variant" checked).'
                )
            for line in lines:
                if not line.keep_this and line.variant_mapping_id:
                    variant_mappings_to_unlink |= line.variant_mapping_id
        variant_unlink_count = len(variant_mappings_to_unlink)
        if variant_mappings_to_unlink:
            variant_mappings_to_unlink.unlink()
            _logger.info('Deduplicate mappings: removed %s duplicate variant mapping(s)', variant_unlink_count)
        if not products_to_archive and not variant_unlink_count:
            raise UserError(
                'No duplicates to process. Ensure exactly one product/variant per group is marked to keep.'
            )
        product_msg = f'Archived {len(products_to_archive)} duplicate product(s) (mappings kept). ' if products_to_archive else ''
        variant_msg = f'Removed {variant_unlink_count} duplicate variant mapping(s). ' if variant_unlink_count else ''
        self.write({
            'state': 'done',
            'result_message': product_msg + variant_msg + 'One product/variant per BigCommerce ID was kept.',
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
