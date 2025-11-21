import os
from typing import Dict, List, Tuple
from jinja2 import Template
import markdown
import re
import json

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
    # remove stray paragraph wrappers
    cleaned = re.sub(r'<\s*p\s*>\s*<\s*s\s*>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\s*/\s*s\s*>\s*<\s*/\s*p\s*>', '', cleaned, flags=re.IGNORECASE)
    # remove any remaining isolated <s> or </s>
    cleaned = re.sub(r'<\s*s\s*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\s*/\s*s\s*>', '', cleaned, flags=re.IGNORECASE)
    # strip leading/trailing paragraph wrappers
    cleaned = re.sub(r'^<\s*p\s*>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*<\s*/\s*p\s*>$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_executive_summary(summary: str) -> str:
    """Clean LLM artifacts then convert markdown/plain text to HTML"""
    cleaned = sanitize_llm_output(summary or "")
    return markdown.markdown(cleaned, extensions=['extra'])


def render_to_html(text: str) -> str:
    """Convert plain text to safe HTML with line breaks and escaping."""
    if not text:
        return ""
    # Escape HTML special characters
    text = (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
    # Convert line breaks to <br>
    text = text.replace("\n", "<br>")
    return f"<p>{text}</p>"


def render_token_links(html_text: str, token_index: dict) -> str:
    """
    Convert [EVT#1], [CIT#1] etc. into clickable HTML elements with data-attributes.
    Tokens remain visible but are now interactive deep-links to source data.
    """
    def replace_token(match):
        token = match.group(1)  # e.g., "EVT#1"
        if token in token_index:
            meta = token_index[token]
            path = meta.get("path", "")
            # Create clickable span with data attribute
            return (
                f"<span class='token-ref' data-token='{token}' data-path='{path}' "
                f"title='Click to view source: {path}'>[{token}]</span>"
            )
        # Token not found, keep as-is
        return match.group(0)
    
    # Replace all token patterns with clickable elements
    return re.sub(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', replace_token, html_text)


def add_token_click_handler(html: str) -> str:
    """Add JavaScript to handle token clicks and deep-linking."""
    js_handler = """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        const tokens = document.querySelectorAll('.token-ref');
        tokens.forEach(token => {
            token.addEventListener('click', function(e) {
                e.preventDefault();
                const path = this.getAttribute('data-path');
                const tokenName = this.getAttribute('data-token');
                // Emit custom event that parent app can listen to
                const event = new CustomEvent('token-clicked', {
                    detail: { token: tokenName, path: path }
                });
                window.dispatchEvent(event);
                // Log for debugging
                console.log('Token clicked:', tokenName, 'Path:', path);
            });
        });
    });
    </script>
    """
    # Inject before closing body tag
    return html.replace("</body>", js_handler + "</body>")


REPORT_HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8"/>
    <style>
        body { 
            font-family: 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
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
        .coverage-banner {
            background-color: #f0f0f0;
            border-left: 4px solid #0066cc;
            padding: 10px;
            margin: 15px 0;
            font-size: 0.95em;
            font-weight: bold;
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
        .section h3 {
            color: #2c5282;
            margin-top: 1.5em;
            font-size: 1.1em;
        }
        .analysis {
            margin: 1em 0;
            line-height: 1.8;
        }
        .bullet-list {
            list-style: none;
            padding-left: 0;
        }
        .bullet-list li {
            margin: 0.5em 0;
            padding-left: 1.5em;
            text-indent: -1.5em;
        }
        .bullet-list li:before {
            content: "• ";
            font-weight: bold;
            color: #0066cc;
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
        .token-ref {
            background-color: #e8f4f8;
            color: #0066cc;
            padding: 2px 6px;
            border-radius: 3px;
            cursor: pointer;
            font-weight: bold;
            text-decoration: underline;
            display: inline-block;
        }
        .token-ref:hover {
            background-color: #cce5ff;
        }
        .warning-banner {
            background-color: #fff3cd;
            border: 2px solid #ffc107;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            color: #856404;
        }
        .top-events {
            background: #f0f8ff;
            padding: 1em;
            border-left: 4px solid #0066cc;
            border-radius: 4px;
            margin: 1em 0;
        }
        .ranked-citations {
            background: #f0fff4;
            padding: 1em;
            border-left: 4px solid #28a745;
            border-radius: 4px;
            margin: 1em 0;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Patent Analysis Report - {{ patent_number }}</h1>
        <p class="meta">Generated: {{ generated_at }}</p>
    </div>

    <div class="coverage-banner">
        Coverage: events={{ coverage.events_present }}, claims={{ coverage.claims_present }}, citations={{ coverage.citations_present }}, designations={{ coverage.designations_present }}. Missing items are omitted.
    </div>

    <div class="section">
        <h2>Executive Summary</h2>
        <div class="analysis">{{ analyses['Executive Summary'] }}</div>
    </div>

    <div class="section">
        <h2>Timeline Analysis</h2>
        <div class="analysis">{{ analyses['Timeline Analysis'] }}</div>
        
        <h3>Key Prosecution Events</h3>
        {% for event in events %}
        <div class="event">
            <span class="event-date">{{ event.date }}</span>
            <br/>
            <span class="event-code">{{ event.code }}</span>: {{ event.desc }}
        </div>
        {% endfor %}
    </div>

    <div class="section">
        <h2>Prior Art Analysis</h2>
        <div class="analysis">{{ analyses['Prior Art Analysis'] }}</div>
    </div>

    <div class="section">
        <h2>Evidence-Linked Recommendations</h2>
        <div class="analysis">{{ analyses.get('Evidence-Linked Recommendations', 'No recommendations available.') }}</div>
    </div>
</body>
</html>
"""


def build_html_report(context):
    """
    Build HTML report with token-to-path deep-linking support.
    Receives context dict with:
    - patent_number, generated_at, patent_details
    - analyses (dict keyed by section title)
    - events, citations, claims, coverage
    - token_index (dict of token->metadata)
    """
    # Extract context values
    patent_number = context.get("patent_number", "UNKNOWN")
    generated_at = context.get("generated_at", "")
    analyses = context.get("analyses", {})
    events = context.get("events", [])
    coverage = context.get("coverage", {})
    token_index = context.get("token_index", {})
    
    # Render template
    template = Template(REPORT_HTML_TEMPLATE)
    html_output = template.render(
        patent_number=patent_number,
        generated_at=generated_at,
        analyses=analyses,
        events=events,
        coverage=coverage
    )
    
    # Apply token deep-linking (convert [EVT#1] to clickable spans)
    html_output = render_token_links(html_output, token_index)
    
    # Add JavaScript handler for token clicks
    html_output = add_token_click_handler(html_output)
    
    # Inject hidden token-index data block for reference
    token_data_script = (
        "<script type='application/json' id='token-index'>\n"
        + json.dumps(token_index, indent=2)
        + "\n</script>\n"
    )
    html_output = html_output.replace("</body>", token_data_script + "</body>")
    
    return html_output


def render_top_pivotal_events(events: list) -> str:
    """Render top 3 pivotal events with tokens instead of JSON paths."""
    priority_map = {
        "scope_narrowed": 0,
        "grant": 1,
        "opposition": 1,
        "term_changed": 2,
        "designation_recorded": 3,
        "unknown": 4
    }
    
    def score(e):
        effs = e.get("effects", []) or ["unknown"]
        s = min([priority_map.get(x, priority_map["unknown"]) for x in effs])
        date = e.get("date") or ""
        return (s, "" if not date else date)
    
    top = sorted(events, key=score)[:3]
    if not top:
        return "(Omitted pending source)"
    
    lines = []
    for idx, ev in enumerate(top, 1):
        date = ev.get("date") or "(date unknown)"
        code = ev.get("code") or "(code)"
        effects_str = ",".join(ev.get("effects", ["unknown"]))
        token = f"EVT#{idx}"
        lines.append(f"{date} – {code} – {effects_str} – [{token}]")
    
    return "\n".join(lines)


def render_ranked_citations(citations: list) -> str:
    """Render up to 5 citations with tokens instead of JSON paths."""
    kind_priority = {
        "examiner": 0,
        "legal": 1,
        "applicant": 2,
        "bibliographic": 3
    }
    
    def score(c):
        k = (c.get("kind") or "bibliographic").lower()
        p = kind_priority.get(k, 99)
        rel = c.get("relevance")
        rel_score = -float(rel) if rel is not None else 0.0
        return (p, rel_score)
    
    top = sorted(citations, key=score)[:5]
    if not top:
        return "(Omitted pending source)"
    
    lines = []
    for idx, c in enumerate(top, 1):
        cid = c.get("id") or c.get("citation_id") or f"CIT:{idx}"
        k = (c.get("kind") or "bibliographic").lower()
        risk = "novelty" if k == "examiner" else ("obviousness" if k == "legal" else "screening-only")
        limitations = c.get("limitations") or "(Omitted pending source)"
        workaround = c.get("workaround") or "(Omitted pending source)"
        token = f"CIT#{idx}"
        lines.append(f"{idx}. {cid} – {risk} – {limitations} – {workaround} – [{token}]")
    
    return "\n".join(lines)


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