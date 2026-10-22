"""Portable supplier and listing drafts; no external outcome is inferred."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLATFORMS = {
    'alibaba': {'name': 'Alibaba', 'url': 'https://www.alibaba.com/', 'role': 'supplier'},
    'madeinchina': {'name': 'Made-in-China', 'url': 'https://www.made-in-china.com/', 'role': 'supplier'},
    'dhgate': {'name': 'DHgate', 'url': 'https://www.dhgate.com/', 'role': 'supplier'},
    'ebay': {'name': 'eBay', 'url': 'https://www.ebay.com/sh/ovw', 'role': 'seller',
             'policy': 'https://export.ebay.com/en/services-tools/shipping/shipping-tips-and-best-practice/drop-shipping/'},
    'etsy': {'name': 'Etsy', 'url': 'https://www.etsy.com/your/shops/me/dashboard', 'role': 'seller',
             'policy': 'https://help.etsy.com/hc/en-us/articles/23948763872151-Does-Etsy-Allow-Drop-Shipping-or-Reselling'},
    'depop': {'name': 'Depop', 'url': 'https://www.depop.com/', 'role': 'seller',
              'policy': 'https://depophelp.zendesk.com/hc/en-gb/articles/22499808769553-Dropshipping-Policy'},
    'facebook': {'name': 'Facebook Marketplace', 'url': 'https://www.facebook.com/marketplace/you/selling', 'role': 'seller'},
}


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, str_max_length=12000, allow_inf_nan=False, validate_default=True)

    @field_validator('url', 'source_url', 'published_url', check_fields=False)
    @classmethod
    def safe_url(cls, value):
        if value:
            parts = urlsplit(value)
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
                raise ValueError('Use an HTTPS page without embedded credentials.')
        return value


class Supplier(Model):
    name: str = Field(min_length=1, max_length=200)
    platform: Literal['alibaba', 'madeinchina', 'dhgate', 'other'] = 'alibaba'
    url: str = Field(default='', max_length=3000)
    product: str = Field(default='', max_length=300)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    unit_cost: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    shipping_cost: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    minimum_order: int = Field(default=1, ge=1, le=10000000)
    lead_time: str = Field(default='', max_length=500)
    terms: str = ''
    evidence: str = ''
    status: Literal['researching', 'contacted', 'quoted', 'agreed'] = 'researching'


class Listing(Model):
    title: str = Field(min_length=1, max_length=200)
    platform: Literal['ebay', 'etsy', 'depop', 'facebook', 'other'] = 'ebay'
    description: str = ''
    supplier_id: str = Field(default='', max_length=100)
    source_url: str = Field(default='', max_length=3000)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    price: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    unit_cost: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    shipping_cost: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    other_cost: Decimal = Field(default=0, ge=0, le=10000000, decimal_places=2)
    fee_percent: Decimal = Field(default=0, ge=0, le=100, decimal_places=2)
    quantity: int = Field(default=1, ge=0, le=10000000)
    fulfillment: Literal['owned', 'wholesale', 'retail_after_sale', 'original_design', 'unknown'] = 'unknown'
    product_kind: Literal['general', 'craft_supply', 'party_supply', 'vintage', 'handmade', 'original_design'] = 'general'
    shipping: str = Field(default='', max_length=2000)
    returns: str = Field(default='', max_length=2000)
    photos: list[str] = Field(default_factory=list, max_length=24)
    eligibility_notes: str = ''
    status: Literal['draft', 'ready', 'published', 'sold', 'ended'] = 'draft'
    published_url: str = Field(default='', max_length=3000)
    confirmation: str = ''


MODELS = {'supplier': Supplier, 'listing': Listing}


def check_listing(item: Listing) -> dict:
    issues = []
    if item.fulfillment == 'unknown':
        issues.append('Choose how this item will be fulfilled.')
    if item.platform == 'ebay' and item.fulfillment == 'retail_after_sale':
        issues.append('eBay does not allow buying from another retailer or marketplace after a sale for direct shipment to the buyer.')
    if item.platform == 'ebay' and item.fulfillment == 'wholesale' and not item.eligibility_notes:
        issues.append('Record the wholesale agreement for these items before listing.')
    if item.platform == 'etsy':
        eligible = item.product_kind in {'craft_supply', 'party_supply'} or (
            item.product_kind in {'handmade', 'original_design'} and item.fulfillment in {'owned', 'original_design'}
        ) or (item.product_kind == 'vintage' and item.fulfillment == 'owned')
        if not eligible:
            issues.append('Etsy limits reselling to eligible categories. Ordinary catalog products are not eligible; original designs need appropriate production-partner disclosure.')
        elif not item.eligibility_notes:
            issues.append('Record why this item meets Etsy’s creativity standards and any required production-partner disclosure.')
    if item.platform == 'depop' and item.fulfillment not in {'owned', 'original_design', 'unknown'}:
        issues.append('Depop does not allow dropshipped catalog items. Use owned items or your own eligible designs.')
    if item.platform == 'depop' and item.fulfillment == 'original_design' and not item.eligibility_notes:
        issues.append('Record sample quality checks, original photography, partner location and shipping disclosures.')
    for key, label in [('description', 'description'), ('shipping', 'shipping details'), ('returns', 'return terms'), ('photos', 'photos you have permission to use')]:
        if not getattr(item, key):
            issues.append(f'Add {label}.')
    if item.price <= 0 or item.quantity <= 0:
        issues.append('Set a positive price and available quantity.')
    margin = item.price * (1 - item.fee_percent / 100) - item.unit_cost - item.shipping_cost - item.other_cost
    return {'ready': not issues, 'issues': issues, 'estimated_profit': str(margin.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'currency': item.currency, 'estimate_note': 'Estimate using your costs and fee rate; include taxes, duties and returns in other costs.',
            'policy_url': PLATFORMS.get(item.platform, {}).get('policy'), 'policy_checked': '2026-10-22'}


def prepare(kind, fields):
    if kind == 'supplier':
        item = Supplier.model_validate(fields)
        message = (f'Hello, I’m interested in {item.product or "your products"}. Please quote {item.minimum_order} units '
                   f'in {item.currency}, including shipping, minimum order quantity and delivery time. '
                   'Can you support individual customer shipments with neutral packaging, stock updates and tracking? '
                   'Please also confirm sample cost, return/defect terms, product certifications and whether we can use your photos. '
                   'Please provide written wholesale fulfillment terms. This is a quote request, not an order.')
        return {'draft_message': message, 'url': item.url or PLATFORMS.get(item.platform, {}).get('url'),
                'steps': ['Open the supplier page and verify the seller and product.', 'Compare landed cost, samples, lead times, stock and return terms.',
                          'Review the inquiry before sending. Record the actual quote and evidence.', 'Get approval for orders, payments or binding acceptance.']}
    item = Listing.model_validate(fields)
    return {**check_listing(item), 'title': item.title,
            'listing_text': f'{item.title}\n\n{item.description}\n\nPrice: {item.currency} {item.price}\nShipping: {item.shipping}\nReturns: {item.returns}',
            'url': item.published_url or PLATFORMS.get(item.platform, {}).get('url'),
            'steps': ['Check current stock, shipping and marketplace eligibility.', 'Open the signed-in selling account; use the original listing for edits or relisting.',
                      'Check for an existing live listing to prevent duplicates. Fill accurate details and owned/licensed photos.',
                      'Review the final listing and any fees, then publish with approval. Record its URL and observed confirmation.',
                      'If interrupted, inspect the site before retrying. A saved draft is not a published listing.']}
