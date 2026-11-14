"""Local OCR only: no network calls and no image upload to a language model."""
import argparse
import hashlib
import json
from pathlib import Path

OCR_VERSION = 'rapidocr-1.4.4-document-v1'
ROOT = Path(__file__).parent
LOW_CONFIDENCE = 0.80


def straighten_page(image):
    """Correct a clearly detected page quadrilateral; otherwise retain the image."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        polygon = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or cv2.contourArea(polygon) < image.shape[0] * image.shape[1] * 0.3:
            continue
        points = polygon.reshape(4, 2).astype('float32')
        sums, differences = points.sum(axis=1), np.diff(points, axis=1).ravel()
        ordered = np.array([points[sums.argmin()], points[differences.argmin()],
                            points[sums.argmax()], points[differences.argmax()]], dtype='float32')
        if len(np.unique(ordered, axis=0)) != 4:
            continue
        tl, tr, br, bl = ordered
        width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
        height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
        if min(width, height) < 100:
            continue
        target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype='float32')
        matrix = cv2.getPerspectiveTransform(ordered, target)
        return cv2.warpPerspective(image, matrix, (width, height)), np.linalg.inv(matrix), True
    return image, np.eye(3), False


def group_lines(tokens):
    """Join text boxes by vertical overlap, retaining left-to-right cell order."""
    groups = []
    for token in sorted(tokens, key=lambda t: sum(p[1] for p in t['box']) / 4):
        cy = sum(p[1] for p in token['box']) / 4
        height = max(p[1] for p in token['box']) - min(p[1] for p in token['box'])
        match = next((g for g in reversed(groups[-3:])
                      if abs(cy - g['cy']) <= 0.5 * min(height, g['height'])), None)
        if match is None:
            match = {'cy': cy, 'height': height, 'tokens': []}
            groups.append(match)
        match['tokens'].append(token)
    lines = []
    for i, group in enumerate(groups, 1):
        cells = sorted(group['tokens'], key=lambda t: min(p[0] for p in t['box']))
        visible = [t['text'] if t['confidence'] >= LOW_CONFIDENCE else '[UNCLEAR OCR TEXT]' for t in cells]
        lines.append({'line_id': f'OCR-L{i:03}', 'text': ' | '.join(visible),
                      'min_confidence': min(t['confidence'] for t in cells), 'tokens': cells})
    return lines


def ocr_image(path, *, force=False, cache_dir=None):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    directory = Path(cache_dir) if cache_dir is not None else ROOT / 'outputs' / 'ocr'
    destination = directory / f'{path.stem}.ocr.json'
    if destination.exists() and not force:
        cached = json.loads(destination.read_text(encoding='utf-8'))
        if cached.get('source_sha256') == digest and cached.get('ocr_version') == OCR_VERSION:
            return cached
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise RuntimeError('Local OCR dependency missing. Run venv/bin/pip install -r requirements.txt') from None
    # Honor phone orientation metadata before detecting the page.
    image = cv2.cvtColor(np.array(ImageOps.exif_transpose(Image.open(path)).convert('RGB')), cv2.COLOR_RGB2BGR)
    original_height, original_width = image.shape[:2]
    prepared, inverse, straightened = straighten_page(image)
    engine = RapidOCR()
    results, _ = engine(prepared)
    if not results:
        raise ValueError('Local OCR found no readable text. No image was sent to OpenRouter.')
    tokens = []
    for box, text, confidence in results:
        if not text.strip():
            continue
        original_box = cv2.perspectiveTransform(np.array([box], dtype='float32'), inverse)[0]
        tokens.append({'box': [[round(float(x), 1), round(float(y), 1)] for x, y in box],
                       'original_box': [[round(float(x), 1), round(float(y), 1)] for x, y in original_box],
                       'text': text, 'confidence': round(float(confidence), 4)})
    lines = group_lines(tokens)
    if not lines or not any(t['confidence'] >= LOW_CONFIDENCE for t in tokens):
        raise ValueError('Local OCR has no confident text. Review the image; nothing sent to OpenRouter.')
    artifact = {'source_file': path.name, 'source_sha256': digest, 'ocr_version': OCR_VERSION,
                'engine': 'RapidOCR / ONNX Runtime (local CPU)', 'straightened': straightened,
                'original_size': [original_width, original_height],
                'confidence_threshold': LOW_CONFIDENCE, 'lines': lines,
                'note': 'OCR confidence is not a guarantee of correct digits or item matching. Coordinates use the EXIF-oriented image.'}
    directory.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(destination)
    return artifact


def ocr_text(artifact):
    lines = [f'Source file: {artifact["source_file"]}',
             'Source reader: local OCR. The language model receives text only.',
             'OCR line IDs and printed vendor row numbers are NOT RFx item IDs.',
             'Match items by descriptions, specifications and units, never by row position.',
             '[UNCLEAR OCR TEXT] is unreadable and must not be guessed.']
    for row in artifact['lines']:
        lines.append(f'[{row["line_id"]}; min OCR confidence {row["min_confidence"]:.2f}] {row["text"]}')
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    print(ocr_text(ocr_image(args.image, force=args.force)))
