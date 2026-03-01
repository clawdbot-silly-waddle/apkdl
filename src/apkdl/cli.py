"""CLI interface for apkdl."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from apkdl import client

console = Console()
err_console = Console(stderr=True)


def _handle_error(e: Exception) -> None:
    """Print a user-friendly error message and exit."""
    if isinstance(e, httpx.TimeoutException):
        err_console.print(f"[red]Request timed out: {e}[/red]")
    elif isinstance(e, httpx.ConnectError):
        err_console.print(f"[red]Connection failed: {e}[/red]")
    elif isinstance(e, httpx.HTTPStatusError):
        err_console.print(f"[red]HTTP error {e.response.status_code}: {e}[/red]")
    elif isinstance(e, (RuntimeError, OSError)):
        err_console.print(f"[red]{e}[/red]")
    else:
        err_console.print(f"[red]Unexpected error: {e}[/red]")
    sys.exit(1)


@click.group()
@click.version_option(package_name="apkdl")
def main() -> None:
    """Download Android APKs from UpToDown."""


@main.command()
@click.argument("query")
@click.option("-n", "--limit", default=10, help="Max results to show.", show_default=True)
def search(query: str, limit: int) -> None:
    """Search for apps by name.

    Example: apkdl search tumblr
    """
    try:
        with err_console.status(f"Searching for '{query}'..."):
            results = client.search(query, limit=limit)
    except Exception as e:
        _handle_error(e)

    if not results:
        err_console.print(f"[yellow]No results for '{query}'[/yellow]")
        sys.exit(1)

    table = Table(title=f"Search results for '{query}'")
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("URL", style="dim")

    for i, app in enumerate(results, 1):
        table.add_row(str(i), app.name, app.version, app.url)

    console.print(table)


@main.command()
@click.argument("app")
def info(app: str) -> None:
    """Show detailed info about an app.

    APP can be an UpToDown URL or a package name (e.g. com.tumblr.tumblr).
    """
    url = _resolve_app(app)
    if not url:
        err_console.print(f"[red]Could not find app: {app}[/red]")
        sys.exit(1)

    try:
        with err_console.status("Fetching app info..."):
            app_info = client.get_app_info(url)
    except Exception as e:
        _handle_error(e)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()

    table.add_row("Name", app_info.name)
    if app_info.package:
        table.add_row("Package", app_info.package)
    table.add_row("Version", app_info.version)
    if app_info.size:
        table.add_row("Size", app_info.size)
    if app_info.developer:
        table.add_row("Developer", app_info.developer)
    table.add_row("URL", app_info.url)
    if app_info.description:
        table.add_row("Description", app_info.description)

    console.print(table)


@main.command()
@click.argument("app")
@click.option("-n", "--limit", default=15, help="Max versions to show.", show_default=True)
def versions(app: str, limit: int) -> None:
    """List available versions of an app.

    APP can be an UpToDown URL or a package name.
    """
    url = _resolve_app(app)
    if not url:
        err_console.print(f"[red]Could not find app: {app}[/red]")
        sys.exit(1)

    try:
        with err_console.status("Fetching versions..."):
            vers = client.list_versions(url, limit=limit)
    except Exception as e:
        _handle_error(e)

    if not vers:
        err_console.print("[yellow]No versions found.[/yellow]")
        sys.exit(1)

    table = Table(title="Available versions")
    table.add_column("Version", style="bold")
    table.add_column("Date")

    for v in vers:
        table.add_row(v.version, v.date)

    console.print(table)


@main.command()
@click.argument("app")
@click.option(
    "-o",
    "--output",
    default=".",
    type=click.Path(),
    help="Output directory or file path.",
    show_default=True,
)
@click.option(
    "-v",
    "--version",
    "ver",
    default=None,
    help="Specific version to download (e.g. 43.3.0.110). Use 'apkdl versions' to list.",
)
def download(app: str, output: str, ver: str | None) -> None:
    """Download the latest APK for an app.

    APP can be an UpToDown URL or a package name (e.g. com.tumblr.tumblr).

    Examples:

        apkdl download com.tumblr.tumblr

        apkdl download com.tumblr.tumblr -v 43.3.0.110

        apkdl download com.tumblr.tumblr -o ~/Downloads/
    """
    url = _resolve_app(app)
    if not url:
        err_console.print(f"[red]Could not find app: {app}[/red]")
        sys.exit(1)

    # Resolve version string to version_id
    version_id = ""
    if ver:
        with err_console.status(f"Looking up version {ver}..."):
            try:
                versions = client.list_versions(url, limit=100)
            except Exception as e:
                _handle_error(e)
        match = next((v for v in versions if v.version == ver), None)
        if not match:
            err_console.print(f"[red]Version '{ver}' not found. Use 'apkdl versions' to list available versions.[/red]")
            sys.exit(1)
        version_id = match.version_id

    err_console.print(f"Resolving download for [bold]{url}[/bold]...")

    with Progress(
        TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=err_console,
    ) as progress:
        task = progress.add_task("download", filename="Preparing...", total=None)

        def on_progress(downloaded: int, total: int) -> None:
            if total:
                progress.update(task, total=total, completed=downloaded)

        try:
            download_url, filename = client.get_download_url(url, version_id=version_id)
            progress.update(task, filename=filename)
            saved = client.download_apk(url, output, version_id=version_id, progress_callback=on_progress)
        except (httpx.HTTPError, RuntimeError, OSError) as e:
            err_console.print(f"[red]Download failed: {e}[/red]")
            sys.exit(1)

    console.print(f"[green]✓[/green] Saved to [bold]{saved}[/bold]")

    size = Path(saved).stat().st_size
    console.print(f"  Size: {_human_size(size)}")


def _resolve_app(app: str) -> str | None:
    """Resolve an app identifier to an UpToDown URL."""
    if app.startswith("http://") or app.startswith("https://"):
        return app

    # Looks like a package name (has dots)
    if "." in app:
        with err_console.status(f"Resolving package '{app}'..."):
            try:
                url = client.resolve_package_url(app)
            except Exception:
                url = None
        if url:
            return url

    # Try as a search query and take first result
    with err_console.status(f"Searching for '{app}'..."):
        try:
            results = client.search(app, limit=1)
        except Exception:
            results = []
    if results:
        return results[0].url

    return None


def _human_size(size: int | float) -> str:
    """Format bytes as human-readable size."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
