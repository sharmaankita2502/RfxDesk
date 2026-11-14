"""Validate uploaded vendor files and extract into isolated comparison workspaces."""
from collections import Counter
from email import policy
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image, UnidentifiedImageError

import extract_openrouter

FORMATS = {
    'Excel workbook': ['xlsx'],
    'PDF quotation': ['pdf'],
    'Word document': ['docx'],
    'Photo / image': ['jpg', 'jpeg', 'png'],
    'Email': ['eml'],
}
MAX_BYTES = 20 * 1024 * 1024


def workspace_path(base, session_id, rfx):
    if len(session_id) != 32 or any(c not in '0123456789abcdef' for c in session_id):
        raise ValueError('Invalid upload session identifier.')
    fingerprint = hashlib.sha256(json.dumps(rfx, sort_keys=True).encode()).hexdigest()[:16]
    return Path(base) / session_id / fingerprint


def validate_file(name, data, format_name):
    if format_name not in FORMATS:
        raise ValueError('Select a supported file format first.')
    suffix = Path(name).suffix.lower().lstrip('.')
    if suffix not in FORMATS[format_name]:
        raise ValueError(f'{name}: this extension does not match the selected {format_name} format.')
    if not data:
        raise ValueError('The uploaded file is empty.')
    if len(data) > MAX_BYTES:
        raise ValueError('The file exceeds the 20 MB upload limit.')
    if suffix in {'xlsx', 'docx'}:
        required = 'xl/workbook.xml' if suffix == 'xlsx' else 'word/document.xml'
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(e.file_size for e in entries) > 100 * 1024 * 1024:
                    raise ValueError('The Office document expands beyond the supported size.')
                if required not in archive.namelist() or '[Content_Types].xml' not in archive.namelist():
                    raise ValueError('The document content does not match its selected Office format.')
        except BadZipFile:
            raise ValueError('This is not a valid Office document. Re-save it as .xlsx or .docx.') from None
    elif suffix == 'pdf':
        if not data.lstrip().startswith(b'%PDF-'):
            raise ValueError('This file does not contain a valid PDF header.')
    elif suffix in {'jpg', 'jpeg', 'png'}:
        try:
            with Image.open(BytesIO(data)) as image:
                expected = 'PNG' if suffix == 'png' else 'JPEG'
                if image.format != expected:
                    raise ValueError('The image content does not match its extension.')
                image.verify()
        except (UnidentifiedImageError, OSError):
            raise ValueError('This image cannot be read. Upload a valid JPG or PNG.') from None
    elif suffix == 'eml':
        message = BytesParser(policy=policy.default).parsebytes(data)
        if not (message['From'] or message['Subject']) or message.get_body() is None:
            raise ValueError('This email has no readable message body or headers. Export it as an .eml file.')
    return '.jpg' if suffix == 'jpeg' else '.' + suffix


def process_upload(workspace, name, data, format_name, rfx, *, extractor=None):
    suffix = validate_file(name, data, format_name)
    workspace = Path(workspace)
    sources = workspace / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    # Content identity prevents duplicate processing, even if the file is renamed.
    path = sources / f'V_upload_{digest[:24]}{suffix}'
    if not path.exists():
        path.write_bytes(data)
    (workspace / 'rfx.json').write_text(json.dumps(rfx, indent=2), encoding='utf-8')
    original_name = Path(name.replace('\\', '/')).name
    previous = workspace / 'outputs' / f'{path.stem}.json'
    if previous.exists():
        original_name = json.loads(previous.read_text())['metadata'].get('original_filename', original_name)
    return (extractor or extract_openrouter.extract)(path, rfx, output_dir=workspace / 'outputs', source_name=original_name)


def label_quotes(quotes):
    counts = Counter(q['vendor_name'] for q in quotes)
    for quote in quotes:
        name = quote['vendor_name']
        quote['display_name'] = name if counts[name] == 1 else f'{name} · {quote["quote_id"][-8:]}'
    return quotes
