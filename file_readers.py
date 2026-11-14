"""Read source files with stable locations, without sending them to an API."""
import email
from email import policy
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader


def read_source(path, *, ocr_cache_dir=None):
    path = Path(path)
    ext = path.suffix.lower()
    if ext in {'.jpg', '.jpeg', '.png'}:
        from image_ocr import ocr_image, ocr_text
        return [{'type': 'text', 'text': ocr_text(ocr_image(path, cache_dir=ocr_cache_dir))}]
    lines = [f'Source file: {path.name}']
    if ext == '.xlsx':
        book = load_workbook(path, read_only=True, data_only=False)
        try:
            for sheet in book:
                for row in sheet.iter_rows():
                    cells = [f'{c.coordinate}={c.value}' for c in row if c.value is not None]
                    if cells:
                        lines.append(f'[Sheet {sheet.title}] ' + ' | '.join(cells))
        finally:
            book.close()
    elif ext == '.pdf':
        for number, page in enumerate(PdfReader(path).pages, 1):
            text = page.extract_text() or ''
            if not text.strip():
                raise ValueError(f'Page {number} has no readable text; it needs image/OCR extraction.')
            lines.append(f'[Page {number}]\n{text}')
    elif ext == '.docx':
        doc = Document(path)
        lines.extend(f'[Paragraph {i}] {p.text}' for i, p in enumerate(doc.paragraphs, 1) if p.text.strip())
        for ti, table in enumerate(doc.tables, 1):
            for ri, row in enumerate(table.rows, 1):
                lines.append(f'[Table {ti}, row {ri}] ' + ' | '.join(c.text for c in row.cells))
    elif ext == '.eml':
        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        body = message.get_body(preferencelist=('plain', 'html'))
        body_text = ''
        if body:
            body_text = body.get_content()
            # Some vendor .eml files contain UTF-8 but omit MIME charset headers.
            if not body.get_content_charset():
                try:
                    body_text = body.get_payload(decode=True).decode('utf-8')
                except (UnicodeDecodeError, AttributeError):
                    pass
        lines.extend([f'From: {message["from"]}', f'Subject: {message["subject"]}',
                      f'Date: {message["date"]}'])
        lines.extend(f'[Body line {i}] {line}' for i, line in enumerate(body_text.splitlines(), 1))
    elif ext == '.txt':
        lines.append(path.read_text(encoding='utf-8'))
    else:
        raise ValueError(f'Unsupported file type: {ext}')
    return [{'type': 'text', 'text': '\n'.join(lines)}]


if __name__ == '__main__':
    for path in sorted((Path(__file__).parent / 'vendor_files').iterdir()):
        if path.suffix.lower() in {'.xlsx', '.pdf', '.docx', '.eml', '.jpg', '.jpeg', '.png'}:
            parts = read_source(path)
            summary = f'{len(parts[0]["text"])} text characters' if parts[0]['type'] == 'text' else 'image ready for vision'
            print(f'{path.name}: {summary}')
