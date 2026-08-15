"""`oknoll ui` — open the daemon's minimal local web UI."""

from __future__ import annotations

import typer
from oknoll_runtime import runtime_dirs
from oknoll_runtime.daemon import daemon_url, ensure_token, read_record

from oknoll_cli.commands.common import fail

ui_app = typer.Typer(help="Open the local daemon's web UI.", no_args_is_help=True)


@ui_app.command("open")
def open_(
    print_only: bool = typer.Option(
        False, "--print", help="Print the URL instead of opening a browser."
    ),
) -> None:
    """Open the daemon UI in a browser (passes the token in the URL fragment)."""
    import webbrowser

    dirs = runtime_dirs()
    record = read_record(dirs.state)
    if record is None:
        raise fail("the daemon is not running — start it with `oknoll daemon start`")
    base = daemon_url(record)
    # The token travels in the fragment: it never leaves the browser, never
    # reaches server logs, and the page moves it to sessionStorage on load.
    url = f"{base}/#token={ensure_token(dirs.state)}"
    typer.echo(f"daemon ui: {base}/")
    if print_only:
        typer.echo(url)
        return
    if not webbrowser.open(url):
        typer.secho("could not open a browser — use --print for the URL", err=True)
        typer.echo(url)
