"""Buyer corrections and quality decisions; raw extraction files are never edited."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import math
import re
from zoneinfo import ZoneInfo

LINE_FIELDS = ('vendor_item_text', 'price_as_stated', 'stated_unit', 'confidence', 'reason', 'source_ref', 'evidence_text')


def save_correction(store, scope, quote, line_no, patch, actor, note, verified=False):
    if not actor.strip() or not note.strip():
        raise ValueError('Enter your name and a reason for the correction.')
    price = patch.get('price_as_stated')
    if price is not None and (isinstance(price, bool) or not math.isfinite(float(price)) or float(price) < 0):
        raise ValueError('Price must be a positive finite number, zero, or missing.')
    if line_no not in {r['rfx_line_no'] for r in quote['lines']}:
        raise ValueError('Unknown RFx line.')
    if price is not None and (not patch.get('stated_unit', '').strip() or not patch.get('source_ref', '').strip()
                              or not patch.get('evidence_text', '').strip()):
        raise ValueError('A priced line needs a unit, source location and evidence.')
    if verified and price is not None:
        numbers = [Decimal(n.replace(',', '')) for n in re.findall(r'(?:\d[\d,]*(?:\.\d+)?|\.\d+)', patch.get('evidence_text', ''))]
        free = float(price) == 0 and re.search(r'\b(free|no charge)\b', patch.get('evidence_text', ''), re.I)
        if Decimal(str(price)) not in numbers and not free:
            raise ValueError('The confirmed price must appear in the source evidence. Update the evidence when correcting a price.')
    clean = {field: patch.get(field) for field in LINE_FIELDS}
    clean['confidence'] = 'missing' if price is None else 'sure' if verified else 'check'
    clean['reason'] = 'Buyer marked this item as not quoted.' if price is None else '' if verified else 'Buyer correction awaiting confirmation.'
    document = {'line_no': line_no, 'patch': clean, 'verified': bool(verified and price is not None),
                'actor': actor.strip(), 'note': note.strip(),
                'before': deepcopy(next(r for r in quote['lines'] if r['rfx_line_no'] == line_no))}
    store.add_events(scope, 'correction', quote, [document])


def swap_mapping(store, scope, quote, first, second, actor, note):
    if first == second or not actor.strip() or not note.strip():
        raise ValueError('Select a different line and enter your name and reason.')
    lines = {r['rfx_line_no']: r for r in quote['lines']}
    events = []
    for target, source in [(first, second), (second, first)]:
        patch = {field: lines[source][field] for field in LINE_FIELDS}
        patch.update(confidence='missing' if patch['price_as_stated'] is None else 'check',
                     reason='Buyer changed item mapping; confirm this line against the source.')
        events.append({'line_no': target, 'patch': patch, 'verified': False, 'actor': actor.strip(),
                       'note': note.strip(), 'before': lines[target]})
    store.add_events(scope, 'correction', quote, events)


def apply_corrections(quotes, events):
    result = deepcopy(quotes)
    index = {q['quote_id']: q for q in result}
    for event in events:
        quote = index.get(event['quote_id'])
        if not quote or quote['metadata']['source_sha256'] != event['source_hash']:
            continue
        change = event['document']
        line = next((r for r in quote['lines'] if r['rfx_line_no'] == change['line_no']), None)
        if line is not None:
            line.update(change['patch'])
            line.update(buyer_verified=change['verified'], reviewed_by=change['actor'],
                        reviewed_at=event['created_at'], review_note=change['note'])
    return result


def save_quality(store, scope, quote, decision):
    if not decision.get('actor', '').strip() or not decision.get('evidence_ref', '').strip():
        raise ValueError('Enter a reviewer and a document/source reference for this quality review.')
    for field in ['iso', 'burst_report']:
        if decision.get(field) not in {'Pending', 'Yes', 'No'}:
            raise ValueError('Invalid quality review state.')
    cobb = decision.get('cobb')
    if cobb is not None and (not math.isfinite(float(cobb)) or cobb < 0):
        raise ValueError('Cobb value must be a finite non-negative number.')
    store.add_events(scope, 'quality', quote, [decision])


def quality_for(quote, events, cobb_limit=25):
    matching = [e for e in events if e['quote_id'] == quote['quote_id']
                and e['source_hash'] == quote['metadata']['source_sha256']]
    if not matching:
        return {'status': 'Pending review', 'reason': 'Buyer has not verified the quality documents.', 'decision': {}}
    decision = matching[-1]['document']
    expiry = decision.get('iso_expiry')
    failed = decision['iso'] == 'No' or decision['burst_report'] == 'No'
    failed |= bool(expiry and date.fromisoformat(expiry) < datetime.now(ZoneInfo('Asia/Kolkata')).date())
    failed |= decision.get('cobb') is not None and decision['cobb'] > cobb_limit
    if failed:
        return {'status': 'Failed', 'reason': 'A recorded quality requirement is not met.', 'decision': decision}
    complete = (decision['iso'] == decision['burst_report'] == 'Yes' and decision.get('cobb') is not None
                and bool(decision.get('actor')) and bool(decision.get('evidence_ref')))
    return {'status': 'Cleared' if complete else 'Pending review',
            'reason': 'Buyer verified ISO, test report and Cobb limit.' if complete else 'Some required checks are unverified.',
            'decision': decision}
