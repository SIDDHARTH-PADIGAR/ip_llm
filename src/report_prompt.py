from typing import Dict
import json

NUDGE = (
    "You are given a JSON extract below. Use only this data. "
    "Every sentence must end with a JSON path token like [JSON:/...]. "
    "If no path exists for a statement, output '(Omitted pending source)'. "
    "Jurisdiction=EP: use 'prosecution interpretation'; do not use 'estoppel'.\n\n"
)

def build_prompts(details: Dict, extract: Dict) -> Dict:
    """
    Return dict with keys: executive_summary, timeline_analysis, prior_art_analysis.
    Extract JSON is included in a fenced block.
    """
    extract_block = json.dumps(extract, ensure_ascii=False, indent=2)
    json_block = f"```json\n{extract_block}\n```"
    return {
        "executive_summary": NUDGE + "Produce a concise executive summary (2-4 sentences) focused on enforceability and high-level risk/recommendations.\n\n" + json_block,
        "timeline_analysis": NUDGE + "Analyze the prosecution timeline, highlight pivotal events and their legal effect.\n\n" + json_block,
        "prior_art_analysis": NUDGE + "Analyze citations and provide ranked/practical prior-art guidance for validity and enforcement.\n\n" + json_block
    }