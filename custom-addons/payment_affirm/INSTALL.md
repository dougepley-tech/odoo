# Quick Installation Guide - Affirm Payment Provider

## Prerequisites

- Odoo 19.0 installed
- Active Affirm merchant account
- Access to Odoo addons directory

## Installation Steps

### 1. Install the Module

```bash
# Copy module to Odoo addons directory
cp -r payment_affirm /path/to/odoo/addons/

# Or create a symlink
ln -s /path/to/payment_affirm /path/to/odoo/addons/payment_affirm

# Restart Odoo service
sudo systemctl restart odoo
```

### 2. Update Apps List

1. Log in to Odoo as administrator
2. Navigate to **Apps**
3. Click **Update Apps List**
4. Search for "Affirm"
5. Click **Install**

### 3. Get Affirm Credentials

#### For Testing (Sandbox):
1. Visit: https://sandbox.affirm.com/dashboard
2. Login with your merchant account
3. Go to Settings → API Keys
4. Copy Public Key and Private Key

#### For Production:
1. Visit: https://www.affirm.com/dashboard
2. Login with your merchant account
3. Go to Settings → API Keys
4. Copy Public Key and Private Key

### 4. Configure Payment Provider

1. Go to **Accounting → Configuration → Payment Providers**
2. Find **Affirm** in the list
3. Click to open the configuration
4. Fill in the following:
   - **State**: "Test Mode" (for sandbox) or "Enabled" (for production)
   - **Affirm Public API Key**: Your public key from Affirm
   - **Affirm Private API Key**: Your private key from Affirm
   - **Merchant Display Name**: Your store name (e.g., "IAG Performance")
5. Click **Save**
6. Click **Published** to activate

### 5. Test the Integration

1. Create a test order on your website
2. Add products to cart
3. Go to checkout
4. Select **Affirm** as payment method
5. Click **Pay with Affirm**
6. Complete the Affirm checkout flow
7. Verify order confirmation

#### Sandbox Testing Tips:
- Use fake customer information
- Any phone number works
- Use PIN: **1234** for verification
- No real charges are made

### 6. Go Live

1. Switch to production API keys
2. Change State to **Enabled**
3. Test with a small real transaction
4. Monitor transactions in Affirm dashboard

## Verification Checklist

- [ ] Module installed successfully
- [ ] Affirm appears in Payment Providers list
- [ ] API keys configured correctly
- [ ] Test transaction completes successfully
- [ ] Order is marked as paid in Odoo
- [ ] Transaction appears in Affirm dashboard
- [ ] Payment provider published on website
- [ ] Production keys configured (when going live)

## Common Installation Issues

### Module Not Appearing

**Solution:**
```bash
# Check if module is in addons path
ls -la /path/to/odoo/addons/payment_affirm

# Check Odoo logs
tail -f /var/log/odoo/odoo-server.log

# Verify module manifest
python3 -c "import ast; print(ast.literal_eval(open('/path/to/payment_affirm/__manifest__.py').read()))"
```

### Dependencies Missing

**Error:** `Module payment_affirm depends on module payment which is not installed`

**Solution:**
- Install the `payment` module first (usually installed by default)

### JavaScript Not Loading

**Solution:**
- Clear browser cache
- Rebuild assets: `./odoo-bin -c /etc/odoo/odoo.conf --dev=all`
- Check browser console for errors

### API Errors

**Solution:**
- Verify API keys are correct
- Ensure keys match environment (sandbox vs production)
- Check Affirm dashboard for account status

## Directory Structure Verification

After installation, verify the structure:

```
/path/to/odoo/addons/payment_affirm/
├── __init__.py
├── __manifest__.py
├── README.md
├── INSTALL.md
├── controllers/
│   ├── __init__.py
│   └── main.py
├── data/
│   └── payment_provider_data.xml
├── models/
│   ├── __init__.py
│   ├── payment_provider.py
│   └── payment_transaction.py
├── security/
│   └── ir.model.access.csv
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

## Support

If you encounter issues during installation:

1. Check Odoo server logs: `/var/log/odoo/odoo-server.log`
2. Review browser console for JavaScript errors
3. Verify all files are properly copied
4. Ensure correct file permissions
5. Restart Odoo after any file changes

## Next Steps

After successful installation:

1. Review the main README.md for detailed usage instructions
2. Test refund functionality
3. Configure payment journals if needed
4. Set up automated reconciliation
5. Train staff on processing Affirm orders

## Uninstallation

To remove the module:

1. Uninstall from Apps menu
2. Remove module directory
3. Restart Odoo

```bash
rm -rf /path/to/odoo/addons/payment_affirm
sudo systemctl restart odoo
```
