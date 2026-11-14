"""Shared server-side OpenRouter connection. Never prints credentials."""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')


def get_setting(name):
    """Support local environment/.env and Streamlit Community Cloud secrets."""
    value = os.getenv(name, '').strip()
    if value:
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name, '')
    except FileNotFoundError:
        return ''
    return str(value).strip() if value is not None else ''


def chat(messages, *, json_output=False):
    key = get_setting('OPENROUTER_API_KEY')
    if not key or key == 'replace_with_your_openrouter_key':
        raise ValueError('Set OPENROUTER_API_KEY in .env locally or in Streamlit app secrets.')
    payload = {'messages': messages, 'temperature': 0, 'max_tokens': 8000}
    model = get_setting('OPENROUTER_MODEL')
    if model:
        payload['model'] = model
    if json_output:
        payload['response_format'] = {'type': 'json_object'}
    try:
        response = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}',
                     'X-OpenRouter-Title': 'Aerchain Quote Comparison'},
            json=payload, timeout=(15, 180),
        )
    except requests.RequestException:
        raise RuntimeError('OpenRouter connection failed. Check your network and retry.') from None
    if not response.ok:
        hints = {401: 'Check your API key.', 402: 'Check your OpenRouter credits.',
                 429: 'Rate limit reached; retry shortly.'}
        raise RuntimeError(f'OpenRouter HTTP {response.status_code}. '
                           + hints.get(response.status_code, 'Check model availability and request settings.'))
    try:
        content = response.json()['choices'][0]['message']['content']
        if response.json()['choices'][0].get('finish_reason') == 'length':
            raise RuntimeError('Model output was truncated. Use a larger output limit or a smaller request.')
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        return content
    except (KeyError, IndexError, ValueError, TypeError):
        raise RuntimeError('OpenRouter returned no usable text response.') from None


if __name__ == '__main__':
    print(chat([{'role': 'user', 'content': 'Reply with exactly: OpenRouter connection successful'}]))
