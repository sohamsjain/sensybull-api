import os
from typing import Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(['html']),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(name: str, context: dict) -> Tuple[str, str]:
    """Render the `{name}.html` and `{name}.txt` sibling templates.

    Returns (html, text). Raises jinja2.TemplateNotFound if either is
    missing - we require both so every email has a plaintext fallback.
    """
    html = _env.get_template(f'{name}.html').render(**context)
    text = _env.get_template(f'{name}.txt').render(**context)
    return html, text
