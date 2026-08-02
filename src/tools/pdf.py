"""
PDF reading tool — extracts text from PDF files or URLs using pypdf.

Design decision: The original arxiv.py only used abstracts, which limited
evidence quality. Full-text extraction lets the verifier check whether a
claim is actually supported by the paper body, not just the abstract.

pypdf is already a project dependency (>=4.0). This module provides:
  - read_pdf_bytes(data: bytes) -> str
  - read_pdf_file(path: str) -> str
  - fetch_pdf_text(url: str, max_pages: int) -> str

All functions degrade gracefully (return "" on failure) so the pipeline
never crashes on a malformed or inaccessible PDF.
"""

from __future__ import annotations

import io
from typing import Optional

import requests
from pypdf import PdfReader


def read_pdf_bytes(data: bytes, max_pages: int = 50) -> str:
    """
    Extract text from a PDF given as raw bytes.
    max_pages caps extraction to avoid huge papers blowing up the context.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        return "\n\n".join(
            (page.extract_text() or "") for page in pages
        ).strip()
    except Exception:
        return ""


def read_pdf_file(path: str, max_pages: int = 50) -> str:
    """Extract text from a local PDF file."""
    try:
        with open(path, "rb") as f:
            return read_pdf_bytes(f.read(), max_pages=max_pages)
    except Exception:
        return ""


def fetch_pdf_text(url: str, max_pages: int = 50, timeout: int = 30) -> str:
    """
    Download a PDF from a URL and extract its text.
    Returns "" on any failure (network, parse, etc.).
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "deep-research-agent/0.1 (research)"},
        )
        resp.raise_for_status()
        return read_pdf_bytes(resp.content, max_pages=max_pages)
    except Exception:
        return ""
