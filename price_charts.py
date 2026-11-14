"""Chart data derived from normalized comparison rows, with consistent coverage."""
from comparison import summarize


def available(row, include_review):
    return row['price_inr'] is not None and row['status'] not in {'missing', 'blocked'} and (
        include_review or row['status'] == 'sure')


def item_rates(groups, line_no, include_review=True):
    data, excluded = [], []
    for group in groups:
        row = next((r for r in group if r['rfx_line_no'] == line_no), None)
        if row is None:
            continue
        if not available(row, include_review):
            excluded.append(row['vendor'])
            continue
        data.append({'Vendor': row['vendor'], 'Unit rate (INR)': row['price_inr'],
                     'Review': 'Review needed' if row['status'] == 'check' else 'No issue detected',
                     'Source': row['source_ref']})
    return data, excluded


def annual_costs(groups, include_review=True, basis='common'):
    if basis not in {'common', 'full'}:
        raise ValueError('Unknown chart basket basis.')
    filtered = [[{**r, 'price_inr': r['price_inr'] if available(r, include_review) else None}
                 for r in group] for group in groups]
    common_ids = set.intersection(*[{r['rfx_line_no'] for r in group if r['price_inr'] is not None}
                                   for group in filtered]) if filtered else set()
    if basis == 'common' and not common_ids:
        return [], [group[0]['vendor'] for group in groups if group], 0
    data, excluded = [], []
    for group, summary in zip(filtered, summarize(filtered)):
        value = summary['Common basket INR'] if basis == 'common' else summary['Full basket INR']
        if value is None:
            excluded.append(summary['Vendor'])
            continue
        scoped = [r for r in group if basis == 'full' or r['rfx_line_no'] in common_ids]
        data.append({'Vendor': summary['Vendor'], 'Annual material cost (INR)': value,
                     'Items': len(scoped), 'Review-priced items': sum(r['status'] == 'check' for r in scoped)})
    return data, excluded, len(common_ids)
