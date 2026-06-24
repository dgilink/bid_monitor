from __future__ import annotations

import logging
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

from config import DOWNLOAD_DIR, DOC_FOCUS_KEYWORDS, Settings


LOGGER = logging.getLogger(__name__)


def extract_attachment_urls(record: dict) -> list[str]:
    urls: list[str] = []
    for key, value in record.items():
        if not value:
            continue
        key_lower = str(key).lower()
        text = str(value)
        if "url" in key_lower or "file" in key_lower or "doc" in key_lower:
            urls.extend(re.findall(r"https?://[^\s,]+", text))
    return list(dict.fromkeys(urls))


def download_file(url: str, settings: Settings) -> Path | None:
    try:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        target = DOWNLOAD_DIR / f"doc_{abs(hash(url))}{suffix}"
        if target.exists():
            return target
        response = requests.get(url, timeout=settings.document_timeout, headers={"User-Agent": "bid-monitor/1.0"})
        response.raise_for_status()
        target.write_bytes(response.content)
        return target
    except Exception as exc:
        LOGGER.warning("Document download failed: %s", exc)
        return None


def extract_text_from_files(urls: Iterable[str], settings: Settings) -> tuple[str, list[str]]:
    texts: list[str] = []
    failed: list[str] = []
    for idx, url in enumerate(urls):
        if idx >= settings.max_document_downloads:
            break
        path = download_file(url, settings)
        if not path:
            failed.append(url)
            continue
        text = extract_text(path)
        if text:
            texts.append(text)
        else:
            failed.append(url)
    return "\n".join(texts), failed


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return extract_pdf(path)
        if suffix == ".docx":
            return extract_docx(path)
        if suffix == ".hwpx":
            return extract_hwpx(path)
        if suffix == ".hwp":
            return extract_hwp(path)
        if suffix in {".txt", ".html", ".htm"}:
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        LOGGER.warning("Document parse failed for %s: %s", path, exc)
    return ""


def extract_pdf(path: Path) -> str:
    import fitz

    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def extract_hwpx(path: Path) -> str:
    from bs4 import BeautifulSoup

    chunks: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith(".xml") and ("Contents/" in name or "content" in name.lower()):
                soup = BeautifulSoup(zf.read(name), "xml")
                chunks.append(soup.get_text(" "))
    return "\n".join(chunks)


def extract_hwp(path: Path) -> str:
    # hwp5txt가 설치되어 있으면 사용한다. 실패 시 수동 확인 대상으로 남긴다.
    result = subprocess.run(["hwp5txt", str(path)], capture_output=True, text=True, timeout=20, check=False)
    if result.returncode == 0:
        return result.stdout
    return ""


def focus_snippets(text: str, width: int = 180) -> str:
    snippets: list[str] = []
    compact = re.sub(r"\s+", " ", text)
    for keyword in DOC_FOCUS_KEYWORDS:
        idx = compact.find(keyword)
        if idx >= 0:
            snippets.append(compact[max(0, idx - 30): idx + width])
    return "\n".join(dict.fromkeys(snippets))[:3000]
