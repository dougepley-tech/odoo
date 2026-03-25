from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Create RMA locations, picking types, and routes for existing warehouses."""
    warehouses = env["stock.warehouse"].search([])
    rma_root_loc = env.ref("rma_odoo_19.stock_location_rma", raise_if_not_found=False)
    for wh in warehouses:
        if not wh.rma_loc_id and rma_root_loc:
            loc = env["stock.location"].create(
                {
                    "name": wh.name,
                    "active": True,
                    "usage": "internal",
                    "company_id": wh.company_id.id,
                    "location_id": rma_root_loc.id,
                    "barcode": wh._valid_barcode(
                        (wh.code or "").replace(" ", "").upper() + "-RMA",
                        wh.company_id.id,
                    ),
                }
            )
            wh.rma_loc_id = loc
        new_vals = wh._create_or_update_sequences_and_picking_types()
        if new_vals:
            wh.write(new_vals)
    # RMA routes are created when the user enables RMA on a warehouse (depends:
    # ["rma"]). Do NOT call _create_or_update_route here—it causes KeyError
    # during install when mrp/purchase_stock are present.
