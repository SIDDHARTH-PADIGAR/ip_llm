import os
from typing import Dict, List, Tuple
from jinja2 import Template

# Simple HTML template (expand as needed)
REPORT_HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <style>
    body{ font-family: Arial, Helvetica, sans-serif; margin: 24px; color:#111; }
    h1,h2,h3{ color:#222; }
    .meta{ font-size:0.9em; color:#444; margin-bottom:10px; }
    .section{ margin-top:18px; }
    .event{ background:#f7f7f9; padding:8px; border-radius:4px; margin-bottom:8px; }
    pre { white-space: pre-wrap; word-break: break-word; }
  </style>
</head>
<body>
  <h1>Patent Analysis Report - {{ patent_number }}</h1>
  <div class="meta">Generated: {{ generated_at }}</div>

  <div class="section">
    <h2>Executive Summary</h2>
    <p>{{ executive_summary }}</p>
  </div>

{% if events %}
<div class="section">
    <h2>Patent Timeline</h2>
    {% for event in events %}
        <div class="event">
            <strong>{{ event.date }}</strong>
            <div><strong>{{ event.code }}</strong>: {{ event.desc }}</div>
            {% if event.details %}
            <pre>{{ event.details }}</pre>
            {% endif %}
        </div>
    {% endfor %}
</div>
{% endif %}
  {% if citations %}
  <div class="section">
    <h2>Prior Art Citations</h2>
    {% for c in citations %}
      <div class="event">
        <strong>{{ c.citation.country }}{{ c.citation.number }}{{ c.citation.kind }}</strong> — Source: {{ c.source }} — Confidence: {{ c.confidence }}
        <pre>{{ c.citation.raw if c.citation.raw else "" }}</pre>
      </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if claims %}
  <div class="section">
    <h2>Claims Evolution</h2>
    {% for version in claims %}
      <div class="event">
        <strong>{{ version.version }}</strong>
        {% for claim in version.claims %}
          <div><strong>Claim {{ claim.id }}:</strong> <pre>{{ claim.text }}</pre></div>
        {% endfor %}
      </div>
    {% endfor %}
  </div>
  {% endif %}
</body>
</html>
"""

def build_html_report(context: Dict) -> str:
    tpl = Template(REPORT_HTML_TEMPLATE)
    return tpl.render(**context)

def export_pdf_from_html(html: str, out_pdf_path: str) -> Tuple[bool, str]:
    """
    Try to export PDF via wkhtmltopdf (pdfkit). If wkhtmltopdf is not available,
    write the HTML to a .html fallback file and return (False, html_path).
    Returns (success_bool, fallback_html_path_or_empty).
    """
    try:
        import pdfkit
    except Exception:
        # pdfkit not installed - write fallback html
        fallback = out_pdf_path.replace(".pdf", ".html")
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(html)
        return False, fallback

    options = {"enable-local-file-access": None}
    # allow explicit path via env var if user set it
    wk_path = os.environ.get("WKHTMLTOPDF_PATH")
    config = None
    try:
        if wk_path:
            config = pdfkit.configuration(wkhtmltopdf=wk_path)
        else:
            # attempt default configuration; this will raise if not found
            config = pdfkit.configuration()
    except Exception:
        # wkhtmltopdf not found - write fallback HTML
        fallback = out_pdf_path.replace(".pdf", ".html")
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(html)
        return False, fallback

    try:
        # generate PDF
        pdfkit.from_string(html, out_pdf_path, options=options, configuration=config)
        return True, ""
    except Exception:
        # on failure, write fallback HTML
        fallback = out_pdf_path.replace(".pdf", ".html")
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(html)
        return False, fallback