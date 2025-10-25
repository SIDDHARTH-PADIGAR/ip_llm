from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

@dataclass
class Title:
    text: str
    language: str

@dataclass
class LegalEvent:
    code: str
    description: str
    date: str
    effect: str
    details: Optional[str] = None

@dataclass
class PatentData:
    patent_number: str
    family_id: str
    publication_date: str
    titles: List[Title]
    applicants: List[str]
    inventors: List[str]
    ipc_classes: List[str]
    legal_events: List[LegalEvent]