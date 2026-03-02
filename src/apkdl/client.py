"""UpToDown API client for searching and downloading APKs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

UPTODOWN_EAPI = "https://www.uptodown.app"
_EAPI_SECRET = "$(=a%·!45J&S"
_EAPI_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13)",
    "Identificador": "Uptodown_Android",
    "Identificador-Version": "711",
    "Accept": "application/json",
    "Accept-Charset": "utf-8",
}
_DEVICE_ID = "0000000000000000"


@dataclass
class AppInfo:
    """Metadata about an app on UpToDown."""

    name: str
    app_code: str
    package: str
    version: str
    size: str
    url: str
    icon_url: str | None = None
    developer: str | None = None
    description: str | None = None


@dataclass
class VersionInfo:
    """A specific version of an app."""

    version: str
    date: str
    file_id: str
    file_type: str = "apk"
    size: str = ""
    sha256: str = ""


def _generate_eapi_key() -> str:
    """Generate the APIKEY header for the UpToDown internal API."""
    epoch_hour = int(time.time()) // 3600 * 3600
    raw = _EAPI_SECRET + str(epoch_hour)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _eapi_get(path: str, params: dict[str, str] | None = None) -> Any:
    """Make an authenticated GET request to the UpToDown internal API."""
    headers = {**_EAPI_HEADERS, "APIKEY": _generate_eapi_key()}
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(
            f"{UPTODOWN_EAPI}{path}",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"API returned non-JSON for {path}"
            ) from exc
    if isinstance(data, dict) and "success" in data and not data["success"]:
        raise RuntimeError(f"API error for {path}: {data}")
    return data


def human_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def search(query: str, *, limit: int = 20) -> list[AppInfo]:
    """Search for apps on UpToDown by name."""
    data = _eapi_get(
        f"/eapi/v2/apps/search/{quote(query, safe='')}",
        {"page[limit]": str(limit), "page[offset]": "0"},
    )
    results: list[AppInfo] = []
    for app in data.get("data", {}).get("results", [])[:limit]:
        results.append(
            AppInfo(
                name=app.get("name", ""),
                app_code=str(app.get("appID", "")),
                package=app.get("packageName", "") or app.get("packagename", ""),
                version="",
                size="",
                url="",
                icon_url=app.get("iconURL"),
                developer=app.get("author") or None,
            )
        )
    return results


def get_app_info(app_code: str) -> AppInfo:
    """Get detailed app info by app code."""
    data = _eapi_get(f"/eapi/v3/apps/{app_code}/device/{_DEVICE_ID}")
    d = data.get("data", data)

    # Get latest version string from versions endpoint
    versions = list_versions(app_code, limit=1)
    version = versions[0].version if versions else ""

    size_bytes = d.get("size", 0)
    return AppInfo(
        name=d.get("name", "Unknown"),
        app_code=str(d.get("appID", app_code)),
        package=d.get("packagename", ""),
        version=version,
        size=_human_size(size_bytes) if size_bytes else "",
        url=d.get("urlShare", ""),
        icon_url=d.get("icon"),
        developer=d.get("author") or None,
        description=d.get("shortDescription") or None,
    )


def list_versions(app_code: str, *, limit: int = 200) -> list[VersionInfo]:
    """List available versions for an app."""
    versions: list[VersionInfo] = []
    offset = 0
    page_size = min(limit, 50)

    while len(versions) < limit:
        data = _eapi_get(
            f"/eapi/v3/app/{app_code}/device/{_DEVICE_ID}/compatible/versions",
            {"page[limit]": str(page_size), "page[offset]": str(offset)},
        )
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            if len(versions) >= limit:
                break
            versions.append(
                VersionInfo(
                    version=item.get("version", ""),
                    date=item.get("lastUpdate", ""),
                    file_id=str(item.get("fileID", "")),
                    file_type=item.get("fileType", "apk"),
                    size=item.get("size", ""),
                    sha256=item.get("sha256", ""),
                )
            )

        if len(items) < page_size:
            break
        offset += page_size

    return versions


def get_download_url(app_code: str, file_id: str) -> tuple[str, str | None]:
    """Get the direct download URL for an app file.

    Returns:
        Tuple of (download_url, sha256_or_none)
    """
    data = _eapi_get(
        f"/eapi/apps/{app_code}/file/{file_id}/downloadUrl",
        {"update": "0"},
    )
    dl_data = data.get("data", {})
    dl_url = dl_data.get("downloadURL", "")
    if not dl_url:
        raise RuntimeError(
            f"API did not return a download URL (app={app_code}, file={file_id})"
        )
    return dl_url, dl_data.get("sha256")


def download_file(
    download_url: str,
    output_path: str,
    filename: str,
    *,
    expected_sha256: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Download a file from a CDN URL.

    Args:
        download_url: Direct download URL from get_download_url()
        output_path: Directory or file path to save to
        filename: Suggested filename (used when output_path is a directory)
        expected_sha256: Optional SHA256 hash to verify after download
        progress_callback: Optional callable(downloaded_bytes, total_bytes)

    Returns:
        Path to the downloaded file
    """
    out = Path(output_path)
    if out.is_dir() or str(output_path).endswith(("/", "\\")):
        out = out / filename

    out.parent.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=out.parent, suffix=".tmp", prefix=".apkdl-"
    )
    temp_path = Path(temp_path_str)
    fd_closed = False
    try:
        headers = {"User-Agent": _EAPI_HEADERS["User-Agent"]}
        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            with client.stream("GET", download_url, headers=headers) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))

                f = os.fdopen(temp_fd, "wb")
                fd_closed = True
                downloaded = 0

                if expected_sha256:
                    sha_hash = hashlib.sha256()

                with f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if expected_sha256:
                            sha_hash.update(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)

        if downloaded == 0:
            raise RuntimeError("Download produced no data")

        if total > 0 and downloaded != total:
            raise RuntimeError(
                f"Download incomplete: got {downloaded} bytes, expected {total}"
            )

        if expected_sha256:
            actual = sha_hash.hexdigest()
            if actual != expected_sha256:
                raise RuntimeError(
                    f"SHA256 mismatch: expected {expected_sha256}, got {actual}"
                )

        temp_path.replace(out)
    except BaseException:
        if not fd_closed:
            os.close(temp_fd)
        temp_path.unlink(missing_ok=True)
        raise

    return str(out)


def resolve_app(identifier: str) -> tuple[str, str]:
    """Resolve a user-provided identifier to (app_code, app_name).

    Accepts:
        - UpToDown URL (https://tumblr.en.uptodown.com/android)
        - Android package name (com.tumblr)
        - App name for search (tumblr)

    Returns:
        Tuple of (app_code, app_name)
    """
    # URL: extract slug and search for it
    if identifier.startswith(("http://", "https://")):
        slug_match = re.search(r"//([^.]+)\.", identifier)
        if slug_match:
            slug = slug_match.group(1).replace("-", " ")
            results = search(slug, limit=1)
            if results:
                return results[0].app_code, results[0].name
        raise RuntimeError(f"Could not resolve URL: {identifier}")

    # Package name (has dots): look up directly
    if "." in identifier:
        try:
            data = _eapi_get(f"/eapi/apps/byPackagename/{identifier}")
            app_code = str(data.get("data", {}).get("appID", ""))
            if app_code:
                info = get_app_info(app_code)
                return app_code, info.name
        except (httpx.HTTPError, RuntimeError):
            pass

    # Fall back to search
    results = search(identifier, limit=1)
    if results:
        return results[0].app_code, results[0].name

    raise RuntimeError(f"Could not find app: {identifier}")
