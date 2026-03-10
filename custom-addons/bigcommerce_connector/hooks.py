# -*- coding: utf-8 -*-

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(cr, registry):
    """Post-init hook to create dashboard records for all existing active configs"""
    _logger.info("Running post-init hook: Creating dashboard records for existing configs")
    
    try:
        # Get models from registry
        ConfigModel = registry['bigcommerce.config']
        DashboardModel = registry['bigcommerce.dashboard']
        
        # Find all active configs using SQL
        cr.execute("""
            SELECT id, name FROM bigcommerce_config WHERE active = TRUE
        """)
        active_configs = cr.fetchall()
        _logger.info(f"Found {len(active_configs)} active BigCommerce configurations")
        
        if not active_configs:
            _logger.info("No active configurations found, skipping dashboard record creation")
            return
        
        # Get table name for dashboard
        table_name = DashboardModel._table
        
        # Check existing dashboard records
        cr.execute(f"""
            SELECT config_id FROM {table_name}
        """)
        existing_config_ids = [row[0] for row in cr.fetchall() if row[0]]
        _logger.info(f"Found {len(existing_config_ids)} existing dashboard records")
        
        # Create dashboard records for configs that don't have one
        created_count = 0
        for config_id, config_name in active_configs:
            if config_id not in existing_config_ids:
                try:
                    cr.execute(f"""
                        INSERT INTO {table_name} (config_id, create_uid, create_date, write_uid, write_date)
                        VALUES (%s, %s, NOW() AT TIME ZONE 'UTC', %s, NOW() AT TIME ZONE 'UTC')
                    """, (config_id, 1, 1))  # Use admin user (ID 1) for system operations
                    created_count += 1
                    _logger.info(f"Created dashboard record for config {config_id} ({config_name})")
                except Exception as e:
                    _logger.error(f"Error creating dashboard record for config {config_id}: {str(e)}", exc_info=True)
        
        if created_count > 0:
            cr.commit()
            _logger.info(f"Post-init hook completed: Created {created_count} dashboard record(s)")
        else:
            _logger.info("Post-init hook completed: No new dashboard records needed")
    except Exception as e:
        _logger.error(f"Error in post_init_hook: {str(e)}", exc_info=True)

