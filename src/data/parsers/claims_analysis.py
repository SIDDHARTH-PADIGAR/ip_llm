from typing import List, Dict, Optional
from difflib import SequenceMatcher
import os
import logging

# attempt to import your OpenRouter client. If missing, analysis will use local heuristics.
try:
    from api.openrouter_llm_client import OpenRouterClient
except Exception:
    OpenRouterClient = None

LOG = logging.getLogger("claim_analyzer")

def _simple_diff(a: str, b: str) -> Dict:
    """Return basic diff info: additions, removals using SequenceMatcher."""
    s = SequenceMatcher(None, a, b)
    added = []
    removed = []
    for opcode, i1, i2, j1, j2 in s.get_opcodes():
        if opcode == "insert":
            added.append(b[j1:j2])
        elif opcode == "delete":
            removed.append(a[i1:i2])
        elif opcode == "replace":
            removed.append(a[i1:i2])
            added.append(b[j1:j2])
    return {"added": added, "removed": removed}

class ClaimAnalyzer:
    """
    Provides claim-level analysis:
      - summary per claim
      - pairwise comparison (if multiple versions provided)
      - narrowing/broadening heuristic
      - LLM-backed analysis via OpenRouterClient when available
    """

    def __init__(self, openrouter_api_key: Optional[str] = None):
        self.client = None
        if OpenRouterClient is not None:
            try:
                # try to instantiate client (implementation-agnostic)
                if openrouter_api_key:
                    self.client = OpenRouterClient(api_key=openrouter_api_key)
                else:
                    # allow client to read from env inside its constructor
                    self.client = OpenRouterClient()
            except Exception as e:
                LOG.warning("OpenRouter client init failed: %s", e)
                self.client = None

    def _call_llm(self, prompt: str) -> Optional[str]:
        if not self.client:
            return None
        # try common method names, handle gracefully
        try:
            if hasattr(self.client, "generate") and callable(self.client.generate):
                return self.client.generate(prompt)
            if hasattr(self.client, "chat") and callable(self.client.chat):
                # accept both simple string or messages; try simple
                return self.client.chat(prompt)
            if hasattr(self.client, "complete") and callable(self.client.complete):
                return self.client.complete(prompt)
            if hasattr(self.client, "create_completion") and callable(self.client.create_completion):
                return self.client.create_completion(prompt)
        except Exception as e:
            LOG.warning("LLM call failed: %s", e)
            return None
        return None

    def summarize_claims(self, claims: List[Dict], use_llm: bool = True) -> List[Dict]:
        """
        Return list of { id, text, short_summary }.
        If LLM available and use_llm True, ask LLM for concise summary per claim.
        Otherwise use simple heuristic: first 200 chars as summary.
        """
        out = []
        if use_llm and self.client:
            # build prompt with all claims
            joined = "\n\n".join([f"Claim {c['id']}: {c['text']}" for c in claims])
            prompt = (
                "You are a concise patent analyst. For each claim below, produce a one-sentence summary of the "
                "technical scope in plain English, prefixed by the claim id.\n\n"
                f"{joined}\n\nRespond in JSON array of objects: {{\"id\":\"...\", \"summary\":\"...\"}}"
            )
            resp = self._call_llm(prompt)
            if resp:
                # try to parse naive JSON appearances
                try:
                    import json
                    parsed = json.loads(resp)
                    for item in parsed:
                        out.append({"id": item.get("id"), "text": next((c["text"] for c in claims if str(c["id"]) == str(item.get("id"))), ""), "summary": item.get("summary")})
                    return out
                except Exception:
                    # fall back to line-splitting
                    lines = (resp or "").splitlines()
                    for line in lines:
                        # try "id: summary"
                        if ":" in line:
                            cid, summ = line.split(":", 1)
                            out.append({"id": cid.strip(), "text": next((c["text"] for c in claims if str(c["id"]) == cid.strip()), ""), "summary": summ.strip()})
                    if out:
                        return out
        # fallback simple summarization
        for c in claims:
            text = c.get("text", "") or ""
            summary = (text[:200] + "...") if len(text) > 200 else text
            out.append({"id": c.get("id"), "text": text, "summary": summary})
        return out

    def compare_claim_sets(self, base: List[Dict], amended: List[Dict]) -> List[Dict]:
        """
        Pairwise compare claims from two sets by id (or position when id missing).
        Returns list of comparisons: { id, base_text, amended_text, diff: {added, removed}, narrowed: bool }
        """
        results = []
        # build map by id
        base_map = {str(c.get("id") or idx): c for idx, c in enumerate(base, start=1)}
        amend_map = {str(c.get("id") or idx): c for idx, c in enumerate(amended, start=1)}

        all_keys = sorted(set(list(base_map.keys()) + list(amend_map.keys())), key=lambda x: int(x) if str(x).isdigit() else x)
        for k in all_keys:
            b = base_map.get(k)
            a = amend_map.get(k)
            btxt = b.get("text","") if b else ""
            atxt = a.get("text","") if a else ""
            diff = _simple_diff(btxt, atxt)
            # heuristic narrowing: if amended text shorter and removed tokens appear more than added
            narrowed = False
            try:
                removed_total = sum(len(x.strip()) for x in diff.get("removed", []))
                added_total = sum(len(x.strip()) for x in diff.get("added", []))
                if removed_total > added_total and len(atxt) < len(btxt):
                    narrowed = True
            except Exception:
                narrowed = False
            results.append({
                "id": k,
                "base_text": btxt,
                "amended_text": atxt,
                "diff": diff,
                "narrowed": narrowed
            })
        return results

    def detect_scope_changes(self, comparisons: List[Dict], use_llm: bool = True) -> List[Dict]:
        """
        Given compare_claim_sets output, optionally annotate each change with LLM if available.
        Returns same list with added 'llm_note' when LLM responded.
        """
        if use_llm and self.client:
            for comp in comparisons:
                prompt = (
                    "You are a patent analyst. Given the base claim text and amended claim text, identify whether the claim "
                    "has been narrowed, broadened, or materially unchanged. List specific terms removed and added and a short reason.\n\n"
                    f"Base claim ({comp['id']}): {comp['base_text']}\n\nAmended claim ({comp['id']}): {comp['amended_text']}\n\n"
                    "Answer in JSON: {\"change\":\"narrowed|broadened|unchanged\",\"added\":[...],\"removed\":[...],\"reason\":\"...\"}"
                )
                resp = self._call_llm(prompt)
                if resp:
                    try:
                        import json
                        comp['llm_note'] = json.loads(resp)
                        continue
                    except Exception:
                        comp['llm_note'] = {"raw": resp}
                else:
                    comp['llm_note'] = None
        else:
            # heuristic note: copy narrowed flag
            for comp in comparisons:
                comp['llm_note'] = {"change": "narrowed" if comp.get("narrowed") else "unchanged", "added": comp["diff"].get("added", []), "removed": comp["diff"].get("removed", []), "reason": "heuristic"}
        return comparisons