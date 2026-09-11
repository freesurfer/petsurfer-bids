"""Isolated subprocess entry point for nilearn/matplotlib figure rendering.

Figure rendering (nilearn's ``plot_stat_map`` / ``plot_surf_stat_map``) has
been observed to trigger a native ``SIGABRT`` (heap corruption in
``libffi``/``ctypes`` during ctypes-object cleanup) on some Python builds.
Because a hard process abort cannot be caught with a Python ``try/except``,
rendering is delegated to this standalone subprocess: a crash here only
kills this worker, and the parent (``petsurfer_km.report_helpers``)
inspects the subprocess's exit/signal status and logs a warning instead of
losing the whole report/pipeline run.

Invoked as ``python -m petsurfer_km._figure_worker`` with a single JSON
object describing the figure to render, written to stdin.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read())
    kind = payload.get("kind")

    # Imported lazily so a bare `--help`/import error doesn't require
    # matplotlib/nilearn to be importable.
    from petsurfer_km.report_helpers import (
        _render_surface_figure_impl,
        _render_volume_figure_impl,
    )

    if kind == "volume":
        _render_volume_figure_impl(
            payload["stat_map"],
            payload["template"],
            payload["output_path"],
            payload["meas"],
        )
    elif kind == "surface":
        _render_surface_figure_impl(
            payload["stat_map"],
            payload["hemi"],
            payload["output_path"],
            payload["meas"],
        )
    else:
        print(f"Unknown figure kind: {kind!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
