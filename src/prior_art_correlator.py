import re
import os
import json
import requests
from typing import List, Dict

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

    def query_llm_for_ambiguous(self, citation, events) -> str:
        """Optional: call LLM to decide mapping when heuristic confidence is low"""
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key or not events:
            return "LLM not available"
        prompt = f"Does the following prosecution event text indicate that citation {citation.get('raw')} was applied against specific claims?\\n\\nCitation: {citation}\\n\\nEvents:\\n"
        for ev in events:
            prompt += f"- Code: {ev.get('code')}, Desc: {ev.get('desc')}, Text: {ev.get('text')[:400]}\\n"
        # minimal call pattern; adapt to your LLM wrapper
        try:
            resp = requests.post(
                "https://api.openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-3.5-turbo",
                    "messages":[{"role":"user","content":prompt}]
                },
                timeout=30
            )
            return resp.json().get("choices",[{}])[0].get("message",{}).get("content","")
        except Exception as e:
            return f"LLM error: {e}"