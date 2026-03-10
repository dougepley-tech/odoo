# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import time
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests

_logger = logging.getLogger(__name__)

SHIPPO_API_BASE = 'https://api.goshippo.com'
SHIPPO_API_TEST_BASE = 'https://api.goshippo.com'  # Shippo uses same host; test mode is per request

REQUEST_TIMEOUT = 15


class ShippoAPIError(Exception):
    def __init__(self, message, response=None):
        self.message = message
        self.response = response
        super().__init__(message)


def _get_base_url(use_test_env):
    return SHIPPO_API_BASE


def _headers(api_key):
    return {
        'Authorization': f'ShippoToken {api_key}',
        'Content-Type': 'application/json',
    }


def _mask_key(key):
    if not key or len(key) < 8:
        return '****'
    return key[:4] + '****' + key[-4:] if len(key) > 8 else '****'


def create_address(api_key, address_dict, validate=True, use_test_env=False):
    """Create/validate an address. address_dict: name, street1, city, state, zip, country, phone, etc.
    Returns address object; when validate=True, check validation_results.is_valid for shipment use."""
    url = urljoin(_get_base_url(use_test_env), 'addresses/')
    payload = {k: v for k, v in address_dict.items() if v is not False and v != ''}
    payload['validate'] = validate
    resp = requests.post(url, json=payload, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()


def _address_is_valid_for_shipment(addr):
    """Return True if Shippo address response is valid for creating a shipment (state VALID)."""
    if not addr or not addr.get('object_id'):
        return False
    # Explicitly invalid: validation_results.is_valid False or is_complete False
    vr = addr.get('validation_results') or {}
    if isinstance(vr, dict) and vr.get('is_valid') is False:
        return False
    if addr.get('is_complete') is False:
        return False
    return True


def _carrier_accounts_next_url_with_results(next_url, base_url, results=100, service_levels=True):
    """Ensure next page request asks for 100 results (API default is 5, so we'd miss your own accounts)."""
    if not next_url:
        return None
    url = next_url if next_url.startswith('http') else urljoin(base_url, next_url if next_url.startswith('/') else '/' + next_url)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs['results'] = [str(results)]
    if service_levels:
        qs['service_levels'] = ['true']
    new_query = urlencode(qs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def get_carrier_accounts(api_key, use_test_env=False, service_levels=True):
    """GET /carrier_accounts. Returns list of all carrier accounts (Shippo + your own connected).
    See https://docs.goshippo.com/docs/carriers/carrieraccounts/
    Follows pagination with 100 results per page so both account types are fetched."""
    base_url = _get_base_url(use_test_env)
    url = urljoin(base_url, 'carrier_accounts/')
    params = {'results': 100, 'service_levels': service_levels}
    all_results = []
    while url:
        resp = requests.get(url, params=params, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise ShippoAPIError(
                resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
                response=resp,
            )
        data = resp.json()
        page_results = data.get('results', [])
        all_results.extend(page_results)
        next_url = data.get('next')
        if not next_url or not page_results:
            break
        url = _carrier_accounts_next_url_with_results(next_url, base_url, results=100, service_levels=service_levels)
        params = {}  # query params are now in url
    return all_results


def create_shipment(api_key, address_from, address_to, parcels, carrier_accounts=None, extra=None, async_=False, use_test_env=False):
    """
    POST /shipments.
    address_from / address_to: either object_id (string) or dict with address fields.
    parcels: list of dicts with length, width, height, distance_unit, weight, mass_unit.
    carrier_accounts: list of carrier_account object_ids for negotiated rates; omit for published.
    extra: dict e.g. insurance, signature_confirmation, is_residential.
    Returns shipment object with rates (if async_=False).
    """
    url = urljoin(_get_base_url(use_test_env), 'shipments/')
    payload = {
        'address_from': address_from if isinstance(address_from, str) else address_from,
        'address_to': address_to if isinstance(address_to, str) else address_to,
        'parcels': parcels,
        'async': async_,
    }
    if carrier_accounts:
        payload['carrier_accounts'] = carrier_accounts
    if extra:
        payload['extra'] = extra
    resp = requests.post(url, json=payload, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()


def get_shipment_rates(api_key, shipment_object_id, currency='USD', use_test_env=False):
    """GET /shipments/{id}/rates/{currency}. Use when shipment was created with async=true."""
    url = urljoin(_get_base_url(use_test_env), f'shipments/{shipment_object_id}/rates/{currency}')
    resp = requests.get(url, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()


def create_transaction(api_key, rate_object_id, label_file_type='PDF_4x6', async_=False, use_test_env=False):
    """POST /transactions - purchase label for a rate."""
    url = urljoin(_get_base_url(use_test_env), 'transactions/')
    payload = {
        'rate': rate_object_id,
        'label_file_type': label_file_type,
        'async': async_,
    }
    resp = requests.post(url, json=payload, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()


def get_transaction(api_key, transaction_object_id, use_test_env=False, expand=None):
    """GET /transactions/{id}. expand: e.g. 'rate' to include rate object with amount."""
    url = urljoin(_get_base_url(use_test_env), f'transactions/{transaction_object_id}')
    params = {}
    if expand:
        params['expand'] = expand
    resp = requests.get(url, params=params or None, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()


def list_transactions_by_rate(api_key, rate_object_id, use_test_env=False):
    """GET /transactions?rate=<rate_object_id>. Returns list of transactions (one per parcel for multi-piece)."""
    base = _get_base_url(use_test_env)
    url = urljoin(base, 'transactions/')
    resp = requests.get(url, params={'rate': rate_object_id}, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    data = resp.json()
    return data.get('results') if isinstance(data, dict) and 'results' in data else (data if isinstance(data, list) else [])


def create_refund(api_key, transaction_object_id, async_=False, use_test_env=False):
    """POST /refunds - cancel/refund a label."""
    url = urljoin(_get_base_url(use_test_env), 'refunds/')
    payload = {'transaction': transaction_object_id, 'async': async_}
    resp = requests.post(url, json=payload, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise ShippoAPIError(
            resp.json().get('detail') or resp.text or f'HTTP {resp.status_code}',
            response=resp,
        )
    return resp.json()
