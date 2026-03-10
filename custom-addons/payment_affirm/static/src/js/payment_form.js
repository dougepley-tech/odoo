/** @odoo-module **/

/**
 * Affirm Payment Integration for Odoo 19
 * 
 * This module intercepts the Affirm redirect form submission and opens
 * the Affirm checkout modal instead.
 */

console.log('[Affirm] Payment module initializing...');

(function() {
    'use strict';
    
    // Store original submit method
    const originalSubmit = HTMLFormElement.prototype.submit;
    
    // Override form.submit() to intercept Affirm forms
    HTMLFormElement.prototype.submit = function() {
        const affirmData = this.querySelector('#affirm_checkout_data');
        
        if (affirmData) {
            console.log('[Affirm] Intercepted programmatic form.submit()');
            openAffirmCheckout(affirmData.dataset);
            return; // Don't submit the form
        }
        
        // Not an Affirm form, use original submit
        return originalSubmit.call(this);
    };
    
    console.log('[Affirm] Form submit override installed');
    
    /**
     * Split a full name into first and last name
     */
    function splitName(fullName) {
        if (!fullName) return { first: '', last: '' };
        const parts = fullName.trim().split(/\s+/);
        if (parts.length === 1) {
            return { first: parts[0], last: parts[0] };
        }
        return {
            first: parts[0],
            last: parts.slice(1).join(' ')
        };
    }
    
    /**
     * Convert 2-letter country code to 3-letter
     */
    function getCountryCode(code) {
        const map = {
            'US': 'USA', 'CA': 'CAN', 'MX': 'MEX', 'GB': 'GBR', 'UK': 'GBR',
            'AU': 'AUS', 'DE': 'DEU', 'FR': 'FRA', 'IT': 'ITA', 'ES': 'ESP'
        };
        if (!code) return 'USA';
        if (code.length === 3) return code;
        return map[code.toUpperCase()] || 'USA';
    }
    
    /**
     * Open the Affirm checkout modal
     */
    async function openAffirmCheckout(data) {
        console.log('[Affirm] Opening checkout with data:', data);
        
        try {
            // Load Affirm SDK
            await loadAffirmSDK(data.jsUrl, data.publicKey);
            
            // Parse line items
            let lineItems = [];
            try {
                const parsed = JSON.parse(data.lineItems || '[]');
                lineItems = parsed.map(item => ({
                    display_name: item.display_name || 'Item',
                    sku: item.sku || 'SKU',
                    unit_price: parseInt(item.unit_price) || 0,
                    qty: parseInt(item.qty) || 1
                }));
            } catch (e) {
                console.warn('[Affirm] Could not parse line items:', e);
            }
            
            if (!lineItems || lineItems.length === 0) {
                lineItems = [{
                    display_name: 'Order ' + data.reference,
                    sku: data.reference,
                    unit_price: parseInt(data.amount),
                    qty: 1
                }];
            }
            
            // Split customer name
            const name = splitName(data.partnerName);
            const countryCode = getCountryCode(data.partnerCountry);
            
            // Get base URL - use provided URLs or fallback to current origin
            const baseUrl = window.location.origin;
            let confirmationUrl = data.confirmationUrl || (baseUrl + '/payment/affirm/return');
            let cancelUrl = data.cancelUrl || (baseUrl + '/payment/affirm/cancel');
            
            // Ensure URLs are absolute
            if (confirmationUrl && !confirmationUrl.startsWith('http')) {
                confirmationUrl = baseUrl + confirmationUrl;
            }
            if (cancelUrl && !cancelUrl.startsWith('http')) {
                cancelUrl = baseUrl + cancelUrl;
            }
            const refParam = 'reference=' + encodeURIComponent(data.reference);
            const appendRef = function(url) {
                return url + (url.indexOf('?') >= 0 ? '&' : '?') + refParam;
            };
            
            console.log('[Affirm] Using URLs - confirm:', confirmationUrl, 'cancel:', cancelUrl);
            
            // Affirm requires non-empty address/contact; use fallbacks to avoid generic "problem with checkout"
            const line1 = (data.partnerStreet || '').trim() || 'N/A';
            const city = (data.partnerCity || '').trim() || 'N/A';
            const state = (data.partnerState || '').trim() || 'NA';
            const zipcode = (data.partnerZip || '').trim() || '00000';
            const email = (data.partnerEmail || '').trim() || 'guest@checkout.affirm.local';
            let phone = (data.partnerPhone || '').replace(/\D/g, '');
            if (phone.length < 10) {
                phone = '0000000000';
            }

            // Build Affirm checkout configuration per their API spec (all required fields non-empty)
            const checkoutData = {
                merchant: {
                    user_confirmation_url: appendRef(confirmationUrl),
                    user_cancel_url: appendRef(cancelUrl),
                    user_confirmation_url_action: 'GET',
                    name: (data.merchantName || 'Store').trim() || 'Store'
                },
                shipping: {
                    name: {
                        first: (name.first || 'Customer').trim() || 'Customer',
                        last: (name.last || 'Name').trim() || 'Name'
                    },
                    address: {
                        line1: line1,
                        line2: (data.partnerStreet2 || '').trim(),
                        city: city,
                        state: state,
                        zipcode: zipcode,
                        country: countryCode
                    },
                    phone_number: phone,
                    email: email
                },
                billing: {
                    name: {
                        first: (name.first || 'Customer').trim() || 'Customer',
                        last: (name.last || 'Name').trim() || 'Name'
                    },
                    address: {
                        line1: line1,
                        line2: (data.partnerStreet2 || '').trim(),
                        city: city,
                        state: state,
                        zipcode: zipcode,
                        country: countryCode
                    },
                    phone_number: phone,
                    email: email
                },
                items: lineItems,
                order_id: (data.reference || '').toString(),
                currency: (data.currency || 'USD').toUpperCase(),
                shipping_amount: parseInt(data.shippingAmount, 10) || 0,
                tax_amount: parseInt(data.taxAmount, 10) || 0,
                total: parseInt(data.amount, 10) || 0
            };

            console.log('[Affirm] Checkout object (sanitized):', JSON.stringify({
                merchant: { name: checkoutData.merchant.name, url_len: checkoutData.merchant.user_confirmation_url.length },
                shipping: { city: checkoutData.shipping.address.city, state: checkoutData.shipping.address.state, zipcode: checkoutData.shipping.address.zipcode, country: checkoutData.shipping.address.country, has_email: !!checkoutData.shipping.email, has_phone: !!checkoutData.shipping.phone_number },
                items_count: checkoutData.items.length,
                total: checkoutData.total,
                order_id: checkoutData.order_id
            }, null, 2));

            // Initialize and open Affirm checkout; capture any error Affirm returns
            try {
                window.affirm.checkout(checkoutData);
                window.affirm.checkout.open({
                    onFail: function(error) {
                        const errMsg = error && (error.reason || error.message || JSON.stringify(error));
                        console.error('[Affirm] onFail:', errMsg, error);
                        alert('Affirm payment could not be completed. Check the browser console (F12) for details: ' + (errMsg || 'Unknown error'));
                        window.location.href = appendRef(data.cancelUrl || (baseUrl + '/payment/affirm/cancel'));
                    },
                    onSuccess: function() {
                        console.log('[Affirm] Checkout completed successfully');
                        // Affirm handles the redirect to confirmation_url with checkout_token
                    }
                });
            } catch (err) {
                console.error('[Affirm] Exception calling affirm.checkout/open:', err);
                alert('Affirm error: ' + (err.message || String(err)) + '. Check console (F12) for details.');
            }
            
        } catch (error) {
            console.error('[Affirm] Error opening checkout:', error);
            alert('Failed to initialize Affirm payment: ' + error.message);
        }
    }
    
    /**
     * Load the Affirm SDK dynamically
     */
    function loadAffirmSDK(jsUrl, publicKey) {
        console.log('[Affirm] Loading SDK from:', jsUrl);
        
        return new Promise(function(resolve, reject) {
            // Check if already loaded
            if (window.affirm && window.affirm.checkout && window.affirm.ui) {
                console.log('[Affirm] SDK already loaded');
                resolve();
                return;
            }
            
            // Configure Affirm
            window._affirm_config = {
                public_api_key: publicKey,
                script: jsUrl,
                locale: 'en_US',
                country_code: 'USA'
            };
            
            // Check for existing script
            const existingScript = document.querySelector('script[src*="affirm.js"]');
            if (existingScript) {
                waitForAffirmReady(resolve, reject);
                return;
            }
            
            // Affirm SDK bootstrap snippet
            (function(l,g,m,e,a,f,b){
                var d,c=l[m]||{},h=document.createElement(f),n=document.getElementsByTagName(f)[0],
                k=function(a,b,c){return function(){a[b]._.push([c,arguments])}};
                c[e]=k(c,e,'set');d=c[e];c[a]={};c[a]._=[];d._=[];c._=[];c[a][b]=k(c,a,b);
                a=0;for(b='set add save post open empty reset on off trigger ready setProduct'.split(' ');a<b.length;a++)d[b[a]]=k(c,e,b[a]);
                a=0;for(b=['get','token','url','items'];a<b.length;a++)d[b[a]]=function(){};
                h.async=!0;h.src=g[f];
                h.onload = function() {
                    console.log('[Affirm] SDK script loaded successfully');
                    waitForAffirmReady(resolve, reject);
                };
                h.onerror = function() {
                    console.error('[Affirm] Failed to load SDK script');
                    reject(new Error('Failed to load Affirm SDK'));
                };
                n.parentNode.insertBefore(h,n);
                delete g[f];d(g);l[m]=c;
            })(window, window._affirm_config, 'affirm', 'checkout', 'ui', 'script', 'ready');
        });
    }
    
    /**
     * Wait for Affirm SDK to be fully ready
     */
    function waitForAffirmReady(resolve, reject) {
        let attempts = 0;
        const maxAttempts = 100;
        
        const check = setInterval(function() {
            attempts++;
            
            if (window.affirm && window.affirm.ui) {
                clearInterval(check);
                console.log('[Affirm] SDK is ready');
                try {
                    if (window.affirm.ui.error && window.affirm.ui.error.on) {
                        window.affirm.ui.error.on('close', function() {
                            console.error('[Affirm] Error modal was closed (generic "problem with checkout" may have been shown by Affirm)');
                        });
                    }
                } catch (e) {
                    console.warn('[Affirm] Could not attach error modal listener:', e);
                }
                window.affirm.ui.ready(function() {
                    resolve();
                });
            } else if (attempts >= maxAttempts) {
                clearInterval(check);
                reject(new Error('Affirm SDK initialization timeout'));
            }
        }, 100);
    }
    
    console.log('[Affirm] Payment module loaded');
})();
