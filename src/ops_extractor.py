from typing import Dict, List, Optional
from datetime import datetime

EVENT_EFFECTS = {
    "AK": ["designation_recorded"],
    "PG25": ["lapse"],
    "INTG": ["grant"]
}

def _norm_date(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None

def to_extract(raw: Dict) -> Dict:
    """
    Normalize OPS payload to Extract JSON schema described in the task.
    Unknown nodes go into extras.unmapped_nodes. Paths preserve ops prefixes when possible.
    """
    payload = raw.get("payload", {}) if isinstance(raw, dict) else {}
    extract = {
        "details": {
            "patent_number": raw.get("ep_number"),
            "title": None,
            "assignee": None,
            "filing_date": None,
            "publication_date": None,
            "jurisdiction": "EP",
            "legal_status": None,
            "paths": {}
        },
        "events": [],
        "claims": [],
        "citations": [],
        "family": [],
        "designations": [],
        "coverage": {
            "events_present": 0,
            "claims_present": 0,
            "citations_present": 0,
            "designations_present": 0,
            "oppositions_present": 0
        },
        "extras": {"unmapped_nodes": []}
    }

    try:
        # Attempt to find exchange-document node(s)
        exch_docs = None
        # common shape: payload["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
        wpd = payload.get("ops:world-patent-data") or payload.get("world-patent-data") or payload
        if isinstance(wpd, dict):
            exch_docs = wpd.get("exchange-documents", {}).get("exchange-document") or wpd.get("exchange-document")
        if not exch_docs and isinstance(payload, dict):
            # fallback: search shallow keys
            for k, v in payload.items():
                if isinstance(v, dict) and "exchange-document" in v:
                    exch_docs = v.get("exchange-document")

        docs = exch_docs if isinstance(exch_docs, list) else ([exch_docs] if exch_docs else [])
        if docs:
            doc = docs[0] or {}
            # title
            inv_title = doc.get("invention-title") or []
            if isinstance(inv_title, list) and inv_title:
                # preserve path with ops: prefix where present in original
                extract["details"]["title"] = inv_title[0].get("#text") if isinstance(inv_title[0], dict) else str(inv_title[0])
                extract["details"]["paths"]["title"] = "/.../exchange-document/invention-title[0]/#text"
            # dates
            pub_date = doc.get("@date") or doc.get("publication-reference", {}).get("document-id", [{}])[0].get("date")
            extract["details"]["publication_date"] = _norm_date(pub_date)
            app_date = doc.get("bibliographic-data", {}).get("application-reference", {}).get("document-id", [{}])[0].get("date")
            extract["details"]["filing_date"] = _norm_date(app_date)
            # patent number
            country = doc.get("@country") or ""
            number = doc.get("@doc-number") or ""
            kind = doc.get("@kind") or ""
            extract["details"]["patent_number"] = f"{country}{number}{kind}".strip() or extract["details"]["patent_number"]

        # Extract legal events if present (OPS legal section may vary)
        legal_root = payload.get("legal") or payload.get("ops:legal") or payload
        # Try common nested path used previously
        patent_family = None
        if isinstance(payload, dict):
            patent_family = payload.get("ops:world-patent-data", {}).get("ops:patent-family") or payload.get("patent-family") or None
        if patent_family and isinstance(patent_family, dict):
            members = patent_family.get("ops:family-member") or []
            if not isinstance(members, list):
                members = [members]
            for m_idx, member in enumerate(members):
                ops_legal = member.get("ops:legal") or member.get("legal", [])
                if not isinstance(ops_legal, list):
                    ops_legal = [ops_legal] if ops_legal else []
                for l_idx, ev in enumerate(ops_legal):
                    if not isinstance(ev, dict):
                        continue
                    code = ev.get("@code") or ev.get("code") or ""
                    date = _norm_date(ev.get("@date") or ev.get("date") or "")
                    desc = ev.get("@desc") or ev.get("desc") or None
                    effects = EVENT_EFFECTS.get(code, ["unknown"])
                    path = f"/legal/ops:world-patent-data/ops:patent-family/ops:family-member[{m_idx}]/ops:legal[{l_idx}]"
                    extract["events"].append({
                        "date": date,
                        "code": code,
                        "description": desc,
                        "effects": effects,
                        "path": path
                    })
                    if code not in EVENT_EFFECTS:
                        extract["extras"]["unmapped_nodes"].append({"path": path, "node": ev})

        # Citations and claims: attempt shallow extraction where present
        bib_cits = payload.get("bibliographic", {}).get("references-cited") or payload.get("references-cited")
        if bib_cits:
            refs = bib_cits.get("citation") if isinstance(bib_cits, dict) else bib_cits
            if isinstance(refs, list):
                for i, r in enumerate(refs):
                    cid = r.get("doc-id", {}).get("country", "") + r.get("doc-id", {}).get("doc-number", "")
                    path = f"/bibliographic/.../references-cited/citation[{i}]"
                    extract["citations"].append({"id": f"CIT:{i}", "kind": "bibliographic", "title": r.get("title"), "path": path})
        # keep claims array empty if absent; LLM will use '(Omitted pending source)'

        # Coverage counts
        extract["coverage"]["events_present"] = len(extract["events"])
        extract["coverage"]["claims_present"] = len(extract["claims"])
        extract["coverage"]["citations_present"] = len(extract["citations"])
        extract["coverage"]["designations_present"] = len(extract["designations"])
    except Exception as e:
        extract["extras"]["unmapped_nodes"].append(str(e))

    return extract