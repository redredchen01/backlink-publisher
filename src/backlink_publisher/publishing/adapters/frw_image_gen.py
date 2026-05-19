import requests
import logging

_log = logging.getLogger(__name__)

def generate_cover_image(api_key: str, prompt: str) -> str | None:
    """Generate cover image using the FRW API."""
    # Assuming standard FRW interface for image generation
    url = "https://api.frw.ai/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024"
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Extract image URL
        return data["data"][0]["url"]
    except Exception as e:
        _log.error(f"Failed to generate cover image: {e}")
        return None
