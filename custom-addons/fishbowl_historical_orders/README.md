# Fishbowl Historical Orders (Odoo 19.0)

This Odoo 19 module imports **historical order data** from a **Fishbowl MySQL** database and stores it in Odoo for viewing and reporting. It does **not** create Odoo sales orders or any accounting entries.

## Features

- **Separate storage**: Historical Fishbowl orders are stored in their own models (`fishbowl.historical.order` and `fishbowl.historical.order.line`), not linked to Odoo sale orders.
- **Customer tab**: For contacts that match a Fishbowl customer, a **Fishbowl History** tab appears on the contact form showing their historical orders.
- **No accounting**: Zero integration with Odoo invoicing or journal entries.
- **Batch import**: Import the last 3 years (or any date range) in configurable batches (e.g. 500 orders per run).
- **Search and filter**: Filter by order number, customer, status, date; search by part number (from lines); group by customer or status.
- **No product matching**: Line items show Fishbowl part number and description only; no link to Odoo products required.

## Requirements

- Odoo 19.0
- Python dependency: **PyMySQL** (declared in the manifest; install with `pip install pymysql` if needed)
- Access to the Fishbowl MySQL database (read-only SELECT on `SO`, `SOITEM`, and `CUSTOMER` is enough)

## Installation

1. Copy the `fishbowl_historical_orders` folder into your Odoo addons path.
2. Install PyMySQL if not present: `pip install pymysql`
3. In Odoo: Apps → Update Apps List, then install **Fishbowl Historical Orders**.

## Configuration

1. Go to **Fishbowl → Connection** and create a record with:
   - **Host**, **Port** (default 3306), **Database**
   - **User** and **Password** for the MySQL user (needs SELECT on `SO`, `SOITEM`, `CUSTOMER`).
2. Save.

## Importing historical orders

1. Go to **Fishbowl → Historical Orders → Import from Fishbowl**.
2. Choose the **Fishbowl Connection**.
3. Set **From Date** and **To Date** (e.g. 3 years ago to today).
4. Set **Batch Size** (e.g. 500) to limit orders per run; run the wizard multiple times to cover the full range.
5. Optionally adjust **Match Customers by Reference** / **Match Customers by Name** (used to link orders to Odoo contacts).
6. Click **Import**.

Running the import again with the same date range will **skip** orders that are already imported (by Fishbowl SO id).

## Customer matching

- **By Reference**: If the Odoo contact has **Reference** set to the Fishbowl customer ID (numeric), orders for that customer are linked to the contact.
- **By Name**: If no ref match, the customer name from Fishbowl is matched to the contact **Name** (case-insensitive).

Matched orders appear in the **Fishbowl History** tab on the contact form. Unmatched orders remain in the list and can still be searched and filtered.

## Fishbowl schema

The module is aligned with the **Fishbowl Advanced Database Dictionary**:
[https://help.fishbowlinventory.com/advanced/s/Database-Dictionary](https://help.fishbowlinventory.com/advanced/s/Database-Dictionary)

Expected tables/columns (Fishbowl Advanced):

- **SO**: `id`, `num`, `customerId`, `customerPO`, `dateCreated`, `dateCompleted`, `statusId`, `note` (SO uses `statusId`, not `status`; the import maps it to order status.)
- **SOITEM**: `soId`, `partId`, `partNum`, `description`, `qtyOrdered`, `productPrice`, `totalPrice`, `sortId`
- **CUSTOMER**: `id`, `name`

Table/column names can be case-sensitive on some MySQL setups. To adapt to a different schema, edit the SQL in `models/fishbowl_config.py` (`_fetch_orders`, `_fetch_customer_name`, `_fetch_order_lines`). The fallback in `_fetch_order_lines` maps alternative column names when the default SOITEM query fails.

## License

LGPL-3.
