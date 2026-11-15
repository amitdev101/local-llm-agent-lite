"""Optional document conversion and web search for the Playground."""

import importlib
import json
import shutil
import subprocess
import sys
import time
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit
from zipfile import BadZipFile, ZipFile

import httpx


OFFICE_DOCUMENTS = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}
MAX_SEARCH_QUERY = 500
MARKITDOWN_SETUP = "Run pip install 'markitdown[all]' in the Playground's virtual environment."


class AddonError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def markitdown_available() -> bool:
    importlib.invalidate_caches()
    return all(find_spec(module) is not None for module in ("markitdown", "mammoth", "openpyxl", "pandas", "pptx"))


def office_text(document: BytesIO, extension: str, limit: int, data_dir: Path) -> str:
    try:
        with ZipFile(document) as archive:
            if OFFICE_DOCUMENTS[extension] not in archive.namelist():
                raise AddonError(422, "The document does not match its file extension.")
            entries = archive.infolist()
            if len(entries) > 5000 or sum(entry.file_size for entry in entries) > 40 * 1024 * 1024:
                raise AddonError(413, "The expanded document is too large. Attach a smaller section.")
    except BadZipFile as error:
        raise AddonError(422, "This file is damaged or is not an Office document.") from error
    document.seek(0)
    try:
        with TemporaryDirectory(prefix=".markitdown-", dir=data_dir) as directory:
            source = Path(directory) / f"input{extension}"
            output = Path(directory) / "output.md"
            with source.open("wb") as stream:
                shutil.copyfileobj(document, stream)
            subprocess.run(
                [sys.executable, "-m", "markitdown", str(source), "-o", str(output)],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=30, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with output.open(encoding="utf-8") as stream:
                contents = stream.read(limit + 1)
            if len(contents.encode("utf-16-le")) // 2 > limit:
                raise AddonError(413, "The document has too much text. Attach a smaller section.")
    except subprocess.TimeoutExpired as error:
        raise AddonError(422, "The document took too long to convert. Attach a smaller section.") from error
    except subprocess.CalledProcessError as error:
        if b"ImportError:" in error.stderr or b"ModuleNotFoundError:" in error.stderr:
            raise AddonError(503, MARKITDOWN_SETUP) from error
        raise AddonError(422, "Could not read the document. Try opening it in Office and saving a fresh copy.") from error
    except (OSError, UnicodeError) as error:
        raise AddonError(422, "Could not read the converted Markdown.") from error
    contents = contents.strip()
    if not contents:
        raise AddonError(422, "The document has no extractable text.")
    return contents


def http_url(value: str):
    try:
        parts = urlsplit(value)
        url = httpx.URL(value)
        if (parts.scheme in {"http", "https"} and url.host and parts.username is None
                and "\\" not in value and not any(character.isspace() or ord(character) < 32 for character in value)):
            return parts
    except (ValueError, httpx.InvalidURL):
        pass
    return None


def searxng_url(value: str) -> str:
    url = http_url(value.strip())
    if url is None or url.query or url.fragment:
        raise AddonError(400, "Enter the HTTP or HTTPS address of your SearXNG instance, without a query or fragment.")
    return urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))


def search_web(base_url: str, query: str) -> list[dict[str, str]]:
    deadline = time.monotonic() + 8
    try:
        with httpx.stream("GET", f"{base_url}/search", params={"q": query, "format": "json"},
                          headers={"Accept-Encoding": "identity"}, timeout=4,
                          follow_redirects=False, trust_env=False) as response:
            if response.status_code == 403:
                raise AddonError(502, "SearXNG returned 403. Enable the json search format and check access to the instance.")
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise AddonError(502, "The SearXNG response was too large.")
                if time.monotonic() >= deadline:
                    raise httpx.TimeoutException("Search timed out")
        results = json.loads(body)["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid results")
    except (httpx.TimeoutException, httpx.NetworkError) as error:
        raise AddonError(502, "Could not reach SearXNG. Check the address and make sure the instance is running.") from error
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise AddonError(502, "SearXNG did not return valid JSON results. Check the instance address and JSON search format.") from error

    sources = []
    seen = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = result.get("url")
        if not isinstance(url, str) or len(url) > 2048 or url in seen or http_url(url) is None:
            continue
        title = result.get("title")
        snippet = result.get("content")
        sources.append({
            "url": url,
            "title": " ".join(title.split())[:160] if isinstance(title, str) and title.strip() else url,
            "snippet": " ".join(snippet.split())[:500] if isinstance(snippet, str) else "",
        })
        seen.add(url)
        if len(sources) == 3:
            break
    return sources
