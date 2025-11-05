import re
import os
import json
import requests
from typing import List, Dict
from datetime import datetime

class PriorArtCorrelator:
    def __init__(self, patent_data: Dict, cache_path: str = None):
        self.data = patent_data
        self.cache_path = cache_path or os.path.join(os.getcwd(), "prior_art_cache.json")
        self.cache = self._load_cache()

    def _load_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception:
            pass

    def extract_citations(self) -> List[Dict]:
        """Scan bibliographic/legal sections for cited prior art; return list of raw citation dicts"""
        citations = []
        seen_citations = set()  # Track unique citations
        
        # scan bibliographic -> references-cited (if present)
        biblio = self.data.get("bibliographic", {}).get("ops:world-patent-data", {}) \
                        .get("exchange-documents", {}).get("exchange-document", [])
        doc = biblio[0] if isinstance(biblio, list) and biblio else (biblio if isinstance(biblio, dict) else {})
        refs = doc.get("references-cited") or {}
        for r in (refs.get("citation") or []):
            if isinstance(r, dict):
                text = r.get("patcit", r.get("citation-text", "")) or ""
                if text and text not in seen_citations:
                    seen_citations.add(text)
                    citations.append({"source": "bibliographic", "raw": text})
        
        # scan legal events details for embedded citations
        legal_section = self.data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {}) 
        for member in (legal_section.get("ops:family-member") or []):
            for ev in (member.get("ops:legal") or []):
                pre = ev.get("ops:pre") or ev.get("pre") or {}
                text = ""
                if isinstance(pre, dict):
                    text = pre.get("#text") or pre.get("text") or ""
                elif isinstance(pre, list):
                    text = " ".join([ (p.get("#text") if isinstance(p, dict) else str(p)) for p in pre ])
                elif isinstance(pre, str):
                    text = pre
                
                # simple heuristic: look for patterns like "EP 99203729A" or "US2002/0123456"
                for m in re.finditer(r'\b([A-Z]{2,3}\s*\d{4,}\w?)\b', text):
                    citation = m.group(0)
                    if citation not in seen_citations:
                        seen_citations.add(citation)
                        citations.append({"source": "legal", "raw": citation, "context": text})
        
        return citations

    def normalize_citation(self, raw: str) -> Dict:
        """Return normalized citation (country, number, kind) where possible"""
        s = raw.strip()
        # quick normalization for patterns like "EP 99203729A"
        m = re.search(r'\b([A-Z]{2,3})\s*0*([0-9]{4,})\s*([A-Z0-9]?)\b', s.replace('-', ' '))
        if m:
            country = m.group(1)
            number = m.group(2)
            kind = m.group(3) or ""
            return {"country": country, "number": number, "kind": kind, "raw": raw}
        return {"raw": raw}
    
    

    def match_to_rejections(self) -> List[Dict]:
        """
        Map extracted/normalized citations to legal events indicating rejections/references.
        Returns list of mappings: {citation, matched_events: [...], confidence}
        """
        citations = self.extract_citations()
        results = []
        # Use a set to track unique citations
        seen_citations = set()
        
        # index legal events by text for matching
        legal_events = []
        legal_section = self.data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        for member in (legal_section.get("ops:family-member") or []):
            for ev in (member.get("ops:legal") or []):
                desc = ev.get("@desc", "")
                pre = ev.get("ops:pre") or ev.get("pre") or {}
                text = ""
                if isinstance(pre, dict):
                    text = pre.get("#text") or pre.get("text") or ""
                elif isinstance(pre, list):
                    text = " ".join([ (p.get("#text") if isinstance(p, dict) else str(p)) for p in pre ])
                elif isinstance(pre, str):
                    text = pre
                legal_events.append({"code": ev.get("@code"), "desc": desc, "text": f"{desc} {text}", "raw": ev})
        
        for c in citations:
            norm = self.normalize_citation(c["raw"])
            # Create a unique key for the citation
            citation_key = f"{norm.get('country','')}{norm.get('number','')}{norm.get('kind','')}"
            
            # Skip if we've seen this citation before
            if citation_key in seen_citations:
                continue
            seen_citations.add(citation_key)
            
            matches = []
            for ev in legal_events:
                # heuristic: citation raw appears in event text
                if c["raw"].lower() in ev["text"].lower() or (norm.get("number") and norm["number"] in ev["text"]):
                    matches.append(ev)
            conf = "high" if matches else "low"
            results.append({
                "citation": norm,
                "matches": matches,
                "confidence": conf,
                "source": c.get("source")
            })
        
        self.cache_key = "prior_art_results"
        self.cache[self.cache_key] = results
        self._save_cache()
        return results
    
    

    def query_llm(self, text: str) -> str:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return "LLM analysis not available - API key not found"
        
        url = "https://openrouter.ai/api/v1/chat/completions"   # Correct URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501"
        }
        
        payload = {
            "model": "mistralai/mistral-7b-instruct",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a senior IP strategist and patent analyst at a top law firm. Provide detailed, actionable analysis with specific examples and business-focused insights."
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        }
        
        try:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(max_retries=3)
            session.mount('https://', adapter)
            
            response = session.post(url, headers=headers, json=payload, timeout=(5, 30))
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Analysis failed: {str(e)}"
        finally:
            session.close()


    def _gather_events_for_viz(self) -> List[Dict]:
        """Format legal events for visualization"""
        events = []
        legal_data = self.data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        
        if "ops:family-member" in legal_data:
            for member in legal_data["ops:family-member"]:
                if "ops:legal" in member:
                    for event in member["ops:legal"]:
                        if isinstance(event, dict):
                            # Extract date
                            date_str = None
                            
                            # Try to get date from event attributes
                            if "@date" in event:
                                date_str = event["@date"]
                            elif "@effective-date" in event:
                                date_str = event["@effective-date"]
                            
                            # If no date found, try to extract from text
                            if not date_str:
                                pre = event.get("ops:pre", {})
                                if isinstance(pre, dict):
                                    text = pre.get("#text", "")
                                    date_match = re.search(r'(\d{4})[-.]?(\d{2})[-.]?(\d{2})', text)
                                    if date_match:
                                        date_str = ''.join(date_match.groups())

                            # Format date if found
                            if date_str:
                                try:
                                    # Handle different date formats
                                    if len(date_str) == 8:  # YYYYMMDD
                                        date = datetime.strptime(date_str, "%Y%m%d")
                                    else:  # Try ISO format
                                        date = datetime.fromisoformat(date_str)
                                    
                                    formatted_date = date.strftime("%Y-%m-%d")
                                    
                                    events.append({
                                        "date": formatted_date,
                                        "code": event.get("@code", ""),
                                        "desc": event.get("@desc", ""),
                                        "text": event.get("ops:pre", {}).get("#text", "") if isinstance(event.get("ops:pre"), dict) else "",
                                        "party": "EPO"
                                    })
                                except (ValueError, TypeError):
                                    continue
        
        # Sort events by date
        return sorted(events, key=lambda x: x["date"])

    def get_claim_versions(self) -> List[Dict]:
        """Extract and format claim versions for visualization"""
        versions = []
        
        # Try to get original claims
        try:
            original_claims = self.data.get("claims", {}).get("ops:world-patent-data", {}) \
                            .get("exchange-documents", {}).get("exchange-document", [{}])[0] \
                            .get("claims", {}).get("claim", [])
            
            if original_claims:
                versions.append({
                    "version": "Original",
                    "claims": [{"id": str(i+1), "text": c.get("claim-text", "")} 
                             for i, c in enumerate(original_claims)]
                })
        except (KeyError, IndexError):
            pass

        # Try to get amended claims from legal events
        legal_data = self.data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        if "ops:family-member" in legal_data:
            for member in legal_data["ops:family-member"]:
                if "ops:legal" in member:
                    for event in member["ops:legal"]:
                        if isinstance(event, dict) and "amended" in event.get("@desc", "").lower():
                            pre = event.get("ops:pre", {})
                            if isinstance(pre, dict):
                                text = pre.get("#text", "")
                                # Extract claims using regex
                                claims = []
                                claim_matches = re.finditer(r'Claim\s+(\d+)[:\.]?\s+([^(Claim \d+)]+)', text, re.IGNORECASE)
                                for m in claim_matches:
                                    claims.append({
                                        "id": m.group(1),
                                        "text": m.group(2).strip()
                                    })
                                if claims:
                                    versions.append({
                                        "version": f"Amendment {event.get('@date', 'Unknown')}",
                                        "claims": claims
                                    })

        return versions
    
    