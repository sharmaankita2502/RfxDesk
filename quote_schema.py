"""Schema and deterministic checks for raw model extractions."""
import math
from jsonschema import Draft202012Validator


def obj(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string'}
NULL_TEXT = {'type': ['string', 'null']}
EVIDENCE = {'source_ref': TEXT, 'evidence_text': TEXT}
LINE = obj({
    'rfx_line_no': {'type': 'integer', 'minimum': 1, 'maximum': 30},
    'vendor_item_text': TEXT,
    'price_as_stated': {'type': ['number', 'null'], 'minimum': 0},
    'stated_unit': NULL_TEXT,
    'confidence': {'enum': ['sure', 'check', 'missing']},
    'reason': TEXT, **EVIDENCE,
})
SCHEMA = obj({
    'vendor_name': {'type': 'string', 'minLength': 1},
    'currency': {'enum': ['INR', 'USD', 'unclear']},
    'currency_source': obj(EVIDENCE),
    'lines': {'type': 'array', 'minItems': 30, 'maxItems': 30, 'items': LINE},
    'terms': {'type': 'array', 'items': obj({
        'kind': {'enum': ['freight', 'payment', 'validity', 'lead_time', 'tax', 'other']},
        'value': TEXT, **EVIDENCE})},
    'discounts': {'type': 'array', 'items': obj({
        'percent': {'type': 'number', 'minimum': 0, 'maximum': 100},
        'applies_to_line_nos': {'type': 'array', 'uniqueItems': True,
                              'items': {'type': 'integer', 'minimum': 1, 'maximum': 30}},
        'condition': TEXT, 'scope_clear': {'type': 'boolean'}, **EVIDENCE})},
    'questionnaire': {'type': 'array', 'items': obj({
        'question_id': {'type': 'integer'}, 'answer': NULL_TEXT,
        'status': {'enum': ['stated', 'missing', 'check']}, **EVIDENCE})},
    'attachments_mentioned': {'type': 'array', 'items': obj({'name': TEXT, **EVIDENCE})},
    'warnings': {'type': 'array', 'items': TEXT},
})


def validate_quote(quote, rfx, source_parts, *, source_is_image=False):
    """Reject malformed results; downgrade unverified evidence instead of hiding it."""
    errors = sorted(Draft202012Validator(SCHEMA).iter_errors(quote), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError('Invalid quote JSON: ' + '; '.join(
            f'{list(e.path)}: {e.message}' for e in errors[:4]))
    expected = {x['line_no'] for x in rfx['lines']}
    ids = [x['rfx_line_no'] for x in quote['lines']]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Quote must contain each RFx line exactly once.')
    question_ids = [x['question_id'] for x in quote['questionnaire']]
    if len(question_ids) != len(set(question_ids)) or set(question_ids) != {
            x['question_id'] for x in rfx['questionnaire']}:
        raise ValueError('Quote must contain each questionnaire question exactly once.')
    source_text = ' '.join(p.get('text', '') for p in source_parts)
    is_image = source_is_image or any(p['type'] == 'image_url' for p in source_parts)
    compact_source = ' '.join(source_text.split())
    for line in quote['lines']:
        price = line['price_as_stated']
        if price is not None and (isinstance(price, bool) or not math.isfinite(price)):
            raise ValueError('Price must be a finite number or null.')
        if price is None:
            line['confidence'] = 'missing'
            if not line['reason'].strip():
                raise ValueError(f'RFx line {line["rfx_line_no"]}: a missing price needs a reason.')
        elif line['confidence'] == 'missing':
            raise ValueError(f'RFx line {line["rfx_line_no"]}: a missing line cannot contain a price.')
        else:
            if not line['source_ref'].strip() or not line['evidence_text'].strip():
                raise ValueError(f'RFx line {line["rfx_line_no"]}: quoted price needs source location and evidence.')
            reasons = []
            if not line['stated_unit'] or not line['stated_unit'].strip():
                reasons.append('Unit is not explicit; buyer review required.')
            if quote['currency'] == 'unclear':
                reasons.append('Currency is unclear.')
            # Vision confidence is model-reported, not a calibrated OCR score.
            if is_image:
                reasons.append('Photo/OCR transcription and item match require visual review.')
            if source_text and ' '.join(line['evidence_text'].split()) not in compact_source:
                reasons.append('Evidence text could not be matched verbatim in the source.')
            if reasons:
                line['confidence'] = 'check'
                line['reason'] = ' '.join(filter(None, [line['reason'], *reasons]))
        if line['confidence'] == 'check' and not line['reason'].strip():
            raise ValueError(f'RFx line {line["rfx_line_no"]}: a review flag needs a reason.')
    for answer in quote['questionnaire']:
        if answer['status'] == 'missing' and answer['answer'] is not None:
            raise ValueError(f'Question {answer["question_id"]}: missing answers must be null.')
        if answer['status'] != 'missing' and (
                answer['answer'] is None or not answer['source_ref'] or not answer['evidence_text']):
            raise ValueError(f'Question {answer["question_id"]}: stated answers need an answer and evidence.')
    quote['lines'].sort(key=lambda x: x['rfx_line_no'])
    return quote
