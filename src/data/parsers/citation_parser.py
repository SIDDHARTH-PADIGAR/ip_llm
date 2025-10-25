from typing import Dict, List

class CitationParser:
    @staticmethod
    def extract_citations(data: Dict) -> List[Dict]:
        """Extract citation information from patent data"""
        citations = []
        try:
            doc = data["bibliographic"]["ops:world-patent-data"]["exchange-documents"]["exchange-document"][0]
            refs = doc.get("bibliographic-data", {}).get("references-cited", {}).get("citation", [])
            
            for ref in refs:
                if "patcit" in ref:
                    doc_id = ref["patcit"]["document-id"][0]
                    citations.append({
                        "type": "patent",
                        "country": doc_id.get("country", ""),
                        "doc_number": doc_id.get("doc-number", ""),
                        "kind": doc_id.get("kind", ""),
                        "date": doc_id.get("date", "")
                    })
                elif "nplcit" in ref:
                    citations.append({
                        "type": "non-patent",
                        "text": ref["nplcit"].get("text", "")
                    })
        except Exception as e:
            print(f"Error extracting citations: {str(e)}")
        
        return citations