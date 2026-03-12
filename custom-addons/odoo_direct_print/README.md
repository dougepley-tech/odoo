# Odoo Direct Print

Odoo 19.0 module that adds **Print** as a hover icon on attachments and in the document preview.

## Features

- **Print icon on hover** - When you hover over any attachment in the chatter, a Print icon appears alongside Download and Remove (not in the action menu)
- **Print in document preview** - When viewing a document (PDF, image, etc.) in the preview, a Print button appears in the header next to Download
- **Removes Print from delivery order action menu** - The standard Print button (Print, Return, Refund Shipment) is hidden on delivery orders
- Works with all record types that use the chatter

## Dependencies

- **mail** - Chatter and attachments
- **stock** - Delivery orders (to hide Print button)
- **web** - Base web module

## Installation

1. Copy the `odoo_direct_print` folder to your Odoo addons directory
2. Update the apps list: **Apps** → **Update Apps List**
3. Search for "Odoo Direct Print" and click **Install**

## Usage

**From chatter attachments:**
1. Hover over any attachment (delivery slip, shipping label, etc.) in the chatter
2. Click the **Print** icon (next to Download and Remove)
3. The document opens in a new tab—use Ctrl+P to print

**From document preview:**
1. Click an attachment to open it in the preview
2. Click **Print** in the header (next to Download)
3. The document opens in a new tab—use Ctrl+P to print
