# Remove Odoo Branding (Emails & Portal)

Single Odoo 19.0 module that removes Odoo branding from **emails** and **portal/login** so you only need to install and activate one app.

Based on [OCA/server-brand](https://github.com/OCA/server-brand/tree/19.0) (mail_debranding, portal_debranding, web login).

## What it does

- **Emails**: Strips odoo.com links and “Powered by” from sent email bodies; removes the word “odoo” from rendered template text where present.
- **Portal**: Removes the “Powered by Odoo” footer from portal record pages (e.g. `/my/orders`, `/my/invoices`) and the Odoo documentation link from the My Security / API Keys section.
- **Sale portal**: Hides the “Connect with your software!” button and its modal on quotation/sales order portal pages.
- **Login**: Removes the “Powered by Odoo” link from the login page footer and hides the Odoo badge on frontend/portal.

## Installation

1. Copy the `odoo_debrand` folder into an addons path (e.g. a custom addons directory or your Odoo 19 addons).
2. Update the app list (Apps → Update Apps List if needed).
3. Search for **Remove Odoo Branding** and install it.

## Dependencies

- `mail`
- `portal`
- `sale`
- `web`

No other OCA or third‑party modules are required.
