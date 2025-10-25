import os
import streamlit as st
import json
import re 
from api.epo_client import EPOClient
from datetime import datetime
from data.parsers.claims_extractor import ClaimsParser
from data.parsers.claims_analysis import ClaimAnalyzer


def format_date(date_str):
    """
    Tolerant date formatter for display:
    - Accepts datetime or strings.
    - Extracts first reasonable date token (YYYYMMDD, YYYY-MM-DD, DD-MM-YYYY).
    - Returns "N/A" for invalid / extremely old / placeholder dates (e.g. year < 1900).
    """
    if not date_str:
        return "N/A"
    # datetime input
    if isinstance(date_str, datetime):
        year = date_str.year
        now_year = datetime.now().year
        if year < 1900 or year > now_year + 1:
            return "N/A"
        return date_str.strftime("%d-%m-%Y")

    s = str(date_str).strip()
    now_year = datetime.now().year

    # 8-digit YYYYMMDD
    m = re.search(r'(\d{4})(\d{2})(\d{2})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(m.group(0), "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass

    # YYYY[-/.\s]MM[-/.\s]DD
    m = re.search(r'(\d{4})[\/\-\.\s](\d{2})[\/\-\.\s](\d{2})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass

    # DD[-/.\s]MM[-/.\s]YYYY
    m = re.search(r'(\d{2})[\/\-\.\s](\d{2})[\/\-\.\s](\d{4})', s)
    if m:
        y = int(m.group(3))
        if 1900 <= y <= now_year + 1:
            try:
                return datetime.strptime(f"{m.group(3)}{m.group(2)}{m.group(1)}", "%Y%m%d").strftime("%d-%m-%Y")
            except Exception:
                pass

    # fallback: find 4-digit year alone but only accept reasonable years (no day/month)
    m = re.search(r'(\d{4})', s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= now_year + 1:
            return f"01-01-{y}"  # best-effort year-only display

    return "N/A"

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

def display_legal_events(data):
    try:
        st.markdown("#### Legal Events Timeline")
        legal_data = data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        
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
                                    # Split into sections if multiple items
                                    sections = details_text.split('\n• ')
                                    for section in sections:
                                        if section.strip():
                                            # Remove redundant prefixes and codes
                                            cleaned = re.sub(r'REFERENCE TO A NATIONAL CODE\s+', '', section)
                                            cleaned = re.sub(r'Ref\s+', '', cleaned)
                                            st.markdown(f"• {cleaned.strip()}")
                                
                                # Show effect if meaningful
                                effect = event.get("@infl", "").strip()
                                if effect and effect != "+":
                                    st.write("**Effect:**", effect)

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

def main():
    st.set_page_config(
        page_title="Patent History Analyzer",
        page_icon="📄",
        layout="wide"
    )

    st.title("Patent History Analyzer")
    st.markdown("### Enter Patent Publication Number")

    # Input section
    col1, col2 = st.columns([3, 1])
    with col1:
        patent_number = st.text_input("Patent Number", value="EP1000000", help="Example: EP1000000")
    with col2:
        analyze_button = st.button("Analyze Patent", type="primary")

    # Initialize EPO client
    client = EPOClient()

    if analyze_button:
        try:
            with st.spinner("Fetching patent data..."):
                # Get patent data
                data = client.get_patent_data(patent_number)

                # Display results in tabs
                tab1, tab2, tab3, tab4 = st.tabs(["Bibliographic Data", "Legal Status", "Patent Family", "Claims Analysis"])
                
                with tab1:
                    display_bibliographic_data(data)

                with tab2:
                    display_legal_events(data)

                with tab3:
                    display_family_data(data)

                # --- Claims analysis integration (added) ---
                with tab4:
                    try:
                        st.markdown("#### Claims Extraction & Analysis")
                        claims = ClaimsParser.extract_claims(data)
                        st.write(f"Extracted {len(claims)} claim(s).")

                        # instantiate analyzer (uses OPENROUTER_API_KEY env if set)
                        analyzer = ClaimAnalyzer(openrouter_api_key=os.getenv("OPENROUTER_API_KEY"))

                        if claims:
                            st.markdown("##### Summaries")
                            summaries = analyzer.summarize_claims(claims, use_llm=True)
                            for s in summaries:
                                st.write(f"- Claim {s.get('id')}: {s.get('summary')}")
                            
                            # If multiple claim sets are provided (rare in single JSON), attempt comparison:
                            # Look for 'claims_versions' key or 'claims_variants' in raw data as optional extension
                            alt_claims_node = data.get("claims_versions") or data.get("claims_variants")
                            if alt_claims_node:
                                # normalize to first alt set (basic)
                                alt_set = alt_claims_node if isinstance(alt_claims_node, list) else [alt_claims_node]
                                # take first alt that looks like claim list
                                alt_claims = []
                                for cand in alt_set:
                                    if isinstance(cand, dict) and "claim" in cand:
                                        alt_claims = ClaimsParser.extract_claims({"bibliographic": {"ops:world-patent-data": {"exchange-documents": {"exchange-document": [cand]}}}})
                                        if alt_claims:
                                            break
                                if alt_claims:
                                    st.markdown("##### Comparison with alternate claim set")
                                    comps = analyzer.compare_claim_sets(claims, alt_claims)
                                    comps = analyzer.detect_scope_changes(comps, use_llm=True)
                                    for comp in comps:
                                        st.write(f"Claim {comp['id']}: narrowed={comp['narrowed']}")
                                        if comp.get("diff"):
                                            st.write("  - Added:", comp['diff'].get("added", []))
                                            st.write("  - Removed:", comp['diff'].get("removed", []))
                                        if comp.get("llm_note"):
                                            st.write("  - LLM note:", comp["llm_note"])
                                else:
                                    st.info("No alternate claim set found in claims_versions.")
                        else:
                            st.info("No claims extracted from JSON.")
                    except Exception as e:
                        st.error(f"Claims analysis failed: {e}")

                # Add download button for full JSON
                st.download_button(
                    label="Download Full Data",
                    data=json.dumps(data, indent=2),
                    file_name=f"{patent_number}_analysis.json",
                    mime="application/json"
                )

        except Exception as e:
            st.error(f"Error: {str(e)}")
            st.info("Please check if the patent number is correct and try again.")

if __name__ == "__main__":
    main()