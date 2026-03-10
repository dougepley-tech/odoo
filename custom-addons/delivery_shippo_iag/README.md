# Shippo Shipping Integration (IAG) - Odoo 19

Custom Shippo integration for Odoo 19 supporting multi-carrier rates, label generation, and split-warehouse workflows (e.g. Hanover / Westminster).

## Features

- **Configuration**: API key (masked in UI), Live/Test toggle, origin addresses (validated with Shippo), carrier accounts (refresh from Shippo), delivery methods with carrier scope, rate type (negotiated vs published), markup rules, package templates.
- **Sales orders**: "Get Shippo Rates" button; rate wizard with origin, parcel, options; apply estimate to SO.
- **Delivery orders**: "Get Shippo Rates" and "Cancel Shippo Shipment" buttons; rate wizard; generate label (PDF attached, tracking on picking).
- **Rates**: Single Shippo Shipment API call with optional carrier_account IDs; markup rules applied; insurance, residential, signature options.

## Installation

1. Copy the `delivery_shippo_iag` folder into your Odoo addons path.
2. Install the module: Apps → Delivery Shippo Integration (IAG).
3. Install Python dependency: `pip install requests` (if not already present).

## Configuration

1. **API key**: Inventory → Configuration → Shippo → API Settings. Enter your Shippo API key and save. Or set System Parameters `delivery_shippo_iag.api_key` and `delivery_shippo_iag.use_test_env` (Settings → Technical → System Parameters).
2. **Origin addresses**: Inventory → Configuration → **Shippo Origin Addresses**. Create entries (e.g. Hanover, Westminster), fill address, then "Validate with Shippo". You can also create an origin from the delivery method form: Default Origin Address → "Create and edit...".
3. **Carrier accounts**: In Shippo section of Settings, click "Refresh Carrier Accounts". Configure under Shippo → Carrier Accounts if needed.
4. **Delivery method**: Inventory → Configuration → Delivery Methods → create or edit → Provider = **Shippo**. Set carrier accounts, default rate type, label format, and markup rules.

## Usage

- **Sales order**: Open a quotation or order → "Get Shippo Rates" → choose origin, parcel, options → Get Rates → select a rate → "Apply Estimate to Order".
- **Delivery order**: Open a delivery order (ready or in progress) → "Get Shippo Rates" → Get Rates → select a rate → "Generate Label". Tracking and label PDF are stored on the picking. Use "Cancel Shippo Shipment" to request a refund.

## Price differences vs Shippo web

Rates in Odoo can differ from Shippo’s web UI for these reasons:

1. **Weight and dimensions** — Package weight and L×W×H must match exactly. Different weight (e.g. 0.70 lb vs 1 lb) or dimensions change the rate. Use the same values in the wizard as in Shippo’s “Create label” form.
2. **Negotiated vs published** — Odoo uses the **Rate type** from the delivery method (Negotiated or Published). **Negotiated** uses your Shippo carrier accounts (e.g. UPS Westminster, Hanover); each account has its own pricing. Shippo’s web UI may show a different mix of accounts or published rates, so the same service (e.g. “UPS Ground”) can show a different price.
3. **Which carrier account** — With Negotiated rates, the list of accounts comes from the delivery method (and origin–carrier mapping). A different account (e.g. “Zone 2 Trans. Small Package” vs “UPS Westminster Test”) will show a different price for the same service name.
4. **Options** — Insurance / declared value, residential delivery, and signature affect the total. Ensure **Insurance / Declared Value**, **Residential**, and **Signature** in the wizard match the options you use in Shippo’s web UI.
5. **Origin and addresses** — Origin and destination affect zones and pricing. Use the same sender/recipient and origin in both places.

6. **Insurance included in rate** — When **Insurance / Declared Value** is set in the wizard, Shippo’s API returns rates that already include the insurance fee. Shippo’s web UI often shows the base shipping rate and insurance as separate lines (e.g. $43.83 + $4.67). In Odoo you see the combined amount (e.g. $48.50). So the same Westminster UPS Ground account will show $48.50 in Odoo (with insurance) and $43.83 on the web (base only); the web total with protection matches. To compare base-to-base, set Insurance to 0 in Odoo.

To compare like-for-like: use the same parcel (weight, dimensions), same options (insurance, residential, signature), same origin and destination, and the same carrier account (or published vs negotiated) in both Odoo and Shippo.

## Technical

- **Odoo 19**: Views use Odoo 17+ syntax (no `attrs`/`states`; use `invisible`, `readonly`, `column_invisible` with expression or `True`). Python uses `@api.model` where appropriate; no deprecated decorators.
- Module name: `delivery_shippo_iag`
- Depends: stock, delivery, sale_stock, mail, account
- Shippo API: addresses, shipments (with rates), transactions (labels), refunds. Uses `requests` (no Shippo SDK required).
- API key must not be logged; store only in `ir.config_parameter`.

## References

- [Shippo API](https://docs.goshippo.com/)
- [Odoo 19 Third-party shipping](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/shipping_receiving/setup_configuration/third_party_shipper.html)
