# -*- coding: utf-8 -*-

def migrate(cr, version):
    """Drop old unique constraint (group+type only) before new group+type+name constraint."""
    cr.execute(
        """
        ALTER TABLE fishbowl_location_map
        DROP CONSTRAINT IF EXISTS fishbowl_location_map_group_type_company_uniq;
        """
    )
