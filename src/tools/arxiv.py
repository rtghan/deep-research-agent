"""
arXiv API tool — fetches academic papers relevant to a query.

Design decision: arXiv is the primary external data source for this system.
The test cases are all ML-literature questions, so arXiv gives us real papers
with real metadata (authors, dates, citations) that are gold for:
- Provenance tracking (claim ↔ paper mapping)
- Confidence scoring (peer-reviewed-ish source authority)
- Contradiction detection (papers from different groups disagreeing)

Uses the public arXiv API (no key required). Falls back to abstracts if
full-text PDF fetch fails (PDF parsing is lossy — documented in FAILURE_LOG).
"""

from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests

from src.obs.trace import Timer


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str
    published: str = ""


def _atom_ns():
    return {"atom": "http://www.w3.org/2005/Atom"}


def search_arxiv(query: str, max_results: int = 5) -> list[ArxivPaper]:
    """
    Search arXiv for papers matching the query.
    Uses the public arXiv API (no authentication required).
    """
    url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except Exception:
        return []

    papers = []
    try:
        root = ET.fromstring(resp.text)
        ns = _atom_ns()
        for entry in root.findall("atom:entry", ns):
            arxiv_id_raw = entry.find("atom:id", ns)
            arxiv_id = arxiv_id_raw.text.split("/abs/")[-1] if arxiv_id_raw is not None and arxiv_id_raw.text else ""
            title_el = entry.find("atom:title", ns)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            abstract_el = entry.find("atom:summary", ns)
            abstract = abstract_el.text.strip().replace("\n", " ") if abstract_el is not None and abstract_el.text else ""
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None and name.text:
                    authors.append(name.text)
            link_el = entry.find("atom:id", ns)
            link = link_el.text if link_el is not None and link_el.text else ""
            pdf_url = ""
            for link_elem in entry.findall("atom:link", ns):
                if link_elem.get("title") == "pdf":
                    pdf_url = link_elem.get("href", "")
                    break
            published_el = entry.find("atom:published", ns)
            published = published_el.text if published_el is not None and published_el.text else ""

            papers.append(ArxivPaper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=link,
                pdf_url=pdf_url,
                published=published,
            ))
    except ET.ParseError:
        pass

    return papers


def fetch_arxiv_abstract(paper: ArxivPaper) -> str:
    """Return the abstract as the text content (always available)."""
    return paper.abstract


def fetch_arxiv_fulltext(paper: ArxivPaper, max_pages: int = 30) -> str:
    """
    Fetch the full text of an arXiv paper by downloading its PDF.

    Falls back to the abstract if the PDF is unavailable or cannot be parsed.
    Uses src.tools.pdf.fetch_pdf_text for the actual extraction.
    """
    from src.tools.pdf import fetch_pdf_text

    if paper.pdf_url:
        text = fetch_pdf_text(paper.pdf_url, max_pages=max_pages)
        if text:
            return text
    # Fallback: abstract is always available
    return paper.abstract


def fetch_arxiv_content(paper: ArxivPaper, max_pages: int = 30) -> str:
    """
    Fetch the best available text content for a paper.

    Tries full-text PDF first, falls back to abstract.
    This is the primary entry point for the research pipeline.
    """
    return fetch_arxiv_fulltext(paper, max_pages=max_pages)
