"""Deterministic comparison calculations. Does not alter raw extractions."""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from quote_schema import SCHEMA


def amount(value):
    return float(value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))


def unit_factor(stated, target):
    text = re.sub(r'\s+', ' ', (stated or '').strip().lower())
    text = re.sub(r'^(per\s+|/\s*)', '', text)
    aliases = {'kgs': 'kg', 'kilogram': 'kg', 'kilograms': 'kg',
               'boxes': 'box', 'pieces': 'piece', 'pcs': 'piece',
               'rolls': 'roll', 'sets': 'set', 'pc': 'piece'}
    if aliases.get(text, text) == target:
        return Decimal(1), ''
    pack = re.fullmatch(r'(\d+)\s*(pcs|pieces|boxes|box)', text)
    if pack and int(pack[1]) > 0 and target in {'box', 'piece'}:
        return Decimal(pack[1]), f'Price divided by {pack[1]} to convert {stated} to one {target}; confirm pack basis.'
    return None, f'Unit mismatch: vendor {stated or "unspecified"}; RFx {target}. Needs mapping review.'


def dimensions(text):
    found = re.findall(r'\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?', text.lower())
    return {re.sub(r'\s+', '', value).replace('×', 'x') for value in found}


def normalize_quote(quote, rfx, *, fx=88.0, accepted_discounts=()):
    if not math.isfinite(float(fx)) or float(fx) <= 0:
        raise ValueError('USD/INR exchange rate must be positive and finite.')
    items = {x['line_no']: x for x in rfx['lines']}
    result = []
    for original in quote['lines']:
        item = items[original['rfx_line_no']]
        row = {**original, 'item': item['name'], 'spec': item['spec'], 'unit': item['unit'],
               'quantity': item['yearly_quantity'], 'vendor': quote.get('display_name', quote['vendor_name']),
               'currency': quote['currency'], 'flags': [], 'price_inr': None,
               'annual_cost_inr': None, 'status': original['confidence']}
        result.append(row)
        if original['reason']:
            row['flags'].append(original['reason'])
        if original['price_as_stated'] is None:
            row['status'] = 'missing'
            continue
        price = Decimal(str(original['price_as_stated']))
        if not price.is_finite() or price < 0:
            row['flags'].append('Invalid price; requires correction.')
            row['status'] = 'blocked'
            continue
        if not original.get('buyer_verified') and Path(quote.get('metadata', {}).get('source_file', '')).suffix.lower() in {'.jpg', '.jpeg', '.png'}:
            row['status'] = 'check'
            row['flags'].append('Photo values and RFx matches require visual review.')
        divisor, note = unit_factor(original['stated_unit'], item['unit'])
        if note:
            row['flags'].append(note)
        requested_dims = dimensions(item['spec'])
        offered_dims = dimensions(original['vendor_item_text'])
        if requested_dims and offered_dims and requested_dims.isdisjoint(offered_dims):
            row['flags'].append(f'Dimension mismatch: requested {", ".join(sorted(requested_dims))}, offered {", ".join(sorted(offered_dims))}. Needs mapping review.')
            row['status'] = 'blocked'
            continue
        if divisor is None:
            row['status'] = 'blocked'
            continue
        price /= divisor
        if divisor != 1 and not original.get('buyer_verified'):
            row['status'] = 'check'
        if quote['currency'] == 'USD':
            price *= Decimal(str(fx))
            row['flags'].append(f'USD × {fx:g} INR/USD; buyer-entered scenario rate, not a live rate.')
        elif quote['currency'] != 'INR':
            row['flags'].append('Unknown currency; excluded from INR comparison.')
            row['status'] = 'blocked'
            continue
        applicable = [(i, d) for i, d in enumerate(quote['discounts'])
                      if i in accepted_discounts and d['scope_clear']
                      and item['line_no'] in d['applies_to_line_nos']]
        if len(applicable) > 1:
            row['flags'].append('Multiple discounts selected; stacking rule is unknown.')
            row['status'] = 'blocked'
            continue
        for i, discount in enumerate(quote['discounts']):
            if i not in accepted_discounts or not discount['scope_clear']:
                row['flags'].append(f'{discount["percent"]:g}% discount not applied; condition/scope requires confirmation.')
        if applicable:
            discount = applicable[0][1]
            price *= 1 - Decimal(str(discount['percent'])) / 100
            row['flags'].append(f'{discount["percent"]:g}% scenario discount applied: {discount["condition"] or "unconditional"}. Source: {discount["source_ref"]}')
            row['status'] = 'check'
        row['price_inr'] = amount(price)
        row['annual_cost_inr'] = amount(price * Decimal(str(item['yearly_quantity'])))
    return result


def load_quotes(folder, vendor_folder):
    """Load only valid outputs; verify source hashes so stale extractions are visible."""
    quotes, errors = [], []
    for path in sorted(Path(folder).glob('V*.json')):
        try:
            quote = json.loads(path.read_text(encoding='utf-8'))
            core = {k: v for k, v in quote.items() if k != 'metadata'}
            Draft202012Validator(SCHEMA).validate(core)
            if sorted(x['rfx_line_no'] for x in quote['lines']) != list(range(1, 31)):
                raise ValueError('RFx line IDs must be unique and cover 1–30.')
            source = Path(vendor_folder) / Path(quote['metadata']['source_file']).name
            if not source.is_file():
                raise ValueError('Original vendor file unavailable; source cannot be verified.')
            if hashlib.sha256(source.read_bytes()).hexdigest() != quote['metadata']['source_sha256']:
                raise ValueError('Source changed since extraction. Re-run the extractor for this file.')
            quote['quote_id'] = path.stem
            quote['local_source'] = str(source)
            quotes.append(quote)
        except Exception as error:
            errors.append(f'{path.name}: {error}')
    return quotes, errors


def summarize(groups):
    common = set.intersection(*[{r['rfx_line_no'] for r in rows if r['price_inr'] is not None}
                               for rows in groups]) if groups else set()
    summary = []
    for rows in groups:
        available = [r for r in rows if r['price_inr'] is not None]
        summary.append({'Vendor': rows[0]['vendor'], 'Comparable lines': len(available),
                        'Review or blocked': sum(r['status'] in {'check', 'blocked'} for r in rows),
                        'Full basket INR': round(sum(r['annual_cost_inr'] for r in available), 2)
                        if len(available) == len(rows) else None,
                        'Common basket INR': round(sum(r['annual_cost_inr'] for r in available
                                                       if r['rfx_line_no'] in common), 2),
                        'Common basket lines': len(common)})
    return summary
