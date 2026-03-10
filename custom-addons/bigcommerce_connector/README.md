# BigCommerce Connector for Odoo

This Odoo module provides integration between Odoo and BigCommerce via the BigCommerce API v2. It enables bidirectional synchronization of products, orders, inventory, and customers between the two platforms.

## Features

- **Product Synchronization**: Sync products between Odoo and BigCommerce (bidirectional)
- **Order Synchronization**: Import orders from BigCommerce to Odoo
- **Inventory Synchronization**: Keep inventory levels in sync between both systems (bidirectional)
- **Customer Synchronization**: Sync customer data between Odoo and BigCommerce (bidirectional)
- **Webhook Support**: Real-time updates via BigCommerce webhooks
- **Manual and Automatic Sync**: Configure automatic syncing or trigger manual syncs
- **Flexible Sync Directions**: Choose sync direction for each entity type

## Installation

1. Copy the `bigcommerce_connector` folder to your Odoo addons directory
2. Update the apps list in Odoo
3. Install the "BigCommerce Connector" module

## Configuration

### BigCommerce API Setup

1. Log in to your BigCommerce store admin panel
2. Go to **Advanced Settings** > **API Accounts**
3. Create a new API account with the following scopes:
   - `store/products`
   - `store/orders`
   - `store/customers`
   - `store/inventory`
4. Copy your **Store Hash** and **Access Token**

### Odoo Configuration

1. Navigate to **BigCommerce** > **Configuration**
2. Create a new configuration:
   - Enter a configuration name
   - Enter your BigCommerce Store Hash
   - Enter your BigCommerce Access Token
   - Configure sync settings and directions
3. Click **Test Connection** to verify your credentials
4. Configure default product category and unit of measure if needed

## Usage

### Product Syncimage.png

1. Go to **BigCommerce** > **Sync Operations** > **Product Sync**
2. Create a new product sync record
3. Select your configuration
4. Choose sync direction:
   - **Odoo to BigCommerce**: Push Odoo products to BigCommerce
   - **BigCommerce to Odoo**: Import products from BigCommerce
   - **Bidirectional**: Sync both ways
5. Click **Start Sync**

### Order Sync

1. Go to **BigCommerce** > **Sync Operations** > **Order Sync**
2. Create a new order sync record
3. Select your configuration
4. Optionally set date filters to sync specific date ranges
5. Click **Start Sync**

Orders will be imported from BigCommerce and created as Sale Orders in Odoo.

### Inventory Sync

1. Go to **BigCommerce** > **Sync Operations** > **Inventory Sync**
2. Create a new inventory sync record
3. Select your configuration
4. Choose sync direction
5. Click **Start Sync**

### Customer Sync

1. Go to **BigCommerce** > **Sync Operations** > **Customer Sync**
2. Create a new customer sync record
3. Select your configuration
4. Choose sync direction
5. Click **Start Sync**

## Webhooks

The module includes comprehensive webhook support for real-time updates from BigCommerce to Odoo. Webhooks eliminate the need for frequent polling and provide instant synchronization when changes occur in BigCommerce.

### Prerequisites

According to [BigCommerce webhook documentation](https://developer.bigcommerce.com/docs/integrations/webhooks):

1. **HTTPS Required**: Webhook destination must use HTTPS (not HTTP)
2. **Port 443 Only**: Must be served on standard HTTPS port 443 (custom ports not supported)
3. **Publicly Accessible**: URL must be reachable from the internet (not localhost)
4. **API Scopes**: Your BigCommerce API token must have the `Information & Settings` scope to manage webhooks

### Setup Instructions

#### 1. Configure Your Odoo Base URL

Ensure your Odoo instance has a publicly accessible HTTPS URL:

**In Odoo:**
- Go to **Settings** > **General Settings** > **Discuss**
- Set the **Base URL** to your public HTTPS URL (e.g., `https://your-domain.com`)

**Or via command line:**
```bash
psql -d your_database -c "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date) VALUES ('web.base.url', 'https://your-domain.com', 1, NOW(), 1, NOW()) ON CONFLICT (key) DO UPDATE SET value = 'https://your-domain.com';"
```

#### 2. Enable Webhooks in BigCommerce Connector

1. Navigate to **BigCommerce** > **Configuration**
2. Open your configuration
3. Go to the **Webhooks** tab
4. Check **Enable Webhooks**
5. (Optional) Set a **Custom Base URL** if using a reverse proxy
6. The **Webhook URL** will be automatically generated: `https://your-domain.com/bigcommerce/webhook/{config_id}`

#### 3. Select Webhook Events

Choose which events you want to receive:

**Product Webhooks:**
- Product Created
- Product Updated
- Product Deleted
- Product Inventory Updated

**Order Webhooks:**
- Order Created
- Order Updated
- Order Archived
- Order Status Updated
- Order Message Created
- Order Refund Created

**Customer Webhooks:**
- Customer Created
- Customer Updated
- Customer Deleted
- Customer Address Created/Updated/Deleted

**Shipment Webhooks:**
- Shipment Created/Updated/Deleted

**Category Webhooks:**
- Category Created/Updated/Deleted

#### 4. Register Webhooks

1. Click **Register Webhooks** button
2. System will create webhooks in BigCommerce for all selected events
3. View results showing successful and failed registrations

#### 5. Monitor Webhook Activity

The configuration will track:
- **Webhooks Registered**: Whether webhooks are active
- **Last Webhook Received**: Timestamp of most recent webhook
- **Total Received/Processed/Failed**: Statistics for monitoring

### Development Setup

For local development, use a tunneling service to expose your local Odoo instance:

**Option 1: ngrok (Recommended)**
```bash
ngrok http 8069
```
Then use the ngrok HTTPS URL as your custom base URL.

**Option 2: webhook.site**
Use https://webhook.site for testing webhook payloads without implementing a full handler.

### Troubleshooting

**Webhooks not registering:**
- Verify your API token has `Information & Settings` scope
- Ensure webhook URL is HTTPS (not HTTP)
- Confirm URL is publicly accessible (test with curl or browser)
- Check BigCommerce API logs for detailed error messages

**Webhooks not being received:**
- Verify Odoo server is publicly accessible
- Check firewall rules allow incoming HTTPS traffic
- Review Odoo logs for incoming webhook requests
- Use BigCommerce webhook testing tools to verify delivery

**Webhooks timing out:**
- Ensure webhook handler responds with HTTP 200 immediately
- Don't perform long-running operations in webhook handler
- Use queued jobs for processing if needed

### Management Actions

- **Register Webhooks**: Create webhooks in BigCommerce for selected events
- **Unregister All Webhooks**: Remove all webhooks from BigCommerce
- **View Registered Webhooks**: See list of currently active webhooks

### Supported Events

All webhook events from [BigCommerce Webhook Events documentation](https://developer.bigcommerce.com/docs/integrations/webhooks/events):
- Products: created, updated, deleted, inventory updated
- Orders: created, updated, archived, status updated, messages, refunds
- Customers: created, updated, deleted, addresses
- Shipments: created, updated, deleted
- Categories: created, updated, deleted

## Technical Details

### Models

- `bigcommerce.config`: Configuration for BigCommerce API connection
- `bigcommerce.product.sync`: Product synchronization records
- `bigcommerce.order.sync`: Order synchronization records
- `bigcommerce.inventory.sync`: Inventory synchronization records
- `bigcommerce.customer.sync`: Customer synchronization records

### Extended Models

- `product.template`: Added BigCommerce ID and sync fields
- `sale.order`: Added BigCommerce ID and sync fields
- `res.partner`: Added BigCommerce ID and sync fields for customers

### API Client

The module uses the BigCommerce API v2. The API client is located in `utils/bigcommerce_api.py` and handles all API communication.

## Requirements

- Odoo 19.0
- Python `requests` library (usually included with Odoo)
- BigCommerce store with API access

## Support

For issues or questions, please contact your Odoo administrator or refer to the BigCommerce API documentation.

## License

LGPL-3
