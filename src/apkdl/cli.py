"""CLI interface for apkdl."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NoReturn

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


def _handle_error(e: Exception) -> NoReturn:
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
    table.add_column("Package")
    table.add_column("ID", style="dim")

    for i, app in enumerate(results, 1):
        table.add_row(str(i), app.name, app.package, app.app_code)

    console.print(table)


@main.command()
@click.argument("app")
def info(app: str) -> None:
    """Show detailed info about an app.

    APP can be an UpToDown URL, a package name (e.g. com.tumblr), or a search term.
    """
    try:
        with err_console.status("Resolving app..."):
            app_code, _ = client.resolve_app(app)
            app_info = client.get_app_info(app_code)
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
    if app_info.url:
        table.add_row("URL", app_info.url)
    if app_info.description:
        table.add_row("Description", app_info.description)

    console.print(table)


@main.command()
@click.argument("app")
@click.option("-n", "--limit", default=15, help="Max versions to show.", show_default=True)
def versions(app: str, limit: int) -> None:
    """List available versions of an app.

    APP can be an UpToDown URL, a package name, or a search term.
    """
    try:
        with err_console.status("Resolving app..."):
            app_code, app_name = client.resolve_app(app)
        with err_console.status(f"Fetching versions for {app_name}..."):
            vers = client.list_versions(app_code, limit=limit)
    except Exception as e:
        _handle_error(e)

    if not vers:
        err_console.print("[yellow]No versions found.[/yellow]")
        sys.exit(1)

    table = Table(title=f"Available versions of {app_name}")
    table.add_column("Version", style="bold")
    table.add_column("Type")
    table.add_column("Size")
    table.add_column("Date")

    for v in vers:
        table.add_row(v.version, v.file_type, v.size, v.date)

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

    APP can be an UpToDown URL, a package name (e.g. com.tumblr), or a search term.

    Examples:

        apkdl download com.tumblr

        apkdl download com.tumblr -v 43.3.0.110

        apkdl download tumblr -o ~/Downloads/
    """
    try:
        with err_console.status("Resolving app..."):
            app_code, app_name = client.resolve_app(app)
            all_versions = client.list_versions(app_code, limit=100)
    except Exception as e:
        _handle_error(e)

    if not all_versions:
        err_console.print("[red]No versions available.[/red]")
        sys.exit(1)

    # Find the target version
    if ver:
        match = next((v for v in all_versions if v.version == ver), None)
        if not match:
            err_console.print(
                f"[red]Version '{ver}' not found. Use 'apkdl versions' to list.[/red]"
            )
            sys.exit(1)
        target = match
    else:
        target = all_versions[0]

    # Build filename
    name_slug = re.sub(r"[^a-zA-Z0-9]+", "-", app_name).strip("-").lower() or "app"
    ext = target.file_type or "apk"
    filename = f"{name_slug}-{target.version}.{ext}"

    err_console.print(
        f"Downloading [bold]{app_name}[/bold] {target.version} ({target.file_type})..."
    )

    with Progress(
        TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=err_console,
    ) as progress:
        task = progress.add_task("download", filename=filename, total=None)

        def on_progress(downloaded: int, total: int) -> None:
            if total:
                progress.update(task, total=total, completed=downloaded)

        try:
            download_url, sha256 = client.get_download_url(app_code, target.file_id)
            saved = client.download_file(
                download_url,
                output,
                filename,
                expected_sha256=sha256,
                progress_callback=on_progress,
            )
        except (httpx.HTTPError, RuntimeError, OSError) as e:
            err_console.print(f"[red]Download failed: {e}[/red]")
            sys.exit(1)

    console.print(f"[green]✓[/green] Saved to [bold]{saved}[/bold]")

    size = Path(saved).stat().st_size
    console.print(f"  Size: {client.human_size(size)}")
