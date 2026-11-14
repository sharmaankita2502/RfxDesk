"""Local quote comparison. Run: venv/bin/streamlit run app.py"""
from datetime import date
import importlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pandas as pd
import altair as alt
import streamlit as st

from comparison import load_quotes, normalize_quote, summarize
import excel_export
import openrouter_client
import image_ocr
import file_readers
import extract_openrouter
import upload_service
from price_charts import annual_costs, item_rates
import workspace_store
import buyer_reviews
import rfx_assistant
import analyst
import feature_panels

ROOT = Path(__file__).parent
# Refresh dependencies in order: a long-running local server may otherwise
# retain pre-upload signatures for extract(), read_source(), or ocr_image().
for module in (openrouter_client, image_ocr, file_readers, extract_openrouter, upload_service, excel_export,
               workspace_store, buyer_reviews, rfx_assistant, analyst, feature_panels):
    importlib.reload(module)
build_workbook = excel_export.build_workbook
read_source = file_readers.read_source
FORMATS = upload_service.FORMATS
label_quotes = upload_service.label_quotes
process_upload = upload_service.process_upload
workspace_path = upload_service.workspace_path
STORE = workspace_store.WorkspaceStore(ROOT / '.rfxdesk')
apply_corrections = buyer_reviews.apply_corrections
quality_for = buyer_reviews.quality_for
known_profiles = {}
for label, path in [('Original packaging RFx', ROOT / 'data/rfx.json'),
                    ('Dataset 2 · Meridian Foods', ROOT / 'demo_dataset_2/rfx.json')]:
    if path.is_file():
        known_profiles[label] = json.loads(path.read_text())
STORE.recover_legacy(ROOT / 'uploads', known_profiles)
st.set_page_config(page_title='RFxDesk | Aerchain', page_icon='📦', layout='wide')
st.set_option('client.disableDataExport', True)
st.markdown('''
<style>
    [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
        background-color: #F5F7FB;
    }
    [data-testid="stSidebar"] {
        background-color: #EAF0F7;
        border-right: 1px solid #D5DFEB;
    }
    [data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #DEE6EF;
        border-top: 3px solid #CDDBEC;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 2px 6px rgba(31, 50, 78, 0.035);
    }
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        background-color: #E7EDF5;
        border: 1px solid #D5DFEB;
        border-radius: 12px;
        padding: 6px;
        gap: 6px;
    }
    [data-testid="stTabs"] [data-baseweb="tab"] {
        background-color: transparent;
        border-radius: 8px;
        min-height: 44px;
        padding: 10px 16px;
    }
    [data-testid="stTabs"] [aria-selected="true"] {
        background-color: #FFF1F0;
        color: #D32F2F;
    }
    [data-testid="stExpander"] {
        background-color: #FFFFFF;
        border-radius: 12px;
    }
    [data-testid="stFileUploaderDropzone"] {
        background-color: #F0F5FC;
        border: 1px dashed #AFC3DD;
        border-radius: 12px;
    }
</style>
''', unsafe_allow_html=True)


def register_comparison():
    history = st.session_state.setdefault('comparison_history', {})
    for identifier, document in STORE.comparisons().items():
        history.setdefault(identifier, document)
    comparison_id = st.session_state.setdefault('upload_session_id', list(history)[-1] if history else uuid4().hex)
    if comparison_id not in history:
        history[comparison_id] = {'name': f'Comparison {len(history) + 1}',
                                  'rfx_choice': st.session_state.get('upload_rfx', 'Original packaging RFx')}
    STORE.save_comparison(comparison_id, history[comparison_id])
    return comparison_id, history


def start_new_comparison():
    if 'upload_session_id' in st.session_state:
        register_comparison()
    st.session_state['upload_session_id'] = uuid4().hex
    comparison_id, _ = register_comparison()
    st.session_state['comparison_picker'] = comparison_id
    st.session_state['quote_source'] = 'Upload quotes'
    st.session_state['upload_format'] = next(iter(FORMATS))


def open_comparison():
    comparison_id = st.session_state['comparison_picker']
    st.session_state['upload_session_id'] = comparison_id
    st.session_state['upload_rfx'] = st.session_state['comparison_history'][comparison_id]['rfx_choice']


saved_rfx = STORE.drafts()
if st.session_state.get('activate_rfx'):
    draft_id = st.session_state.pop('activate_rfx')
    st.session_state['upload_session_id'] = uuid4().hex
    st.session_state['upload_rfx'] = f'Saved RFx · {saved_rfx[draft_id]["title"]} [{draft_id[:6]}]'
    active_id, _ = register_comparison()
    st.session_state['comparison_picker'] = active_id
    st.session_state['quote_source'] = 'Upload quotes'

st.markdown('<h1 style="text-align:center;color:#D32F2F;">RFxDesk</h1>', unsafe_allow_html=True)
st.markdown(
    '<p style="text-align:center;color:#D32F2F;">Corrugated packaging</p>',
    unsafe_allow_html=True,
)

if not (ROOT / 'data/rfx.json').exists():
    st.info('Prepare the buyer inputs first: venv/bin/python prepare_rfx.py')
    st.stop()
rfx_path = ROOT / 'data/rfx.json'
source_mode = st.radio('Quote source', ['Demo quotes', 'Upload quotes', 'Draft RFx'], horizontal=True, key='quote_source')
if source_mode == 'Draft RFx':
    feature_panels.rfx_panel(STORE, json.loads(rfx_path.read_text()))
    st.stop()
if source_mode == 'Upload quotes':
    session_id, history = register_comparison()
    if st.session_state.get('comparison_picker') not in history:
        st.session_state['comparison_picker'] = session_id
    with st.sidebar:
        st.subheader('Your comparisons')
        st.selectbox('Open comparison', list(history), index=None,
                     format_func=lambda identifier: history[identifier]['name'],
                     key='comparison_picker', on_change=open_comparison)
        st.caption('Comparisons are saved on this device and can be reopened after restarting the app.')
    current_comparison = history[session_id]
    current_comparison['name'] = st.sidebar.text_input('Comparison name', current_comparison['name'], key=f'comparison-name-{session_id}')
    st.caption(current_comparison['name'])
    st.subheader('Upload vendor quotes')
    rfx_options = {'Original packaging RFx': ROOT / 'data/rfx.json'}
    if (ROOT / 'demo_dataset_2/rfx.json').exists():
        rfx_options['Dataset 2 · Meridian Foods'] = ROOT / 'demo_dataset_2/rfx.json'
    for identifier, draft in saved_rfx.items():
        if draft.get('buyer_approved'):
            rfx_options[f'Saved RFx · {draft["title"]} [{identifier[:6]}]'] = STORE.directory / 'rfx' / f'{identifier}.json'
    if st.session_state.get('upload_rfx') not in rfx_options:
        st.session_state['upload_rfx'] = current_comparison['rfx_choice'] if current_comparison['rfx_choice'] in rfx_options else next(iter(rfx_options))
    rfx_choice = st.selectbox('Buyer RFx to compare against', list(rfx_options), key='upload_rfx')
    current_comparison['rfx_choice'] = rfx_choice
    rfx_path = rfx_options[rfx_choice]
    rfx = current_comparison.setdefault('rfx_snapshots', {}).setdefault(rfx_choice, json.loads(rfx_path.read_text()))
    STORE.save_comparison(session_id, current_comparison)
    workspace = workspace_path(ROOT / 'uploads', session_id, rfx)
    source_folder, output_folder = workspace / 'sources', workspace / 'outputs'
    st.caption('Select the format, upload one or more vendor quotes, then extract. Add other formats in another batch; successful quotes stay in this comparison.')
    st.caption('This comparison is saved locally. Use Your comparisons to reopen it later, or New comparison to start another.')
    with st.expander('View the 30 buyer line items'):
        st.dataframe(pd.DataFrame(rfx['lines']), hide_index=True, width='stretch')
    file_format = st.selectbox('File format', list(FORMATS), key='upload_format')
    uploaded_files = st.file_uploader('Upload vendor quotations', type=FORMATS[file_format],
                                      accept_multiple_files=True,
                                      key=f'quote-files-{session_id}-{workspace.name}-{file_format}')
    st.caption('Up to 20 MB per file. Extraction sends document text to OpenRouter using the configured API key. Photos are read by local OCR; the image itself is not uploaded to the model.')
    if st.button('Extract & compare', disabled=not uploaded_files, type='primary', key='extract-quotes'):
        results = []
        progress = st.progress(0.0, text='Preparing vendor files…')
        for index, uploaded in enumerate(uploaded_files):
            with st.spinner(f'Reading {uploaded.name}…'):
                try:
                    quote = process_upload(workspace, uploaded.name, uploaded.getvalue(), file_format, rfx)
                    priced = sum(line['price_as_stated'] is not None for line in quote['lines'])
                    results.append({'File': uploaded.name, 'Status': 'Ready',
                                    'Details': f'{quote["vendor_name"]}: {priced}/30 items priced'})
                except Exception as error:
                    results.append({'File': uploaded.name, 'Status': 'Failed', 'Details': str(error)})
            progress.progress((index + 1) / len(uploaded_files), text=f'Processed {index + 1} of {len(uploaded_files)} files')
        progress.empty()
        st.session_state[f'upload-results-{workspace}'] = results
    for result in st.session_state.get(f'upload-results-{workspace}', []):
        if result['Status'] == 'Ready':
            st.success(f'{result["File"]} — {result["Details"]}')
        else:
            st.error(f'{result["File"]} — {result["Details"]}')
else:
    source_folder, output_folder = ROOT / 'vendor_files', ROOT / 'outputs'
if source_mode == 'Demo quotes':
    rfx = json.loads(rfx_path.read_text())
quotes, errors = load_quotes(output_folder, source_folder)
quotes = label_quotes(quotes)
scope_key = str(output_folder.resolve())
raw_quotes = {q['quote_id']: q for q in quotes}
quotes = apply_corrections(quotes, STORE.events(scope_key, 'correction'))
for error in errors:
    st.error(error)
if not quotes:
    st.info('Select a file format and upload your first quote to start comparing.' if source_mode == 'Upload quotes'
            else 'No verified demo quotes yet. Switch to Upload quotes to add vendor files.')
    st.stop()

all_quotes = quotes
quality_events = STORE.events(scope_key, 'quality')
cobb_limit = rfx.get('quality_gate', {}).get('cobb_max', 25)
quality = {q['quote_id']: quality_for(q, quality_events, cobb_limit) for q in all_quotes}
feature_panels.quality_panel(STORE, scope_key, all_quotes, quality, output_folder, cobb_limit)
settings = (current_comparison.setdefault('settings', {}).setdefault(str(output_folder), {})
            if source_mode == 'Upload quotes' else st.session_state.setdefault('demo_settings', {}))
only_cleared = st.sidebar.checkbox('Only quality-cleared vendors', value=settings.get('quality_only', False), key=f'quality-only-{output_folder}')
settings['quality_only'] = only_cleared
if source_mode == 'Upload quotes':
    STORE.save_comparison(session_id, current_comparison)
if only_cleared:
    quotes = [q for q in all_quotes if quality[q['quote_id']]['status'] == 'Cleared']
    if not quotes:
        st.info('No vendors have buyer-verified quality clearance. Record reviews above or turn off the quality filter.')
        st.stop()

with st.sidebar:
    st.header('Comparison assumptions')
    saved_date = settings.get('fx_date', date(2026, 11, 13))
    if isinstance(saved_date, str):
        saved_date = date.fromisoformat(saved_date)
    fx = st.number_input('INR per USD', min_value=0.01, value=settings.get('fx', 88.0), step=0.1,
                         key=f'fx-{output_folder}')
    fx_date = st.date_input('Rate as-of date', value=saved_date,
                            key=f'fx-date-{output_folder}')
    settings.update(fx=fx, fx_date=fx_date)
    st.caption('88 is an illustrative starting assumption. Enter your own rate and date; no live FX feed.')
    st.divider()
    st.subheader('Discount scenarios')
    accepted = {}
    discount_settings = settings.setdefault('discounts', {})
    for quote in quotes:
        selected = set()
        for i, discount in enumerate(quote['discounts']):
            st.write(f'{quote["display_name"]}: {discount["percent"]:g}%')
            st.caption(discount['condition'] or 'No condition stated')
            discount_key = f'{quote["quote_id"]}-{i}'
            selected_discount = st.checkbox('Assume condition met for this scenario',
                           value=discount_settings.get(discount_key, False),
                           key=f'{output_folder}-{discount_key}', disabled=not discount['scope_clear'])
            discount_settings[discount_key] = selected_discount
            if selected_discount:
                selected.add(i)
            st.caption(discount['source_ref'])
        accepted[quote['quote_id']] = selected
    st.caption('Scenario discounts do not establish eligibility for a split award.')
    if st.button('Reload extracted files'):
        st.rerun()
if source_mode == 'Upload quotes':
    STORE.save_comparison(session_id, current_comparison)

groups = [normalize_quote(q, rfx, fx=fx, accepted_discounts=accepted[q['quote_id']]) for q in quotes]
flat = [row for group in groups for row in group]
col1, col2, col3 = st.columns(3)
col1.metric('Vendor quotes', len(quotes))
col2.metric('Prices available for comparison', sum(r['price_inr'] is not None for r in flat))
col3.metric('Cells needing review', sum(r['status'] in {'check', 'blocked'} for r in flat))
st.info('Prices are quoted material rates, not landed costs. Freight, import duty, taxes and other extras are not added. Quality eligibility is based on recorded buyer reviews; use the quality filter to include only cleared vendors.')
st.markdown(
    '<style>.st-key-new-comparison button {background-color:#1565C0 !important;'
    'color:#FFFFFF !important;border-color:#1565C0 !important;}'
    '.st-key-new-comparison button:hover {background-color:#0D47A1 !important;'
    'border-color:#0D47A1 !important;}</style>',
    unsafe_allow_html=True,
)
st.button('New comparison', key='new-comparison', type='primary', on_click=start_new_comparison)
comparison_tab, charts_tab, analyst_tab, source_tab, terms_tab, assumptions_tab = st.tabs(['Comparison', 'Charts', 'Analyst chat', 'Source review', 'Terms & questionnaire', 'Assumptions & export'])

with comparison_tab:
    st.subheader('Unit prices · INR')
    palette = {
        'check': ('#fff3cd', '#664d03'),
        'missing': ('#f8d7da', '#842029'),
        'blocked': ('#d1e7dd', '#0f5132'),
    }
    legend = [
        ('#fff3cd', 'Check: review this price'),
        ('#f8d7da', 'Missing: no usable price'),
        ('#d1e7dd', 'Blocked: cannot safely compare'),
        ('#ffffff', 'No issue detected'),
    ]
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;gap:12px 24px;margin-bottom:12px">'
        + ''.join(f'<span style="display:inline-flex;align-items:center;gap:8px">'
                  f'<span aria-hidden="true" style="display:inline-block;width:16px;height:16px;'
                  f'border:1px solid #777;border-radius:3px;background:{colour}"></span>{label}</span>'
                  for colour, label in legend) + '</div>',
        unsafe_allow_html=True,
    )
    st.caption('Select a vendor and item in Source review for the status and exact reason. A dash means no comparable price is available.')
    comparison_download = st.empty()
    table = []
    cell_styles = []
    for item in rfx['lines']:
        row = {'Line': item['line_no'], 'Item': item['name'], 'RFx unit': item['unit']}
        styles = {'Line': '', 'Item': '', 'RFx unit': ''}
        for quote, group in zip(quotes, groups):
            cell = next(r for r in group if r['rfx_line_no'] == item['line_no'])
            value = '—' if cell['price_inr'] is None else f'{cell["price_inr"]:,.4f}'
            row[quote['display_name']] = value
            colors = palette.get(cell['status'])
            styles[quote['display_name']] = (
                f'background-color: {colors[0]}; color: {colors[1]}' if colors else '')
        table.append(row)
        cell_styles.append(styles)

    frame = pd.DataFrame(table)
    styled = frame.style.apply(lambda _: pd.DataFrame(cell_styles, index=frame.index), axis=None)
    st.dataframe(styled, hide_index=True, width='stretch', height=620)
    st.subheader('Annual material cost at RFx quantities')
    st.caption('Full basket totals are blank if any price is unavailable. Common basket totals use exactly the same available lines across all loaded vendors. Totals include review-flagged prices and are provisional.')
    st.dataframe(pd.DataFrame(summarize(groups)), hide_index=True, width='stretch')

with charts_tab, st.container(key='price-charts'):
    st.markdown('''<style>
        .st-key-price-charts [data-testid="stCaptionContainer"] p {
            color: #000000; font-size: 0.85rem;
        }
        .st-key-price-charts [data-testid="stWidgetLabel"] p,
        .st-key-price-charts [data-testid="stMarkdownContainer"] {
            color: #000000;
        }
    </style>''', unsafe_allow_html=True)
    st.subheader('Vendor price comparison')
    include_review = st.checkbox('Include prices needing review', value=True, key=f'chart-review-{output_folder}')
    st.caption('Charts use the normalized prices, exchange rate and discount scenario currently selected. Missing and blocked prices are excluded. Included review prices are provisional.')
    rate_column, basket_column = st.columns(2)
    with rate_column:
        st.markdown('**Rate for one RFx item**')
        chart_line = st.selectbox('Item to compare', [item['line_no'] for item in rfx['lines']],
            format_func=lambda number: f'{number}. {next(i["name"] for i in rfx["lines"] if i["line_no"] == number)}',
            key=f'chart-item-{output_folder}')
        item = next(i for i in rfx['lines'] if i['line_no'] == chart_line)
        rates, excluded = item_rates(groups, chart_line, include_review)
        st.caption(f'INR per {item["unit"]} · {item["spec"]}')
        if rates:
            rate_frame = pd.DataFrame(rates)
            rate_frame['Rate label'] = rate_frame['Unit rate (INR)'].map(lambda value: f'₹{value:,.2f}/{item["unit"]}')
            base = alt.Chart(rate_frame).encode(
                x=alt.X('Unit rate (INR):Q', title=f'INR / {item["unit"]}',
                        scale=alt.Scale(domain=[0, max(1, max(r['Unit rate (INR)'] for r in rates) * 1.55)])),
                y=alt.Y('Vendor:N', sort=alt.SortField(field='Unit rate (INR)', order='ascending'),
                        title=None, axis=alt.Axis(labelLimit=190)),
                tooltip=[alt.Tooltip('Vendor:N'), alt.Tooltip('Unit rate (INR):Q', format=',.2f'),
                         alt.Tooltip('Review:N'), alt.Tooltip('Source:N')],
            )
            bars = base.mark_bar(cornerRadiusEnd=4).encode(
                color=alt.Color('Review:N', title=None,
                    scale=alt.Scale(domain=['No issue detected', 'Review needed'], range=['#245B96', '#AA7400']),
                    legend=alt.Legend(orient='bottom')),
            )
            labels = base.mark_text(align='left', baseline='middle', dx=6, fontSize=11,
                                    fontWeight='bold', color='#000000').encode(text='Rate label:N')
            chart = (bars + labels).properties(height=max(240, len(rates) * 48))
            chart = chart.configure_axis(labelColor='#000000', titleColor='#000000', labelFontSize=11,
                titleFontSize=12, labelFontWeight='bold', titleFontWeight='bold').configure_legend(
                labelColor='#000000', titleColor='#000000', labelFontSize=11, labelFontWeight='bold')
            st.altair_chart(chart, width='stretch')
        else:
            st.info('No comparable price is available for this item with the selected filter.')
        if excluded:
            st.caption('No comparable price shown: ' + '; '.join(excluded))
    with basket_column:
        st.markdown('**Annual material cost**')
        basket = st.radio('Basket to compare', ['Common basket', 'Full basket'], horizontal=True,
                          key=f'chart-basket-{output_folder}')
        costs, excluded, common_count = annual_costs(groups, include_review, 'common' if basket == 'Common basket' else 'full')
        st.caption(f'Same {common_count} comparable items across every vendor.' if basket == 'Common basket'
                   else f'Only vendors with comparable prices for all {len(rfx["lines"])} RFx items are shown.')
        if costs:
            cost_frame = pd.DataFrame(costs)
            cost_frame['Cost label'] = cost_frame['Annual material cost (INR)'].map(lambda value: f'₹{value:,.2f}')
            base = alt.Chart(cost_frame).encode(
                x=alt.X('Annual material cost (INR):Q', title='Annual material cost · INR',
                        scale=alt.Scale(domain=[0, max(1, max(c['Annual material cost (INR)'] for c in costs) * 1.85)])),
                y=alt.Y('Vendor:N', sort=alt.SortField(field='Annual material cost (INR)', order='ascending'),
                        title=None, axis=alt.Axis(labelLimit=190)),
                tooltip=[alt.Tooltip('Vendor:N'), alt.Tooltip('Annual material cost (INR):Q', format=',.2f'),
                         alt.Tooltip('Items:Q'), alt.Tooltip('Review-priced items:Q')],
            )
            bars = base.mark_bar(color='#5B4B8A', cornerRadiusEnd=4)
            labels = base.mark_text(align='left', baseline='middle', dx=6, fontSize=11,
                                    fontWeight='bold', color='#000000').encode(text='Cost label:N')
            chart = (bars + labels).properties(height=max(240, len(costs) * 48))
            chart = chart.configure_axis(labelColor='#000000', titleColor='#000000', labelFontSize=11,
                titleFontSize=12, labelFontWeight='bold', titleFontWeight='bold').configure_legend(
                labelColor='#000000', titleColor='#000000', labelFontSize=11, labelFontWeight='bold')
            st.altair_chart(chart, width='stretch')
        else:
            st.info('No comparable basket is available with this selection. Try including review prices or changing the basket.')
        if excluded and basket == 'Full basket':
            st.caption('Excluded because coverage is incomplete: ' + '; '.join(excluded))

with analyst_tab:
    feature_panels.analyst_panel(STORE, scope_key, quotes, rfx, quality, fx, accepted)

with source_tab:
    vendor_index = st.selectbox('Vendor', range(len(quotes)), format_func=lambda i: quotes[i]['display_name'],
                                key=f'vendor-{source_mode}-{rfx_path.stem}')
    quote = quotes[vendor_index]
    line_no = st.selectbox('RFx item', range(1, 31), format_func=lambda i: f'{i}. {rfx["lines"][i-1]["name"]}')
    row = next(r for r in groups[vendor_index] if r['rfx_line_no'] == line_no)
    left, right = st.columns(2)
    with left:
        st.write('**Requested:**', row['item'], '—', row['spec'])
        st.write('**Vendor description:**', row['vendor_item_text'] or 'Not supplied')
        st.write('**As quoted:**', row['price_as_stated'], row['currency'], '/', row['stated_unit'])
        st.write('**Normalized:**', row['price_inr'], 'INR /', row['unit'])
        if row['status'] != 'sure':
            st.write('**Status:**', row['status'].upper())
        for flag in row['flags']:
            st.warning(flag)
        st.write('**Source location:**', row['source_ref'] or 'No location')
        st.code(row['evidence_text'] or 'No price evidence supplied', language=None)
        feature_panels.correction_panel(STORE, scope_key, quote, row)
        with st.expander('Original extracted value — unchanged'):
            st.json(next(r for r in raw_quotes[quote['quote_id']]['lines'] if r['rfx_line_no'] == line_no))
    with right:
        source = Path(quote['local_source'])
        st.download_button('Download original vendor file', source.read_bytes(),
                           file_name=quote['metadata'].get('original_filename', source.name),
                           key=f'download-{quote["quote_id"]}')
        if source.suffix.lower() in {'.jpg', '.jpeg', '.png'}:
            if not quote.get('metadata', {}).get('source_reader', '').startswith('rapidocr'):
                st.warning('This saved quote predates local OCR. Re-run extraction for this image to replace it with an OCR-based result.')
            st.image(str(source), caption='Original rate card — verify item mapping and digits', width='stretch')
            report = Path(quote['metadata'].get('ocr_report', ROOT / 'outputs' / 'ocr' / f'{source.stem}.ocr.json'))
            if report.exists():
                artifact = json.loads(report.read_text(encoding='utf-8'))
                if artifact.get('source_sha256') == quote['metadata']['source_sha256']:
                    from image_ocr import ocr_text
                    with st.expander('Local OCR text and confidence'):
                        st.text(ocr_text(artifact))
                        st.download_button('Download OCR boxes and confidence (JSON)', report.read_bytes(),
                                           file_name=report.name, mime='application/json')
        else:
            with st.expander('Read original source text', expanded=True):
                st.text(read_source(source)[0]['text'])

with terms_tab:
    for quote in quotes:
        with st.expander(quote['display_name']):
            st.write('Extracted commercial terms')
            st.dataframe(pd.DataFrame(quote['terms']), hide_index=True, width='stretch')
            st.write('Questionnaire responses — vendor statements; supporting documents are not verified')
            questions = {q['question_id']: q['question'] for q in rfx['questionnaire']}
            st.dataframe(pd.DataFrame([{**a, 'question': questions.get(a['question_id'], '')}
                                       for a in quote['questionnaire']]), hide_index=True, width='stretch')
            st.write('Attachments mentioned (a mention does not confirm receipt)')
            st.write(quote['attachments_mentioned'] or 'None mentioned')
            for warning in quote['warnings']:
                st.warning(warning)

with assumptions_tab:
    assumptions = [f'USD/INR: {fx} as of {fx_date}; buyer-entered, not live.',
                   'Annual quantities are from the buyer RFx.',
                   'Freight, duty, taxes and other extras are excluded.',
                   'Review-flagged values are provisional. Blocked or missing values are excluded.',
                   f'Quality filter: {"only buyer-cleared vendors" if only_cleared else "all vendors, including pending or failed"}.',
                   'Pack conversion is supported for boxes and individual pieces only. Other mismatches are blocked.']
    st.write('\n\n'.join(assumptions))
    audit = pd.DataFrame([{**r, 'flags': ' | '.join(r['flags'])} for r in flat])
    display_audit = audit.replace({'status': {'sure': ''}})
    st.dataframe(display_audit[['vendor', 'rfx_line_no', 'price_as_stated', 'stated_unit', 'price_inr', 'status', 'flags']],
                 hide_index=True, width='stretch')
    review_events = STORE.events(scope_key)
    review_audit = pd.DataFrame([{'Event': e['id'], 'Type': e['kind'], 'Quote': e['quote_id'], 'At': e['created_at'],
        'Reviewer': e['document'].get('actor', ''), 'Change': json.dumps(e['document'])} for e in review_events])
    with st.expander('Buyer review audit trail'):
        st.dataframe(review_audit, hide_index=True, width='stretch')
    quality_audit = pd.DataFrame([{'Vendor': q['display_name'], 'Status': quality[q['quote_id']]['status'],
        'Reason': quality[q['quote_id']]['reason'], 'Evidence': quality[q['quote_id']]['decision'].get('evidence_ref', '')} for q in all_quotes])
    workbook = build_workbook(frame, cell_styles, audit, summarize(groups), assumptions, legend,
                              extra_sheets={'Buyer reviews': review_audit, 'Quality': quality_audit})
    export_path = output_folder / 'Vendor Rate Comparison.xlsx'
    with NamedTemporaryFile(dir=output_folder, suffix='.xlsx.tmp', delete=False) as handle:
        handle.write(workbook)
        export_temporary = Path(handle.name)
    try:
        export_temporary.replace(export_path)
    finally:
        if export_temporary.exists():
            export_temporary.unlink()
    with comparison_download:
        st.download_button('Download Vendor Rate Comparison (.xlsx)', workbook, 'Vendor Rate Comparison.xlsx',
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           key='comparison-xlsx')
    st.download_button('Download Vendor Rate Comparison (.xlsx)', workbook, 'Vendor Rate Comparison.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key='audit-xlsx')
