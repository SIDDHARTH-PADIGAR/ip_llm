from ..api.llm_client import OpenRouterClient

class ClaimAnalyzer:
    def __init__(self):
        self.llm_client = OpenRouterClient()

    def analyze_claim_changes(self, original_claims: str, amended_claims: str) -> dict:
        prompt = f"""
        Compare these patent claims and identify key changes:
        
        Original Claims:
        {original_claims}
        
        Amended Claims:
        {amended_claims}
        
        Please identify:
        1. Added limitations
        2. Removed elements
        3. Scope changes
        4. Potential estoppel issues
        """
        
        analysis = self.llm_client.analyze_text(prompt)
        return {
            "analysis": analysis,
            "original_claims": original_claims,
            "amended_claims": amended_claims
        }