import re
from typing import Dict

def enforce_json_citations(text: str) -> str:
    """
    Append [JSON:/missing] to any sentence containing letters that lacks a [JSON:/ token.
    """
    if not text:
        return text
    sentences = re.split(r'(?<=[\.!?])\s+', text.strip())
    out = []
    for s in sentences:
        if not re.search(r'[A-Za-z]', s):
            out.append(s)
            continue
        if '[JSON:/' in s:
            out.append(s)
        else:
            out.append(f"{s} [JSON:/missing]")
    return " ".join(out)

def drop_uncited_sentences(text: str) -> str:
    """
    Remove sentences that end with [JSON:/missing].
    """
    if not text:
        return text
    sentences = re.split(r'(?<=[\.!?])\s+', text.strip())
    kept = [s for s in sentences if not s.rstrip().endswith('[JSON:/missing]')]
    return " ".join(kept)

def sanitize_ep_language(text: str, jurisdiction: str = "EP") -> str:
    """
    Replace 'estoppel' with 'prosecution interpretation' for EP jurisdiction.
    """
    if not text:
        return text
    if jurisdiction and jurisdiction.upper() == "EP":
        text = re.sub(r'\bestoppel\b', 'prosecution interpretation', text, flags=re.IGNORECASE)
    return text

def inject_coverage_header(html: str, coverage: Dict) -> str:
    """
    Prepend a coverage line inside <body> or at top if <body> missing.
    """
    if not html:
        return html
    ev = coverage.get("events_present", 0)
    cl = coverage.get("claims_present", 0)
    ci = coverage.get("citations_present", 0)
    de = coverage.get("designations_present", 0)
    header = f"<p><strong>Coverage:</strong> events={ev}, claims={cl}, citations={ci}, designations={de}. Missing items are omitted.</p>\n"
    m = re.search(r'(<body[^>]*>)', html, flags=re.IGNORECASE)
    if m:
        insert_at = m.end()
        return html[:insert_at] + "\n" + header + html[insert_at:]
    else:
        return header + html

# legacy-named wrappers expected by app.py
def require_json_tokens(text: str) -> str:
    return enforce_json_citations(text)

def prepend_coverage_header(text: str, extract: Dict) -> str:
    cov = extract.get("coverage", {}) if isinstance(extract, dict) else {}
    ev = cov.get("events_present", 0)
    cl = cov.get("claims_present", 0)
    ci = cov.get("citations_present", 0)
    de = cov.get("designations_present", 0)
    header = f"Coverage: events={ev}, claims={cl}, citations={ci}, designations={de}. Missing items are omitted.\n\n"
    return header + (text or "")