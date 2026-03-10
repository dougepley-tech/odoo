# Testing Guide - Affirm Payment Provider

## Overview

This guide provides comprehensive testing procedures for the Affirm payment provider module.

## Prerequisites

Before testing:
- [ ] Module installed successfully
- [ ] Affirm sandbox account created
- [ ] Sandbox API keys configured
- [ ] Test Mode enabled in Odoo
- [ ] Website published and accessible
- [ ] Test products available in catalog

## Testing Environment Setup

### 1. Configure Sandbox Mode

```
Accounting → Configuration → Payment Providers → Affirm

Settings:
- State: Test Mode
- Affirm Public API Key: [Your sandbox public key]
- Affirm Private API Key: [Your sandbox private key]
- Merchant Display Name: Test Store
- Published: Yes
```

### 2. Sandbox Test Credentials

Use these for testing (Affirm Sandbox):
- **Phone**: Any US format (e.g., 555-123-4567)
- **Email**: Any email format
- **SSN (last 4)**: 0000 or 5678
- **PIN**: 1234
- **Address**: Any valid US address

## Test Scenarios

### Test 1: Successful Payment Flow

**Objective**: Verify complete payment authorization and capture

**Steps**:
1. Add product to cart (amount: $100)
2. Proceed to checkout
3. Fill in customer information
4. Select "Affirm" as payment method
5. Click "Pay with Affirm"
6. Complete Affirm checkout:
   - Enter phone number
   - Enter PIN: 1234
   - Select payment plan
   - Confirm loan terms
7. Return to store

**Expected Results**:
- ✓ Affirm modal opens successfully
- ✓ Customer can complete authentication
- ✓ Payment is authorized
- ✓ Transaction captured automatically
- ✓ Order marked as paid in Odoo
- ✓ Payment transaction shows "Done" status
- ✓ Affirm charge ID recorded

**Verification**:
```sql
SELECT reference, state, provider_code, affirm_charge_id, amount
FROM payment_transaction
WHERE provider_code = 'affirm'
ORDER BY create_date DESC
LIMIT 5;
```

### Test 2: Payment Cancellation

**Objective**: Verify proper handling of cancelled payments

**Steps**:
1. Add product to cart
2. Proceed to checkout
3. Select "Affirm" as payment method
4. Click "Pay with Affirm"
5. In Affirm modal, click close/cancel
6. Choose to cancel the checkout

**Expected Results**:
- ✓ Redirected back to payment page
- ✓ Transaction marked as cancelled
- ✓ Order remains unpaid
- ✓ Can try payment again

### Test 3: Amount Validation

**Objective**: Ensure authorized amount matches order total

**Test Cases**:

**A. Standard Amount ($100)**
- Expected: Success

**B. Minimum Amount ($1)**  
- Expected: Success (if above Affirm minimum)

**C. Maximum Amount ($17,500)**
- Expected: Success (if below Affirm maximum)

**D. Amount Exceeding Limits**
- Expected: Appropriate error message

### Test 4: Customer Information Handling

**Objective**: Verify customer data is properly sent to Affirm

**Test with various customer profiles**:

**A. Complete Information**
```
Name: John Doe
Email: [email protected]
Phone: 555-123-4567
Address: 123 Main St, Apt 4
City: San Francisco
State: CA
Zip: 94107
Country: USA
```
Expected: ✓ Success

**B. Missing Secondary Address**
```
[Same as above but no Apt/Suite]
```
Expected: ✓ Success

**C. Missing Phone**
```
[No phone number provided]
```
Expected: ⚠ Warning or use default

### Test 5: Multiple Items in Cart

**Objective**: Verify line items are properly formatted

**Steps**:
1. Add multiple products:
   - Product A: $50 x 2 = $100
   - Product B: $75 x 1 = $75
   - Total: $175
2. Complete checkout with Affirm

**Expected Results**:
- ✓ All line items sent to Affirm
- ✓ SKUs included correctly
- ✓ Quantities accurate
- ✓ Total matches order amount

**Verification in Affirm Dashboard**:
- Check transaction details show all items

### Test 6: Refund Processing

**Objective**: Verify full refund functionality

**Steps**:
1. Complete a successful test payment ($100)
2. Wait for payment to be captured
3. In Odoo:
   - Navigate to Sales → Orders
   - Open the test order
   - Go to Payments tab
   - Click on transaction
   - Click "Refund" button
4. Confirm refund

**Expected Results**:
- ✓ Refund processed successfully
- ✓ Refund transaction created
- ✓ Original transaction updated
- ✓ Refund visible in Affirm dashboard

**Verification**:
- Check `payment_transaction` table for refund record
- Verify amount matches

### Test 7: Error Handling

**Objective**: Verify proper error handling

**Test Cases**:

**A. Invalid API Keys**
```
Configuration: Wrong API keys
Expected: Clear error message, payment fails gracefully
```

**B. Network Timeout**
```
Condition: Simulate slow network
Expected: Appropriate timeout message
```

**C. Declined Transaction**
```
Condition: Use amount that triggers decline in sandbox
Expected: User-friendly error message
```

### Test 8: Concurrent Transactions

**Objective**: Verify handling of simultaneous orders

**Steps**:
1. Open two browser sessions
2. Start checkout in both
3. Complete payments nearly simultaneously

**Expected Results**:
- ✓ Both transactions process independently
- ✓ No data corruption
- ✓ Each gets unique charge ID
- ✓ Both orders confirmed correctly

### Test 9: Session Timeout

**Objective**: Verify behavior with expired sessions

**Steps**:
1. Start checkout process
2. Wait 30+ minutes
3. Complete Affirm checkout
4. Return to store

**Expected Results**:
- ✓ Proper session handling
- ✓ Transaction still processes OR
- ✓ User redirected to re-authenticate

### Test 10: Mobile Responsiveness

**Objective**: Verify mobile device compatibility

**Test on**:
- iPhone Safari
- Android Chrome
- iPad
- Android Tablet

**Expected Results**:
- ✓ Payment button displays correctly
- ✓ Affirm modal renders properly
- ✓ Touch interactions work
- ✓ Checkout completes successfully

## Browser Compatibility Testing

Test on major browsers:
- [ ] Chrome (latest)
- [ ] Firefox (latest)
- [ ] Safari (latest)
- [ ] Edge (latest)
- [ ] Mobile Safari
- [ ] Chrome Mobile

## Performance Testing

### Load Testing

**Objective**: Verify performance under load

**Steps**:
1. Process 10 concurrent transactions
2. Monitor response times
3. Check for errors
4. Verify database performance

**Metrics to Track**:
- Average transaction time
- API response time
- Database query time
- Error rate

### Stress Testing

**Objective**: Identify breaking points

**Steps**:
1. Gradually increase concurrent users
2. Monitor system resources
3. Note when degradation occurs
4. Document limits

## Integration Testing

### Test with eCommerce Flow

**Complete Order Flow**:
1. Browse catalog
2. Add multiple items
3. Apply coupon/discount
4. Proceed to checkout
5. Select shipping method
6. Enter customer information
7. Select Affirm payment
8. Complete payment
9. Receive confirmation

**Verify**:
- ✓ Inventory updated
- ✓ Invoice generated
- ✓ Shipping created
- ✓ Confirmation email sent
- ✓ Order in correct state

### Test with Sale Orders

**Direct Sale Order Creation**:
1. Create sale order manually
2. Confirm order
3. Register payment with Affirm
4. Verify flow

## Security Testing

### Test Cases

**1. SQL Injection**
- Attempt SQL injection in form fields
- Expected: Inputs sanitized, no injection

**2. XSS Attacks**
- Try injecting JavaScript in fields
- Expected: Proper escaping, no execution

**3. API Key Exposure**
- Check page source
- Check JavaScript console
- Expected: Private key never exposed

**4. CSRF Protection**
- Attempt cross-site requests
- Expected: Requests blocked

## Logging and Monitoring

### Check Logs For

**Success Path**:
```
INFO: Sending authorization request to Affirm for transaction TX-123
INFO: Affirm authorization response for transaction TX-123
INFO: Capturing Affirm charge CH-456 for transaction TX-123
```

**Error Path**:
```
ERROR: Error authorizing Affirm transaction TX-123
```

### Log Locations

```bash
# Odoo server logs
tail -f /var/log/odoo/odoo-server.log | grep -i affirm

# Filter for Affirm transactions
grep "affirm" /var/log/odoo/odoo-server.log
```

## Database Verification

### Check Transaction Records

```sql
-- View recent Affirm transactions
SELECT 
    reference,
    state,
    amount,
    currency_id,
    affirm_checkout_token,
    affirm_charge_id,
    create_date
FROM payment_transaction
WHERE provider_code = 'affirm'
ORDER BY create_date DESC
LIMIT 10;

-- Check for failed transactions
SELECT 
    reference,
    state,
    state_message
FROM payment_transaction
WHERE provider_code = 'affirm'
    AND state IN ('error', 'cancel')
ORDER BY create_date DESC;
```

## Affirm Dashboard Verification

**For each test transaction**:
1. Log into Affirm sandbox dashboard
2. Navigate to Transactions
3. Find corresponding transaction
4. Verify:
   - Amount matches
   - Status is correct
   - Customer information accurate
   - Line items present

## Pre-Production Checklist

Before enabling in production:

- [ ] All test scenarios passed
- [ ] No errors in logs
- [ ] Database records correct
- [ ] Refunds working properly
- [ ] Mobile testing complete
- [ ] Browser compatibility verified
- [ ] Performance acceptable
- [ ] Security review complete
- [ ] Documentation reviewed
- [ ] Team training completed

## Production Testing

### Initial Production Test

**Before going fully live**:
1. Switch to production API keys
2. Process ONE small test transaction ($1-10)
3. Verify in production Affirm dashboard
4. Process refund of test transaction
5. If all successful, enable for customers

### Monitoring in Production

**First Week**:
- Monitor all transactions closely
- Check logs daily
- Review Affirm dashboard
- Track success/failure rates
- Collect customer feedback

**Ongoing**:
- Weekly transaction review
- Monthly reconciliation with Affirm
- Quarterly performance analysis

## Troubleshooting Test Failures

### Common Issues and Solutions

**Issue: Modal doesn't open**
```
Solutions:
- Check browser console for errors
- Verify Affirm.js loading
- Check API keys
- Clear browser cache
```

**Issue: Authorization fails**
```
Solutions:
- Verify API credentials
- Check transaction amount
- Review customer information
- Check Affirm sandbox status
```

**Issue: Capture fails**
```
Solutions:
- Verify charge ID exists
- Check API permissions
- Review Affirm dashboard
```

## Test Reporting

### Report Template

```markdown
## Affirm Payment Provider Test Report

**Test Date**: [Date]
**Tester**: [Name]
**Environment**: Sandbox/Production
**Odoo Version**: 19.0

### Tests Completed
- [ ] Successful Payment: PASS/FAIL
- [ ] Payment Cancellation: PASS/FAIL
- [ ] Refund Processing: PASS/FAIL
- [ ] Error Handling: PASS/FAIL
- [ ] Mobile Testing: PASS/FAIL

### Issues Found
1. [Issue description]
   - Severity: High/Medium/Low
   - Status: Open/Resolved

### Notes
[Additional observations]

### Recommendation
Ready for Production: YES/NO
```

## Automated Testing (Future)

Consider implementing:
- Unit tests for models
- Integration tests for API calls
- UI tests with Selenium
- Load tests with Locust
- API mocking for CI/CD

## Support Contacts

**For Testing Issues**:
- Odoo Logs: `/var/log/odoo/odoo-server.log`
- Affirm Dashboard: https://sandbox.affirm.com/dashboard
- Affirm Support: https://businesshub.affirm.com/

**Documentation**:
- Affirm API Docs: https://docs.affirm.com/
- Odoo Payment Docs: https://www.odoo.com/documentation/
