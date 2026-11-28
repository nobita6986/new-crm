"""Use site branding before the CRM frontend has loaded its settings.

Run against the built Frappe CRM template after building its frontend. This
does not change hashed JavaScript bundles or replace framework image assets.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


MARKER = "<!-- hrpartner-branding-prepaint -->"
BRANDING = """<!-- hrpartner-branding-prepaint -->
    {% set hrpartner_logo = splash_image | default('') %}
    {% if hrpartner_logo %}
    <link rel="preload" as="image" href="{{ hrpartner_logo | e }}">
    <style id="hrpartner-branding-prepaint">
      /* BrandLogo's SVG fallback precedes the asynchronous FCRM Settings fetch. */
      #app button[id^="reka-dropdown-menu-trigger"] > svg.size-8.shrink-0.rounded:first-child {
        background: url({{ hrpartner_logo | tojson }}) center / contain no-repeat;
        width: 40px;
        height: 32px;
      }
      #app button[id^="reka-dropdown-menu-trigger"] > svg.size-8.shrink-0.rounded:first-child > * {
        visibility: hidden;
      }
      #app button[id^="reka-dropdown-menu-trigger"] img[src={{ hrpartner_logo | tojson }}] {
        object-fit: contain;
      }
    </style>
    {% endif %}
    <!-- /hrpartner-branding-prepaint -->"""


def patch_template(text: str) -> str:
    """Patch only the initial favicon and the sidebar brand fallback."""
    if MARKER in text:
        return text

    icon = re.compile(r'<link\b(?=[^>]*\brel="icon")[^>]*>', re.S)
    text, count = icon.subn(
        '<link rel="icon" href="{{ favicon | e }}" type="image/x-icon">',
        text,
        count=1,
    )
    if count != 1 or "<head>" not in text:
        raise ValueError("Expected one CRM favicon declaration and a head element")

    return text.replace("<head>", "<head>\n    " + BRANDING, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, help="Built crm/www/crm.html path")
    args = parser.parse_args()
    original = args.template.read_text(encoding="utf-8")
    updated = patch_template(original)
    if updated != original:
        args.template.write_text(updated, encoding="utf-8", newline="\n")
    print("CRM branding template updated" if updated != original else "Already applied")


if __name__ == "__main__":
    main()
