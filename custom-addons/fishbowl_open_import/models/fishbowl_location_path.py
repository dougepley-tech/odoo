# -*- coding: utf-8 -*-
#
# Port of standalone ``build_location_id`` (Fishbowl group/type/location → Odoo path segments).
# Keep in sync with ``odoo_inventory_location.py`` when those rules change.

def build_fishbowl_odoo_location_path(locationgroup_name, locationtype_name, location_name):
    """
    Return a slash-separated path (no leading slash) matching Odoo internal location names,
    e.g. ``WWH/Stock/W:1:1:03`` for Westminster Main + Stock + bin.

    This mirrors the export script used for CSV inventory so Odoo sync targets the same
    locations as manual imports.
    """
    lg = (locationgroup_name or '').strip()
    lt = (locationtype_name or '').strip()
    loc = (location_name or '').strip()

    if lg == 'Westminster Main':
        lg_code = 'WWH'
    elif lg == 'Hanover':
        lg_code = 'HWH'
    elif lg == 'Engine Inspections':
        lg_code = 'WWH/Engine Inspections'
    else:
        lg_code = lg

    if lg == 'IAG Performance Order Review':
        return 'WWH/IAG Performance Order Review/%s' % loc

    if lg == 'Amazon FBA':
        if lt.lower() == 'stock' and loc.lower() == 'amazon fba':
            return 'WWH/Amazon FBA/Stock'
        if loc.lower() == 'stock':
            return 'WWH/Amazon FBA/Stock'

    if lg == 'Quality Control':
        return 'WWH/QC/%s' % loc

    if lg == 'Hanover' and lt == 'Inspection':
        return 'HWH/QC'

    if lg == 'Offices' and lt == 'Locked':
        return 'WWH/Offices/%s' % loc

    if lg == 'Show Inventory' and lt == 'Receiving':
        return 'SHINV/Race Trailer'

    if lg == 'Show Inventory' and loc == 'Hidden Inventory':
        return 'SHINV/Hidden'

    if lg == 'Show Inventory' and loc == 'Hidden Head Inventory':
        return 'SHINV/Stock/Hidden Head Inventory'

    if lg == 'Active Transfer' and lt == 'Shipping':
        return 'WWH/Active Transfer/Shipping'

    if lg == 'Active Transfer' and loc == 'Stock':
        return 'WWH/Active Transfer/Stock'

    if lg == 'Westminster Main' and loc == 'Manufacturing':
        return 'WWH/Stock/Manufacturing'

    if lg == 'Westminster Main' and loc == 'Production Assembly Room':
        return 'WWH/Stock/Manufacturing/Production Assembly Room'
    if lg == 'Westminster Main' and loc == 'Receiving':
        return 'WWH/Stock/Manufacturing/Production/Receiving'

    if lt == loc:
        return '%s/%s' % (lg_code, loc)

    return '%s/%s/%s' % (lg_code, lt, loc)
