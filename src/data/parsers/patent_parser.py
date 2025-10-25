from typing import Dict, List
from datetime import datetime
import re
from ..models.patent_data import PatentData, Title, LegalEvent

def _ensure_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

class PatentDataParser:
    @staticmethod
    def extract_date_from_text(text: str) -> str:
        """Extract a reasonable date from text.

        Try strict patterns first (e.g. 'DATE 20020423', 'Effective DATE 20020423'),
        then try plain YYYYMMDD / YYYY-MM-DD / DD-MM-YYYY tokens.
        Return 'N/A' when no sensible date found.
        """
        if not text:
            return "N/A"

        s = str(text).strip()
        now_year = datetime.now().year

        # 1) Strict strptime patterns for common labeled forms
        for fmt in ("DATE %Y%m%d", "Effective DATE %Y%m%d"):
            try:
                # try to parse when the format appears in the string
                if re.search(r'\b' + fmt.split()[0], s, flags=re.IGNORECASE):
                    # extract trailing 8-digit token if present
                    m = re.search(r'(\d{8})', s)
                    if m:
                        dt = datetime.strptime(m.group(1), "%Y%m%d")
                        if 1900 <= dt.year <= now_year + 1:
                            return dt.strftime("%d-%m-%Y")
            except Exception:
                pass

        # 2) Direct strptime when entire string equals YYYYMMDD
        if s.isdigit() and len(s) == 8:
            try:
                dt = datetime.strptime(s, "%Y%m%d")
                if 1900 <= dt.year <= now_year + 1:
                    return dt.strftime("%d-%m-%Y")
            except Exception:
                pass

        # 3) Regex lookups for common tokens
        m = re.search(r'\b(\d{8})\b', s)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y%m%d")
                if 1900 <= dt.year <= now_year + 1:
                    return dt.strftime("%d-%m-%Y")
            except Exception:
                pass

        # 4) YYYY[-/.]MM[-/.]DD
        m = re.search(r'\b(\d{4})[\/\-\.\s](\d{2})[\/\-\.\s](\d{2})\b', s)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%Y%m%d")
                if 1900 <= dt.year <= now_year + 1:
                    return dt.strftime("%d-%m-%Y")
            except Exception:
                pass

        # 5) DD[-/.]MM[-/.]YYYY
        m = re.search(r'\b(\d{2})[\/\-\.\s](\d{2})[\/\-\.\s](\d{4})\b', s)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(3)}{m.group(2)}{m.group(1)}", "%Y%m%d")
                if 1900 <= dt.year <= now_year + 1:
                    return dt.strftime("%d-%m-%Y")
            except Exception:
                pass

        return "N/A"

    @staticmethod
    def format_date(date_str: str) -> str:
        """Tolerant wrapper: accept datetime or delegate to extract_date_from_text."""
        if not date_str:
            return "N/A"
        if isinstance(date_str, datetime):
            y = date_str.year
            now_year = datetime.now().year
            if 1900 <= y <= now_year + 1:
                return date_str.strftime("%d-%m-%Y")
            return "N/A"
        return PatentDataParser.extract_date_from_text(str(date_str).strip())

    @staticmethod
    def parse_patent_data(data: Dict) -> PatentData:
        # safe navigation
        exchange = data.get("bibliographic", {}) \
                       .get("ops:world-patent-data", {}) \
                       .get("exchange-documents", {}) \
                       .get("exchange-document", [])
        doc = exchange[0] if isinstance(exchange, list) and exchange else (exchange if isinstance(exchange, dict) else {})
        biblio = doc.get("bibliographic-data", {}) or {}

        country = doc.get("@country", "")
        doc_number = doc.get("@doc-number", "")
        kind = doc.get("@kind", "")
        patent_number = f"{country}{doc_number}{kind}"
        family_id = doc.get("@family-id", "")

        pub_doc_id = (biblio.get("publication-reference", {}).get("document-id", []) or [{}])
        pub_date_raw = pub_doc_id[0].get("date") if isinstance(pub_doc_id, list) and pub_doc_id else pub_doc_id.get("date") if isinstance(pub_doc_id, dict) else None
        publication_date = PatentDataParser.format_date(pub_date_raw)

        titles: List[Title] = []
        for t in _ensure_list(biblio.get("invention-title")):
            if isinstance(t, dict):
                text = t.get("#text") or ""
                lang = (t.get("@lang") or "").upper()
                if text:
                    titles.append(Title(text=text, language=lang))

        applicants = []
        inventors = []
        parties = biblio.get("parties") or {}
        for app in _ensure_list(parties.get("applicants", {}).get("applicant")):
            if isinstance(app, dict):
                name = app.get("applicant-name", {}).get("name") or app.get("name")
                if name:
                    applicants.append(name)
        for inv in _ensure_list(parties.get("inventors", {}).get("inventor")):
            if isinstance(inv, dict):
                name = inv.get("inventor-name", {}).get("name") or inv.get("name")
                if name:
                    inventors.append(name)

        ipc_raw = biblio.get("classification-ipc", {}).get("text", [])
        ipc_classes = []
        for ipc in _ensure_list(ipc_raw):
            if isinstance(ipc, str):
                ipc_classes.append(ipc)
            elif isinstance(ipc, dict) and "#text" in ipc:
                ipc_classes.append(ipc["#text"])

        # legal events
        legal_events = []
        legal_section = data.get("legal", {}) \
                            .get("ops:world-patent-data", {}) \
                            .get("ops:patent-family", {}) or {}
        for member in _ensure_list(legal_section.get("ops:family-member")):
            for raw_legal in _ensure_list(member.get("ops:legal")):
                items = raw_legal if isinstance(raw_legal, list) else [raw_legal]
                for ev in items:
                    if not isinstance(ev, dict):
                        continue
                    code = ev.get("@code", "")
                    desc = ev.get("@desc", "") or ev.get("description", "")
                    # primary date sources
                    date_raw = ev.get("@dateMigr") or ev.get("@date") or ev.get("date") or ""
                    date = PatentDataParser.format_date(date_raw)

                    # If no date found, try multiple fallback sources (details, description, notes, or full event text)
                    if date == "N/A":
                        candidates = []
                        # ops:pre / pre text
                        pre = ev.get("ops:pre") or ev.get("pre")
                        if pre:
                            if isinstance(pre, dict):
                                candidates.append(pre.get("#text") or pre.get("text") or "")
                            elif isinstance(pre, list):
                                candidates.append(" ".join([ (p.get("#text") if isinstance(p, dict) else str(p)) for p in pre ]))
                            else:
                                candidates.append(str(pre))
                        # explicit description fields
                        candidates.append(ev.get("description", "") or "")
                        candidates.append(ev.get("@desc", "") or "")
                        # other possible fields
                        candidates.append(ev.get("note", "") or "")
                        candidates.append(ev.get("ops:note", "") or "")
                        candidates.append(ev.get("text", "") or "")
                        # stringify entire event dict as last resort
                        try:
                            candidates.append(str(ev))
                        except Exception:
                            pass

                        # scan candidates for a date token
                        for cand in candidates:
                            if not cand:
                                continue
                            d = PatentDataParser.extract_date_from_text(cand)
                            if d != "N/A":
                                date = d
                                break

                    effect = ev.get("@infl", "")
                    details = None
                    pre = ev.get("ops:pre") or ev.get("pre")
                    if isinstance(pre, dict):
                        details = pre.get("#text") or pre.get("text") or str(pre)
                    elif isinstance(pre, list):
                        parts = []
                        for p in pre:
                            if isinstance(p, dict):
                                parts.append(p.get("#text") or p.get("text") or str(p))
                            else:
                                parts.append(str(p))
                        details = " ".join(parts)
                    elif pre is not None:
                        details = str(pre)
                    legal_events.append(LegalEvent(code=code, description=desc, date=date, effect=effect, details=details))

        # put valid dates first
        legal_events.sort(key=lambda e: (e.date == "N/A", e.date))
        return PatentData(
            patent_number=patent_number,
            family_id=family_id,
            publication_date=publication_date,
            titles=titles,
            applicants=applicants,
            inventors=inventors,
            ipc_classes=ipc_classes,
            legal_events=legal_events
        )