# Affirm Payment Provider for Odoo 19

## Overview

This module integrates the Affirm payment gateway with Odoo 19, enabling customers to use Affirm's "Buy Now, Pay Later" payment option during checkout.

## Features

- **Buy Now, Pay Later**: Customers can split purchases into monthly installments
- **Seamless Integration**: Direct API integration with Affirm's checkout flow
- **Multiple Environments**: Support for both sandbox (testing) and production modes
- **Automatic Processing**: Automatic authorization and capture of payments
- **Refund Support**: Full refund capability through Odoo
- **Real-time Status**: Real-time payment status tracking and updates
- **USD Support**: Currently supports USD transactions

## Installation

1. Copy the `payment_affirm` folder to your Odoo addons directory
2. Update the apps list in Odoo (Apps → Update Apps List)
3. Search for "Affirm" in the Apps menu
4. Click "Install"

## Configuration

### Step 1: Get Affirm API Credentials

1. Sign up for an Affirm merchant account at [https://www.affirm.com/business](https://www.affirm.com/business)
2. Log in to your Affirm Merchant Dashboard:
   - **Sandbox**: [https://sandbox.affirm.com/dashboard](https://sandbox.affirm.com/dashboard)
   - **Production**: [https://www.affirm.com/dashboard](https://www.affirm.com/dashboard)
3. Navigate to Settings → API Keys
4. Copy your Public API Key and Private API Key

### Step 2: Configure in Odoo

1. Go to **Accounting → Configuration → Payment Providers**
2. Find and open the **Affirm** payment provider
3. Configure the following fields:
   - **State**: Choose "Test Mode" for testing or "Enabled" for production
   - **Affirm Public API Key**: Paste your Public API Key from Affirm
   - **Affirm Private API Key**: Paste your Private API Key from Affirm
   - **Merchant Display Name**: Enter your customer-facing store name
4. Click "Save"
5. Click "Published" to make it available on your website

### Step 3: Test the Integration

1. Create a test order on your website
2. Proceed to checkout
3. Select "Affirm" as the payment method
4. Complete the Affirm checkout flow
5. Verify the order is confirmed in Odoo

**Testing Tips:**
- Use sandbox mode with sandbox API keys for testing
- In Affirm sandbox, you can use fake personal information
- Use security PIN "1234" when testing in sandbox
- Affirm sandbox doesn't send real SMS or emails

## Usage

### For Customers

1. Add items to cart and proceed to checkout
2. Select "Affirm" as the payment method
3. Click "Pay with Affirm"
4. Complete identity verification on Affirm
5. Choose a payment plan
6. Confirm the loan terms
7. Complete the order

### For Store Admins

**Viewing Payments:**
- Go to **Accounting → Payments → Transactions**
- Filter by "Affirm" provider to see all Affirm transactions

**Processing Refunds:**
1. Open the sales order
2. Go to the payment transaction
3. Click "Refund" button
4. Confirm the refund amount
5. The refund will be processed through Affirm automatically

## Technical Details

### API Endpoints Used

- **Authorization**: `POST /api/v1/charges`
- **Capture**: `POST /api/v1/charges/{charge_id}/capture`
- **Refund**: `POST /api/v1/charges/{charge_id}/refund`

### Transaction Flow

1. Customer selects Affirm at checkout
2. Affirm.js modal opens with checkout details
3. Customer authorizes the loan with Affirm
4. Affirm returns checkout token to Odoo
5. Odoo authorizes the transaction with Affirm API
6. Odoo automatically captures the payment
7. Order is confirmed and fulfilled

### Supported Currencies

Currently, this module supports **USD only**. Affirm primarily operates in the United States market.

### Odoo Version Compatibility

- Odoo 19.0 (Community and Enterprise)

## Important Notes

1. **Currency**: Only USD transactions are supported
2. **Capture**: Payments are automatically captured after authorization
3. **Refund Window**: Affirm charges must be captured before refunding
4. **Testing**: Always test thoroughly in sandbox mode before going live
5. **API Keys**: Keep your Private API Key secure and never share it

## Troubleshooting

### Payment Not Processing

- Verify API keys are correct and match the environment (sandbox/production)
- Check that the transaction amount is within Affirm's limits
- Ensure customer information is complete (address, phone, email)

### Checkout Modal Not Opening

- Check browser console for JavaScript errors
- Verify Affirm.js is loading correctly
- Ensure website is accessible via HTTPS (required for production)

### Authorization Failed

- Check Affirm Merchant Dashboard for transaction details
- Verify authorized amount matches order total
- Review Odoo logs for detailed error messages

### Refund Issues

- Ensure payment was captured before attempting refund
- Verify charge ID exists in transaction record
- Check API credentials have refund permissions

## Support

For module-specific issues:
- Review Odoo server logs for detailed error messages
- Check Affirm Merchant Dashboard for transaction status
- Contact your Odoo implementation partner

For Affirm-specific questions:
- Visit [Affirm Business Hub](https://businesshub.affirm.com/)
- Review [Affirm API Documentation](https://docs.affirm.com/)
- Contact Affirm merchant support

## Development

### Module Structure

```
payment_affirm/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── __init__.py
│   └── main.py
├── data/
│   └── payment_provider_data.xml
├── models/
│   ├── __init__.py
│   ├── payment_provider.py
│   └── payment_transaction.py
├── static/
│   ├── description/
│   │   └── icon.png
│   └── src/
│       ├── img/
│       │   └── affirm_logo.png
│       └── js/
│           └── payment_form.js
└── views/
    ├── payment_affirm_templates.xml
    └── payment_provider_views.xml
```

### Key Files

- `models/payment_provider.py`: Configuration and API URL handling
- `models/payment_transaction.py`: Transaction processing and API calls
- `controllers/main.py`: Callback handling for return/cancel URLs
- `views/payment_affirm_templates.xml`: Frontend checkout form and Affirm.js integration

## License

LGPL-3

## Author

IAG Performance
https://www.iagperformance.com

## Version History

### 1.0.0 (2025-01-28)
- Initial release
- Direct API integration with Affirm
- Automatic authorization and capture
- Full refund support
- Sandbox and production mode support
