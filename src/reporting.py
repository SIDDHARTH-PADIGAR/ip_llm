import os
from typing import Dict, List, Tuple
from jinja2 import Template
import markdown
import re

def sanitize_llm_output(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    # remove BOT markers
    cleaned = re.sub(r'\[\/?BOT\]', '', cleaned, flags=re.IGNORECASE)
    # remove explicit <s> / </s> or <del> tags (anywhere)
    cleaned = re.sub(r'<\s*(s|del)\b[^>]*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\s*/\s*(s|del)\s*>', '', cleaned, flags=re.IGNORECASE)
    # remove markdown strike patterns ~~like this~~
    cleaned = re.sub(r'~~', '', cleaned)
    # remove stray paragraph wrappers that may surround an <s> e.g. <p><s>...</s></p>
    cleaned = re.sub(r'<\s*p\s*>\s*<\s*s\s*>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\s*/\s*s\s*>\s*<\s*/\s*p\s*>', '', cleaned, flags=re.IGNORECASE)
    # also remove any remaining isolated <s> or </s>
    cleaned = re.sub(r'<\s*s\s*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\s*/\s*s\s*>', '', cleaned, flags=re.IGNORECASE)
    # strip leading/trailing paragraph wrappers
    cleaned = re.sub(r'^<\s*p\s*>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*<\s*/\s*p\s*>$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_executive_summary(summary: str) -> str:
    """Clean LLM artifacts then convert markdown/plain text to HTML"""
    cleaned = sanitize_llm_output(summary or "")
    # convert markdown (if any) to HTML; keep it simple
    return markdown.markdown(cleaned, extensions=['extra'])
# Simple HTML template (expand as needed)
# In reporting.py, update REPORT_HTML_TEMPLATE:

REPORT_HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8"/>
    <style>
        body { 
            font-family: 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            margin: 40px;
            color: #2d2d2d;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            border-bottom: 2px solid #2c5282;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }
        .meta {
            color: #4a5568;
            font-size: 0.9em;
        }
        .section {
            margin: 2em 0;
            padding: 2em;
            background: #fff;
            border: 1px solid #e2e8f0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border-radius: 8px;
        }
        .section h2 {
            color: #2c5282;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 0.5em;
            margin-bottom: 1em;
        }
        .analysis {
            margin: 1em 0;
            line-height: 1.8;
            white-space: pre-line;
        }
        .event {
            margin: 1em 0;
            padding: 1em;
            background: #f7fafc;
            border-left: 4px solid #4299e1;
            border-radius: 4px;
        }
        .event-date {
            color: #2c5282;
            font-weight: 600;
        }
        .event-code {
            color: #4a5568;
            font-family: monospace;
        }
        .key-point {
            background: #ebf8ff;
            padding: 0.5em 1em;
            margin: 1em 0;
            border-radius: 4px;
        }
        .recommendations {
            background: #f0fff4;
            padding: 1em;
            margin: 1em 0;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Patent Analysis Report - {{ patent_number }}</h1>
        <p class="meta">Generated: {{ generated_at }}</p>
    </div>

    <div class="section">
        <h2>Executive Summary</h2>
        <div class="analysis">{{ analyses.executive_summary }}</div>
    </div>

    <div class="section">
        <h2>Timeline Analysis</h2>
        <div class="analysis">{{ analyses.timeline_analysis }}</div>
        
        <h3>Key Prosecution Events</h3>
        {% for event in events %}
        <div class="event">
            <span class="event-date">{{ event.date }}</span>
            <br/>
            <span class="event-code">{{ event.code }}</span>: {{ event.desc }}
        </div>
        {% endfor %}
    </div>

    {% if analyses.prior_art_analysis %}
    <div class="section">
        <h2>Prior Art Analysis</h2>
        <div class="analysis">{{ analyses.prior_art_analysis }}</div>
    </div>
    {% endif %}

    {% if analyses.claims_analysis %}
    <div class="section">
        <h2>Claims Analysis</h2>
        <div class="analysis">{{ analyses.claims_analysis }}</div>
    </div>
    {% endif %}
</body>
</html>
"""

def build_html_report(context: Dict) -> str:
    tpl = Template(REPORT_HTML_TEMPLATE)
    analyses = context.get("analyses", {}) or {}
    # sanitize + format exec summary
    analyses["executive_summary"] = format_executive_summary(analyses.get("executive_summary","") or "")
    # sanitize other free-text LLM outputs (keep as plain text)
    analyses["timeline_analysis"] = sanitize_llm_output(analyses.get("timeline_analysis","") or "")
    analyses["prior_art_analysis"] = sanitize_llm_output(analyses.get("prior_art_analysis","") or "")
    analyses["claims_analysis"] = sanitize_llm_output(analyses.get("claims_analysis","") or "")
    context["analyses"] = analyses
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