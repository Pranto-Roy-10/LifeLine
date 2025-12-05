# googletrans/__init__.py
"""
Lightweight local replacement for the googletrans package.

It exposes a Translator class with a translate(text, dest='en', src='auto')
method, so you can keep:

    from googletrans import Translator

in your code without installing the real googletrans package.
"""

from dataclasses import dataclass
from typing import Optional
import requests


@dataclass
class Translated:
    text: str
    src: str = "auto"
    dest: str = "en"


class Translator:
    def __init__(self, *args, **kwargs):
        # Accept arbitrary args/kwargs so existing code doesn't break
        pass

    def translate(self, text: str, dest: str = "en", src: str = "auto") -> Translated:
        """
        Very simple translation using Google's unofficial endpoint.
        If anything goes wrong (network, API change), it just returns the
        original text so the app never crashes.
        """
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": src,
                "tl": dest,
                "dt": "t",
                "q": text,
            }
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()

            # data[0] is a list of segments like [[translated, original, ...], ...]
            translated_chunks = [chunk[0] for chunk in data[0] if chunk[0]]
            translated_text = "".join(translated_chunks)
            return Translated(text=translated_text, src=src, dest=dest)
        except Exception:
            # Fallback: return original text if translation fails
            return Translated(text=str(text), src=src, dest=dest)


__all__ = ["Translator", "Translated"]
