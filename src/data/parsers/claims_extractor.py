from typing import Dict, List

def _ensure_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

class ClaimsParser:
    """
    Robust claim extractor for EPO-like JSON responses.
    Returns list of dicts: { 'id': str, 'text': str, 'raw': dict }
    """
    @staticmethod
    def _scan_for_claims(node):
        """Recursively find any 'claim' nodes under a JSON structure."""
        found = []
        if isinstance(node, dict):
            for k, v in node.items():
                # match keys that start with 'claim' (claim / claims / claim-text etc)
                if k.lower().startswith("claim"):
                    found.extend(_ensure_list(v))
                else:
                    found.extend(ClaimsParser._scan_for_claims(v))
        elif isinstance(node, list):
            for it in node:
                found.extend(ClaimsParser._scan_for_claims(it))
        return found

    @staticmethod
    def extract_claims(data: Dict) -> List[Dict]:
        claims_out: List[Dict] = []
        try:
            # gather all exchange-document entries (defensive)
            exchanges = []
            try:
                exch = data.get("bibliographic", {}) \
                           .get("ops:world-patent-data", {}) \
                           .get("exchange-documents", {}) \
                           .get("exchange-document", [])
                if isinstance(exch, list):
                    exchanges = exch
                elif isinstance(exch, dict):
                    exchanges = [exch]
            except Exception:
                exchanges = []

            # scan each doc for claim-like structures
            claim_nodes = []
            for doc in exchanges:
                biblio = doc.get("bibliographic-data", {}) if isinstance(doc, dict) else {}
                candidates = [
                    biblio.get("claims"),
                    doc.get("claims"),
                    biblio,
                    doc
                ]
                for cand in candidates:
                    if cand:
                        claim_nodes.extend(ClaimsParser._scan_for_claims(cand))

            # fallback: scan entire JSON
            if not claim_nodes:
                claim_nodes = ClaimsParser._scan_for_claims(data)

            # Normalize claim nodes
            for idx, c in enumerate(_ensure_list(claim_nodes), start=1):
                if not isinstance(c, dict):
                    text = str(c).strip()
                    claims_out.append({"id": str(idx), "text": text, "raw": c})
                    continue

                claim_id = c.get("@num") or c.get("@id") or c.get("id") or c.get("claim-num") or str(idx)

                # collect possible text candidates
                text_candidates = []
                if "claim-text" in c:
                    ct = c["claim-text"]
                    if isinstance(ct, dict):
                        text_candidates.append(ct.get("#text") or ct.get("text") or "")
                    else:
                        text_candidates.append(str(ct))
                if "#text" in c:
                    text_candidates.append(c.get("#text"))
                if "text" in c:
                    text_candidates.append(c.get("text"))
                if "claim" in c and isinstance(c["claim"], str):
                    text_candidates.append(c["claim"])

                # flatten nested structures for strings
                if not any(text_candidates):
                    for v in c.values():
                        if isinstance(v, str) and len(v) > 1:
                            text_candidates.append(v)
                        elif isinstance(v, dict) and "#text" in v:
                            text_candidates.append(v["#text"])
                        elif isinstance(v, list):
                            for it in v:
                                if isinstance(it, dict) and "#text" in it:
                                    text_candidates.append(it["#text"])
                                elif isinstance(it, str):
                                    text_candidates.append(it)

                claim_text = next((t for t in text_candidates if t), "")
                claim_text = claim_text.strip()

                claims_out.append({
                    "id": str(claim_id),
                    "text": claim_text,
                    "raw": c
                })

        except Exception as e:
            # do not raise; return what we have
            print(f"[ClaimsParser] extraction error: {e}")

        return claims_out