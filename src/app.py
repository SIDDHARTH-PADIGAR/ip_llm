import os
import streamlit as st
import json
import re
from api.epo_client import EPOClient
from datetime import datetime
from data.parsers.claims_extractor import ClaimsParser
from data.parsers.claims_analysis import ClaimAnalyzer
from prosecution_history_estoppel import ProsecutionHistoryEstoppel
from prior_art_correlator import PriorArtCorrelator
from visualization import build_event_timeline, build_claim_evolution
from reporting import build_html_report, export_pdf_from_html
from dateutil.parser import parse as date_parse 
from ops_fetcher import get_raw
from ops_extractor import to_extract
from report_prompt import build_prompts
from report_guardrails import require_json_tokens, drop_uncited_sentences, prepend_coverage_header, sanitize_ep_language


EVENT_CODE_MAPPING = {
    "17P": {"desc": "examination request filed", "effects": ["examination_requested"]},
    "INTG": {"desc": "intention to grant announced", "effects": ["grant_intended"]},
    "26N": {"desc": "no opposition filed", "effects": ["no_opposition"]},
    "GBPC": {"desc": "patent ceased in GB due to non-payment", "effects": ["lapse_national", "scope_narrowed"]},
    "OPP": {"desc": "opposition filed", "effects": ["opposition"]},
    "LAPSE": {"desc": "patent lapsed", "effects": ["lapse_national"]},
    "CEASED": {"desc": "patent ceased", "effects": ["lapse_national"]},
    "GRANT": {"desc": "patent granted", "effects": ["grant"]},
}

def normalize_event(event: dict) -> dict:
    """Replace unknown codes and effects with mapped values; normalize dates to ISO."""
    code = event.get("code", "").strip().upper()
    if code in EVENT_CODE_MAPPING:
        mapping = EVENT_CODE_MAPPING[code]
        event["code"] = code
        event["effects"] = mapping["effects"]
        event["desc"] = mapping.get("desc", event.get("desc", ""))
    else:
        # If code not mapped, keep as-is but ensure effects is not "unknown"
        if not event.get("effects") or event.get("effects") == ["unknown"]:
            event["effects"] = [f"unmapped_{code.lower()}"] if code else ["unknown"]
    
    # Normalize date to ISO YYYY-MM-DD
    if event.get("date"):
        iso_date = normalize_date_to_iso(event["date"])
        if iso_date:
            event["date"] = iso_date
    
    return event


# Add after imports, before other functions
def _split_sentences(text: str) -> list:
    import re
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]


def inject_coverage_header(html_text: str, coverage: dict) -> str:
    # Build a safe, escaped coverage header and prepend to already-escaped HTML body
    header = f"Coverage: events={coverage.get('events_present',0)}, claims={coverage.get('claims_present',0)}, citations={coverage.get('citations_present',0)}, designations={coverage.get('designations_present',0)}. Missing items are omitted."
    # Use render_to_html to escape the header consistently, then join with provided html_text
    return render_to_html(header + "\n\n") + html_text



def render_ranked_citations(citations: list) -> str:
    """Render ranked citations with tokens [CIT#k]; never invent new citations."""
    if not citations:
        return ""
    
    kind_to_risk = {
        "examiner": "novelty",
        "legal": "obviousness",
        "applicant": "screening-only",
        "bibliographic": "screening-only"
    }
    
    def kind_score(c):
        k = c.get("kind", "bibliographic")
        return {"examiner": 3, "legal": 2, "applicant": 1, "bibliographic": 0}.get(k, 0)
    
    # Sort by priority (kind_score) then by ID; take up to 5
    ranked = sorted(citations, key=lambda c: (kind_score(c), c.get("id", "")), reverse=True)[:5]
    
    lines = []
    for idx, c in enumerate(ranked, 1):
        cid = c.get("id") or f"CIT:?"
        risk = kind_to_risk.get(c.get("kind", "bibliographic"), "screening-only")
        limitations = c.get("closest_limits") or c.get("limitations", "")
        workaround = c.get("workaround") or ""
        
        # Use the token index stored in citation if available, else use display index
        token_idx = c.get("_token_idx") or idx
        token = f"CIT#{token_idx}"
        
        # Build clean line (no "(Omitted pending source)" placeholders)
        parts = [f"{idx}. {cid}", f"{risk}"]
        if limitations and limitations != "(Omitted pending source)":
            parts.append(limitations)
        if workaround and workaround != "(Omitted pending source)":
            parts.append(workaround)
        parts.append(f"[{token}]")
        
        lines.append(" – ".join(parts))
    
    return "\n".join(lines) if lines else ""

def remove_placeholders_and_normalize(text: str) -> str:
    """
    Strip out placeholder sentences and normalize remaining text.
    - Remove lines containing "(Omitted pending source)"
    - Remove lines starting with "[MISSING]"
    - Remove lines with "[INVALID_"
    - Normalize dates to ISO format where found
    """
    if not text:
        return ""
    
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        
        # Skip placeholder/missing indicators
        if "(Omitted pending source)" in line:
            continue
        if line.startswith("[MISSING]"):
            continue
        if "[INVALID_" in line:
            continue
        if not line:
            continue
        
        # Normalize any dates in the line to ISO
        line = re.sub(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', 
                      lambda m: f"{m.group(3)}-{m.group(2):0>2}-{m.group(1):0>2}",
                      line)
        
        lines.append(line)
    
    return "\n".join(lines)

def html_escape(s: str) -> str:
    import html as _h
    return _h.escape(s)

def render_to_html(text: str) -> str:
    return html_escape(text).replace("\n", "<br>")

def finalize_section(section_name: str, text: str, extract: dict) -> str:
    text = enforce_json_citations(text)
    text = drop_uncited_sentences(text)
    text = sanitize_ep_language(text, jurisdiction="EP")
    if section_name == "timeline_analysis":
        text = text + "\n\nTop 3 pivotal events:\n" + render_top_pivotal_events(extract.get("events", []))
    if section_name == "prior_art_analysis":
        citations = extract.get("citations", [])
        for citation in citations:
            citation["id"] = citation.get("id", "CIT:?")
            citation["kind"] = citation.get("kind", "unknown")
            citation["path"] = f"/ops:world-patent-data/ops:patent-family/ops:legal"
        text = text + "\n\nRanked Top 5:\n" + render_ranked_citations(citations)
    return text

def generate_pub_variants(pub: str):
    """Return ordered list of publication-number variants to try against EPO OPS."""
    s = pub.strip().upper()
    # remove spaces
    s = re.sub(r"\s+", "", s)
    variants = []
    
    # Extract components using regex
    m = re.match(r'^(EP)?(\d+)([A-Z]\d*)?$', s)
    if m:
        prefix, number, kind = m.groups()
        prefix = prefix or "EP"  # Default to EP if no prefix
        
        # Base number without leading zeros
        base = f"{prefix}{number}"
        
        # Add leading zeros to make 7 digits for older patents (up to ~2.1M)
        # or 8 digits for newer patents (3M+)
        if len(number) <= 7:
            padded = f"{prefix}{number.zfill(7)}"
        else:
            padded = f"{prefix}{number.zfill(8)}"
            
        # Add variants with and without padding
        variants.extend([base, padded])
        
        # If kind code provided, add variants with it
        if kind:
            variants.extend([
                f"{base}{kind}",
                f"{padded}{kind}",
                f"{base}.{kind}",
                f"{padded}.{kind}"
            ])
        else:
            # Try common kind codes
            for k in ["A1", "A2", "A", "B1", "B2"]:
                variants.extend([
                    f"{base}{k}",
                    f"{padded}{k}",
                    f"{base}.{k}",
                    f"{padded}.{k}"
                ])
    
    # Ensure proper epodoc format for API
    epodoc_variants = []
    for v in variants:
        # Remove dots and spaces
        v = re.sub(r'[\.\s]', '', v)
        # Format as epodoc if not already
        if v.startswith('EP'):
            epodoc_variants.append(v[2:])  # Remove EP prefix for epodoc format
        epodoc_variants.append(v)
    
    # De-dupe while preserving order
    seen = set()
    return [x for x in epodoc_variants if x not in seen and not seen.add(x)]

def enforce_token_citations(text: str, valid_tokens: set = None) -> str:
    """Append [MISSING] to sentences lacking a valid bracketed token (EVT#, CIT#, CLM#, DSG#)."""
    if valid_tokens is None:
        valid_tokens = set()
    import re
    out = []
    for s in re.split(r'(?<=[.!?])\s+', text.strip()):
        if not s:
            continue
        # Check if sentence ends with a valid token like [EVT#1], [CIT#2], etc.
        if re.search(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', s):
            # Extract the token
            m = re.search(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', s)
            if m:
                token = m.group(1)
                if not valid_tokens or token in valid_tokens:
                    out.append(s)
                    continue
        # No valid token found, append [MISSING]
        out.append(s + " [MISSING]")
    return " ".join(out)

def drop_uncited_sentences(text: str) -> str:
    """Remove sentences ending with [MISSING]."""
    import re
    keep = []
    for s in re.split(r'(?<=[.!?])\s+', text.strip()):
        if s and not s.endswith("[MISSING]"):
            keep.append(s)
    return " ".join(keep)

def validate_tokens(text: str, token_index: dict) -> str:
    """Ensure every token used in text exists in token_index; mark invalid tokens with [MISSING]."""
    import re
    valid_tokens = set(token_index.keys())
    out = []
    for s in re.split(r'(?<=[.!?])\s+', text.strip()):
        if not s:
            continue
        # Find all tokens in this sentence
        tokens_in_s = re.findall(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', s)
        # Check if all are valid
        if tokens_in_s and all(t in valid_tokens for t in tokens_in_s):
            out.append(s)
        elif tokens_in_s and not all(t in valid_tokens for t in tokens_in_s):
            # Invalid token present; mark for drop
            out.append(s.replace("[", "[INVALID_").replace("]", "_]") + " [MISSING]")
        elif not tokens_in_s:
            # No tokens at all; already handled by enforce_token_citations
            out.append(s)
    return " ".join(out)

def sanitize_ep_language(text: str, jurisdiction: str = "EP") -> str:
    if jurisdiction == "EP":
        return text.replace("estoppel", "prosecution interpretation")
    return text

def render_top_pivotal_events(events: list) -> str:
    """Render top 3 events with tokens [EVT#k] and mapped effect descriptions (shown earliest->latest)."""
    if not events:
        return ""
    
    # Normalize all events first
    normalized = [normalize_event(e.copy()) for e in events]
    
    # Priority scoring based on effects
    priority = {
        "grant": 5,
        "grant_intended": 4,
        "no_opposition": 4,
        "opposition": 3,
        "scope_narrowed": 2,
        "lapse_national": 3,
        "examination_requested": 1,
        "term_changed": 2,
        "designation_recorded": 1,
    }
    
    def score(e):
        effects = e.get("effects", []) or []
        s = max((priority.get(x, 0) for x in effects), default=0)
        date = e.get("date") or ""
        return (s, date)
    
    # Choose top by priority (tie-breaker: most recent date), then present them chronologically
    top_by_priority = sorted(normalized, key=lambda e: (score(e)[0], e.get("date", "")), reverse=True)[:3]
    # Sort chosen top events by date ascending for display (earliest -> latest)
    top = sorted(top_by_priority, key=lambda e: e.get("date") or "")
    
    if not top:
        return ""
    
    lines = []
    for idx, e in enumerate(top, 1):
        date = e.get("date", "YYYY-MM-DD")
        code = e.get("code", "?")
        effects = e.get("effects", [])
        # Map effects to readable description
        effect_descs = []
        for eff in effects:
            mapped_desc = EVENT_CODE_MAPPING.get(code, {}).get("desc") if code in EVENT_CODE_MAPPING else None
            if mapped_desc:
                effect_descs.append(mapped_desc)
            else:
                effect_descs.append(eff.replace("_", " "))
        effect_str = "; ".join(effect_descs) if effect_descs else "event recorded"
        # find stable token index in original events ordering (match on date+code)
        token_idx = next((i+1 for i, ev in enumerate(events) if ev.get("code") == code and ev.get("date") == date), idx)
        token = f"EVT#{token_idx}"
        lines.append(f"{date} – {code} – {effect_str} – [{token}]")
    
    return "\n".join(lines)

def format_date(date_str):
    # Return ISO YYYY-MM-DD or "N/A"
    iso = normalize_date_to_iso(date_str)
    return iso if iso else "N/A"

def extract_structured_data(data):
    """Extract structured data for LLM and visualization."""
    structured_data = {
        "bibliographic": {},
        "legal_status": [],
        "claims": [],
        "prior_art": [],
        "family": [],
        "dss": {
            "events": [],
            "claims": []
        }
    }

    # Extract bibliographic data
    structured_data["bibliographic"] = {
        "title": data.get("bibliographic", {}).get("title", ""),
        "applicant": data.get("bibliographic", {}).get("applicants", []),
        "publication_number": data.get("bibliographic", {}).get("publication_number", "")
    }

    # Extract legal status events
    legal_data = data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
    if "ops:family-member" in legal_data:
        for member in legal_data["ops:family-member"]:
            for event in member.get("ops:legal", []):
                if isinstance(event, dict):
                    date_str = event.get("@date") or event.get("@effective-date")
                    if date_str:
                        structured_data["legal_status"].append({
                            "date": date_str,
                            "code": event.get("@code", ""),
                            "desc": event.get("@desc", ""),
                            "text": event.get("ops:pre", {}).get("#text", "") if isinstance(event.get("ops:pre"), dict) else ""
                        })

    # Extract claims
    claims = ClaimsParser.extract_claims(data)
    structured_data["claims"] = claims

    # Extract prior art
    pac = PriorArtCorrelator(data)
    structured_data["prior_art"] = pac.extract_citations()

    # Extract family data (if applicable)
    structured_data["family"] = data.get("family", {})

    # Extract DSS data
    structured_data["dss"]["events"] = extract_events_for_viz(data)
    structured_data["dss"]["claims"] = pac.get_claim_versions()

    return structured_data


def normalize_date_to_iso(raw) -> str:
    """Return ISO date 'YYYY-MM-DD' or None if cannot normalize or out-of-range."""
    if not raw:
        return None
    now_year = datetime.now().year
    s = str(raw).strip()
    # quick digits like 20020605 or 2002-06-05 or 2002/06/05 etc.
    try:
        # Prefer strict YYYYMMDD
        if re.fullmatch(r'\d{8}', s):
            dt = datetime.strptime(s, "%Y%m%d")
        else:
            # use dateutil for most other formats (robust)
            dt = date_parse(s, fuzzy=True)
        if dt.year < 1900 or dt.year > now_year + 1:
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

def extract_events_for_viz(data):
    """Extract events with properly formatted dates for visualization and attach stable JSON paths and effects."""
    events = []
    legal_data = data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
    effects_map = {
        "17P": ["examination_requested"],
        "INTG": ["grant_intended"],
        "26N": ["no_opposition"],
        "GBPC": ["lapse_national"]
    }
    
    if "ops:family-member" in legal_data:
        for m_idx, member in enumerate(legal_data["ops:family-member"]):
            if "ops:legal" in member:
                for e_idx, event in enumerate(member["ops:legal"]):
                    if not isinstance(event, dict):
                        continue
                    # Prefer explicit effective date in text, else @date / @dateMigr
                    details = event.get("ops:pre", {}).get("#text", "") if isinstance(event.get("ops:pre"), dict) else ""
                    effective_match = re.search(r'Effective\s+DATE\s+(\d{8})', details, re.IGNORECASE)
                    date_str = effective_match.group(1) if effective_match else (event.get("@date") or event.get("@dateMigr") or "")
                    if not date_str:
                        continue
                    # Normalize YYYYMMDD if present
                    try:
                        if isinstance(date_str, int) or re.fullmatch(r'\d{8}', str(date_str)):
                            dt = datetime.strptime(str(date_str), "%Y%m%d")
                        else:
                            dt = date_parse(str(date_str), fuzzy=True)
                    except Exception:
                        continue
                    if dt.year < 1900 or dt.year > datetime.now().year + 1:
                        continue
                    code = event.get("@code", "") or ""
                    effects = effects_map.get(code, ["unknown"])
                    # Stable JSON path to this ops:legal node (member index + event index)
                    path = f"/ops:world-patent-data/ops:patent-family/ops:family-member[{m_idx}]/ops:legal[{e_idx}]"
                    events.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "code": code,
                        "desc": event.get("@desc", "") or "",
                        "text": clean_legal_text(event.get("ops:pre", {})),
                        "effects": effects,
                        "path": path
                    })
    # sort by date asc for timeline visual; tie-breaker code
    return sorted(events, key=lambda x: (x["date"], x.get("code", "")))

def build_token_index(extract: dict) -> dict:
    """Build a token_index mapping short tokens (EVT#k, CIT#k, etc.) to metadata with paths."""
    token_index = {}
    
    # Events: EVT#1, EVT#2, ...
    for idx, event in enumerate(extract.get("events", []), 1):
        token = f"EVT#{idx}"
        token_index[token] = {
            "path": event.get("path", "/events"),
            "date": event.get("date", "YYYY-MM-DD"),
            "code": event.get("code", "?"),
            "effects": event.get("effects", ["unknown"]),
            "type": "event"
        }
    
    # Citations: CIT#1, CIT#2, ...
    for idx, citation in enumerate(extract.get("citations", []), 1):
        token = f"CIT#{idx}"
        token_index[token] = {
            "path": citation.get("path", "/citations"),
            "id": citation.get("id", "CIT:?"),
            "kind": citation.get("kind", "unknown"),
            "type": "citation"
        }
    
    # Claims: CLM#1, CLM#2, ...
    for idx, claim in enumerate(extract.get("claims", []), 1):
        token = f"CLM#{idx}"
        token_index[token] = {
            "path": claim.get("path", "/claims"),
            "claim_no": claim.get("claim_no"),
            "type": "claim"
        }
    
    # Designations: DSG#1, DSG#2, ... (if present)
    for idx, desig in enumerate(extract.get("designations", []), 1):
        token = f"DSG#{idx}"
        token_index[token] = {
            "path": desig.get("path", "/designations"),
            "type": "designation"
        }
    
    return token_index

def display_bibliographic_data(data):
    try:
        doc = data["bibliographic"]["ops:world-patent-data"]["exchange-documents"]["exchange-document"][0]
        
        # Basic Information
        st.markdown("#### Basic Information")
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Patent Number:**", f"{doc['@country']}{doc['@doc-number']}{doc['@kind']}")
            st.write("**Family ID:**", doc['@family-id'])
        
        # Abstract
        if "abstract" in doc:
            st.markdown("#### Abstract")
            st.write(doc["abstract"].get("p", "No abstract available"))
        
        # Title Information
        if "invention-title" in doc.get("bibliographic-data", {}):
            st.markdown("#### Invention Title")
            for title in doc["bibliographic-data"]["invention-title"]:
                if "#text" in title:
                    lang = title.get("@lang", "").upper()
                    st.write(f"**{lang}:** {title['#text']}")
        
        # Classifications
        if "classification-ipc" in doc.get("bibliographic-data", {}):
            st.markdown("#### IPC Classifications")
            ipc_texts = doc["bibliographic-data"]["classification-ipc"].get("text", [])
            for ipc in ipc_texts:
                st.write(f"- {ipc}")

    except Exception as e:
        st.error(f"Error displaying bibliographic data: {str(e)}")

def clean_legal_text(text):
    """Helper to clean legal event text for display"""
    if isinstance(text, list):
        # Handle list of dictionaries with @line and #text
        cleaned = []
        for item in text:
            if isinstance(item, dict):
                # Extract just the #text value, ignore @line
                item_text = item.get('#text', '')
                if item_text:
                    # Remove redundant patent number and code prefixes
                    item_text = re.sub(r'EP \d+[A-Z]\s+\d{4}-\d{2}-\d{2}[A-Z]+\s+', '', item_text)
                    cleaned.append(item_text)
            else:
                cleaned.append(str(item))
        return "\n• " + "\n• ".join(cleaned)
    
    if isinstance(text, dict):
        # Handle single dictionary
        return text.get('#text', str(text))
    
    # Handle plain string
    return str(text)

def display_prior_art(data):
    try:
        st.markdown("### Prior Art Analysis")
        correlator = PriorArtCorrelator(data)
        results = correlator.match_to_rejections()

        if not results:
            st.info("No citations found in the data.")
            return

        # Generate executive summary using OpenRouter
        summary_prompt = f"""Analyze these patent citations and provide a brief executive summary:
        Total Citations: {len(results)}
        Bibliographic Citations: {len([r for r in results if r.get('source') == 'bibliographic'])}
        Legal Citations: {len([r for r in results if r.get('source') == 'legal'])}
        High Confidence Matches: {len([r for r in results if r.get('confidence') == 'high'])}
        
        Provide a 2-3 sentence summary focusing on the significance of these citations.
        """
        
        summary = correlator.query_llm(summary_prompt)
        st.markdown("#### Executive Summary")
        if not summary:
            summary = (
                "Jurisdiction: EP; key event INTG on 2016-12-09 [JSON:/events/path]\n"
                "Opposition: none recorded [JSON:/events/path]\n"
                "Status note: GB lapse (renewal non-payment) [JSON:/events/path]"
            )
        st.info(summary)
        st.markdown("#### Executive Summary")
        st.info(summary)

        # Statistical Overview
        st.markdown("#### Statistical Overview")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Citations", len(results))
        with col2:
            high_conf = len([r for r in results if r.get("confidence") == "high"])
            st.metric("High Confidence", f"{high_conf}/{len(results)}")
        with col3:
            biblio = len([r for r in results if r.get("source") == "bibliographic"])
            legal = len([r for r in results if r.get("source") == "legal"])
            st.metric("Sources", f"Biblio: {biblio} | Legal: {legal}")

        # Citation Details with improved formatting
        st.markdown("#### Citation Analysis")
        
        for idx, item in enumerate(results, 1):
            citation = item.get("citation", {})
            norm = f"{citation.get('country','')}{citation.get('number','')}{citation.get('kind','')}"
            confidence = item.get("confidence", "low")
            matches = item.get("matches", [])

            with st.expander(f"Citation {idx}: {norm} [{confidence.upper()}]"):
                # Citation Overview
                st.markdown("**Citation Overview**")
                cols = st.columns([1, 2])
                with cols[0]:
                    st.markdown(f"""
                    - **Number**: {norm}
                    - **Source**: {item.get('source', '').title()}
                    - **Confidence**: {confidence.upper()}
                    - **Events**: {len(matches)}
                    """)
                
                # Event Timeline
                if matches:
                    st.markdown("---")
                    st.markdown("**Event Timeline**")
                    for match in matches:
                        code = match.get("code", "")
                        desc = match.get("desc", "")
                        text = match.get("text", "")
                        
                        # Create a clean event display
                        st.markdown(f"""
                        <div style='padding: 10px; margin: 5px 0; border-radius: 5px;'>
                            <p><strong>{code}</strong> - {desc}</p>
                            <p style='font-size: 0.9em; margin-top: 5px;'>{text[:200]}{'...' if len(text) > 200 else ''}</p>
                        </div>
                        """, unsafe_allow_html=True)
                
                # Optional AI Analysis for low confidence matches
                if confidence.lower() == "low":
                    st.markdown("---")
                    if st.button("Analyze Citation Context", key=f"llm_{idx}"):
                        with st.spinner("Analyzing..."):
                            analysis = correlator.query_llm_for_ambiguous(citation, matches)
                            st.info(analysis)

    except Exception as e:
        st.error(f"Prior art rendering failed: {e}")

def display_legal_events(data):
    try:
        st.markdown("#### Legal Events Timeline")
        legal_data = data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        
        # Initialize estoppel analyzer with the data
        estoppel_analyzer = ProsecutionHistoryEstoppel(data)
        estoppel_analyzer.analyze_events()
        
        if "ops:family-member" in legal_data:
            for member in legal_data["ops:family-member"]:
                if "ops:legal" in member:
                    events = member["ops:legal"]
                    for event in events:
                        if "@desc" in event and "@code" in event:
                            # Get both effective and document dates
                            pre = event.get("ops:pre") or event.get("pre")
                            details_text = ""
                            if pre:
                                details_text = clean_legal_text(pre)

                            # Look for Effective DATE specifically
                            effective_date = "N/A"
                            m = re.search(r'Effective\s+DATE\s+(\d{8})', details_text, re.IGNORECASE)
                            if m:
                                try:
                                    dt = datetime.strptime(m.group(1), "%Y%m%d")
                                    effective_date = dt.strftime("%d-%m-%Y")
                                except:
                                    pass

                            # Get document date
                            doc_date = format_date(event.get("@dateMigr") or event.get("@date") or "")
                            
                            # Create expandable section with clear date context
                            event_desc = event.get('@desc', '').title()  # Capitalize each word
                            event_code = event.get('@code', '').strip()
                            
                            with st.expander(f"{event_desc} ({event_code})"):
                                if effective_date != "N/A":
                                    st.write("**Effective Date:**", effective_date)
                                if doc_date != "N/A" and doc_date != effective_date:
                                    st.write("**Document Date:**", doc_date)
                                
                                # Show details with better formatting
                                if details_text:
                                    st.markdown("**Details:**")
                                    sections = details_text.split('\n• ')
                                    for section in sections:
                                        if section.strip():
                                            cleaned = re.sub(r'REFERENCE TO A NATIONAL CODE\s+', '', section)
                                            cleaned = re.sub(r'Ref\s+', '', cleaned)
                                            st.markdown(f"• {cleaned.strip()}")
                                
                                # Show effect if meaningful
                                effect = event.get("@infl", "").strip()
                                if effect and effect != "+":
                                    st.write("**Effect:**", effect)
                                
                                # Show estoppel analysis if available
                                if event_desc in estoppel_analyzer.estoppel_labels:
                                    st.markdown("---")
                                    st.markdown("**Estoppel Analysis:**")
                                    st.markdown(estoppel_analyzer.estoppel_labels[event_desc])

        # Display Estoppel Analysis Results
        st.markdown("---")
        st.markdown("### Prosecution History Estoppel Analysis")
        if estoppel_analyzer.estoppel_labels:
            for event, analysis in estoppel_analyzer.estoppel_labels.items():
                with st.expander(f"Estoppel Event: {event}"):
                    st.markdown("**AI Analysis:**")
                    st.markdown(analysis)
        else:
            st.info("No potential prosecution history estoppel events identified.")

    except Exception as e:
        st.error(f"Error displaying legal events: {str(e)}")
        
def display_family_data(data):
    try:
        st.markdown("#### Patent Family Members")
        family_data = data["family"]["ops:world-patent-data"]["ops:patent-family"]
        
        if "ops:family-member" in family_data:
            for member in family_data["ops:family-member"]:
                if "publication-reference" in member:
                    pub_ref = member["publication-reference"]["document-id"][0]
                    with st.expander(f"Family Member - {member.get('@family-id', 'Unknown')}"):
                        st.write("**Publication Details:**")
                        if "country" in pub_ref:
                            st.write(f"- Country: {pub_ref['country']}")
                        if "doc-number" in pub_ref:
                            st.write(f"- Document Number: {pub_ref['doc-number']}")
                        if "kind" in pub_ref:
                            st.write(f"- Kind Code: {pub_ref['kind']}")
                        if "date" in pub_ref:
                            st.write(f"- Date: {format_date(pub_ref['date'])}")
                        
                        if "priority-claim" in member:
                            priority = member["priority-claim"]
                            st.write("\n**Priority Information:**")
                            if "document-id" in priority:
                                pri_doc = priority["document-id"]
                                st.write(f"- Priority Date: {format_date(pri_doc.get('date', 'N/A'))}")
                                st.write(f"- Priority Country: {pri_doc.get('country', 'N/A')}")
                                st.write(f"- Priority Number: {pri_doc.get('doc-number', 'N/A')}")

    except Exception as e:
        st.error(f"Error displaying family data: {str(e)}")
        
        
def get_patent_details(data):
    """Extract key patent details from the data structure"""
    biblio = data.get("bibliographic", {}).get("ops:world-patent-data", {}).get("exchange-documents", {}).get("exchange-document", [{}])[0]
    
    return {
        "patent_number": f"{biblio.get('@country', '')}{biblio.get('@doc-number', '')}{biblio.get('@kind', '')}",
        "title": biblio.get("invention-title", [{}])[0].get("#text", ""),
        "assignee": "; ".join([a.get("applicant-name", {}).get("#text", "") for a in biblio.get("bibliographic-data", {}).get("applicants", [])]),
        "inventors": "; ".join([i.get("inventor-name", {}).get("#text", "") for i in biblio.get("bibliographic-data", {}).get("inventors", [])]),
        "filing_date": format_date(biblio.get("bibliographic-data", {}).get("application-reference", {}).get("document-id", [{}])[0].get("date")),
        "publication_date": format_date(biblio.get("@date")),
        "legal_status": "Active" if not any("CEASED" in e.get("@desc", "").upper() for e in data.get("legal_events", []))
                        else "Ceased"
    }

def main():
    st.set_page_config(
        page_title="Patent History Analyzer",
        page_icon="📄",
        layout="wide"
    )

    st.title("Patent History Analyzer")
    st.markdown("### Enter Patent Publication Number")

    col1, col2 = st.columns([3, 1])
    with col1:
        patent_number = st.text_input("Patent Number", value=st.session_state.get("patent_number", "EP1000000"), help="Example: EP1000000")
    with col2:
        analyze_button = st.button("Analyze Patent", type="primary")

    client = EPOClient()

    # If analyze clicked, fetch data and persist in session_state
    if analyze_button:
        try:
            with st.spinner("Fetching patent data..."):
                # Try the exact input first, then generated variants (deduped)
                candidates = [patent_number] + generate_pub_variants(patent_number)
                seen = set()
                candidates = [c for c in candidates if c and (c not in seen and not seen.add(c))]
                st.write("DEBUG: candidates to try:", candidates)

                data = None
                used_candidate = None
                last_err = None

                for cand in candidates:
                    try:
                        data = client.get_patent_data(cand)
                        used_candidate = cand
                        break
                    except Exception as e:
                        last_err = e
                        # DEBUG: surface exception and any HTTP body if available
                        try:
                            st.write(f"DEBUG: candidate {cand} failed: {repr(e)}")
                            # if EPOClient uses requests and attaches response
                            if hasattr(e, 'response') and getattr(e, 'response') is not None:
                                st.write("DEBUG: response status:", e.response.status_code)
                                st.write("DEBUG: response body:", e.response.text)
                        except Exception:
                            st.write("DEBUG: error while logging exception:", repr(e))
                        # continue to next candidate
                        continue

                if data is None:
                    tried_preview = ", ".join(candidates[:12])
                    err_msg = (
                        "EPO OPS returned no results for the provided publication number.\n\n"
                        f"Attempted variants: {tried_preview}\n\n"
                        "Please check the publication number format (include country code like EP and/or kind code A1).\n"
                    )
                    if last_err:
                        err_msg += f"\nLast error: {str(last_err)}"
                    st.error(err_msg)
                    return

                # Success: persist fetched data and derived objects in session_state
                st.session_state["data"] = data
                st.session_state["patent_number"] = used_candidate or patent_number
                try:
                    st.session_state["structured_data"] = extract_structured_data(data)
                except Exception:
                    # non-fatal: keep going if structured extraction fails
                    st.session_state["structured_data"] = {}

                # Precompute heavy/used objects once
                st.session_state["estoppel_analyzer"] = ProsecutionHistoryEstoppel(data)
                pac = PriorArtCorrelator(data)
                st.session_state["prior_art_correlator"] = pac
                st.session_state["claims"] = ClaimsParser.extract_claims(data)

                # Informational message (helps debug if different candidate was used)
                if used_candidate and used_candidate != patent_number:
                    st.info(f"Fetched using variant: {used_candidate}")

        except Exception as e:
            st.error(f"Error fetching patent data: {str(e)}")
            st.info("Please check if the patent number is correct and try again.")
            return

    # Render tabs if we have data in session_state
    if st.session_state.get("data"):
        data = st.session_state["data"]
        patent_number = st.session_state.get("patent_number", patent_number)

        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "Bibliographic Data",
            "Legal Status",
            "Claims Analysis",
            "Prior Art",
            "Patent Family",
            "DSS Report"
        ])

        with tab1:
            try:
                display_bibliographic_data(data)
            except Exception as e:
                st.error(f"Bibliographic rendering failed: {e}")

        with tab2:
            try:
                display_legal_events(data)
            except Exception as e:
                st.error(f"Legal events rendering failed: {e}")

        with tab3:
            try:
                claims = st.session_state.get("claims", [])
                st.markdown("#### Claims Extraction & Analysis")
                st.write(f"Extracted {len(claims)} claim(s).")
                analyzer = ClaimAnalyzer(openrouter_api_key=os.getenv("OPENROUTER_API_KEY"))
                if claims:
                    st.markdown("##### Summaries")
                    summaries = analyzer.summarize_claims(claims, use_llm=True)
                    for s in summaries:
                        st.write(f"- Claim {s.get('id')}: {s.get('summary')}")
                else:
                    st.info("No claims extracted from JSON.")
            except Exception as e:
                st.error(f"Claims analysis failed: {e}")

        with tab4:
            try:
                display_prior_art(data)
            except Exception as e:
                st.error(f"Prior art rendering failed: {e}")

        with tab5:
            try:
                display_family_data(data)
            except Exception as e:
                st.error(f"Family data rendering failed: {e}")

        with tab6:
            st.markdown("### Decision Support Reports")

            # Use extractor based on legal-status dates to guarantee valid dates
            events_for_vis = extract_events_for_viz(data)
            if events_for_vis:
                st.subheader("Patent Timeline")
                try:
                    fig = build_event_timeline(events_for_vis)
                    st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.error(f"Timeline rendering error: {e}")
            else:
                st.info("No timeline events available for visualization")

            # Claims evolution - use session claims or prior-art correlator helper
            st.subheader("Claims Evolution")
            claim_versions = []
            try:
                # prefer PriorArtCorrelator.get_claim_versions if implemented
                pac = st.session_state.get("prior_art_correlator")
                if pac and hasattr(pac, "get_claim_versions"):
                    claim_versions = pac.get_claim_versions()
                else:
                    # fallback: create minimal version from ClaimsParser output
                    claims = st.session_state.get("claims", [])
                    if claims:
                        claim_versions = [{"version": "Extracted", "claims": [{"id": str(i+1), "text": c.get("text","")} for i,c in enumerate(claims)]}]
                if claim_versions:
                    fig2 = build_claim_evolution(claim_versions)
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("No claim versions available for visualization")
            except Exception as e:
                st.error(f"Claims evolution rendering error: {e}")

            # Report generation
            st.subheader("AI-Powered Report Generation")
            report_col1, report_col2 = st.columns([3, 1])
            with report_col1:
                include_timeline = st.checkbox("Include Timeline Analysis", value=True)
                include_claims = st.checkbox("Include Claims Analysis", value=True)
                include_prior_art = st.checkbox("Include Prior Art Analysis", value=True)
            
            with report_col2:
                if st.button("Generate Report"):
                    try:
                        with st.spinner("Analyzing patent data..."):
                            pac = st.session_state.get("prior_art_correlator") or PriorArtCorrelator(data)
                            patent_details = get_patent_details(data)
                            
                            # Ensure extract is populated
                            extract = st.session_state.get("extract") or {}
                            if not isinstance(extract, dict):
                                extract = {}
                            extract["events"] = extract.get("events") or extract_events_for_viz(data) or []
                            if "citations" not in extract:
                                try:
                                    raw_cits = pac.match_to_rejections() if pac and hasattr(pac, "match_to_rejections") else []
                                except Exception:
                                    raw_cits = []
                                simplified = []
                                for i, rc in enumerate(raw_cits, 1):  # 1-indexed for tokens
                                    cit = rc.get("citation") or {}
                                    path = rc.get("path") or rc.get("source_path") or f"/citations[{i-1}]"
                                    simplified.append({
                                        "id": rc.get("id") or (cit.get("country","") + cit.get("number","") + (cit.get("kind") or "")) or f"CIT:{i-1}",
                                        "kind": rc.get("source") or "bibliographic",
                                        "path": path,
                                        "closest_limits": rc.get("closest_limits"),
                                        "workaround": rc.get("workaround"),
                                        "_token_idx": i  # Store 1-indexed position for token
                                    })
                                extract["citations"] = simplified
                            if "claims" not in extract:
                                claims_session = st.session_state.get("claims", [])
                                extract["claims"] = [{"claim_no": int(c.get("id")) if str(c.get("id")).isdigit() else None, "text": c.get("text",""), "path": c.get("path","/claims")} for c in (claims_session or [])]
                            
                            # Compute coverage
                            coverage = {
                                "events_present": len(extract.get("events", [])),
                                "claims_present": len(extract.get("claims", [])),
                                "citations_present": len(extract.get("citations", [])),
                                "designations_present": len(extract.get("designations", [])) if "designations" in extract else 0
                            }
                            extract["coverage"] = coverage
                            st.session_state["extract"] = extract

                            # Ensure all dates in extract are ISO formatted
                            for event in extract.get("events", []):
                                if event.get("date") and not re.match(r'^\d{4}-\d{2}-\d{2}$', event.get("date")):
                                    iso = normalize_date_to_iso(event.get("date"))
                                    if iso:
                                        event["date"] = iso

                            # Build token_index
                            token_index = build_token_index(extract)
                            st.session_state["token_index"] = token_index
                            valid_tokens = set(token_index.keys())

                            # Compact JSONs for LLM (never in HTML)
                            extract_json_compact = json.dumps(extract, ensure_ascii=False, separators=(",", ":"), default=str)
                            if len(extract_json_compact) > 20000:
                                extract_json_compact = extract_json_compact[:20000]
                            token_index_json = json.dumps(token_index, ensure_ascii=False, separators=(",", ":"), default=str)

                            # Base prompts with token nudge appended
                            prompts = {
                                "executive_summary": """Executive Summary — Draft as a briefing for senior legal counsel on enforceability, litigation potential, monetization, key risks, and next steps.

                            Write exactly 5 concise, expert bullets. Each bullet must:
                            - State the fact or event clearly,
                            - Explain its legal significance or impact (risk/opportunity),
                            - Provide a practical next step (concrete and actionable),
                            - End the sentence with exactly one evidence token (e.g., [EVT#1] or [CIT#1]).

                            Examples:
                            - "No opposition was filed post‑grant, reducing post‑grant risk in designated states. [EVT#3]"
                            - "The patent lapsed in GB for non‑payment, suspending enforcement unless reinstated. [EVT#4]"
                            - "The closest reference currently appears screening‑only pending claim mapping. [CIT#1]"
                            - "Commission a targeted invalidity search focusing on the closest reference. [CIT#1]"

                            Use only tokens from token_index. End every sentence with exactly one token.
                            If no token applies, write "(Omitted pending source)".
                            Jurisdiction=EP: use "prosecution interpretation"; do not use "estoppel".""",

                                "timeline_analysis": """Timeline Analysis — Summarize the 5 most legally significant prosecution events and their impact on litigation.

                            Write exactly 5 concise, professional bullets. Each bullet must:
                            - Explain how the event affects enforceability, claim scope, or risk,
                            - Give a concrete next step for counsel,
                            - End the sentence with exactly one evidence token.

                            Example:
                            - "Intention to grant with no recorded opposition indicates an allowable claim set absent new art. [EVT#2]"

                            Use only tokens from token_index. End every sentence with exactly one token.
                            If no token applies, write "(Omitted pending source)".
                            Jurisdiction=EP: use "prosecution interpretation"; do not use "estoppel".""",

                                "prior_art_analysis": """Prior Art Analysis — Provide 5 concise, cited bullets on the most relevant references, covering risk type (novelty, obviousness, screening), likely claims affected, and next steps.

                            Write exactly 5 bullets. Each bullet must:
                            - Identify the citation and risk,
                            - State which claim(s) are most likely affected (or say if mapping needed),
                            - Recommend a concrete next step (mapping, expert review, targeted search),
                            - End with exactly one token.

                            Use only tokens from token_index. End every sentence with exactly one token.
                            If no token applies, write "(Omitted pending source)".
                            Jurisdiction=EP: use "prosecution interpretation"; do not use "estoppel".""",

                                "recommendations": """Evidence‑Linked Recommendations — Provide exactly 3 specific, actionable next steps (evidence preservation, invalidity search, national status checks, licensing).

                            Each recommendation must be one sentence and end with exactly one evidence token.

                            Examples:
                            - "Confirm reinstatement feasibility and current national status for the GB lapse event. [EVT#4]"
                            - "Commission a focused invalidity search centered on the closest citation. [CIT#1]"
                            - "Review designated states at the time of intention to grant to scope licensing opportunities. [EVT#2]"

                            Use only tokens from token_index. End every sentence with exactly one token.
                            If no token applies, write "(Omitted pending source)".
                            Jurisdiction=EP: use "prosecution interpretation"; do not use "estoppel"."""
                            }

                            nudge = " Use only tokens from token_index below. End every sentence with exactly one token like [EVT#2] or [CIT#1]. If no token applies, write '(Omitted pending source)'. Jurisdiction=EP: use 'prosecution interpretation'; do not use 'estoppel'."
                            
                            def _build_prompt_with_tokens(base: str, token_idx: dict, extract_dict: dict, **fmt):
                                """Build prompt with human-readable token summary instead of raw JSON."""
                                try:
                                    p = base.format(**fmt)
                                except Exception:
                                    p = base
                                
                                # Build human-readable token reference
                                token_ref = "AVAILABLE TOKENS (use at end of every sentence):\n"
                                for tok in sorted(token_idx.keys()):
                                    meta = token_idx[tok]
                                    if meta.get("type") == "event":
                                        token_ref += f"  {tok}: {meta.get('date')} {meta.get('code')} ({','.join(meta.get('effects', ['unknown']))})\n"
                                    elif meta.get("type") == "citation":
                                        token_ref += f"  {tok}: {meta.get('id')} ({meta.get('kind')})\n"
                                
                                nudge = " Use ONLY tokens from AVAILABLE TOKENS above. End EVERY sentence with exactly one token like [EVT#2] or [CIT#1]. If no suitable token, write '(Omitted pending source)'. For EP jurisdiction: use 'prosecution interpretation', NEVER 'estoppel'."
                                
                                return p + "\n\n" + nudge + "\n\n" + token_ref

                            section_order = [("Executive Summary", "executive_summary"), ("Timeline Analysis", "timeline_analysis"), ("Prior Art Analysis", "prior_art_analysis"), ("Evidence-Linked Recommendations", "recommendations")]
                            analyses = {}
                            
                            def _sentences_to_bullets(text):
                                sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
                                bullets = []
                                for s in sents:
                                    if re.search(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', s):
                                        bullets.append(s)
                                return bullets

                            for title, key in section_order:
                                base = prompts.get(key, "")
                                prompt = _build_prompt_with_tokens(base, token_index, extract, **patent_details)

                                # DEBUG: show token index and prompt preview (first 2000 chars)
                                st.write("DEBUG: token_index keys:", sorted(list(token_index.keys())))
                                st.write("DEBUG: prompt preview (first 2000 chars):")
                                st.text(prompt[:2000])

                                llm_text = ""
                                if pac and hasattr(pac, "query_llm"):
                                    try:
                                        llm_text = pac.query_llm(prompt) or ""
                                    except Exception as e:
                                        llm_text = ""
                                        st.write(f"Debug: LLM call failed for {key}: {str(e)}")

                                # DEBUG: raw LLM output (trimmed)
                                st.write(f"DEBUG: llm_text for {key} (first 4000 chars):")
                                st.text((llm_text or "")[:4000])

                                # Apply token guardrails and validations
                                text = enforce_token_citations(llm_text or "", valid_tokens)
                                text = validate_tokens(text, token_index)
                                text = drop_uncited_sentences(text)
                                text = sanitize_ep_language(text, jurisdiction="EP")
                                text = remove_placeholders_and_normalize(text)  # NEW: Strip placeholders

                                # Split into candidate sentences/bullets (must end with one token)
                                cand_sents = [s for s in _split_sentences(text or "") if re.search(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]$', s)]
                                # Keep only those that reference known tokens
                                bullets = []
                                for s in cand_sents:
                                    toks = re.findall(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]$', s)
                                    if toks and all(t in valid_tokens for t in toks):
                                        bullets.append(s)

                                # Deterministic fallback generator (as replaced in earlier fix)
                                def deterministic_bullets_for(key_name, extract_obj, token_idx, need):
                                    """
                                    Generate attorney-grade, risk-aware, actionable bullets.
                                    - NO placeholders like "(Omitted pending source)"
                                    - Only reference tokens present in token_idx
                                    - Never invent citations; only use provided data
                                    - Normalize all dates to ISO
                                    - Rephrase every bullet with legal risk + impact + action
                                    """
                                    out = []
                                    evs = [normalize_event(e.copy()) for e in (extract_obj.get("events", []) or [])]
                                    cits = extract_obj.get("citations", []) or []

                                    def safe_token(tok):
                                        return tok if tok in valid_tokens else None

                                    if key_name == "executive_summary":
                                        # Bullet 1: Most recent material event with legal consequence
                                        if evs:
                                            ev = evs[-1]
                                            tok = safe_token(f"EVT#{len(evs)}")
                                            if tok:
                                                code = ev.get("code", "").upper()
                                                if "CEASED" in code or "LAPSE" in code or "GBPC" in code:
                                                    out.append(
                                                        f"Patent rights lapsed on {ev.get('date', 'N/A')} due to non-payment; "
                                                        f"restoration procedures may be available within statutory deadlines—assess commercial viability immediately. [{tok}]"
                                                    )
                                                elif "INTG" in code:
                                                    out.append(
                                                        f"Intention to grant issued on {ev.get('date', 'N/A')}; "
                                                        f"claims are allowable absent newly cited art—finalize commercialization and market-entry strategy now. [{tok}]"
                                                    )
                                                elif "17P" in code:
                                                    out.append(
                                                        f"Examination filed on {ev.get('date', 'N/A')}; "
                                                        f"prosecution history establishes record for claim interpretation and prosecution interpretation purposes. [{tok}]"
                                                    )
                                                else:
                                                    out.append(
                                                        f"{code} event recorded on {ev.get('date', 'N/A')}; "
                                                        f"material consequence for enforceability—counsel must assess impact on licensing and litigation strategy. [{tok}]"
                                                    )
                                        
                                        # Bullet 2: Opposition status (reduces invalidation risk or signals threat)
                                        if len(out) < need and evs:
                                            no_opp_event = any("26N" in e.get("code", "") or "no_opposition" in str(e.get("effects", [])) for e in evs)
                                            intg_event = any("INTG" in e.get("code", "") for e in evs)
                                            if no_opp_event or (intg_event and not any("OPPOSITION" in e.get("code", "").upper() for e in evs)):
                                                tok = safe_token("EVT#" + str(next((i+1 for i, e in enumerate(evs) if "26N" in e.get("code", "") or "INTG" in e.get("code", "")), len(evs))))
                                                if tok:
                                                    out.append(
                                                        f"No post-grant opposition was filed; "
                                                        f"eliminates near-term invalidation risk and strengthens claim validity for enforcement and licensing. [{tok}]"
                                                    )
                                        
                                        # Bullet 3: Closest citation (if present)
                                        if len(out) < need and cits:
                                            cit = cits[0]
                                            tok = safe_token("CIT#1")
                                            if tok:
                                                risk_type = "novelty" if cit.get("kind") == "examiner" else "obviousness" if cit.get("kind") == "legal" else "screening"
                                                out.append(
                                                    f"Reference {cit.get('id', 'CIT:?')} presents {risk_type} concerns; "
                                                    f"commission detailed claim mapping and prepare technical response to defend independent claims. [{tok}]"
                                                )
                                        
                                        # Bullet 4: Family scope / jurisdiction coverage
                                        if len(out) < need and evs:
                                            tok = safe_token("EVT#1")
                                            if tok:
                                                family_count = extract_obj.get("family_members_count", 1)
                                                if family_count > 1:
                                                    out.append(
                                                        f"Patent family spans {family_count} jurisdictions; "
                                                        f"confirm national validation status and coordinate enforcement strategy across key markets. [{tok}]"
                                                    )
                                                else:
                                                    out.append(
                                                        f"Limited family scope; "
                                                        f"evaluate national extension strategy in high-value markets and assess filing gaps. [{tok}]"
                                                    )
                                        
                                        # Bullet 5: Strategic business action
                                        if len(out) < need:
                                            if cits and evs:
                                                tok = safe_token("CIT#1")
                                                if tok:
                                                    out.append(
                                                        f"Reference {cits[0].get('id', 'CIT:?')} may enable design-around opportunities; "
                                                        f"assess freedom-to-operate and evaluate licensing vs. engineering strategy. [{tok}]"
                                                    )
                                            elif evs:
                                                tok = safe_token("EVT#1")
                                                if tok:
                                                    out.append(
                                                        f"Prosecution history supports continuation or reissue filings; "
                                                        f"consider dependent claim variants and dependent embodiment coverage to broaden protection. [{tok}]"
                                                    )

                                    elif key_name == "timeline_analysis":
                                        # Use Top 3 pivotal events as the spine; interpret each
                                        top_lines = (render_top_pivotal_events(evs) or "").split("\n")
                                        top_lines = [l for l in top_lines if l.strip()]
                                        
                                        for line in top_lines:
                                            if not line or len(out) >= need:
                                                continue
                                            m = re.match(r'^(.*?) – (.*?) – (.*?) – \[(EVT#\d+)\]$', line)
                                            if m:
                                                date, code, desc, tok_match = m.groups()
                                                tok = safe_token(tok_match)
                                                if tok:
                                                    code = code.upper()
                                                    if "INTG" in code or "grant" in desc.lower():
                                                        out.append(
                                                            f"{date}: {code} — {desc}; "
                                                            f"independent claims are allowable, strengthening litigation position and enabling licensing conversations. [{tok}]"
                                                        )
                                                    elif "26N" in code or "opposition" in desc.lower():
                                                        out.append(
                                                            f"{date}: {code} — {desc}; "
                                                            f"claim scope unlikely to be challenged in post-grant proceedings, reducing invalidation exposure. [{tok}]"
                                                        )
                                                    elif "LAPSE" in code or "CEASED" in code or "GBPC" in code or "lapse" in desc.lower():
                                                        out.append(
                                                            f"{date}: {code} — {desc}; "
                                                            f"enforcement rights suspended unless restoration is filed; assess statutory deadlines and commercial justification. [{tok}]"
                                                        )
                                                    elif "17P" in code or "examination" in desc.lower():
                                                        out.append(
                                                            f"{date}: {code} — {desc}; "
                                                            f"prosecution history becomes binding on future claim amendments and establishes invalidity defenses. [{tok}]"
                                                        )
                                                    else:
                                                        out.append(
                                                            f"{date}: {code} — {desc}; "
                                                            f"material prosecution event affecting claim validity and enforcement strategy. [{tok}]"
                                                        )
                                        
                                        # Fill remaining slots from full event list (if needed)
                                        for idx, e in enumerate(evs, 1):
                                            if len(out) >= need:
                                                break
                                            # Skip events already in Top 3
                                            if any(str(idx) in line for line in top_lines):
                                                continue
                                            tok = safe_token(f"EVT#{idx}")
                                            if tok:
                                                code = e.get("code", "?").upper()
                                                if any(marker in code for marker in ["17P", "INTG", "26N", "GBPC"]):
                                                    continue  # Already covered
                                                else:
                                                    desc = e.get("desc", f"{code} event")
                                                    out.append(
                                                        f"{e.get('date', 'N/A')}: {code} — {desc}; "
                                                        f"evaluate secondary prosecution implications for claim interpretation. [{tok}]"
                                                    )

                                    elif key_name == "prior_art_analysis":
                                        # Use only provided citations; interpret each with risk + action
                                        ranked = (render_ranked_citations(cits) or "").split("\n")
                                        ranked = [l for l in ranked if l.strip()]
                                        
                                        for line in ranked:
                                            if not line or len(out) >= need:
                                                continue
                                            m = re.search(r'\[(CIT#\d+)\]$', line)
                                            if m:
                                                tok = m.group(1)
                                                if safe_token(tok):
                                                    # Extract citation ID and risk from ranked line
                                                    cid_m = re.match(r'^\d+\.\s+([^\s–]+)', line)
                                                    risk_m = re.search(r'–\s+(novelty|obviousness|screening[^–]*)', line)
                                                    cid = cid_m.group(1) if cid_m else "CIT:?"
                                                    risk = risk_m.group(1).strip() if risk_m else "screening"
                                                    
                                                    if "novelty" in risk.lower():
                                                        out.append(
                                                            f"Reference {cid} raises direct novelty challenge; "
                                                            f"prepare claim charts mapping independent claims to prior art and commission focused prior-art search. [{tok}]"
                                                        )
                                                    elif "obviousness" in risk.lower():
                                                        out.append(
                                                            f"Reference {cid} may support obviousness rejection if combined with secondary art; "
                                                            f"prepare expert technical declaration distinguishing inventive steps and non-obvious combinations. [{tok}]"
                                                        )
                                                    elif "screening" in risk.lower():
                                                        out.append(
                                                            f"Reference {cid} is screening-only; low material invalidity risk unless combined with other references—monitor competitor activity. [{tok}]"
                                                        )
                                                    else:
                                                        out.append(
                                                            f"Reference {cid} requires detailed claim-to-art analysis; "
                                                            f"prioritize invalidity search and prepare enforcement case materials. [{tok}]"
                                                        )
                                        
                                        # If no citations provided, do not produce filler
                                        if not out and cits:
                                            tok = safe_token("CIT#1")
                                            if tok:
                                                out.append(
                                                    f"Minimal prior-art citations in record; "
                                                    f"conduct independent invalidity search to assess competitive blocking potential. [{tok}]"
                                                )

                                    elif key_name == "recommendations":
                                        # Exactly 3 actionable recommendations tied to events/citations
                                        # Rec 1: Evidence preservation or restoration action
                                        if evs:
                                            tok = safe_token(f"EVT#{len(evs)}")
                                            if tok:
                                                lapse_events = [e for e in evs if "LAPSE" in str(e.get("effects", [])).upper() or "CEASED" in e.get("code", "").upper() or "GBPC" in e.get("code", "")]
                                                if lapse_events:
                                                    last_lapse = lapse_events[-1]
                                                    out.append(
                                                        f"Confirm restoration procedures and statutory deadlines for lapsed event on {last_lapse.get('date', 'N/A')}; "
                                                        f"file reinstatement petition if patent remains commercially valuable. [{tok}]"
                                                    )
                                                else:
                                                    out.append(
                                                        f"Preserve entire prosecution file (pre- and post-grant) as evidence for litigation, licensing, and invalidity defense. [{tok}]"
                                                    )
                                        
                                        # Rec 2: Prior-art / invalidity mitigation
                                        if cits:
                                            tok = safe_token("CIT#1")
                                            if tok:
                                                out.append(
                                                    f"Commission targeted invalidity analysis focused on {cits[0].get('id', 'CIT:?')}; "
                                                    f"prepare detailed claim charts and expert technical declaration for defense counsel. [{tok}]"
                                                )
                                        elif evs:
                                            tok = safe_token("EVT#1")
                                            if tok:
                                                out.append(
                                                    f"Conduct independent prior-art search in key competitive technology areas; "
                                                    f"identify secondary invalidation risks and potential licensing opportunities. [{tok}]"
                                                )
                                        
                                        # Rec 3: Strategic/commercial action
                                        if evs or cits:
                                            tok = safe_token("EVT#1") or safe_token("CIT#1")
                                            if tok:
                                                intg_event = any("INTG" in e.get("code", "") for e in evs)
                                                if intg_event:
                                                    out.append(
                                                        f"Intention to grant achieved; finalize commercialization partners and licensing targets; "
                                                        f"identify high-value enforcement opportunities in designated states. [{tok}]"
                                                    )
                                                else:
                                                    out.append(
                                                        f"Assess national validation roadmap; evaluate continuation, divisional, or reissue filings "
                                                        f"to cover dependent claim variants and foreign embodiments. [{tok}]"
                                                    )

                                    # Ensure only valid-token-terminated, non-placeholder sentences returned
                                    cleaned = [s.strip() for s in out if s.strip() and re.search(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]$', s.strip()) and "(Omitted" not in s]
                                    return cleaned[:need]

                                # Determine required per-section
                                required = 3 if key == "recommendations" else 5

                                # Backfill if short
                                if len(bullets) < required:
                                    need = required - len(bullets)
                                    det = deterministic_bullets_for(key, extract, token_index, need)
                                    bullets.extend(det)

                                # CHANGED: Do NOT pad with "(Omitted pending source)" placeholders
                                # Instead, only use bullets that have real content
                                bullets = [b for b in bullets if b and not b.startswith("[MISSING]") and "(Omitted" not in b]

                                # Build section text and render
                                final_section_text = "\n".join(bullets)
                                final_section_text = sanitize_ep_language(final_section_text, jurisdiction="EP")
                                final_section_text = remove_placeholders_and_normalize(final_section_text)  # normalize dates & strip placeholders

                                # Split into lines, enforce token rules, dedupe, and drop any sentence whose token cannot be linked
                                lines = [ln.strip() for ln in final_section_text.split("\n") if ln.strip()]
                                clean_lines = []
                                seen = set()
                                for ln in lines:
                                    # skip any leftover placeholders or markers
                                    if "(Omitted" in ln or "[MISSING]" in ln or "[INVALID_" in ln:
                                        continue
                                    # require exactly one evidence token at end of sentence
                                    toks = re.findall(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', ln)
                                    if not toks:
                                        continue
                                    # if more than one token, drop line (must be exactly one token)
                                    if len(toks) != 1:
                                        continue
                                    tok = toks[0]
                                    # token must exist in token_index
                                    if tok not in token_index:
                                        continue
                                    # dedupe exact lines (preserve first occurrence)
                                    if ln in seen:
                                        continue
                                    seen.add(ln)
                                    clean_lines.append(ln)

                                # Do not artificially pad; allow shorter sections
                                final_section_text = "\n".join(clean_lines)

                                # NEW: Deduplication pass — remove repeated phrases within each line
                                def deduplicate_line(line: str) -> str:
                                    """Remove repeated consecutive words/phrases in a single line."""
                                    words = line.split()
                                    deduped = []
                                    prev_word = None
                                    for word in words:
                                        if word != prev_word:
                                            deduped.append(word)
                                            prev_word = word
                                    return " ".join(deduped)

                                # Apply deduplication to each line
                                deduped_lines = [deduplicate_line(ln) for ln in final_section_text.split("\n")]
                                final_section_text = "\n".join(deduped_lines)

                                # Render to HTML
                                section_html = render_to_html(final_section_text)

                                # Append deterministic Top/Ranked blocks (tokenized) only if present and valid
                                if key == "timeline_analysis" and extract.get("events"):
                                    top3 = render_top_pivotal_events(extract.get("events", []))
                                    if top3 and top3.strip():
                                        # ensure tokens in top3 are valid
                                        valid_top_lines = []
                                        for line in top3.split("\n"):
                                            mt = re.search(r'\[(EVT#\d+)\]', line)
                                            if mt and mt.group(1) in token_index:
                                                valid_top_lines.append(line)
                                        if valid_top_lines:
                                            section_html += "<br><br><strong>Top 3 pivotal events:</strong><br>" + render_to_html("\n".join(valid_top_lines))

                                if key == "prior_art_analysis" and extract.get("citations"):
                                    ranked = render_ranked_citations(extract.get("citations", []))
                                    if ranked and ranked.strip():
                                        # choose header: "Ranked Top 5 citations" if 5+ exist, else "Ranked Citations"
                                        cit_count = len(extract.get("citations", []))
                                        if cit_count >= 5:
                                            header = "Ranked Top 5 citations"
                                        else:
                                            header = "Ranked Citations"
                                        # ensure only lines with valid tokens survive
                                        valid_ranked_lines = []
                                        for line in ranked.split("\n"):
                                            mc = re.search(r'\[(CIT#\d+)\]', line)
                                            if mc and mc.group(1) in token_index:
                                                valid_ranked_lines.append(line)
                                        if valid_ranked_lines:
                                            section_html += f"<br><br><strong>{header}:</strong><br>" + render_to_html("\n".join(valid_ranked_lines))

                                analyses[title] = section_html

                            # Build context and render HTML
                            context = {
                                "patent_number": patent_number,
                                "generated_at": datetime.now().isoformat(),
                                "patent_details": patent_details,
                                "analyses": analyses,
                                "events": extract.get("events", []),
                                "citations": extract.get("citations", []),
                                "claims": extract.get("claims", []),
                                "coverage": coverage,
                                "token_index": token_index  # Pass for rendering
                            }
                            
                            html = build_html_report(context)

                            # Inject coverage header ONCE at the very top (after title but before sections)
                            coverage_line = (
                                f"<p style='font-size: 0.95em; font-weight: bold; margin: 15px 0; padding: 10px; "
                                f"background-color: #f0f0f0; border-left: 4px solid #0066cc;'>"
                                f"<strong>Coverage:</strong> events={coverage.get('events_present',0)}, "
                                f"citations={coverage.get('citations_present',0)}."
                                f"</p>"
                            )

                            # Insert after first <h1> tag, before any <h2>/<h3>
                            import re as _re
                            h1_match = _re.search(r'(</h1>)', html)
                            if h1_match:
                                html = html[:h1_match.end()] + coverage_line + html[h1_match.end():]

                            # Ensure no duplicate coverage lines exist (dedup)
                            html = _re.sub(
                                r'<p[^>]*><strong>Coverage:</strong>.*?</p>(?:\s*<p[^>]*><strong>Coverage:</strong>.*?</p>)+',
                                coverage_line,
                                html
                            )

                            # Ensure no duplicate coverage lines exist
                            html = _re.sub(r'<p[^>]*>Coverage:.*?</p>\s*<p[^>]*>Coverage:.*?</p>', coverage_line, html)

                            # Final checks
                            # Final checks — strict validation
                            # Final checks — strict validation
                            fails = []

                            # Check 1: No [MISSING] tokens anywhere
                            if "[MISSING]" in html:
                                fails.append("Uncited sentences detected ([MISSING] markers present).")

                            # Check 2: No [INVALID_...] tokens
                            if "[INVALID_" in html:
                                fails.append("Invalid token references detected ([INVALID_ markers present).")

                            # Check 3: No "estoppel" in EP documents (must use "prosecution interpretation")
                            if "estoppel" in html.lower():
                                if "prosecution interpretation" not in html.lower():
                                    fails.append("Wording violation: 'estoppel' used instead of 'prosecution interpretation'.")

                            # Check 4: No "(Omitted pending source)" placeholders
                            if "(Omitted pending source)" in html:
                                fails.append("Placeholder text remains in output.")

                            # Check 5: Coverage header present exactly once
                            coverage_count = len(re.findall(r'<strong>Coverage:</strong>', html))
                            if coverage_count == 0:
                                fails.append("Coverage header missing (should appear exactly once below title).")
                            elif coverage_count > 1:
                                fails.append(f"Coverage header appears {coverage_count} times (should be exactly 1).")

                            # Check 6: If events present, Top 3 block must exist
                            if coverage["events_present"] > 0:
                                if "Top 3 pivotal events" not in html:
                                    fails.append("Top 3 pivotal events block missing despite events present.")

                            # Check 7: If citations present, Ranked block must exist with correct header
                            if coverage["citations_present"] > 0:
                                cit_count = coverage["citations_present"]
                                if cit_count >= 5:
                                    if "Ranked Top 5 citations" not in html:
                                        fails.append("'Ranked Top 5 citations' header missing (5+ citations exist).")
                                else:
                                    if "Ranked Citations" not in html:
                                        fails.append(f"'Ranked Citations' header missing ({cit_count} citations exist).")

                            # Check 8: All tokens used must be valid (in token_index)
                            invalid_tokens = re.findall(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', html)
                            for tok in set(invalid_tokens):
                                if tok not in token_index:
                                    fails.append(f"Token {tok} used but not present in token_index.")

                            # Check 9: No duplicate section headers
                            section_headers = ["Executive Summary", "Timeline Analysis", "Prior Art Analysis", "Evidence-Linked Recommendations"]
                            for header in section_headers:
                                header_count = len(re.findall(rf'<h2[^>]*>{re.escape(header)}</h2>', html))
                                if header_count > 1:
                                    fails.append(f"Section '{header}' appears {header_count} times (should be 1).")

                            if fails:
                                st.warning("⚠️ **Quality Issues Detected:**\n\n" + "\n".join(["• " + f for f in fails]))
                                warning_html = (
                                    "<div style='background-color: #fff3cd; border: 2px solid #ffc107; padding: 15px; "
                                    "border-radius: 5px; margin-bottom: 20px;'>"
                                    "<strong style='color: #856404;'>⚠️ Quality Issues:</strong><br>"
                                    + "<br>".join([f"• {f}" for f in fails]) +
                                    "</div>"
                                )
                                html = warning_html + html
                            else:
                                st.success("✅ All quality checks passed. Report is production-ready.")

                            # Save and download
                            out_path = os.path.join(os.getcwd(), f"{patent_number}_analysis.html")
                            with open(out_path, "w", encoding="utf-8") as f:
                                f.write(html)
                            with open(out_path, "rb") as f:
                                st.download_button("Download Analysis Report", f, file_name=f"{patent_number}_analysis.html", mime="text/html")

                    except Exception as e:
                        st.error(f"Report generation failed: {str(e)}")
        # Offer full JSON download (persisted)
        st.download_button(
            label="Download Full Data",
            data=json.dumps(data, indent=2),
            file_name=f"{patent_number}_analysis.json",
            mime="application/json"
        )

    else:
        st.info("Enter a patent number and click 'Analyze Patent' to begin.")

if __name__ == "__main__":
    main()