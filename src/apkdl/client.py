"""UpToDown API client for searching and downloading APKs."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

UPTODOWN_BASE = "https://en.uptodown.com"
UPTODOWN_DW = "https://dw.uptodown.com/dwn"


@dataclass
class AppInfo:
    """Metadata about an app on UpToDown."""

    name: str
    package: str
    version: str
    size: str
    url: str  # UpToDown page URL
    icon_url: str | None = None
    developer: str | None = None
    description: str | None = None


@dataclass
class VersionInfo:
    """A specific version of an app."""

    version: str
    date: str
    url: str
    version_id: str = ""


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )


def search(query: str, *, limit: int = 20) -> list[AppInfo]:
    """Search for apps on UpToDown by name."""
    with _make_client() as client:
        resp = client.post(
            f"{UPTODOWN_BASE}/android/en/s",
            data={"queryString": query},
        )
        resp.raise_for_status()

    data = resp.json()
    if not data.get("success") or not data.get("data"):
        return []

    results: list[AppInfo] = []
    for app in data["data"].get("apps", [])[:limit]:
        name = re.sub(r"<[^>]+>", "", app.get("name", ""))
        url = app.get("url", "")

        # Extract slug from URL: https://{slug}.en.uptodown.com/android
        slug_match = re.search(r"//([^.]+)\.", url)
        slug = slug_match.group(1) if slug_match else ""

        results.append(
            AppInfo(
                name=name,
                package=slug,
                version="",
                size="",
                url=url,
            )
        )

    return results


def get_app_info(url: str) -> AppInfo:
    """Get detailed app info from an UpToDown app page URL."""
    with _make_client() as client:
        resp = client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    name_el = soup.select_one("h1#detail-app-name")
    name = name_el.get_text(strip=True) if name_el else "Unknown"

    version = "unknown"
    for ver_el in soup.select(".version"):
        text = ver_el.get_text(strip=True)
        if re.match(r"\d+\.\d+", text):
            version = text
            break

    # Try to find package name from Play Store link
    package = ""
    play_link = soup.select_one('a[href*="play.google.com"]')
    if play_link:
        m = re.search(r"id=([^&]+)", play_link.get("href", ""))
        if m:
            package = m.group(1)

    # Technical info (size, etc.)
    size = ""
    for row in soup.select(".technical-information .full tr, .info-item"):
        text = row.get_text()
        if "size" in text.lower() or "tamaño" in text.lower():
            size_match = re.search(r"[\d.,]+\s*[KMGT]?B", text)
            if size_match:
                size = size_match.group()

    # Get description
    desc_el = soup.select_one("#detail-description")
    desc = desc_el.get_text(strip=True)[:200] if desc_el else None

    # Icon
    icon_el = soup.select_one("img.detail-icon, img#detail-icon")
    icon_url = icon_el.get("src") if icon_el else None

    # Developer
    dev_el = soup.select_one(".developer, .author")
    developer = dev_el.get_text(strip=True) if dev_el else None

    return AppInfo(
        name=name,
        package=package,
        version=version,
        size=size,
        url=url,
        icon_url=str(icon_url) if icon_url else None,
        developer=developer,
        description=desc,
    )


def _get_app_code(url: str) -> str:
    """Extract the app code (numeric ID) from an UpToDown app page."""
    with _make_client() as client:
        resp = client.get(url.rstrip("/") + "/versions")
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    el = soup.select_one("[data-code]")
    if el:
        code = el.get("data-code", "")
        if code and code.isdigit():
            return str(code)

    raise RuntimeError(f"Could not find app code on {url}")


def list_versions(url: str, *, limit: int = 200) -> list[VersionInfo]:
    """List available versions for an app using the JSON API."""
    app_code = _get_app_code(url)
    versions: list[VersionInfo] = []
    page = 1

    with _make_client() as client:
        while len(versions) < limit:
            resp = client.get(
                f"{UPTODOWN_BASE}/android/apps/{app_code}/versions/{page}"
            )
            resp.raise_for_status()

            data = resp.json()
            if not data.get("success") or not data.get("data"):
                break

            for item in data["data"]:
                if len(versions) >= limit:
                    break
                versions.append(
                    VersionInfo(
                        version=item.get("version", ""),
                        date=item.get("lastUpdate", ""),
                        url=url,
                        version_id=str(
                            item.get("versionURL", {}).get("versionID", "")
                        ),
                    )
                )

            if len(data["data"]) < 20:
                break
            page += 1

    return versions


def get_download_url(url: str, *, version_id: str = "") -> tuple[str, str]:
    """Get the direct download URL and filename for an app.

    Args:
        url: UpToDown app page URL (e.g. https://tumblr.en.uptodown.com/android)
        version_id: Optional version ID for downloading a specific version.

    Returns:
        Tuple of (download_url, suggested_filename)
    """
    download_page = url.rstrip("/") + "/download"
    if version_id:
        download_page += f"/{version_id}"
    with _make_client() as client:
        resp = client.get(download_page)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the download button with the data-url token
    btn = soup.select_one("#detail-download-button")
    if not btn:
        raise RuntimeError("Could not find download button on page")

    token = btn.get("data-url", "")
    if not token or len(token) < 20:
        raise RuntimeError(f"Invalid download token: {token!r}")

    download_url = f"{UPTODOWN_DW}/{token}"

    # Build a sensible filename
    name_el = soup.select_one("h1#detail-app-name, .detail-app-name")
    name = name_el.get_text(strip=True) if name_el else "app"
    name_slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "app"

    ver_el = soup.select_one(".version")
    version = ""
    if ver_el:
        text = ver_el.get_text(strip=True)
        if re.match(r"\d+\.\d+", text):
            version = text

    ext = "apk"
    if "xapk" in btn.get("class", []):
        ext = "xapk"

    filename = f"{name_slug}-{version}.{ext}" if version else f"{name_slug}.{ext}"

    return download_url, filename


def download_apk(
    url: str,
    output_path: str,
    *,
    version_id: str = "",
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Download an APK from UpToDown.

    Args:
        url: UpToDown app page URL
        output_path: Directory or file path to save to
        version_id: Optional version ID for downloading a specific version.
        progress_callback: Optional callable(downloaded_bytes, total_bytes)

    Returns:
        Path to the downloaded file
    """
    download_url, suggested_name = get_download_url(url, version_id=version_id)
    download_page = url.rstrip("/") + "/download"
    if version_id:
        download_page += f"/{version_id}"

    out = Path(output_path)
    # Treat as directory if it exists as a dir or the path ends with /
    if out.is_dir() or str(output_path).endswith(("/", "\\")):
        out = out / suggested_name

    out.parent.mkdir(parents=True, exist_ok=True)

    # Download to a temp file, then atomically rename on success
    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=out.parent, suffix=".tmp", prefix=".apkdl-"
    )
    temp_path = Path(temp_path_str)
    fd_closed = False
    try:
        import os

        with _make_client() as client:
            with client.stream(
                "GET",
                download_url,
                headers={"Referer": download_page},
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))

                f = os.fdopen(temp_fd, "wb")
                fd_closed = True
                with f:
                    downloaded = 0
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)

        if downloaded == 0:
            raise RuntimeError("Download produced no data")

        if total > 0 and downloaded != total:
            raise RuntimeError(
                f"Download incomplete: got {downloaded} bytes, expected {total}"
            )

        temp_path.replace(out)
    except BaseException:
        if not fd_closed:
            os.close(temp_fd)
        temp_path.unlink(missing_ok=True)
        raise

    return str(out)


def resolve_package_url(package_name: str) -> str | None:
    """Try to find an UpToDown URL for a given Android package name.

    Searches UpToDown and checks Play Store links on result pages
    to match the exact package name.
    """
    # Try multiple search strategies: full package name, then last part(s)
    queries = [package_name]
    parts = package_name.split(".")
    if len(parts) >= 2:
        queries.append(parts[-1])  # e.g. "tumblr"
    if len(parts) >= 3:
        queries.append(f"{parts[-2]} {parts[-1]}")  # e.g. "tumblr tumblr"

    all_results: list[AppInfo] = []
    seen_urls: set[str] = set()
    for q in queries:
        for r in search(q, limit=10):
            if r.url not in seen_urls:
                all_results.append(r)
                seen_urls.add(r.url)

    if not all_results:
        return None

    # Check each result's detail page for a matching Play Store link
    with _make_client() as client:
        for result in all_results:
            try:
                resp = client.get(result.url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue

            if f"id={package_name}" in resp.text:
                return result.url

            soup = BeautifulSoup(resp.text, "html.parser")
            for a_tag in soup.select("a[href*='play.google.com']"):
                href = a_tag.get("href", "")
                if f"id={package_name}" in href:
                    return result.url

    # Fallback: return first result if the app name slug matches
    pkg_slug = parts[-1].lower()
    for result in all_results:
        if pkg_slug in result.url.lower():
            return result.url

    return None
