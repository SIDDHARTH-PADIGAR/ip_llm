import streamlit as st
import json
from api.epo_client import EPOClient
from datetime import datetime

def format_date(date_str):
    """Convert date string to readable format"""
    if not date_str:
        return "N/A"
    try:
        if len(date_str) == 8:
            return datetime.strptime(date_str, '%Y%m%d').strftime('%d-%m-%Y')
        return date_str
    except:
        return date_str

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

def display_legal_events(data):
    try:
        st.markdown("#### Legal Events Timeline")
        legal_data = data["legal"]["ops:world-patent-data"]["ops:patent-family"]
        
        if "ops:family-member" in legal_data:
            for member in legal_data["ops:family-member"]:
                if "ops:legal" in member:
                    events = member["ops:legal"]
                    for event in events:
                        if "@desc" in event and "@code" in event:
                            with st.expander(f"{event['@desc']} ({event['@code'].strip()})"):
                                st.write("**Event Date:**", format_date(event.get("@dateMigr", "N/A")))
                                if "ops:pre" in event and "#text" in event["ops:pre"]:
                                    st.write("**Details:**", event["ops:pre"]["#text"])
                                st.write("**Effect:**", event.get("@infl", "N/A"))

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
                tab1, tab2, tab3 = st.tabs(["Bibliographic Data", "Legal Status", "Patent Family"])
                
                with tab1:
                    display_bibliographic_data(data)

                with tab2:
                    display_legal_events(data)

                with tab3:
                    display_family_data(data)

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