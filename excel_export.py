"""Excel comparison with the same colours as the dashboard."""
from io import BytesIO

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TITLE = 'Vendor Rate Comparison'
MUSTARD = 'FFD4A72C'


def build_workbook(frame, cell_styles, audit, summary, assumptions, legend, extra_sheets=None):
    buffer = BytesIO()
    export_frame = frame.copy()
    # Keep rates numeric in Excel so buyers can sort and calculate with them.
    for column in export_frame.columns[3:]:
        export_frame[column] = export_frame[column].map(
            lambda value: float(value.replace(',', '')) if isinstance(value, str) and value != '—' else value)
    styled = export_frame.style.apply(lambda _: pd.DataFrame(cell_styles, index=frame.index), axis=None)
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        styled.to_excel(writer, sheet_name='Comparison', index=False, startrow=8)
        audit.to_excel(writer, sheet_name='Audit', index=False)
        pd.DataFrame(summary).to_excel(writer, sheet_name='Annual costs', index=False)
        pd.DataFrame({'Assumption': assumptions}).to_excel(writer, sheet_name='Assumptions', index=False)
        for name, data in (extra_sheets or {}).items():
            data.to_excel(writer, sheet_name=name, index=False)
        book = writer.book
        book.properties.title = TITLE
        comparison = writer.sheets['Comparison']
        comparison.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(frame.columns))
        comparison['A1'] = TITLE
        comparison['A1'].alignment = Alignment(horizontal='center', vertical='center')
        comparison['A1'].font = Font(size=20, bold=True, color='FFFFFFFF')
        comparison['A1'].fill = PatternFill('solid', fgColor='FF17324D', bgColor='FF17324D')
        comparison.row_dimensions[1].height = 34
        comparison.freeze_panes = 'D10'
        comparison.auto_filter.ref = f'A9:{get_column_letter(len(frame.columns))}{9 + len(frame)}'
        for i, column in enumerate(frame.columns, 1):
            comparison.column_dimensions[get_column_letter(i)].width = 9 if i == 1 else 40 if i == 2 else 12 if i == 3 else 28
            comparison.cell(9, i).alignment = Alignment(wrap_text=True, vertical='center')
        comparison.row_dimensions[9].height = 42

        legend_sheet = book.create_sheet('Legend')
        legend_sheet['A1'] = f'{TITLE} — Legend'
        legend_sheet['A1'].font = Font(size=16, bold=True)
        legend_sheet.column_dimensions['A'].width = 16
        legend_sheet.column_dimensions['B'].width = 48
        for sheet in [comparison, legend_sheet]:
            sheet['A3'], sheet['B3'] = 'Legend', 'Meaning'
            for row_number, (colour, meaning) in enumerate(legend, 4):
                sheet.cell(row_number, 2, meaning)
                sheet.cell(row_number, 1).fill = PatternFill('solid', fgColor=colour.lstrip('#'))
        for sheet in book:
            header_rows = [3, 9] if sheet.title == 'Comparison' else [3] if sheet.title == 'Legend' else [1]
            for row in header_rows:
                for cell in sheet[row]:
                    if cell.value is not None:
                        cell.fill = PatternFill('solid', fgColor=MUSTARD, bgColor=MUSTARD)
                        cell.font = Font(bold=True, color='FF000000')
        edge = Side(style='thin', color='FF808080')
        grid = Border(left=edge, right=edge, top=edge, bottom=edge)
        for column in range(1, len(frame.columns) + 1):
            cell = comparison.cell(1, column)
            cell.fill = PatternFill('solid', fgColor='FF17324D', bgColor='FF17324D')
            cell.border = Border(
                top=edge, bottom=edge,
                left=edge if column == 1 else Side(),
                right=edge if column == len(frame.columns) else Side(),
            )
        for sheet in book:
            first_row = 9 if sheet.title == 'Comparison' else 3 if sheet.title == 'Legend' else 1
            for row in sheet.iter_rows(min_row=first_row):
                for cell in row:
                    cell.border = grid
            if sheet.title == 'Comparison':
                for row in sheet.iter_rows(min_row=3, max_row=7, max_col=2):
                    for cell in row:
                        cell.border = grid
            # Use opaque ARGB fills consistently across Excel-compatible viewers.
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.lstrip().startswith(('=', '+', '-', '@')):
                        cell.data_type = 's'
                        cell.quotePrefix = True
                    colour = cell.fill.fgColor
                    if cell.fill.patternType == 'solid' and colour.type == 'rgb':
                        opaque = 'FF' + colour.rgb[-6:].upper()
                        cell.fill = PatternFill('solid', fgColor=opaque, bgColor=opaque)
    return buffer.getvalue()
