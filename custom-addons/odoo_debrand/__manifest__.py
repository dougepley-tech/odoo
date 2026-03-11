# Copyright 2016-2021 OCA and contributors (mail_debranding, portal_debranding)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Remove Odoo Branding (Emails & Portal)",
    "version": "19.0.1.0.0",
    "category": "Hidden",
    "summary": "Remove Odoo branding from sent emails, portal pages, and login screen",
    "author": "OCA, Custom",
    "website": "https://github.com/OCA/server-brand",
    "license": "AGPL-3",
    "depends": [
        "mail",
        "portal",
        "sale",
        "web",
    ],
    "data": [
        "views/portal_templates.xml",
        "views/web_login_debrand.xml",
        "views/sale_portal_debrand.xml",
    ],
    "installable": True,
    "auto_install": False,
}
