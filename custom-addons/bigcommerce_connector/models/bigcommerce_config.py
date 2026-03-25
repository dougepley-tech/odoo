# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from ..utils.bigcommerce_api import BigCommerceAPI
from datetime import datetime
import html
import logging

_logger = logging.getLogger(__name__)


class BigCommerceConfig(models.Model):
    _name = 'bigcommerce.config'
    _description = 'BigCommerce Configuration'
    _inherit = ['mail.thread']
    _rec_name = 'name'

    name = fields.Char(string='Configuration Name', required=True)
    store_hash = fields.Char(string='Store Hash', required=True, help='Your BigCommerce store hash')
    access_token = fields.Char(string='Access Token', required=True, help='BigCommerce API access token')
    api_version = fields.Selection([
        ('v2', 'V2 (Legacy)'),
        ('v3', 'V3 (Recommended)'),
    ], string='API Version', default='v3', required=True, help='BigCommerce API version to use. V3 is recommended.')
    active = fields.Boolean(string='Active', default=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company,
                                  help='Company for orders and products synced from this BigCommerce store')
    
    @api.model
    def create(self, vals):
        """Create config and ensure dashboard record exists"""
        config = super().create(vals)
        if config.active:
            config._ensure_dashboard_record()
        return config
    
    def write(self, vals):
        """Update config and ensure dashboard record exists if activated"""
        result = super().write(vals)
        if vals.get('active', False):
            for config in self:
                if config.active:
                    config._ensure_dashboard_record()
        return result
    
    def _ensure_dashboard_record(self):
        """Ensure a dashboard record exists for this config"""
        # Use SQL to check and create to avoid triggering search override
        dashboard_model = self.env['bigcommerce.dashboard']
        table_name = dashboard_model._table
        self.env.cr.execute(f"""
            SELECT id FROM {table_name} WHERE config_id = %s LIMIT 1
        """, (self.id,))
        existing = self.env.cr.fetchone()
        
        if not existing:
            try:
                # Use SQL to insert directly
                self.env.cr.execute(f"""
                    INSERT INTO {table_name} (config_id, create_uid, create_date, write_uid, write_date)
                    VALUES (%s, %s, NOW() AT TIME ZONE 'UTC', %s, NOW() AT TIME ZONE 'UTC')
                """, (self.id, self.env.user.id, self.env.user.id))
                self.env.cr.commit()
                _logger.info(f"Created dashboard record for config {self.id} ({self.name})")
            except Exception as e:
                _logger.error(f"Error creating dashboard record for config {self.id}: {str(e)}", exc_info=True)
    
    # Sync Settings
    auto_sync_products = fields.Boolean(string='Auto Sync Products', default=False)
    auto_sync_products_frequency = fields.Selection([
        ('minute', 'Minute'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annually', 'Annually'),
    ], string='Products Sync Frequency', default='hourly', help='Frequency for automatically syncing products')
    auto_sync_products_interval_number = fields.Integer(string='Interval Number', default=1, help='Number of intervals (e.g., every 2 hours, every 3 days)')
    auto_sync_products_minute_interval = fields.Integer(string='Minute Interval', default=30, help='Interval in minutes (only used when frequency is Minute)')
    auto_sync_products_time_hour = fields.Selection([
        ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'),
        ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12')
    ], string='Hour', help='Hour of day (1-12)')
    auto_sync_products_time_minute = fields.Selection([
        ('0', '00'), ('1', '01'), ('2', '02'), ('3', '03'), ('4', '04'), ('5', '05'),
        ('6', '06'), ('7', '07'), ('8', '08'), ('9', '09'), ('10', '10'), ('11', '11'),
        ('12', '12'), ('13', '13'), ('14', '14'), ('15', '15'), ('16', '16'), ('17', '17'),
        ('18', '18'), ('19', '19'), ('20', '20'), ('21', '21'), ('22', '22'), ('23', '23'),
        ('24', '24'), ('25', '25'), ('26', '26'), ('27', '27'), ('28', '28'), ('29', '29'),
        ('30', '30'), ('31', '31'), ('32', '32'), ('33', '33'), ('34', '34'), ('35', '35'),
        ('36', '36'), ('37', '37'), ('38', '38'), ('39', '39'), ('40', '40'), ('41', '41'),
        ('42', '42'), ('43', '43'), ('44', '44'), ('45', '45'), ('46', '46'), ('47', '47'),
        ('48', '48'), ('49', '49'), ('50', '50'), ('51', '51'), ('52', '52'), ('53', '53'),
        ('54', '54'), ('55', '55'), ('56', '56'), ('57', '57'), ('58', '58'), ('59', '59')
    ], string='Minute', help='Minute of hour (0-59)')
    auto_sync_products_time_period = fields.Selection([
        ('AM', 'AM'),
        ('PM', 'PM'),
    ], string='Period', help='AM or PM')
    auto_sync_products_time_of_day = fields.Char(string='Time of Day', compute='_compute_products_time_of_day', store=True, help='Time of day to run sync in 12-hour format (for Daily, Weekly, Monthly, Annually)')
    auto_sync_products_day_of_week = fields.Selection([
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], string='Day of Week', help='Day of week to run sync (only used when frequency is Weekly)')
    auto_sync_products_day_of_month = fields.Integer(string='Day of Month', help='Day of month to run sync (only used when frequency is Monthly or Annually)')
    auto_sync_products_month = fields.Selection([
        ('1', 'January'),
        ('2', 'February'),
        ('3', 'March'),
        ('4', 'April'),
        ('5', 'May'),
        ('6', 'June'),
        ('7', 'July'),
        ('8', 'August'),
        ('9', 'September'),
        ('10', 'October'),
        ('11', 'November'),
        ('12', 'December'),
    ], string='Month', help='Month to run sync (only used when frequency is Annually)')
    
    auto_sync_orders = fields.Boolean(string='Auto Sync Orders', default=False)
    auto_sync_orders_frequency = fields.Selection([
        ('minute', 'Minute'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annually', 'Annually'),
    ], string='Orders Sync Frequency', default='hourly', help='Frequency for automatically syncing orders')
    auto_sync_orders_interval_number = fields.Integer(string='Interval Number', default=1, help='Number of intervals (e.g., every 2 hours, every 3 days)')
    auto_sync_orders_minute_interval = fields.Integer(string='Minute Interval', default=30, help='Interval in minutes (only used when frequency is Minute)')
    auto_sync_orders_time_hour = fields.Selection([
        ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'),
        ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12')
    ], string='Hour', help='Hour of day (1-12)')
    auto_sync_orders_time_minute = fields.Selection([
        ('0', '00'), ('1', '01'), ('2', '02'), ('3', '03'), ('4', '04'), ('5', '05'),
        ('6', '06'), ('7', '07'), ('8', '08'), ('9', '09'), ('10', '10'), ('11', '11'),
        ('12', '12'), ('13', '13'), ('14', '14'), ('15', '15'), ('16', '16'), ('17', '17'),
        ('18', '18'), ('19', '19'), ('20', '20'), ('21', '21'), ('22', '22'), ('23', '23'),
        ('24', '24'), ('25', '25'), ('26', '26'), ('27', '27'), ('28', '28'), ('29', '29'),
        ('30', '30'), ('31', '31'), ('32', '32'), ('33', '33'), ('34', '34'), ('35', '35'),
        ('36', '36'), ('37', '37'), ('38', '38'), ('39', '39'), ('40', '40'), ('41', '41'),
        ('42', '42'), ('43', '43'), ('44', '44'), ('45', '45'), ('46', '46'), ('47', '47'),
        ('48', '48'), ('49', '49'), ('50', '50'), ('51', '51'), ('52', '52'), ('53', '53'),
        ('54', '54'), ('55', '55'), ('56', '56'), ('57', '57'), ('58', '58'), ('59', '59')
    ], string='Minute', help='Minute of hour (0-59)')
    auto_sync_orders_time_period = fields.Selection([
        ('AM', 'AM'),
        ('PM', 'PM'),
    ], string='Period', help='AM or PM')
    auto_sync_orders_time_of_day = fields.Float(string='Time of Day', help='Time of day to run sync (for Daily, Weekly, Monthly, Annually)')
    auto_sync_orders_day_of_week = fields.Selection([
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], string='Day of Week', help='Day of week to run sync (only used when frequency is Weekly)')
    auto_sync_orders_day_of_month = fields.Integer(string='Day of Month', help='Day of month to run sync (only used when frequency is Monthly or Annually)')
    auto_sync_orders_month = fields.Selection([
        ('1', 'January'),
        ('2', 'February'),
        ('3', 'March'),
        ('4', 'April'),
        ('5', 'May'),
        ('6', 'June'),
        ('7', 'July'),
        ('8', 'August'),
        ('9', 'September'),
        ('10', 'October'),
        ('11', 'November'),
        ('12', 'December'),
    ], string='Month', help='Month to run sync (only used when frequency is Annually)')
    
    auto_sync_inventory = fields.Boolean(string='Auto Sync Inventory', default=False)
    auto_sync_inventory_frequency = fields.Selection([
        ('minute', 'Minute'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annually', 'Annually'),
    ], string='Inventory Sync Frequency', default='minute', help='Frequency for automatically syncing inventory')
    auto_sync_inventory_interval_number = fields.Integer(string='Interval Number', default=1, help='Number of intervals (e.g., every 2 hours, every 3 days)')
    auto_sync_inventory_minute_interval = fields.Integer(string='Minute Interval', default=30, help='Interval in minutes (only used when frequency is Minute)')
    auto_sync_inventory_time_hour = fields.Selection([
        ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'),
        ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12')
    ], string='Hour', help='Hour of day (1-12)')
    auto_sync_inventory_time_minute = fields.Selection([
        ('0', '00'), ('1', '01'), ('2', '02'), ('3', '03'), ('4', '04'), ('5', '05'),
        ('6', '06'), ('7', '07'), ('8', '08'), ('9', '09'), ('10', '10'), ('11', '11'),
        ('12', '12'), ('13', '13'), ('14', '14'), ('15', '15'), ('16', '16'), ('17', '17'),
        ('18', '18'), ('19', '19'), ('20', '20'), ('21', '21'), ('22', '22'), ('23', '23'),
        ('24', '24'), ('25', '25'), ('26', '26'), ('27', '27'), ('28', '28'), ('29', '29'),
        ('30', '30'), ('31', '31'), ('32', '32'), ('33', '33'), ('34', '34'), ('35', '35'),
        ('36', '36'), ('37', '37'), ('38', '38'), ('39', '39'), ('40', '40'), ('41', '41'),
        ('42', '42'), ('43', '43'), ('44', '44'), ('45', '45'), ('46', '46'), ('47', '47'),
        ('48', '48'), ('49', '49'), ('50', '50'), ('51', '51'), ('52', '52'), ('53', '53'),
        ('54', '54'), ('55', '55'), ('56', '56'), ('57', '57'), ('58', '58'), ('59', '59')
    ], string='Minute', help='Minute of hour (0-59)')
    auto_sync_inventory_time_period = fields.Selection([
        ('AM', 'AM'),
        ('PM', 'PM'),
    ], string='Period', help='AM or PM')
    auto_sync_inventory_time_of_day = fields.Float(string='Time of Day', help='Time of day to run sync (for Daily, Weekly, Monthly, Annually)')
    auto_sync_inventory_day_of_week = fields.Selection([
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], string='Day of Week', help='Day of week to run sync (only used when frequency is Weekly)')
    auto_sync_inventory_day_of_month = fields.Integer(string='Day of Month', help='Day of month to run sync (only used when frequency is Monthly or Annually)')
    auto_sync_inventory_month = fields.Selection([
        ('1', 'January'),
        ('2', 'February'),
        ('3', 'March'),
        ('4', 'April'),
        ('5', 'May'),
        ('6', 'June'),
        ('7', 'July'),
        ('8', 'August'),
        ('9', 'September'),
        ('10', 'October'),
        ('11', 'November'),
        ('12', 'December'),
    ], string='Month', help='Month to run sync (only used when frequency is Annually)')
    
    auto_sync_customers = fields.Boolean(string='Auto Sync Customers', default=False)
    auto_sync_customers_frequency = fields.Selection([
        ('minute', 'Minute'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annually', 'Annually'),
    ], string='Customers Sync Frequency', default='hourly', help='Frequency for automatically syncing customers')
    auto_sync_customers_interval_number = fields.Integer(string='Interval Number', default=1, help='Number of intervals (e.g., every 2 hours, every 3 days)')
    auto_sync_customers_minute_interval = fields.Integer(string='Minute Interval', default=30, help='Interval in minutes (only used when frequency is Minute)')
    auto_sync_customers_time_hour = fields.Selection([
        ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'),
        ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12')
    ], string='Hour', help='Hour of day (1-12)')
    auto_sync_customers_time_minute = fields.Selection([
        ('0', '00'), ('1', '01'), ('2', '02'), ('3', '03'), ('4', '04'), ('5', '05'),
        ('6', '06'), ('7', '07'), ('8', '08'), ('9', '09'), ('10', '10'), ('11', '11'),
        ('12', '12'), ('13', '13'), ('14', '14'), ('15', '15'), ('16', '16'), ('17', '17'),
        ('18', '18'), ('19', '19'), ('20', '20'), ('21', '21'), ('22', '22'), ('23', '23'),
        ('24', '24'), ('25', '25'), ('26', '26'), ('27', '27'), ('28', '28'), ('29', '29'),
        ('30', '30'), ('31', '31'), ('32', '32'), ('33', '33'), ('34', '34'), ('35', '35'),
        ('36', '36'), ('37', '37'), ('38', '38'), ('39', '39'), ('40', '40'), ('41', '41'),
        ('42', '42'), ('43', '43'), ('44', '44'), ('45', '45'), ('46', '46'), ('47', '47'),
        ('48', '48'), ('49', '49'), ('50', '50'), ('51', '51'), ('52', '52'), ('53', '53'),
        ('54', '54'), ('55', '55'), ('56', '56'), ('57', '57'), ('58', '58'), ('59', '59')
    ], string='Minute', help='Minute of hour (0-59)')
    auto_sync_customers_time_period = fields.Selection([
        ('AM', 'AM'),
        ('PM', 'PM'),
    ], string='Period', help='AM or PM')
    auto_sync_customers_time_of_day = fields.Float(string='Time of Day', help='Time of day to run sync (for Daily, Weekly, Monthly, Annually)')
    auto_sync_customers_day_of_week = fields.Selection([
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], string='Day of Week', help='Day of week to run sync (only used when frequency is Weekly)')
    auto_sync_customers_day_of_month = fields.Integer(string='Day of Month', help='Day of month to run sync (only used when frequency is Monthly or Annually)')
    auto_sync_customers_month = fields.Selection([
        ('1', 'January'),
        ('2', 'February'),
        ('3', 'March'),
        ('4', 'April'),
        ('5', 'May'),
        ('6', 'June'),
        ('7', 'July'),
        ('8', 'August'),
        ('9', 'September'),
        ('10', 'October'),
        ('11', 'November'),
        ('12', 'December'),
    ], string='Month', help='Month to run sync (only used when frequency is Annually)')
    
    auto_sync_fulfillments = fields.Boolean(string='Auto Sync Fulfillments', default=False)
    auto_sync_fulfillments_frequency = fields.Selection([
        ('minute', 'Minute'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('annually', 'Annually'),
    ], string='Fulfillments Sync Frequency', default='hourly', help='Frequency for automatically syncing fulfillments')
    auto_sync_fulfillments_interval_number = fields.Integer(string='Interval Number', default=1, help='Number of intervals (e.g., every 2 hours, every 3 days)')
    auto_sync_fulfillments_minute_interval = fields.Integer(string='Minute Interval', default=30, help='Interval in minutes (only used when frequency is Minute)')
    auto_sync_fulfillments_time_hour = fields.Selection([
        ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6'),
        ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12')
    ], string='Hour', help='Hour of day (1-12)')
    auto_sync_fulfillments_time_minute = fields.Selection([
        ('0', '00'), ('1', '01'), ('2', '02'), ('3', '03'), ('4', '04'), ('5', '05'),
        ('6', '06'), ('7', '07'), ('8', '08'), ('9', '09'), ('10', '10'), ('11', '11'),
        ('12', '12'), ('13', '13'), ('14', '14'), ('15', '15'), ('16', '16'), ('17', '17'),
        ('18', '18'), ('19', '19'), ('20', '20'), ('21', '21'), ('22', '22'), ('23', '23'),
        ('24', '24'), ('25', '25'), ('26', '26'), ('27', '27'), ('28', '28'), ('29', '29'),
        ('30', '30'), ('31', '31'), ('32', '32'), ('33', '33'), ('34', '34'), ('35', '35'),
        ('36', '36'), ('37', '37'), ('38', '38'), ('39', '39'), ('40', '40'), ('41', '41'),
        ('42', '42'), ('43', '43'), ('44', '44'), ('45', '45'), ('46', '46'), ('47', '47'),
        ('48', '48'), ('49', '49'), ('50', '50'), ('51', '51'), ('52', '52'), ('53', '53'),
        ('54', '54'), ('55', '55'), ('56', '56'), ('57', '57'), ('58', '58'), ('59', '59')
    ], string='Minute', help='Minute of hour (0-59)')
    auto_sync_fulfillments_time_period = fields.Selection([
        ('AM', 'AM'),
        ('PM', 'PM'),
    ], string='Period', help='AM or PM')
    auto_sync_fulfillments_time_of_day = fields.Float(string='Time of Day', help='Time of day to run sync (for Daily, Weekly, Monthly, Annually)')
    auto_sync_fulfillments_day_of_week = fields.Selection([
        ('0', 'Monday'),
        ('1', 'Tuesday'),
        ('2', 'Wednesday'),
        ('3', 'Thursday'),
        ('4', 'Friday'),
        ('5', 'Saturday'),
        ('6', 'Sunday'),
    ], string='Day of Week', help='Day of week to run sync (only used when frequency is Weekly)')
    auto_sync_fulfillments_day_of_month = fields.Integer(string='Day of Month', help='Day of month to run sync (only used when frequency is Monthly or Annually)')
    auto_sync_fulfillments_month = fields.Selection([
        ('1', 'January'),
        ('2', 'February'),
        ('3', 'March'),
        ('4', 'April'),
        ('5', 'May'),
        ('6', 'June'),
        ('7', 'July'),
        ('8', 'August'),
        ('9', 'September'),
        ('10', 'October'),
        ('11', 'November'),
        ('12', 'December'),
    ], string='Month', help='Month to run sync (only used when frequency is Annually)')
    
    sync_direction_products = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
        ('bc_to_odoo', 'BigCommerce to Odoo'),
        ('bidirectional', 'Bidirectional'),
    ], string='Product Sync Direction', default='bidirectional')
    
    sync_direction_orders = fields.Selection([
        ('bc_to_odoo', 'BigCommerce to Odoo'),
    ], string='Order Sync Direction', default='bc_to_odoo')
    
    sync_direction_inventory = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
    ], string='Inventory Sync Direction', default='odoo_to_bc')
    
    sync_direction_customers = fields.Selection([
        ('odoo_to_bc', 'Odoo to BigCommerce'),
        ('bc_to_odoo', 'BigCommerce to Odoo'),
        ('bidirectional', 'Bidirectional'),
    ], string='Customer Sync Direction', default='bidirectional')
    
    # Auto sync failure notifications
    sync_failure_notification_emails = fields.Char(
        string='Failure notification emails',
        help='Comma-separated email addresses to notify when an auto sync fails or errors (e.g. user@example.com, other@example.com). You can also add contacts below.',
    )
    sync_failure_notification_partner_ids = fields.Many2many(
        'res.partner',
        'bigcommerce_config_sync_failure_partner_rel',
        'config_id',
        'partner_id',
        string='Failure notification contacts',
        help='Contacts to email when an auto sync fails or errors. Add partners and/or use the email addresses field above.',
    )
    sync_failure_notification_channel_ids = fields.Many2many(
        'discuss.channel',
        'bigcommerce_config_sync_failure_channel_rel',
        'config_id',
        'channel_id',
        string='Failure notification channels',
        help='Discuss channels to post a message to when an auto sync fails or errors.',
    )
    sync_failure_notification_user_ids = fields.Many2many(
        'res.users',
        'bigcommerce_config_sync_failure_user_rel',
        'config_id',
        'user_id',
        string='Failure notification users',
        help='Users to send a Discuss notification to when an auto sync fails or errors.',
    )
    
    # Product Mapping
    product_default_category = fields.Many2one('product.category', string='Default Product Category')
    product_default_uom = fields.Many2one('uom.uom', string='Default Unit of Measure', 
                                          default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    sync_product_variants = fields.Boolean(string='Sync Product Variants', default=False, 
                                          help='Enable syncing of product variants and attributes. Disable for faster imports. Recommended to disable for initial bulk imports.')
    sync_product_images = fields.Boolean(string='Sync Product Images', default=False,
                                        help='Enable syncing of product images from BigCommerce to Odoo. Images will be downloaded and attached to products.')
    product_tag_ids = fields.Many2many('product.tag', string='Product Tags', 
                                       help='When syncing from Odoo to BigCommerce, only products with these tags will be synced. BigCommerce to Odoo sync will always sync all products regardless of tags.')
    category_mapping_ids = fields.One2many('bigcommerce.category.mapping', 'config_id', string='Category Mappings (deprecated)')
    category_rule_ids = fields.One2many('bigcommerce.category.rule', 'config_id', string='Category Rules (by filter)', help='Set Odoo category by product SKU/Internal Reference. First matching rule wins. No category sync with BigCommerce.')
    field_mapping_ids = fields.One2many('bigcommerce.field.mapping', 'config_id', string='Field Mappings')
    warehouse_mapping_ids = fields.One2many('bigcommerce.warehouse.mapping', 'config_id', string='Warehouse Mappings')
    location_mapping_ids = fields.One2many('bigcommerce.location.mapping', 'config_id', string='Location Mappings')
    
    # Last Sync Times
    last_product_sync = fields.Datetime(string='Last Product Sync')
    last_order_sync = fields.Datetime(string='Last Order Sync')
    last_inventory_sync = fields.Datetime(string='Last Inventory Sync')
    last_customer_sync = fields.Datetime(string='Last Customer Sync')
    last_fulfillment_sync = fields.Datetime(string='Last Fulfillment Sync')
    
    # Last Sync Statistics - Products
    last_product_sync_total = fields.Integer(string='Last Product Sync - Total', default=0)
    last_product_sync_updated = fields.Integer(string='Last Product Sync - Updated', default=0)
    last_product_sync_failed = fields.Integer(string='Last Product Sync - Failed', default=0)
    last_product_sync_warnings = fields.Integer(string='Last Product Sync - Warnings', default=0)
    
    # Last Sync Statistics - Orders
    last_order_sync_total = fields.Integer(string='Last Order Sync - Total', default=0)
    last_order_sync_updated = fields.Integer(string='Last Order Sync - Updated', default=0)
    last_order_sync_failed = fields.Integer(string='Last Order Sync - Failed', default=0)
    last_order_sync_warnings = fields.Integer(string='Last Order Sync - Warnings', default=0)
    
    # Last Sync Statistics - Inventory
    last_inventory_sync_total = fields.Integer(string='Last Inventory Sync - Total', default=0)
    last_inventory_sync_updated = fields.Integer(string='Last Inventory Sync - Updated', default=0)
    last_inventory_sync_failed = fields.Integer(string='Last Inventory Sync - Failed', default=0)
    last_inventory_sync_warnings = fields.Integer(string='Last Inventory Sync - Warnings', default=0)
    
    # Last Sync Statistics - Customers
    last_customer_sync_total = fields.Integer(string='Last Customer Sync - Total', default=0)
    last_customer_sync_updated = fields.Integer(string='Last Customer Sync - Updated', default=0)
    last_customer_sync_failed = fields.Integer(string='Last Customer Sync - Failed', default=0)
    last_customer_sync_warnings = fields.Integer(string='Last Customer Sync - Warnings', default=0)
    
    # Last Sync Statistics - Fulfillments
    last_fulfillment_sync_total = fields.Integer(string='Last Fulfillment Sync - Total', default=0)
    last_fulfillment_sync_updated = fields.Integer(string='Last Fulfillment Sync - Updated', default=0)
    last_fulfillment_sync_failed = fields.Integer(string='Last Fulfillment Sync - Failed', default=0)
    last_fulfillment_sync_warnings = fields.Integer(string='Last Fulfillment Sync - Warnings', default=0)
    
    # Order Import Settings
    order_number_prefix = fields.Char(string='Order Number Prefix', default='BC', help='Prefix to add to BigCommerce order numbers')
    import_payments = fields.Boolean(string='Import Payment Information', default=True, help='Import payment method and status from BigCommerce')
    import_taxes = fields.Boolean(string='Import Taxes', default=True, help='Import tax information as line items')
    tax_product_id = fields.Many2one('product.product', string='Default Tax Product', domain=[('type', '=', 'service')], help='Product to use for tax line items')
    # Delivery method for imported orders: sets sale order carrier and shipping line product
    delivery_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Delivery Method',
        help='Odoo delivery method to set on imported orders. The BigCommerce shipping method (e.g. UPS Ground) is shown as the line description below this carrier\'s product.'
    )
    delivery_carrier_wholesale_id = fields.Many2one(
        'delivery.carrier',
        string='Delivery Method (Wholesale)',
        help='When the BigCommerce shipping method name ends with *, use this carrier (e.g. Shippo Wholesale) instead of Delivery Method (e.g. Shippo Shipping) for the order and shipping line.'
    )
    shipping_product_id = fields.Many2one('product.product', string='Shipping Product (fallback)', domain=[('type', '=', 'service')],
                                         help='Used for shipping line only when no Delivery Method is set or the carrier has no product.')
    
    # General Order Settings
    confirm_sale_orders = fields.Boolean(string='Confirm Sale Orders', default=False, 
                                         help='Allow confirming sale orders automatically when imported from BigCommerce store.')
    import_order_without_status = fields.Boolean(string='Import Order without order status', default=False,
                                                  help='Import order without order status code.')
    default_customer_enabled = fields.Boolean(string='Default Customer', default=False,
                                             help='Automatically set selected partner in case imported order customer was not found in system.')
    default_customer_id = fields.Many2one('res.partner', string='Default Partner', 
                                         help='Default partner to use when customer is not found')
    order_status_update = fields.Boolean(string='Order status update', default=False,
                                        help='I want to update order status in odoo after it has been updated in BigCommerce.')
    
    # Invoicing, Payment & Transactions
    create_invoices = fields.Boolean(string='Create Invoices', default=False,
                                    help='Automatically create invoices while importing orders from BigCommerce to Odoo.')
    register_payment_on_import = fields.Boolean(string='Register Payment on Import', default=False,
                                               help='Automatically register payments when importing orders from the store.')
    import_bc_transactions = fields.Boolean(string='Import BigCommerce Transactions', default=False,
                                          help='Enable this option to import payment transaction details from BigCommerce when syncing orders.')
    
    # Order Shipment & Stock Configuration
    create_shipment = fields.Boolean(string='Create Shipment', default=False,
                                    help='Create a shipment in BigCommerce whenever a delivery is validated in Odoo.')
    import_shipment_details = fields.Boolean(string='Import Shipment details', default=False,
                                            help='Auto import shipment details during order import. Only selected order status orders will be imported.')
    import_shipment_status_ids = fields.Many2many('bigcommerce.order.status', string='Order Status',
                                                  help='Only orders with these statuses will have shipment details imported.')
    order_warehouse_id = fields.Many2one('stock.warehouse', string='Default Sales Order Delivery Warehouse',
                                        help='Default warehouse for imported orders. Overridden by Order Warehouse Mapping rules when a product SKU matches.')
    order_warehouse_mapping_ids = fields.One2many(
        'bigcommerce.order.warehouse.mapping',
        'config_id',
        string='Order Warehouse Mapping Rules',
        help='Map order to warehouse by product SKU. Rules evaluated in sequence; first match wins. E.g. SKU starts with "iag-mss" → Engine Department warehouse.',
    )
    
    # Order Status Filtering
    sync_order_status_ids = fields.Many2many('bigcommerce.order.status', 'config_order_status_rel', 
                                            'config_id', 'status_id', string='Sync Order Statuses',
                                            help='Only sync orders with these BigCommerce statuses. Leave empty to sync all orders.')
    
    # Sales Team
    default_sales_team_id = fields.Many2one('crm.team', string='Default Sales Team',
                                           help='Sales team to assign to all imported orders from BigCommerce.')
    
    # Tax Mapping
    state_tax_mapping_ids = fields.One2many('bigcommerce.state.tax.mapping', 'config_id', string='State Tax Mappings', help='Map states to tax products')
    
    # Fulfillment Settings
    last_fulfillment_sync = fields.Datetime(string='Last Fulfillment Sync')
    
    # Carrier Mapping
    carrier_mapping_ids = fields.One2many('bigcommerce.carrier.mapping', 'config_id', string='Carrier Mappings', help='Map Odoo carriers to BigCommerce carrier codes')
    
    # Webhook Settings
    webhook_enabled = fields.Boolean(string='Enable Webhooks', default=False, 
                                     help='Enable automatic webhooks to sync data in real-time from BigCommerce to Odoo')
    webhook_url = fields.Char(string='Webhook URL', compute='_compute_webhook_url', store=False,
                              help='URL where BigCommerce will send webhook notifications. This is automatically generated based on your Odoo instance URL.')
    webhook_base_url = fields.Char(string='Custom Base URL', 
                                   help='Optional: Override the base URL for webhooks (e.g., if using a reverse proxy). Leave empty to use the default Odoo base URL.')
    
    # Webhook Scopes
    webhook_product_created = fields.Boolean(string='Product Created', default=True, help='Receive notifications when products are created in BigCommerce')
    webhook_product_updated = fields.Boolean(string='Product Updated', default=True, help='Receive notifications when products are updated in BigCommerce')
    webhook_product_deleted = fields.Boolean(string='Product Deleted', default=True, help='Receive notifications when products are deleted in BigCommerce')
    webhook_product_inventory_updated = fields.Boolean(string='Product Inventory Updated', default=True, help='Receive notifications when product inventory is updated in BigCommerce')
    
    webhook_order_created = fields.Boolean(string='Order Created', default=True, help='Receive notifications when orders are created in BigCommerce')
    webhook_order_updated = fields.Boolean(string='Order Updated', default=True, help='Receive notifications when orders are updated in BigCommerce')
    webhook_order_archived = fields.Boolean(string='Order Archived', default=False, help='Receive notifications when orders are archived in BigCommerce')
    webhook_order_status_updated = fields.Boolean(string='Order Status Updated', default=True, help='Receive notifications when order status changes in BigCommerce')
    webhook_order_message_created = fields.Boolean(string='Order Message Created', default=False, help='Receive notifications when order messages are created in BigCommerce')
    webhook_order_refund_created = fields.Boolean(string='Order Refund Created', default=True, help='Receive notifications when order refunds are created in BigCommerce')
    
    webhook_customer_created = fields.Boolean(string='Customer Created', default=True, help='Receive notifications when customers are created in BigCommerce')
    webhook_customer_updated = fields.Boolean(string='Customer Updated', default=True, help='Receive notifications when customers are updated in BigCommerce')
    webhook_customer_deleted = fields.Boolean(string='Customer Deleted', default=False, help='Receive notifications when customers are deleted in BigCommerce')
    webhook_customer_address_created = fields.Boolean(string='Customer Address Created', default=False, help='Receive notifications when customer addresses are created in BigCommerce')
    webhook_customer_address_updated = fields.Boolean(string='Customer Address Updated', default=False, help='Receive notifications when customer addresses are updated in BigCommerce')
    webhook_customer_address_deleted = fields.Boolean(string='Customer Address Deleted', default=False, help='Receive notifications when customer addresses are deleted in BigCommerce')
    
    webhook_shipment_created = fields.Boolean(string='Shipment Created', default=True, help='Receive notifications when shipments are created in BigCommerce')
    webhook_shipment_updated = fields.Boolean(string='Shipment Updated', default=True, help='Receive notifications when shipments are updated in BigCommerce')
    webhook_shipment_deleted = fields.Boolean(string='Shipment Deleted', default=False, help='Receive notifications when shipments are deleted in BigCommerce')
    
    webhook_category_created = fields.Boolean(string='Category Created', default=False, help='Receive notifications when categories are created in BigCommerce')
    webhook_category_updated = fields.Boolean(string='Category Updated', default=False, help='Receive notifications when categories are updated in BigCommerce')
    webhook_category_deleted = fields.Boolean(string='Category Deleted', default=False, help='Receive notifications when categories are deleted in BigCommerce')
    
    # Webhook Status
    webhooks_registered = fields.Boolean(string='Webhooks Registered', default=False, readonly=True,
                                        help='Indicates whether webhooks have been registered with BigCommerce')
    webhook_registration_date = fields.Datetime(string='Registration Date', readonly=True,
                                               help='Date and time when webhooks were last registered')
    webhook_ids_json = fields.Text(string='Webhook IDs', readonly=True,
                                   help='JSON storage of registered webhook IDs in BigCommerce')
    webhook_last_received = fields.Datetime(string='Last Webhook Received', readonly=True,
                                          help='Date and time when the last webhook was received')
    webhook_total_received = fields.Integer(string='Total Webhooks Received', default=0, readonly=True,
                                          help='Total number of webhooks received from BigCommerce')
    webhook_total_processed = fields.Integer(string='Total Webhooks Processed', default=0, readonly=True,
                                           help='Total number of webhooks successfully processed')
    webhook_total_failed = fields.Integer(string='Total Webhooks Failed', default=0, readonly=True,
                                        help='Total number of webhooks that failed to process')
    
    @api.depends('webhook_base_url')
    def _compute_webhook_url(self):
        """Compute the webhook URL based on the Odoo base URL"""
        for record in self:
            if record.webhook_base_url:
                base_url = record.webhook_base_url.rstrip('/')
            else:
                # Get base URL from system parameter or request
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
            
            # Get database name
            db_name = self.env.cr.dbname
            
            if base_url:
                record.webhook_url = f"{base_url}/bigcommerce/webhook/{record.id}?db={db_name}"
            else:
                record.webhook_url = f"/bigcommerce/webhook/{record.id}?db={db_name}"
    
    @api.constrains('store_hash', 'access_token')
    def _check_credentials(self):
        """Validate BigCommerce credentials"""
        for record in self:
            if record.store_hash and record.access_token:
                try:
                    api = BigCommerceAPI(record.store_hash, record.access_token, api_version=record.api_version or 'v3')
                    if not api.test_connection():
                        raise ValidationError("Invalid BigCommerce credentials. Please check your store hash and access token.")
                except Exception as e:
                    raise ValidationError(f"Error validating credentials: {str(e)}")
    
    @api.depends('auto_sync_products_time_hour', 'auto_sync_products_time_minute', 'auto_sync_products_time_period')
    def _compute_products_time_of_day(self):
        """Compute time_of_day from hour, minute, and period fields"""
        for record in self:
            if record.auto_sync_products_time_hour and record.auto_sync_products_time_minute and record.auto_sync_products_time_period:
                hour = record.auto_sync_products_time_hour
                minute = record.auto_sync_products_time_minute
                period = record.auto_sync_products_time_period
                # Ensure minute is always 2 digits (e.g., "02" instead of "2")
                minute_str = f"{int(minute):02d}" if minute else "00"
                record.auto_sync_products_time_of_day = f"{hour}:{minute_str} {period}"
            else:
                record.auto_sync_products_time_of_day = False
    
    @api.onchange('auto_sync_products_time_of_day')
    def _onchange_products_time_of_day(self):
        """Parse existing time_of_day value and populate hour, minute, and period fields"""
        if self.auto_sync_products_time_of_day and not (self.auto_sync_products_time_hour and self.auto_sync_products_time_minute and self.auto_sync_products_time_period):
            hour, minute = self._parse_time_12h(self.auto_sync_products_time_of_day)
            if hour is not None and minute is not None:
                # Convert 24-hour to 12-hour format
                period = 'AM'
                if hour == 0:
                    hour_12 = 12
                elif hour == 12:
                    hour_12 = 12
                    period = 'PM'
                elif hour > 12:
                    hour_12 = hour - 12
                    period = 'PM'
                else:
                    hour_12 = hour
                
                self.auto_sync_products_time_hour = str(hour_12)
                self.auto_sync_products_time_minute = str(minute).zfill(2)
                self.auto_sync_products_time_period = period
    
    def get_api_client(self):
        """Get BigCommerce API client instance"""
        self.ensure_one()
        if not self.store_hash or not self.access_token:
            raise UserError("BigCommerce credentials are not configured.")
        return BigCommerceAPI(self.store_hash, self.access_token, api_version=self.api_version or 'v3')
    
    def test_connection(self):
        """Test connection to BigCommerce"""
        self.ensure_one()
        try:
            api = self.get_api_client()
            if api.test_connection():
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Success',
                        'message': 'Connection to BigCommerce successful!',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError("Connection test failed.")
        except Exception as e:
            raise UserError(f"Connection test failed: {str(e)}")
    
    def _parse_time_12h(self, time_str):
        """Parse 12-hour format time string (e.g., '11:00 PM', '9:30 AM', '2:2 PM') to (hour, minute) tuple in 24-hour format.
        Returns (hour, minute) or (None, None) if parsing fails."""
        if not time_str or not isinstance(time_str, str):
            return (None, None)
        
        import re
        # Remove extra whitespace and convert to uppercase for easier parsing
        time_str = time_str.strip().upper()
        
        # Pattern to match: HH:MM AM/PM or H:MM AM/PM or H:M AM/PM (allow single digit minutes)
        # First try with 2-digit minutes (preferred format)
        pattern = r'(\d{1,2}):(\d{1,2})\s*(AM|PM)'
        match = re.match(pattern, time_str)
        
        if not match:
            return (None, None)
        
        hour = int(match.group(1))
        minute = int(match.group(2))
        period = match.group(3)
        
        # Validate hour and minute ranges
        if hour < 1 or hour > 12 or minute < 0 or minute > 59:
            return (None, None)
        
        # Convert to 24-hour format
        if period == 'PM' and hour != 12:
            hour += 12
        elif period == 'AM' and hour == 12:
            hour = 0
        
        return (hour, minute)
    
    def _get_local_time(self):
        """Get current local time based on company timezone, with fallback to system timezone"""
        import pytz
        from datetime import datetime
        import os
        
        # Get UTC time (fields.Datetime.now() returns a datetime object in UTC)
        utc_now = fields.Datetime.now()
        
        # Get timezone: use this config's company first (so "2:00 AM" is in that company's TZ), then env company, then creator/writer, then user
        tz_name = 'UTC'
        tz_source = 'UTC (fallback)'
        try:
            # Config's company (critical for cron: each config may have a different company/timezone)
            if hasattr(self, 'company_id') and self.company_id and getattr(self.company_id, 'tz', None):
                tz_name = self.company_id.tz
                tz_source = 'Config Company'
            elif hasattr(self.env.company, 'tz') and self.env.company.tz:
                tz_name = self.env.company.tz
                tz_source = 'Odoo Company'
            # Try config record's creator/writer timezone (if this is a record with create_uid/write_uid)
            elif hasattr(self, 'write_uid') and self.write_uid and hasattr(self.write_uid, 'tz') and self.write_uid.tz:
                tz_name = self.write_uid.tz
                tz_source = 'Config Writer'
            elif hasattr(self, 'create_uid') and self.create_uid and hasattr(self.create_uid, 'tz') and self.create_uid.tz:
                tz_name = self.create_uid.tz
                tz_source = 'Config Creator'
            # Try current user timezone as fallback
            elif hasattr(self.env.user, 'tz') and self.env.user.tz:
                tz_name = self.env.user.tz
                tz_source = 'Odoo User'
        except (AttributeError, Exception):
            pass
        
        # Get the pytz timezone object
        try:
            user_tz = pytz.timezone(tz_name)
            # Log at INFO level so it's always visible for debugging
            _logger.info(f"Using timezone: {tz_name} (source: {tz_source})")
        except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
            # If timezone is invalid, use UTC
            _logger.warning(f"Invalid timezone '{tz_name}', falling back to UTC")
            user_tz = pytz.UTC
            tz_name = 'UTC'
            tz_source = 'UTC (invalid timezone fallback)'
        
        # Convert UTC to local time
        # Odoo's fields.Datetime.now() returns a naive datetime in UTC
        if utc_now.tzinfo is None:
            # Localize as UTC first
            utc_now = pytz.UTC.localize(utc_now)
        
        # Convert to target timezone
        local_time = utc_now.astimezone(user_tz)
        
        return local_time
    
    def _should_run_sync(self, frequency, interval_number, minute_interval, time_of_day, day_of_week, day_of_month, month, last_sync):
        """Check if sync should run based on frequency settings"""
        from datetime import datetime, timedelta
        import pytz
        
        # Get current local time (not UTC)
        now_local = self._get_local_time()
        now_utc = fields.Datetime.now()  # Keep UTC for interval comparisons
        
        if not last_sync:
            return True
        
        interval_number = interval_number or 1  # Default to 1 if not set
        
        # Helper function to get target hour and minute from time_of_day
        def get_target_time(time_of_day):
            if not time_of_day:
                return (None, None)
            # Try parsing as 12-hour format first (Char field)
            if isinstance(time_of_day, str):
                return self._parse_time_12h(time_of_day)
            # Fallback to Float format for backward compatibility
            elif isinstance(time_of_day, (int, float)):
                target_hour = int(time_of_day) if time_of_day else 0
                target_minute = int(round((time_of_day - target_hour) * 60)) if time_of_day else 0
                return (target_hour, target_minute)
            return (None, None)
        
        if frequency == 'minute':
            return (now_utc - last_sync) >= timedelta(minutes=minute_interval or 30)
        elif frequency == 'hourly':
            return (now_utc - last_sync) >= timedelta(hours=interval_number)
        elif frequency == 'daily':
            if not time_of_day:
                # No scheduled time - use interval check only
                return (now_utc - last_sync) >= timedelta(days=interval_number) if last_sync else True
            # Check if current LOCAL time matches time_of_day (within 5 minute window)
            current_hour = now_local.hour
            current_minute = now_local.minute
            target_hour, target_minute = get_target_time(time_of_day)
            if target_hour is None:
                # Time parsing failed - log warning and don't run (safer than falling back to interval)
                _logger.warning(f"Failed to parse time_of_day: {time_of_day} for daily sync. Skipping until time is properly configured.")
                return False
            
            # Get timezone name for logging (use same logic as _get_local_time: config company first)
            tz_name = 'UTC'
            tz_source = 'Unknown'
            try:
                if hasattr(self, 'company_id') and self.company_id and getattr(self.company_id, 'tz', None):
                    tz_name = self.company_id.tz
                    tz_source = 'Config Company'
                elif hasattr(self.env.company, 'tz') and self.env.company.tz:
                    tz_name = self.env.company.tz
                    tz_source = 'Odoo Company'
                elif hasattr(self.env.user, 'tz') and self.env.user.tz:
                    tz_name = self.env.user.tz
                    tz_source = 'Odoo User'
                else:
                    # Check system timezone
                    import os
                    if os.path.exists('/etc/timezone'):
                        with open('/etc/timezone', 'r') as f:
                            system_tz = f.read().strip()
                            if system_tz:
                                tz_name = system_tz
                                tz_source = 'System (/etc/timezone)'
            except Exception as e:
                _logger.warning(f"Error getting timezone: {str(e)}")
            
            # Allow 5 minute window for cron execution (BEFORE or AFTER target time)
            current_time_minutes = current_hour * 60 + current_minute
            target_time_minutes = target_hour * 60 + target_minute
            time_diff = abs(current_time_minutes - target_time_minutes)

            _logger.info(
                f"Daily sync time check: current={current_hour:02d}:{current_minute:02d} ({tz_name}, source: {tz_source}), "
                f"target={target_hour:02d}:{target_minute:02d}, diff={time_diff} minutes, "
                f"time_of_day={time_of_day}, will_run={time_diff <= 5}, now_local={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )

            # CRITICAL: Only run if we're within 5 minutes of the scheduled time
            if time_diff <= 5:
                if not last_sync:
                    return True
                # Convert last_sync to local time for comparison
                import pytz
                if isinstance(last_sync, str):
                    last_sync_dt = fields.Datetime.from_string(last_sync)
                else:
                    last_sync_dt = last_sync
                tz_for_last = 'UTC'
                try:
                    if hasattr(self, 'company_id') and self.company_id and getattr(self.company_id, 'tz', None):
                        tz_for_last = self.company_id.tz
                    elif hasattr(self.env.company, 'tz') and self.env.company.tz:
                        tz_for_last = self.env.company.tz
                    elif hasattr(self.env.user, 'tz') and self.env.user.tz:
                        tz_for_last = self.env.user.tz
                    else:
                        import os
                        if os.path.exists('/etc/timezone'):
                            with open('/etc/timezone', 'r') as f:
                                system_tz = f.read().strip()
                                if system_tz:
                                    tz_for_last = system_tz
                except (AttributeError, Exception) as e:
                    _logger.warning(f"Error getting timezone for last_sync conversion: {str(e)}")
                    tz_for_last = 'UTC'
                try:
                    user_tz = pytz.timezone(tz_for_last)
                except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
                    user_tz = pytz.UTC
                if last_sync_dt.tzinfo is None:
                    last_sync_dt = pytz.UTC.localize(last_sync_dt)
                last_sync_local = last_sync_dt.astimezone(user_tz)
                last_sync_time_minutes = last_sync_local.hour * 60 + last_sync_local.minute
                last_sync_date = last_sync_local.date()
                today = now_local.date()
                if last_sync_date < today:
                    return True
                elif last_sync_date == today:
                    last_sync_time_diff = abs(last_sync_time_minutes - target_time_minutes)
                    if last_sync_time_diff > 5:
                        return True
                    return False
                else:
                    return False
            _logger.debug(
                f"Daily sync: current {current_hour:02d}:{current_minute:02d} is {time_diff} min from "
                f"scheduled {target_hour:02d}:{target_minute:02d}. Not running."
            )
            return False
        elif frequency == 'weekly':
            if day_of_week is False:
                return (now_utc - last_sync) >= timedelta(weeks=interval_number)
            # Check if current LOCAL day matches day_of_week
            current_weekday = now_local.weekday()  # 0=Monday, 6=Sunday
            if int(day_of_week) == current_weekday:
                if not time_of_day:
                    return (now_utc - last_sync) >= timedelta(weeks=interval_number)
                current_hour = now_local.hour
                current_minute = now_local.minute
                target_hour, target_minute = get_target_time(time_of_day)
                if target_hour is None:
                    return (now_utc - last_sync) >= timedelta(weeks=interval_number)
                time_diff = abs((current_hour * 60 + current_minute) - (target_hour * 60 + target_minute))
                if time_diff <= 5:
                    return (now_utc - last_sync) >= timedelta(weeks=interval_number)
            return False
        elif frequency == 'monthly':
            if day_of_month is False:
                return (now_utc - last_sync) >= timedelta(days=30 * interval_number)
            # Check if current LOCAL day matches day_of_month
            if now_local.day == day_of_month:
                if not time_of_day:
                    return (now_utc - last_sync) >= timedelta(days=30 * interval_number)
                current_hour = now_local.hour
                current_minute = now_local.minute
                target_hour, target_minute = get_target_time(time_of_day)
                if target_hour is None:
                    return (now_utc - last_sync) >= timedelta(days=30 * interval_number)
                time_diff = abs((current_hour * 60 + current_minute) - (target_hour * 60 + target_minute))
                if time_diff <= 5:
                    return (now_utc - last_sync) >= timedelta(days=30 * interval_number)
            return False
        elif frequency == 'annually':
            if month is False or day_of_month is False:
                return (now_utc - last_sync) >= timedelta(days=365 * interval_number)
            # Check if current LOCAL month and day match
            if now_local.month == int(month) and now_local.day == day_of_month:
                if not time_of_day:
                    return (now_utc - last_sync) >= timedelta(days=365 * interval_number)
                current_hour = now_local.hour
                current_minute = now_local.minute
                target_hour, target_minute = get_target_time(time_of_day)
                if target_hour is None:
                    return (now_utc - last_sync) >= timedelta(days=365 * interval_number)
                time_diff = abs((current_hour * 60 + current_minute) - (target_hour * 60 + target_minute))
                if time_diff <= 5:
                    return (now_utc - last_sync) >= timedelta(days=365 * interval_number)
            return False
        
        return False
    
    def _send_sync_failure_email(self, sync_type, error_message=None, details=None, created=None, updated=None, failed=None, sync_name=None):
        """Send email and/or Discuss notifications when an auto sync fails or completes with failures.
        Call with error_message when state='error'; or with failed>0 when state='done' but items failed."""
        self.ensure_one()
        if error_message:
            subject = f"[BigCommerce] {self.name} - {sync_type} sync failed"
        else:
            subject = f"[BigCommerce] {self.name} - {sync_type} sync completed with failures"
        body = f"""
        <p>Configuration: <strong>{html.escape(self.name)}</strong></p>
        <p>Sync type: <strong>{html.escape(sync_type)}</strong></p>
        """
        if sync_name:
            body += f"<p>Sync: {html.escape(sync_name)}</p>"
        if error_message:
            body += f"<p><strong>Error:</strong> {html.escape(str(error_message))}</p>"
        if details:
            body += f"<pre>{html.escape(details)}</pre>"
        if created is not None or updated is not None or failed is not None:
            body += "<p><strong>Summary:</strong></p><ul>"
            if created is not None:
                body += f"<li>Created: {int(created)}</li>"
            if updated is not None:
                body += f"<li>Updated: {int(updated)}</li>"
            if failed is not None:
                body += f"<li>Failed: {int(failed)}</li>"
            body += "</ul>"
        # Send email
        emails = set()
        if self.sync_failure_notification_emails:
            for raw in self.sync_failure_notification_emails.replace('\n', ',').split(','):
                addr = raw.strip()
                if addr and '@' in addr:
                    emails.add(addr)
        partners = self.sync_failure_notification_partner_ids.filtered(lambda p: p.email)
        for p in partners:
            emails.add(p.email.strip())
        if emails:
            try:
                self.env['mail.mail'].sudo().create({
                    'email_to': ','.join(sorted(emails)),
                    'subject': subject,
                    'body_html': body,
                }).send()
            except Exception as e:
                _logger.warning("Could not send sync failure email: %s", e)
        # Send Discuss notifications (channels and users)
        self._send_sync_failure_discuss(sync_type, subject, body)

    def _send_sync_failure_discuss(self, sync_type, subject, body):
        """Post failure notification to configured Discuss channels and notify configured users."""
        self.ensure_one()
        # Post to channels
        for channel in self.sync_failure_notification_channel_ids:
            try:
                channel.sudo().message_post(
                    body=body,
                    subject=subject,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
            except Exception as e:
                _logger.warning("Could not post sync failure to channel %s: %s", channel.name, e)
        # Notify users (in-app notification in Discuss)
        if self.sync_failure_notification_user_ids:
            partner_ids = self.sync_failure_notification_user_ids.mapped('partner_id').ids
            if partner_ids:
                try:
                    self.sudo().message_notify(
                        body=body,
                        subject=subject,
                        partner_ids=partner_ids,
                    )
                except Exception as e:
                    _logger.warning("Could not send sync failure notification to users: %s", e)

    @api.model
    def _cron_auto_sync_products(self):
        """Cron job to auto-sync products based on configured intervals.
        _trigger_product_sync commits last_product_sync immediately to reduce duplicate runs."""
        configs = self.search([('active', '=', True), ('auto_sync_products', '=', True)])
        for config in configs:
            try:
                # Ensure time_of_day is computed before checking
                config._compute_products_time_of_day()
                time_of_day = config.auto_sync_products_time_of_day
                
                # Get current local time (this will also log the timezone source)
                now_local = config._get_local_time()
                
                # Get timezone info for logging (same order as _get_local_time: config company first)
                tz_name = 'UTC'
                tz_source = 'Unknown'
                try:
                    if hasattr(config, 'company_id') and config.company_id and getattr(config.company_id, 'tz', None):
                        tz_name = config.company_id.tz
                        tz_source = 'Config Company'
                    elif hasattr(config.env.company, 'tz') and config.env.company.tz:
                        tz_name = config.env.company.tz
                        tz_source = 'Odoo Company'
                    elif hasattr(config.env.user, 'tz') and config.env.user.tz:
                        tz_name = config.env.user.tz
                        tz_source = 'Odoo User'
                    else:
                        import os
                        if os.path.exists('/etc/timezone'):
                            with open('/etc/timezone', 'r') as f:
                                system_tz = f.read().strip()
                                if system_tz:
                                    tz_name = system_tz
                                    tz_source = 'System (/etc/timezone)'
                except Exception as e:
                    _logger.warning(f"Error getting timezone info: {str(e)}")
                
                # Log current state for debugging
                _logger.info(f"Checking sync for {config.name}: frequency={config.auto_sync_products_frequency}, "
                            f"time_of_day={time_of_day}, hour={config.auto_sync_products_time_hour}, "
                            f"minute={config.auto_sync_products_time_minute}, period={config.auto_sync_products_time_period}, "
                            f"timezone={tz_name} (source: {tz_source}), current_local_time={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}, "
                            f"last_sync={config.last_product_sync}")
                
                if config._should_run_sync(
                    config.auto_sync_products_frequency,
                    config.auto_sync_products_interval_number,
                    config.auto_sync_products_minute_interval,
                    time_of_day,
                    config.auto_sync_products_day_of_week,
                    config.auto_sync_products_day_of_month,
                    config.auto_sync_products_month,
                    config.last_product_sync
                ):
                    _logger.info(f"Auto-syncing products for config: {config.name} (scheduled time: {time_of_day}, timezone: {tz_name}, last sync: {config.last_product_sync})")
                    config._trigger_product_sync()
                else:
                    _logger.debug(f"Sync not needed for {config.name} at this time (scheduled: {time_of_day}, current local: {now_local.strftime('%H:%M')}, last sync: {config.last_product_sync})")
            except Exception as e:
                _logger.error(f"Error in auto-sync products for config {config.name}: {str(e)}", exc_info=True)
                import traceback
                config._send_sync_failure_email('Products', str(e), details=traceback.format_exc())
    
    @api.model
    def _cron_auto_sync_orders(self):
        """Cron job to auto-sync orders based on configured intervals"""
        configs = self.search([('active', '=', True), ('auto_sync_orders', '=', True)])
        for config in configs:
            try:
                if config._should_run_sync(
                    config.auto_sync_orders_frequency,
                    config.auto_sync_orders_interval_number,
                    config.auto_sync_orders_minute_interval,
                    config.auto_sync_orders_time_of_day,
                    config.auto_sync_orders_day_of_week,
                    config.auto_sync_orders_day_of_month,
                    config.auto_sync_orders_month,
                    config.last_order_sync
                ):
                    _logger.info(f"Auto-syncing orders for config: {config.name}")
                    config._trigger_order_sync()
            except Exception as e:
                _logger.error(f"Error in auto-sync orders for config {config.name}: {str(e)}", exc_info=True)
                import traceback
                config._send_sync_failure_email('Orders', str(e), details=traceback.format_exc())
    
    @api.model
    def _cron_auto_sync_inventory(self):
        """Cron job to auto-sync inventory based on configured intervals"""
        configs = self.search([('active', '=', True), ('auto_sync_inventory', '=', True)])
        for config in configs:
            try:
                if config._should_run_sync(
                    config.auto_sync_inventory_frequency,
                    config.auto_sync_inventory_interval_number,
                    config.auto_sync_inventory_minute_interval,
                    config.auto_sync_inventory_time_of_day,
                    config.auto_sync_inventory_day_of_week,
                    config.auto_sync_inventory_day_of_month,
                    config.auto_sync_inventory_month,
                    config.last_inventory_sync
                ):
                    _logger.info(f"Auto-syncing inventory for config: {config.name}")
                    config._trigger_inventory_sync()
            except Exception as e:
                _logger.error(f"Error in auto-sync inventory for config {config.name}: {str(e)}", exc_info=True)
                import traceback
                config._send_sync_failure_email('Inventory', str(e), details=traceback.format_exc())
    
    @api.model
    def _cron_auto_sync_customers(self):
        """Cron job to auto-sync customers based on configured intervals"""
        configs = self.search([('active', '=', True), ('auto_sync_customers', '=', True)])
        for config in configs:
            try:
                if config._should_run_sync(
                    config.auto_sync_customers_frequency,
                    config.auto_sync_customers_interval_number,
                    config.auto_sync_customers_minute_interval,
                    config.auto_sync_customers_time_of_day,
                    config.auto_sync_customers_day_of_week,
                    config.auto_sync_customers_day_of_month,
                    config.auto_sync_customers_month,
                    config.last_customer_sync
                ):
                    _logger.info(f"Auto-syncing customers for config: {config.name}")
                    config._trigger_customer_sync()
            except Exception as e:
                _logger.error(f"Error in auto-sync customers for config {config.name}: {str(e)}", exc_info=True)
                import traceback
                config._send_sync_failure_email('Customers', str(e), details=traceback.format_exc())
    
    @api.model
    def _cron_auto_sync_fulfillments(self):
        """Cron job to auto-sync fulfillments based on configured intervals"""
        configs = self.search([('active', '=', True), ('auto_sync_fulfillments', '=', True)])
        for config in configs:
            try:
                if config._should_run_sync(
                    config.auto_sync_fulfillments_frequency,
                    config.auto_sync_fulfillments_interval_number,
                    config.auto_sync_fulfillments_minute_interval,
                    config.auto_sync_fulfillments_time_of_day,
                    config.auto_sync_fulfillments_day_of_week,
                    config.auto_sync_fulfillments_day_of_month,
                    config.auto_sync_fulfillments_month,
                    config.last_fulfillment_sync
                ):
                    _logger.info(f"Auto-syncing fulfillments for config: {config.name}")
                    config._trigger_fulfillment_sync()
            except Exception as e:
                _logger.error(f"Error in auto-sync fulfillments for config {config.name}: {str(e)}", exc_info=True)
                import traceback
                config._send_sync_failure_email('Fulfillments', str(e), details=traceback.format_exc())
    
    def _trigger_product_sync(self):
        """Trigger product sync.
        
        IMPORTANT:
        - Use the *previous* last_product_sync (or last successful operation) as the
          min_date_modified filter so we sync everything modified since the last run.
        - Then update last_product_sync to NOW and commit immediately so only one
          cron worker can claim this scheduled run.
        """
        self.ensure_one()
        try:
            # Determine min_date_modified based on previous successful syncs
            previous_last_sync = self.last_product_sync
            min_date_modified = None
            if previous_last_sync:
                # Normal case: use the previous last_product_sync timestamp
                min_date_modified = previous_last_sync
            else:
                # Fallback: use the end_date of the last successful product sync operation
                last_sync_op = self.env['bigcommerce.sync.operation'].search([
                    ('sync_type', '=', 'product'),
                    ('config_id', '=', self.id),
                    ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                    ('end_date', '!=', False),
                ], order='end_date desc', limit=1)
                if last_sync_op:
                    min_date_modified = last_sync_op.end_date
            
            # Record this run's start time for scheduling and dashboards
            self.last_product_sync = fields.Datetime.now()
            # Commit immediately so other cron workers see this and do not trigger
            # another auto-sync in the same scheduled window.
            self.env.cr.commit()
            
            _logger.info(
                f"Triggering product sync for config: {self.name}, "
                f"sync_direction: {self.sync_direction_products}, "
                f"min_date_modified filter: "
                f"{min_date_modified if min_date_modified else 'None (will sync all products)'}"
            )
            
            # Create a product sync record with the computed min_date_modified.
            # Store previous_last_sync so product sync can revert config on cancel/failure.
            # Name includes (Auto Sync) so it's visible in Sync Operations like (Full Sync).
            sync_record = self.env['bigcommerce.product.sync'].create({
                'config_id': self.id,
                'sync_direction': self.sync_direction_products,
                'min_date_modified': min_date_modified,
                'config_last_sync_before_run': previous_last_sync,
                'name': f"Product Sync (Auto Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            sync_record.action_sync_products()
        except Exception as e:
            _logger.error(f"Error triggering product sync: {str(e)}", exc_info=True)
    
    def _trigger_order_sync(self):
        """Trigger order sync.

        IMPORTANT:
        - Use the *previous* last_order_sync (or last successful operation) as the
          min_date_modified filter so we sync orders modified since the last run.
        - Then update last_order_sync to NOW and commit immediately so only one
          cron worker can claim this scheduled run.
        """
        self.ensure_one()
        try:
            # Determine min_date_modified based on previous successful syncs
            previous_last_sync = self.last_order_sync
            min_date_modified = None
            if previous_last_sync:
                # Normal case: use the previous last_order_sync timestamp
                min_date_modified = previous_last_sync
            else:
                # Fallback: use the end_date of the last successful order sync operation
                last_sync_op = self.env['bigcommerce.sync.operation'].search([
                    ('sync_type', '=', 'order'),
                    ('config_id', '=', self.id),
                    ('state', 'in', ['completed', 'completed_with_warnings', 'completed_with_errors']),
                    ('end_date', '!=', False),
                ], order='end_date desc', limit=1)
                if last_sync_op:
                    min_date_modified = last_sync_op.end_date

            # Record this run's start time for scheduling and dashboards
            self.last_order_sync = fields.Datetime.now()
            self.env.cr.commit()

            _logger.info(
                f"Triggering order sync for config: {self.name}, "
                f"min_date_modified filter: "
                f"{min_date_modified if min_date_modified else 'None (will sync all orders)'}"
            )

            sync_record = self.env['bigcommerce.order.sync'].create({
                'config_id': self.id,
                'min_date_modified': min_date_modified,
                'config_last_sync_before_run': previous_last_sync,
                'name': f"Order Sync (Auto Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            sync_record.action_sync_orders()
        except Exception as e:
            _logger.error(f"Error triggering order sync: {str(e)}", exc_info=True)
    
    def _trigger_inventory_sync(self):
        """Trigger inventory sync"""
        self.ensure_one()
        try:
            self.last_inventory_sync = fields.Datetime.now()
            self.env.cr.commit()
            sync_record = self.env['bigcommerce.inventory.sync'].create({
                'config_id': self.id,
                'sync_direction': 'odoo_to_bc',
                'min_date_modified': self.last_inventory_sync,  # Only sync inventory changed since last sync
                'name': f"Inventory Sync (Auto Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            sync_record.action_sync_inventory()
        except Exception as e:
            _logger.error(f"Error triggering inventory sync: {str(e)}", exc_info=True)
    
    def _trigger_customer_sync(self):
        """Trigger customer sync"""
        self.ensure_one()
        try:
            self.last_customer_sync = fields.Datetime.now()
            self.env.cr.commit()
            sync_record = self.env['bigcommerce.customer.sync'].create({
                'config_id': self.id,
                'min_date_modified': self.last_customer_sync,  # Only sync customers modified since last sync
                'name': f"Customer Sync (Auto Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            sync_record.action_sync_customers()
        except Exception as e:
            _logger.error(f"Error triggering customer sync: {str(e)}", exc_info=True)
    
    def _trigger_fulfillment_sync(self):
        """Trigger fulfillment sync"""
        self.ensure_one()
        try:
            self.last_fulfillment_sync = fields.Datetime.now()
            self.env.cr.commit()
            sync_record = self.env['bigcommerce.fulfillment.sync'].create({
                'config_id': self.id,
                'date_from': self.last_fulfillment_sync,  # Only sync fulfillments from last sync date
                'name': f"Fulfillment Sync (Auto Sync) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            })
            sync_record.action_sync_fulfillments()
        except Exception as e:
            _logger.error(f"Error triggering fulfillment sync: {str(e)}", exc_info=True)
    
    # Webhook Management Methods
    def action_register_webhooks(self):
        """Register webhooks with BigCommerce"""
        self.ensure_one()
        if not self.webhook_enabled:
            raise UserError("Webhooks are not enabled. Please enable webhooks first.")
        
        try:
            api = self.get_api_client()
            
            # Get the webhook URL
            webhook_url = self.webhook_url
            if not webhook_url or webhook_url.startswith('/'):
                raise UserError("Webhook URL is not configured properly. Please set a custom base URL or ensure your Odoo instance has a valid web.base.url configured.")
            
            # Define webhook scopes based on enabled fields
            scopes_to_register = []
            
            # Product webhooks
            if self.webhook_product_created:
                scopes_to_register.append(('store/product/created', 'Product Created'))
            if self.webhook_product_updated:
                scopes_to_register.append(('store/product/updated', 'Product Updated'))
            if self.webhook_product_deleted:
                scopes_to_register.append(('store/product/deleted', 'Product Deleted'))
            if self.webhook_product_inventory_updated:
                scopes_to_register.append(('store/product/inventory/updated', 'Product Inventory Updated'))
            
            # Order webhooks
            if self.webhook_order_created:
                scopes_to_register.append(('store/order/created', 'Order Created'))
            if self.webhook_order_updated:
                scopes_to_register.append(('store/order/updated', 'Order Updated'))
            if self.webhook_order_archived:
                scopes_to_register.append(('store/order/archived', 'Order Archived'))
            if self.webhook_order_status_updated:
                scopes_to_register.append(('store/order/statusUpdated', 'Order Status Updated'))
            if self.webhook_order_message_created:
                scopes_to_register.append(('store/order/message/created', 'Order Message Created'))
            if self.webhook_order_refund_created:
                scopes_to_register.append(('store/order/refund/created', 'Order Refund Created'))
            
            # Customer webhooks
            if self.webhook_customer_created:
                scopes_to_register.append(('store/customer/created', 'Customer Created'))
            if self.webhook_customer_updated:
                scopes_to_register.append(('store/customer/updated', 'Customer Updated'))
            if self.webhook_customer_deleted:
                scopes_to_register.append(('store/customer/deleted', 'Customer Deleted'))
            if self.webhook_customer_address_created:
                scopes_to_register.append(('store/customer/address/created', 'Customer Address Created'))
            if self.webhook_customer_address_updated:
                scopes_to_register.append(('store/customer/address/updated', 'Customer Address Updated'))
            if self.webhook_customer_address_deleted:
                scopes_to_register.append(('store/customer/address/deleted', 'Customer Address Deleted'))
            
            # Shipment webhooks
            if self.webhook_shipment_created:
                scopes_to_register.append(('store/shipment/created', 'Shipment Created'))
            if self.webhook_shipment_updated:
                scopes_to_register.append(('store/shipment/updated', 'Shipment Updated'))
            if self.webhook_shipment_deleted:
                scopes_to_register.append(('store/shipment/deleted', 'Shipment Deleted'))
            
            # Category webhooks
            if self.webhook_category_created:
                scopes_to_register.append(('store/category/created', 'Category Created'))
            if self.webhook_category_updated:
                scopes_to_register.append(('store/category/updated', 'Category Updated'))
            if self.webhook_category_deleted:
                scopes_to_register.append(('store/category/deleted', 'Category Deleted'))
            
            if not scopes_to_register:
                raise UserError("No webhook scopes selected. Please enable at least one webhook type.")
            
            # Validate webhook URL is HTTPS
            if not webhook_url.startswith('https://'):
                raise UserError(
                    f"Invalid webhook URL: {webhook_url}\n\n"
                    f"According to BigCommerce documentation:\n"
                    f"- Webhook destination must use HTTPS (not HTTP)\n"
                    f"- Must be served on port 443 (custom ports not supported)\n"
                    f"- Must be publicly accessible (not localhost)\n\n"
                    f"Please configure a custom base URL with HTTPS or set up a reverse proxy."
                )
            
            # Check if URL is localhost
            if 'localhost' in webhook_url or '127.0.0.1' in webhook_url:
                raise UserError(
                    f"Invalid webhook URL: {webhook_url}\n\n"
                    f"Localhost URLs cannot be used for webhooks.\n"
                    f"BigCommerce webhooks require publicly accessible HTTPS URLs.\n\n"
                    f"For development:\n"
                    f"- Use ngrok or similar tunneling service\n"
                    f"- Use webhook.site for testing\n"
                    f"- Deploy to a public server"
                )
            
            # Register each webhook
            import json
            registered_webhooks = {}
            success_count = 0
            failed_scopes = []
            detailed_errors = []
            
            for scope, description in scopes_to_register:
                try:
                    webhook_data = {
                        'scope': scope,
                        'destination': webhook_url,
                        'is_active': True,
                        'headers': {
                            'X-Odoo-Config-ID': str(self.id)
                        }
                    }
                    
                    _logger.info(f"Attempting to register webhook: {description}")
                    _logger.debug(f"Webhook data: {webhook_data}")
                    
                    response = api._make_request('POST', 'hooks', data=webhook_data)
                    _logger.info(f"Webhook registration response for {description}: {response}")
                    
                    # Handle both wrapped (V3 API) and unwrapped responses
                    webhook_info = None
                    if response:
                        if isinstance(response, dict):
                            # Check if response has 'data' field (V3 API)
                            if 'data' in response:
                                webhook_info = response['data']
                            # Or if response has 'id' directly
                            elif 'id' in response:
                                webhook_info = response
                    
                    if webhook_info and 'id' in webhook_info:
                        registered_webhooks[scope] = webhook_info['id']
                        success_count += 1
                        _logger.info(f"✓ Successfully registered webhook: {description} (ID: {webhook_info['id']})")
                    else:
                        error_msg = 'No valid response received'
                        if response:
                            if isinstance(response, dict):
                                error_msg = response.get('title') or response.get('message') or str(response)
                            else:
                                error_msg = str(response)
                        failed_scopes.append(description)
                        detailed_errors.append(f"{description}: {error_msg}")
                        _logger.error(f"✗ Failed to register webhook: {description}. Response: {response}")
                
                except UserError as ue:
                    # UserError from _make_request indicates an API error
                    error_msg = str(ue)
                    failed_scopes.append(description)
                    detailed_errors.append(f"{description}: {error_msg}")
                    _logger.error(f"✗ API error registering webhook {description}: {error_msg}")
                
                except Exception as e:
                    error_msg = str(e)
                    failed_scopes.append(description)
                    detailed_errors.append(f"{description}: {error_msg}")
                    _logger.error(f"✗ Error registering webhook {description}: {error_msg}", exc_info=True)
            
            # Update webhook status
            self.write({
                'webhooks_registered': True if success_count > 0 else False,
                'webhook_registration_date': fields.Datetime.now() if success_count > 0 else False,
                'webhook_ids_json': json.dumps(registered_webhooks) if registered_webhooks else False
            })
            
            # Show notification with detailed errors
            if success_count > 0:
                message = f"Successfully registered {success_count} webhook(s)."
                if failed_scopes:
                    message += f"\n\nFailed to register {len(failed_scopes)} webhook(s)."
                    if detailed_errors:
                        message += "\n\nErrors:\n" + "\n".join(detailed_errors[:5])  # Show first 5 errors
                        if len(detailed_errors) > 5:
                            message += f"\n... and {len(detailed_errors) - 5} more errors (check logs)"
            else:
                message = f"Failed to register all {len(failed_scopes)} webhook(s)."
                if detailed_errors:
                    message += "\n\nErrors:\n" + "\n".join(detailed_errors[:5])  # Show first 5 errors
                    if len(detailed_errors) > 5:
                        message += f"\n... and {len(detailed_errors) - 5} more errors (check logs)"
                
                # Add common troubleshooting tips
                message += "\n\nCommon issues:"
                message += "\n• Webhook URL must be HTTPS (not HTTP)"
                message += "\n• Must be publicly accessible (not localhost)"
                message += "\n• API token must have 'Information & Settings' scope"
                message += "\n\nFor development, use ngrok or similar tunneling service."
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Webhook Registration',
                    'message': message,
                    'type': 'success' if success_count > 0 else 'danger',
                    'sticky': True,
                }
            }
            
        except Exception as e:
            _logger.error(f"Error registering webhooks: {str(e)}", exc_info=True)
            raise UserError(f"Failed to register webhooks: {str(e)}")
    
    def action_unregister_webhooks(self):
        """Unregister all webhooks from BigCommerce"""
        self.ensure_one()
        
        try:
            api = self.get_api_client()
            
            # Get registered webhook IDs
            import json
            if self.webhook_ids_json:
                webhook_ids = json.loads(self.webhook_ids_json)
            else:
                # Try to fetch all webhooks and find ours
                response = api._make_request('GET', 'hooks')
                webhook_ids = {}
                
                if response:
                    # Handle wrapped response (V3 API)
                    webhooks_list = []
                    if isinstance(response, dict) and 'data' in response:
                        webhooks_list = response['data']
                    elif isinstance(response, list):
                        webhooks_list = response
                    
                    for webhook in webhooks_list:
                        if isinstance(webhook, dict) and 'destination' in webhook:
                            if self.webhook_url in webhook.get('destination', ''):
                                webhook_ids[webhook.get('scope', '')] = webhook.get('id')
            
            if not webhook_ids:
                raise UserError("No registered webhooks found for this configuration.")
            
            # Delete each webhook
            success_count = 0
            failed_count = 0
            
            for scope, webhook_id in webhook_ids.items():
                try:
                    api._make_request('DELETE', f'hooks/{webhook_id}')
                    success_count += 1
                    _logger.info(f"Unregistered webhook: {scope} (ID: {webhook_id})")
                except Exception as e:
                    failed_count += 1
                    _logger.error(f"Error unregistering webhook {scope} (ID: {webhook_id}): {str(e)}")
            
            # Update webhook status
            self.write({
                'webhooks_registered': False,
                'webhook_ids_json': False,
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Webhook Unregistration',
                    'message': f"Successfully unregistered {success_count} webhook(s)." + 
                              (f"\n\nFailed to unregister {failed_count} webhook(s)." if failed_count > 0 else ""),
                    'type': 'success' if failed_count == 0 else 'warning',
                    'sticky': True,
                }
            }
            
        except Exception as e:
            _logger.error(f"Error unregistering webhooks: {str(e)}", exc_info=True)
            raise UserError(f"Failed to unregister webhooks: {str(e)}")
    
    def action_view_webhooks(self):
        """View registered webhooks in BigCommerce"""
        self.ensure_one()
        
        try:
            api = self.get_api_client()
            
            # Fetch all webhooks
            response = api._make_request('GET', 'hooks')
            
            if not response:
                raise UserError("No webhooks found or failed to fetch webhooks from BigCommerce.")
            
            # Handle wrapped response (V3 API)
            webhooks_list = []
            if isinstance(response, dict) and 'data' in response:
                webhooks_list = response['data']
            elif isinstance(response, list):
                webhooks_list = response
            
            # Filter webhooks that belong to this configuration
            our_webhooks = []
            for webhook in webhooks_list:
                if isinstance(webhook, dict) and 'destination' in webhook:
                    if self.webhook_url in webhook.get('destination', '') or \
                       str(self.id) in webhook.get('headers', {}).get('X-Odoo-Config-ID', ''):
                        our_webhooks.append(webhook)
            
            # Format webhook information
            webhook_info = "Registered Webhooks:\n\n"
            if our_webhooks:
                for webhook in our_webhooks:
                    webhook_info += f"ID: {webhook.get('id')}\n"
                    webhook_info += f"Scope: {webhook.get('scope')}\n"
                    webhook_info += f"Destination: {webhook.get('destination')}\n"
                    webhook_info += f"Active: {'Yes' if webhook.get('is_active') else 'No'}\n"
                    webhook_info += f"Created: {webhook.get('created_at', 'N/A')}\n"
                    webhook_info += "\n"
                webhook_info += f"\nTotal: {len(our_webhooks)} webhook(s)"
            else:
                webhook_info += "No webhooks found for this configuration."
                webhook_info += f"\n\nWebhook URL: {self.webhook_url}"
                webhook_info += f"\nTotal webhooks in store: {len(webhooks_list)}"
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Registered Webhooks',
                    'message': webhook_info,
                    'type': 'info',
                    'sticky': True,
                }
            }
            
        except Exception as e:
            _logger.error(f"Error viewing webhooks: {str(e)}", exc_info=True)
            raise UserError(f"Failed to view webhooks: {str(e)}")

