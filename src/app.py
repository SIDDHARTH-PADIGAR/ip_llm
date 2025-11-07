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

def format_date(date_str):
    if not date_str:
        return "N/A"
    if isinstance(date_str, datetime):
        year = date_str.year
        now_year = datetime.now().year
        if year < 1900 or year > now_year + 1:
            return "N/A"
        return date_str.strftime("%d-%m-%Y")
    s = str(date_str).strip()
    now_year = datetime.now().year
    m = re.search(r'(\d{4})(\d{2})(\d{2})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(m.group(0), "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass
    m = re.search(r'(\d{4})[\/\-\.\s](\d{2})[\/\-\.\s](\d{2})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass
    m = re.search(r'(\d{2})[\/\-\.\s](\d{2})[\/\-\.\s](\d{4})', s)
    if m:
        y = int(m.group(3))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(f"{m.group(3)}{m.group(2)}{m.group(1)}", "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass
    m = re.search(r'(\d{4})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            return f"01-01-{y}"
    return "N/A"

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
    """Extract events with properly formatted dates for visualization"""
    events = []
    legal_data = data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
    
    if "ops:family-member" in legal_data:
        for member in legal_data["ops:family-member"]:
            if "ops:legal" in member:
                for event in member["ops:legal"]:
                    if isinstance(event, dict):
                        # Get effective date first
                        date_str = None
                        details = event.get("ops:pre", {}).get("#text", "") if isinstance(event.get("ops:pre"), dict) else ""
                        
                        # Try to extract effective date from details
                        effective_match = re.search(r'Effective\s+DATE\s+(\d{8})', details, re.IGNORECASE)
                        if effective_match:
                            date_str = effective_match.group(1)
                        else:
                            # Fallback to document date
                            date_str = event.get("@date") or event.get("@dateMigr")
                        
                        if date_str and len(str(date_str)) == 8:
                            try:
                                date = datetime.strptime(str(date_str), "%Y%m%d")
                                if 1900 <= date.year <= 2100:
                                    events.append({
                                        "date": date.strftime("%Y-%m-%d"),
                                        "code": event.get("@code", "").strip(),
                                        "desc": event.get("@desc", "").strip(),
                                        "text": clean_legal_text(event.get("ops:pre", {}))
                                    })
                            except ValueError:
                                continue

    return sorted(events, key=lambda x: x["date"])

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

                            # Ensure we have patent details
                            patent_details = get_patent_details(data)

                            # Simple named-placeholders prompts
                            prompts = {
                                "executive_summary": """Patent {patent_number} - Litigation & Strategy Analysis

                            PATENT DETAILS (use only these, do NOT invent or assume any other facts):
                            Title: {title}
                            Assignee: {assignee}
                            Filed: {filing_date}
                            Published: {publication_date}
                            Legal Status: {legal_status}

                            Provide a high-value executive summary focused on:

                            1. ENFORCEABILITY STATUS
                            - Current legal status & term
                            - Key jurisdictions coverage
                            - Enforceability strength

                            2. LITIGATION VALUE
                            - Technology significance
                            - Market impact potential
                            - Strength of protection

                            3. MONETIZATION POTENTIAL
                            - Licensing opportunities
                            - Portfolio strategic value
                            - Industry applicability

                            4. KEY RECOMMENDATIONS
                            - Immediate actions needed
                            - Risk mitigation strategies
                            - Strategic opportunities

                            Format as clear sections. Focus on actionable insights.""",

                                "timeline_analysis": """PROSECUTION HISTORY ANALYSIS

                            Events:
                            {timeline_data}

                            Analyze from a litigation perspective:

                            1. PROSECUTION STRENGTH
                            - Critical decisions & amendments
                            - Arguments presented/accepted
                            - Estoppel implications

                            2. ENFORCEABILITY IMPACT
                            - Effect on claim scope
                            - Term adjustments
                            - Opposition/challenge history

                            3. STRATEGIC IMPLICATIONS
                            - Litigation readiness
                            - Validity vulnerabilities
                            - Enforcement strategy

                            Reference specific dates/events. Focus on litigation value.""",

                                "prior_art_analysis": """PRIOR ART STRATEGIC ANALYSIS

                            Citations:
                            {citation_data}

                            Provide litigation-focused analysis:

                            1. VALIDITY ASSESSMENT
                            - Closest prior art identified
                            - Novelty/obviousness risks
                            - Distinguishing features

                            2. ENFORCEMENT STRATEGY
                            - Prior art barriers
                            - Workaround difficulty
                            - Defensive positions

                            3. LITIGATION RECOMMENDATIONS
                            - Pre-suit investigations needed
                            - Validity challenge risks
                            - Strategic considerations

                            Use specific citations. Focus on actionable insights."""
                            }
                            # Build claim versions (prefer existing variable, else pac or session claims)
                            claim_versions_local = []
                            try:
                                if 'claim_versions' in globals() or 'claim_versions' in locals():
                                    claim_versions_local = claim_versions or []
                            except Exception:
                                claim_versions_local = []

                            if not claim_versions_local and pac and hasattr(pac, "get_claim_versions"):
                                try:
                                    claim_versions_local = pac.get_claim_versions() or []
                                except Exception:
                                    claim_versions_local = []

                            if not claim_versions_local:
                                claims_session = st.session_state.get("claims", [])
                                if claims_session:
                                    claim_versions_local = [{"version": "Extracted", "claims": [{"id": str(i+1), "text": c.get("text","")} for i,c in enumerate(claims_session)]}]

                            # Prepare analyses with safe fallbacks
                            analyses = {}

                            # Executive summary (LLM if available, else factual fallback)
                            try:
                                exec_prompt = prompts["executive_summary"].format(**patent_details)
                                if pac and hasattr(pac, "query_llm"):
                                    analyses["executive_summary"] = pac.query_llm(exec_prompt) or ""
                                else:
                                    analyses["executive_summary"] = (
                                        f"{patent_details.get('title','No title')}. "
                                        f"Assignee: {patent_details.get('assignee','N/A')}. "
                                        f"Status: {patent_details.get('legal_status','N/A')}."
                                    )
                            except Exception as e:
                                analyses["executive_summary"] = f"Executive summary generation failed: {e}"

                            # Timeline analysis
                            try:
                                if include_timeline and events_for_vis:
                                    timeline_data = "\n".join([f"- {e['date']}: {e['code']} — {e['desc']} ({e.get('text','')[:200]})" for e in events_for_vis])
                                    if pac and hasattr(pac, "query_llm"):
                                        analyses["timeline_analysis"] = pac.query_llm(prompts["timeline_analysis"].format(timeline_data=timeline_data)) or ""
                                    else:
                                        analyses["timeline_analysis"] = "Timeline available; LLM not configured. See Event Timeline section."
                                else:
                                    analyses["timeline_analysis"] = "No timeline events available."
                            except Exception as e:
                                analyses["timeline_analysis"] = f"Timeline analysis failed: {e}"

                            try:
                                if include_prior_art:
                                    # Use the same logic as display_prior_art
                                    correlator = PriorArtCorrelator(data)
                                    results = correlator.match_to_rejections()

                                    if results:
                                        citation_data = "\n".join([
                                            f"Citation {idx}: {c['citation'].get('country','')}{c['citation'].get('number','')}{c['citation'].get('kind','')}"
                                            f"\nConfidence: {c.get('confidence', 'low')}"
                                            f"\nSource: {c.get('source', 'unknown')}"
                                            for idx, c in enumerate(results, 1)
                                        ])
                                        
                                        analyses["prior_art_analysis"] = pac.query_llm(prompts["prior_art_analysis"].format(
                                            citation_data=citation_data
                                        )) if pac and hasattr(pac, "query_llm") else f"{len(results)} citations found"
                                    else:
                                        analyses["prior_art_analysis"] = "No citations found for analysis."
                                else:
                                    analyses["prior_art_analysis"] = "Prior art analysis not requested."
                            except Exception as e:
                                analyses["prior_art_analysis"] = f"Prior art analysis failed: {e}"

                            # Claims analysis
                            try:
                                if include_claims:
                                    if claim_versions_local:
                                        claims_data = "\n".join([
                                            f"Version: {v['version']}\n" + "\n".join([f"Claim {c['id']}: {c['text']}" for c in v['claims']])
                                            for v in claim_versions_local
                                        ])
                                        if pac and hasattr(pac, "query_llm"):
                                            analyses["claims_analysis"] = pac.query_llm(prompts["claims_analysis"].format(claims_data=claims_data)) or ""
                                        else:
                                            analyses["claims_analysis"] = "Claims present; LLM not configured for deep analysis."
                                    else:
                                        analyses["claims_analysis"] = "No claim versions available for analysis."
                                else:
                                    analyses["claims_analysis"] = "Claims analysis not requested."
                            except Exception as e:
                                analyses["claims_analysis"] = f"Claims analysis failed: {e}"

                            # Final context and report generation
                            context = {
                                "patent_number": patent_number,
                                "generated_at": datetime.now().isoformat(),
                                "patent_details": patent_details,
                                "analyses": analyses,
                                "events": events_for_vis if include_timeline else [],
                                "citations": (pac.match_to_rejections() if (include_prior_art and pac and hasattr(pac, "match_to_rejections")) else []),
                                "claims": claim_versions_local if include_claims else []
                            }
                            
                            

                            html = build_html_report(context)
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