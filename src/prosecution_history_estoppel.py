import json
import re
import requests
import os

class ProsecutionHistoryEstoppel:
    def __init__(self, data):
        """Initialize with patent data dictionary instead of JSON file"""
        self.legal_data = data
        self.estoppel_labels = {}

    def extract_timeline_text(self):
        """Extract legal event descriptions from the data dictionary"""
        events = []
        legal_data = self.legal_data.get("legal", {}).get("ops:world-patent-data", {}).get("ops:patent-family", {})
        if "ops:family-member" in legal_data:
            for member in legal_data["ops:family-member"]:
                if "ops:legal" in member:
                    for event in member["ops:legal"]:
                        if isinstance(event, dict):
                            desc = event.get("@desc", "")
                            if desc:
                                events.append(desc)
        return events

    def detect_scope_limiting_arguments(self, events):
        scope_limiting_phrases = [
            r'\bnot claimed\b',
            r'\blimited to\b',
            r'\bonly\b',
            r'\bexclude\b',
            r'\bexcept\b',
            r'\bnot applicable\b',
            r'\bamended\b',
            r'\bnarrow\w*\b',  # matches narrow, narrowed, narrowing
            r'\brestrict\w*\b',  # matches restrict, restricted, restricting
            r'\bdisclaim\w*\b',  # matches disclaim, disclaiming, disclaimer
            r'\bwithdraw\w*\b',  # matches withdraw, withdrawn, withdrawing
            r'\bmodif\w*\b',     # matches modify, modified, modification
            r'\bspecific\w*\b',  # matches specific, specifically
            r'\bparticular\w*\b' # matches particular, particularly
        ]
        
        detected_arguments = []
        for event in events:
            for phrase in scope_limiting_phrases:
                if re.search(phrase, event, re.IGNORECASE):
                    detected_arguments.append(event)
                    break
        return detected_arguments

    def query_llm(self, text):
        """Query OpenRouter LLM for estoppel analysis"""
        url = "https://api.openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501"
        }
        
        system_prompt = """You are a patent law expert specializing in prosecution history estoppel analysis.
        
    For the following legal event, provide a structured analysis:

    1. Estoppel Assessment
    - Identify if prosecution history estoppel applies
    - Specify the type (amendment-based or argument-based)
    - Detail which claim elements are affected

    2. Scope Impact
    - Analyze how the prosecution record limits claim interpretation
    - Identify surrendered subject matter
    - Assess the doctrine of equivalents implications

    3. Legal Implications
    - Evaluate enforceability impact
    - Flag potential validity issues
    - Note any strategic considerations

    Be specific in citing language that supports your conclusions. Format in clear sections."""

        payload = {
            "model": "mistralai/mistral-7b-instruct",
            "messages": [{
                "role": "system",
                "content": system_prompt
            }, {
                "role": "user",
                "content": text
            }],
            "temperature": 0.3
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Analysis failed: {str(e)}"

    def analyze_events(self):
        """Analyze events for estoppel relevance"""
        events = self.extract_timeline_text()
        detected_arguments = self.detect_scope_limiting_arguments(events)
        
        for event in detected_arguments:
            llm_response = self.query_llm(event)
            self.estoppel_labels[event] = llm_response