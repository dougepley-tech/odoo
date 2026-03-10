# Attribute and Attribute Value Sync from BigCommerce

## Overview
When products are synced from BigCommerce to Odoo, the module automatically creates product attributes and attribute values in Odoo based on BigCommerce product options and option values.

## How It Works

### 1. Attribute Creation (`_sync_product_attributes`)
When a product with variants is synced from BigCommerce:
- The module fetches product options from BigCommerce using the `/products/{id}/options` endpoint
- For each option (e.g., "Size", "Color"), it creates a `product.attribute` record in Odoo
- For each option value (e.g., "Small", "Red"), it creates a `product.attribute.value` record
- These are linked to the product template via `product.attribute.line`

### 2. Fallback Method (`_extract_attributes_from_variants`)
If the options endpoint is not available or returns no data:
- The module extracts attribute information directly from variant data
- It processes the `option_values` field in each variant
- Creates attributes and values from the extracted data

### 3. Variant Creation (`_sync_product_variants`)
After attributes are created:
- The module fetches variants from BigCommerce
- Creates `product.product` records in Odoo
- Maps BigCommerce variant option values to Odoo attribute values
- Links variants to the product template with proper attribute combinations

## Implementation Details

### Attribute Creation Process
1. **Fetch Options**: Calls `api.get_product_options(bc_product_id)`
2. **Create Attributes**: For each option, creates `product.attribute` if it doesn't exist
3. **Fetch Option Values**: Calls `api.get_product_option_values(bc_product_id, option_id)`
4. **Create Attribute Values**: For each option value, creates `product.attribute.value` if it doesn't exist
5. **Link to Product**: Creates `product.attribute.line` to link attributes to the product template

### Code Location
- **Method**: `bigcommerce_product.py::_sync_product_attributes()`
- **Lines**: ~407-530
- **Creates**:
  - `product.attribute` records (line ~449)
  - `product.attribute.value` records (line ~486)
  - `product.attribute.line` records (line ~507)

## Notes
- Attributes are created with `create_variant='always'` to ensure variants are generated
- Existing attributes and values are reused if they already exist (matched by name)
- The sync process logs all attribute and value creation for troubleshooting
- If attribute sync fails, it doesn't fail the entire product sync (wrapped in try/except)

