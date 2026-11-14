"""Streamlit views for RFx drafting, analyst chat, and buyer review workflows."""
from datetime import date
from io import BytesIO
import json
from pathlib import Path

from docx import Document
import pandas as pd
import streamlit as st

from analyst import answer_question, snapshot
from buyer_reviews import save_correction, save_quality, swap_mapping
from rfx_assistant import draft_rfx, validate_rfx
from upload_service import FORMATS, validate_file


def rfx_panel(store, seed):
    st.subheader('RFx drafting assistant')
    saved = store.drafts()
    default = list(saved.values())[-1] if saved else {
        **seed, 'title': 'Corrugated packaging RFx', 'scope': 'Annual supply of the packaging items listed below.',
        'terms': [], 'assumptions': ['Existing quantities and specifications come from the buyer RFx.']}
    current = st.session_state.setdefault('rfx_working_draft', default)
    with st.form('rfx-prompt'):
        prompt = st.text_area('Describe your requirements or ask for a change',
            placeholder='Draft an RFx for our Nashik plant. Keep the 30 items, require delivery within 14 days and 45-day payment terms.')
        generate = st.form_submit_button('Generate / refine RFx', type='primary')
    if generate:
        if not prompt.strip():
            st.warning('Describe what you want to draft or change.')
        else:
            try:
                with st.spinner('Drafting your RFx with OpenRouter…'):
                    current = draft_rfx(prompt, current, st.session_state.get('rfx_requests', []))
                current['buyer_approved'] = False
                identifier, _ = store.save_draft(current, st.session_state.get('rfx_draft_id'))
                st.session_state.update(rfx_working_draft=current, rfx_draft_id=identifier)
                st.session_state.setdefault('rfx_requests', []).append(prompt)
            except Exception as error:
                st.error(str(error))
    draft_key = str(hash(json.dumps(current, sort_keys=True)))
    st.caption('AI proposals remain drafts. Review quantities, specifications, questionnaire and terms before using them. This prototype supports 30 lines.')
    with st.form(f'rfx-editor-{draft_key}'):
        title = st.text_input('RFx title', current['title'])
        scope = st.text_area('Scope', current['scope'])
        lines = st.data_editor(pd.DataFrame(current['lines']), hide_index=True, disabled=['line_no'],
                               width='stretch', key=f'rfx-lines-{draft_key}')
        questions = st.data_editor(pd.DataFrame(current['questionnaire']), hide_index=True, num_rows='dynamic',
                                   width='stretch', key=f'rfx-questions-{draft_key}')
        terms = st.text_area('Commercial terms — one clause per line', '\n'.join(current['terms']))
        cobb_limit = st.number_input('Quality gate: maximum Cobb value (g/m²)', min_value=0.0,
                                    value=float(current.get('quality_gate', {}).get('cobb_max', 25)))
        st.write('**Proposed assumptions**')
        assumptions = current.get('assumptions', [])
        if assumptions:
            for assumption in assumptions:
                st.markdown(f'- {assumption}')
        else:
            st.caption('No additional assumptions proposed.')
        save = st.form_submit_button('Save RFx draft')
        use = st.form_submit_button('Save & use this RFx', type='primary')
    edited = {**current, 'title': title, 'scope': scope, 'lines': json.loads(lines.to_json(orient='records')),
              'questionnaire': json.loads(questions.dropna(how='all').to_json(orient='records')),
              'terms': [t.strip() for t in terms.splitlines() if t.strip()], 'buyer_approved': bool(use),
              'quality_gate': {'cobb_max': cobb_limit}}
    if save or use:
        try:
            validate_rfx(edited)
            identifier, _ = store.save_draft(edited, st.session_state.get('rfx_draft_id'))
            st.session_state.update(rfx_working_draft=edited, rfx_draft_id=identifier)
            if use:
                st.session_state['activate_rfx'] = identifier
                st.rerun()
            st.success('RFx draft saved on this device.')
        except ValueError as error:
            st.error(str(error))
    st.download_button('Download RFx (JSON)', json.dumps(edited, indent=2), 'Buyer RFx.json', 'application/json')
    document = Document()
    document.add_heading(title, 0)
    document.add_paragraph(scope)
    table = document.add_table(rows=1, cols=5)
    for cell, text in zip(table.rows[0].cells, ['Line', 'Item', 'Specification', 'Unit', 'Annual quantity']):
        cell.text = text
    for line in edited['lines']:
        for cell, text in zip(table.add_row().cells, [line['line_no'], line['name'], line['spec'], line['unit'], line['yearly_quantity']]):
            cell.text = str(text)
    document.add_heading('Questionnaire', 1)
    for q in edited['questionnaire']:
        document.add_paragraph(f'{q.get("question", "")} — {q.get("rule", "")}')
    document.add_heading('Commercial terms', 1)
    for term in edited['terms']:
        document.add_paragraph(term)
    document.add_heading('Assumptions to confirm', 1)
    for assumption in current.get('assumptions', []):
        document.add_paragraph(str(assumption))
    buffer = BytesIO()
    document.save(buffer)
    st.download_button('Download RFx (Word)', buffer.getvalue(), 'Buyer RFx.docx',
                       'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    st.caption('Vendor sending is stubbed: RFx downloads are real; no emails are sent.')


def quality_panel(store, scope, quotes, quality, folder, cobb_limit=25):
    with st.expander('Quality eligibility — verify vendors', expanded=False):
        st.dataframe(pd.DataFrame([{'Vendor': q['display_name'], 'Status': quality[q['quote_id']]['status'],
            'Reason': quality[q['quote_id']]['reason']} for q in quotes]), hide_index=True, width='stretch')
        quote_id = st.selectbox('Vendor to review', [q['quote_id'] for q in quotes],
            format_func=lambda identifier: next(q['display_name'] for q in quotes if q['quote_id'] == identifier), key=f'quality-vendor-{scope}')
        quote = next(q for q in quotes if q['quote_id'] == quote_id)
        decision = quality[quote_id]['decision']
        st.caption(f'Clearance requires buyer-verified ISO validity, a bursting-strength report and Cobb ≤ {cobb_limit:g} g/m². Supplier statements alone do not clear a vendor.')
        st.write('Supplier questionnaire evidence')
        st.dataframe(pd.DataFrame(quote['questionnaire']), hide_index=True, width='stretch')
        with st.form(f'quality-review-{scope}-{quote_id}'):
            options = ['Pending', 'Yes', 'No']
            iso = st.selectbox('Valid ISO certificate verified?', options, index=options.index(decision.get('iso', 'Pending')))
            burst = st.selectbox('Bursting-strength report verified?', options, index=options.index(decision.get('burst_report', 'Pending')))
            has_cobb = st.checkbox('Cobb measurement verified', value=decision.get('cobb') is not None)
            cobb = st.number_input('Cobb value (g/m²)', min_value=0.0, value=float(decision.get('cobb') or 0), step=0.5)
            has_expiry = st.checkbox('ISO expiry date known', value=bool(decision.get('iso_expiry')))
            expiry = st.date_input('ISO valid until', value=date.fromisoformat(decision['iso_expiry']) if decision.get('iso_expiry') else date.today())
            reviewer = st.text_input('Reviewed by', decision.get('actor', ''))
            reference = st.text_input('Document or source reference', decision.get('evidence_ref', ''))
            note = st.text_area('Quality review notes', decision.get('note', ''))
            attachments = st.file_uploader('Supporting quality documents (optional)', type=['pdf', 'xlsx', 'docx', 'jpg', 'jpeg', 'png'], accept_multiple_files=True)
            submitted = st.form_submit_button('Save quality review', type='primary')
        if submitted:
            try:
                import hashlib
                documents = []
                for attachment in attachments:
                    suffix = Path(attachment.name).suffix.lower().lstrip('.')
                    format_name = next(k for k, v in FORMATS.items() if suffix in v)
                    data = attachment.getvalue()
                    validate_file(attachment.name, data, format_name)
                    destination = Path(folder) / 'quality_documents' / (hashlib.sha256(data).hexdigest()[:24] + '.' + suffix)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    documents.append({'name': attachment.name, 'path': str(destination)})
                save_quality(store, scope, quote, {'iso': iso, 'burst_report': burst, 'cobb': cobb if has_cobb else None,
                    'iso_expiry': expiry.isoformat() if has_expiry else None, 'actor': reviewer,
                    'evidence_ref': reference or '; '.join(d['name'] for d in documents), 'note': note,
                    'attachments': documents or decision.get('attachments', [])})
                st.rerun()
            except Exception as error:
                st.error(str(error))
        for index, document in enumerate(decision.get('attachments', [])):
            if Path(document['path']).is_file():
                st.download_button(document['name'], Path(document['path']).read_bytes(), file_name=document['name'],
                                   key=f'quality-doc-{scope}-{quote_id}-{index}')


def correction_panel(store, scope, quote, row):
    line_no = row['rfx_line_no']
    with st.expander('Correct or confirm this value'):
        with st.form(f'correction-{scope}-{quote["quote_id"]}-{line_no}'):
            missing = st.checkbox('No usable price is quoted', value=row['price_as_stated'] is None)
            price = st.number_input('Price as stated in the source', min_value=0.0, value=float(row['price_as_stated'] or 0), format='%.4f')
            unit = st.text_input('Billing unit as stated', row['stated_unit'] or '')
            description = st.text_input('Vendor item description', row['vendor_item_text'])
            source_ref = st.text_input('Source location', row['source_ref'])
            evidence = st.text_area('Exact source evidence', row['evidence_text'])
            actor = st.text_input('Reviewer name', row.get('reviewed_by', ''))
            note = st.text_area('Reason for correction or confirmation')
            confirmed = st.checkbox('I verified the price, unit, item match and any pack conversion against the source', value=row.get('buyer_verified', False))
            submit = st.form_submit_button('Save correction', type='primary')
        if submit:
            try:
                save_correction(store, scope, quote, line_no,
                    {'price_as_stated': None if missing else price, 'stated_unit': unit, 'vendor_item_text': description,
                     'source_ref': source_ref, 'evidence_text': evidence}, actor, note, confirmed)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    with st.expander('Fix an item mapping'):
        candidates = [r['rfx_line_no'] for r in quote['lines'] if r['rfx_line_no'] != line_no]
        other = st.selectbox('Swap extracted source rows with RFx line', candidates,
            format_func=lambda number: f'{number}: {next(r["vendor_item_text"] for r in quote["lines"] if r["rfx_line_no"] == number)}',
            key=f'mapping-{scope}-{quote["quote_id"]}-{line_no}')
        st.caption('Swapping preserves both source rows. Both mappings require confirmation afterwards.')
        with st.form(f'mapping-form-{scope}-{quote["quote_id"]}-{line_no}'):
            actor = st.text_input('Reviewer')
            note = st.text_input('Mapping correction reason')
            swap = st.form_submit_button('Swap item mappings')
        if swap:
            try:
                swap_mapping(store, scope, quote, line_no, other, actor, note)
                st.rerun()
            except ValueError as error:
                st.error(str(error))


def analyst_panel(store, scope, quotes, rfx, quality, fx, discounts):
    st.subheader('Ask your procurement analyst')
    st.caption('Ask about rates, vendor totals, split scenarios, missing quotes, terms or quality. Examples: “Cheapest per line among quality-cleared vendors?” or “What if USD/INR were 90?”')
    messages = store.answers(scope)
    current_snapshot = snapshot(quotes, rfx, fx, discounts, quality)
    question = st.chat_input('Ask a question about this comparison', key=f'analyst-question-{scope}')
    if question:
        try:
            with st.spinner('Planning and calculating from the extracted quotes…'):
                result = answer_question(question, quotes, rfx, quality, fx, discounts, [m['question'] for m in messages])
            store.save_answer(scope, result)
            st.rerun()
        except Exception as error:
            st.error(f'Analysis could not be completed: {error}')
    for index, message in enumerate(messages):
        with st.chat_message('user'):
            st.write(message['question'])
        with st.chat_message('assistant'):
            st.write(message['answer'])
            if message['snapshot'] != current_snapshot:
                st.caption('Earlier answer: based on a previous version of prices, reviews or assumptions. Ask again for current results.')
            if message.get('rows'):
                frame = pd.DataFrame(message['rows'])
                st.dataframe(frame, hide_index=True, width='stretch')
                numeric = next((c for c in ['Annual INR', 'INR per unit'] if c in frame.columns), None)
                if numeric and 'Vendor' in frame.columns and frame[numeric].notna().any():
                    st.bar_chart(frame.dropna(subset=[numeric]).set_index('Vendor')[[numeric]])
            if message.get('totals'):
                st.write('Computed totals:', message['totals'])
            for warning in message.get('warnings', []):
                st.warning(warning)
            with st.expander('Show query, assumptions and rows used'):
                st.json(message['query'])
                st.write(message['assumptions'])
                st.write('Cited source rows:', message.get('citations', []))
                st.dataframe(pd.DataFrame(message['evidence']), hide_index=True, width='stretch')
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                pd.DataFrame(message.get('rows', [])).to_excel(writer, sheet_name='Analysis', index=False)
                pd.DataFrame(message.get('evidence', [])).to_excel(writer, sheet_name='Source rows', index=False)
                pd.DataFrame({'Query': [json.dumps(message['query'])], 'Assumptions': [json.dumps(message['assumptions'])]}).to_excel(writer, sheet_name='Query', index=False)
                from openpyxl.styles import Font, PatternFill, Border, Side
                edge = Side(style='thin', color='FF808080')
                for sheet in writer.book:
                    for row in sheet:
                        for cell in row:
                            if isinstance(cell.value, str) and cell.value.lstrip().startswith(('=', '+', '-', '@')):
                                cell.data_type = 's'
                                cell.quotePrefix = True
                            cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
                    for cell in sheet[1]:
                        cell.font = Font(bold=True, color='FF000000')
                        cell.fill = PatternFill('solid', fgColor='FFD4A72C')
            st.download_button('Download this analysis (.xlsx)', buffer.getvalue(), 'RFxDesk Analysis.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key=f'analysis-download-{scope}-{index}')
