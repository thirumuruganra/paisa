from __future__ import annotations

import re


NOISE_PATTERNS = [
    r"\bUPI/P2[AM]/[A-Z0-9]+/",
    r"\bUPI/[A-Z0-9]+/",
    r"\bUPI/P2[AM]/",
    r"\bUPI/",
    r"\bINF/INB/",
    r"\bNEFT[-/]",
    r"\bIMPS[-/]",
    r"\bRTGS[-/]",
    r"\bACH[-/]",
    r"\bPOS\s+",
    r"\bVIN/",
    r"\bREF(?:ERENCE)?(?:\s*NO)?[:\-/]?\s*[A-Z0-9]+",
    r"\bTXN(?:\s*ID)?[:\-/]?\s*[A-Z0-9]+",
    r"\bRRN[:\-/]?\s*[A-Z0-9]+",
    r"\b[0-9]{8,}\b",
]


def clean_merchant_name(narration: str) -> str:
    cleaned = narration.upper()
    cleaned = cleaned.replace("\n", " ")
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[/|*:_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if cleaned else "Unknown"
