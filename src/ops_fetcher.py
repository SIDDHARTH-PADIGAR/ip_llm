from typing import Dict
from datetime import datetime
import hashlib
import json
from api.epo_client import EPOClient

def get_raw(ep_number: str) -> Dict:
    """
    Wrap existing EPO fetch with metadata. Returns:
    {"payload": raw_json, "retrieved_at": iso_ts, "hash": sha256, "ep_number": ep_number}
    """
    client = EPOClient()
    payload = client.get_patent_data(ep_number)
    wrapper = {
        "payload": payload,
        "retrieved_at": datetime.now().isoformat(),
        "hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
        "ep_number": ep_number
    }
    return wrapper