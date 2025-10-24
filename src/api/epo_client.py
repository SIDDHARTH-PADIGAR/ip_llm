import requests
import base64
import json
import time
import xmltodict
from typing import Tuple, Dict, Any
from dotenv import load_dotenv
import os

class EPOClient:
    def __init__(self):
        load_dotenv()
        self.consumer_key = os.getenv('EPO_CONSUMER_KEY')
        self.consumer_secret = os.getenv('EPO_CONSUMER_SECRET')
        self.token_url = "https://ops.epo.org/3.2/auth/accesstoken"
        self.base_url = "https://ops.epo.org/3.2/rest-services"
        self.access_token = None
        self.token_expiry = None

    def get_access_token(self) -> Tuple[str, float]:
        """Get an access token from EPO OPS."""
        credentials = f"{self.consumer_key}:{self.consumer_secret}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("ascii")

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {"grant_type": "client_credentials"}
        response = requests.post(self.token_url, headers=headers, data=data)

        if response.status_code != 200:
            raise Exception(f"EPO OPS Auth Error: {response.status_code}, {response.text}")

        token_response = json.loads(response.text)
        self.access_token = token_response["access_token"]
        expires_in = int(token_response["expires_in"])
        self.token_expiry = time.time() + expires_in - 60  # Renew 1 min before expiry

        return self.access_token, self.token_expiry

    def ensure_valid_token(self) -> str:
        """Ensure we have a valid token, refresh if necessary."""
        if not self.access_token or not self.token_expiry or time.time() >= self.token_expiry:
            self.get_access_token()
        return self.access_token

    def call_ops_api(self, endpoint: str, params: Dict = None) -> Dict[str, Any]:
        """Make a call to the EPO OPS API."""
        token = self.ensure_valid_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/xml"
        }
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 429:
            print("Rate limit hit — waiting before retry...")
            time.sleep(5)
            return self.call_ops_api(endpoint, params)

        if response.status_code != 200:
            raise Exception(f"EPO OPS Request Error: {response.status_code}, {response.text}")

        return xmltodict.parse(response.text)

    def get_patent_data(self, pub_number: str) -> Dict[str, Any]:
        """Get comprehensive patent data including biblio, legal, and family data."""
        data = {
            "bibliographic": self.call_ops_api(f"published-data/publication/epodoc/{pub_number}/biblio"),
            "legal": self.call_ops_api(f"legal/publication/epodoc/{pub_number}"),
            "family": self.call_ops_api(f"family/publication/epodoc/{pub_number}")
        }
        return data

    def get_full_text(self, pub_number: str) -> Dict[str, Any]:
        """Get full text of the patent document if available."""
        return self.call_ops_api(f"published-data/publication/epodoc/{pub_number}/fulltext")