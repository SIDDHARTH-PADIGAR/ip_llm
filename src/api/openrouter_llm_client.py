import os
import requests
from dotenv import load_dotenv

class OpenRouterClient:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        self.base_url = 'https://openrouter.ai/api/v1'
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": os.getenv('APP_URL', 'http://localhost:3000'),
            "Content-Type": "application/json"
        }

    def analyze_text(self, prompt: str) -> str:
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json={
                    "model": "deepseek-v3",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                }
            )
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.exceptions.RequestException as e:
            raise Exception(f"OpenRouter API request failed: {str(e)}")