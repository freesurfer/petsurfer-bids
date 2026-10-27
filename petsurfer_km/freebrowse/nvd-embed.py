#!/usr/bin/env python3
"""Embed NVD data into a freebrowse HTML file for self-contained viewing.

The document is placed in a ``<script type="application/json">`` element and
handed to FreeBrowse (2.5.x) as bytes via ``window.__EMBEDDED_NVD_DATA__`` and
the ``loadEmbeddedNvd`` event; FreeBrowse detects the encoding (JSON or CBOR)
and loads it with niivue's ``loadDocument``.

niivue shows an embedded mesh with its stored per-vertex colours and does not
apply the mesh's overlay layers on load.  For documents with such layers a
second script waits for the meshes to load and asks FreeBrowse's niivue
instance (``window.freebrowse.nv``) to apply them.

This script has no external dependencies (standard library only) and can be
copied and distributed independently.

Example usage:
    # Basic: embed NVD into HTML
    ./nvd-embed.py freebrowse.html scan.nvd

    # Specify output file
    ./nvd-embed.py -o my-viewer.html freebrowse.html scan.nvd

    # Verbose output
    ./nvd-embed.py -v freebrowse.html scan.nvd
"""

import argparse
import json
import sys
from pathlib import Path


def escape_json_for_html(nvd_json: str) -> str:
    """Make JSON safe inside a ``<script>`` element.

    In JSON ``<`` can only occur inside strings, where ``\\u003c`` is an
    equivalent escape, so no ``</script>`` or ``<!--`` can end the element.
    """
    return nvd_json.replace("<", "\\u003c")


def create_bootstrap_script(nvd_json: str) -> str:
    """Create the JavaScript bootstrap that loads embedded NVD data."""
    return f'''<script type="application/json" id="embedded-nvd-data">
{escape_json_for_html(nvd_json)}
</script>
<script>
(function() {{
  // Hand the embedded NVD to FreeBrowse as bytes (it detects the encoding)
  var dataElement = document.getElementById('embedded-nvd-data');
  if (!dataElement) return;

  try {{
    var nvdData = new TextEncoder().encode(dataElement.textContent);
    window.__EMBEDDED_NVD_DATA__ = nvdData;

    // Dispatch event after DOM is ready
    function dispatchLoadEvent() {{
      window.dispatchEvent(new CustomEvent('loadEmbeddedNvd', {{
        detail: nvdData
      }}));
    }}

    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', dispatchLoadEvent);
    }} else {{
      // DOM already ready, dispatch after a short delay to let React mount
      setTimeout(dispatchLoadEvent, 100);
    }}
  }} catch (e) {{
    console.error('Failed to read embedded NVD data:', e);
  }}
}})();
</script>'''


def create_layer_script(n_meshes: int) -> str:
    """Create the script that applies mesh overlay layers once meshes load."""
    return f'''<script>
(function() {{
  // niivue keeps the stored colours of an embedded mesh and does not apply its
  // overlay layers on load; setMeshLayerProperty re-composites them.
  var expectedMeshes = {n_meshes};
  var deadline = Date.now() + 120000;
  function applyLayers(nv) {{
    try {{
      var jobs = [];
      nv.meshes.forEach(function(mesh, i) {{
        if (mesh.layers && mesh.layers.length > 0) {{
          jobs.push(nv.setMeshLayerProperty(i, 0, {{}}));
        }}
      }});
      Promise.all(jobs).catch(function(e) {{
        console.warn('Could not apply surface overlays:', e);
      }});
    }} catch (e) {{
      console.warn('Could not apply surface overlays:', e);
    }}
  }}
  function waitForMeshes() {{
    var nv = window.freebrowse && window.freebrowse.nv;
    if (nv && nv.meshes && nv.meshes.length >= expectedMeshes) {{
      applyLayers(nv);
    }} else if (Date.now() < deadline) {{
      setTimeout(waitForMeshes, 100);
    }} else {{
      console.warn('Surface overlays not applied: meshes did not load');
    }}
  }}
  waitForMeshes();
}})();
</script>'''


def embed_nvd(html_content: str, nvd_content: str, apply_layers: bool = True) -> str:
    """Embed NVD JSON into HTML content.

    Args:
        html_content: The freebrowse HTML content
        nvd_content: The NVD JSON content
        apply_layers: Add the script that applies mesh overlay layers after
            loading (only emitted when a mesh has layers)

    Returns:
        Modified HTML with embedded NVD data
    """
    # Validate NVD is valid JSON
    try:
        nvd = json.loads(nvd_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid NVD JSON: {e}")

    # Create bootstrap script with embedded data
    bootstrap = create_bootstrap_script(nvd_content)
    meshes = nvd.get("meshes") or []
    if apply_layers and any(mesh.get("layers") for mesh in meshes):
        bootstrap += "\n" + create_layer_script(len(meshes))

    # Insert before the LAST </body> tag (important: there may be </body>
    # inside JS string literals in single-file builds)
    body_lower = html_content.rfind('</body>')
    body_upper = html_content.rfind('</BODY>')

    # Use whichever appears last (closest to end of file)
    insert_pos = max(body_lower, body_upper)

    if insert_pos != -1:
        return html_content[:insert_pos] + bootstrap + '\n' + html_content[insert_pos:]
    else:
        # No body tag found, append to end
        return html_content + '\n' + bootstrap


def main():
    parser = argparse.ArgumentParser(
        description="Embed NVD data into a freebrowse HTML file for self-contained viewing.",
        epilog="Example: %(prog)s freebrowse.html scan.nvd -o viewer.html"
    )
    parser.add_argument(
        "html_file",
        help="Input freebrowse HTML file"
    )
    parser.add_argument(
        "nvd_file",
        help="NVD file to embed"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output HTML file (default: {nvd_stem}--{html_filename})"
    )
    parser.add_argument(
        "--no-apply-layers",
        action="store_true",
        help="Do not add the script that applies mesh overlay layers after loading"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print info to stderr"
    )

    args = parser.parse_args()

    html_path = Path(args.html_file)
    nvd_path = Path(args.nvd_file)

    # Validate input files exist
    if not html_path.exists():
        print(f"Error: HTML file not found: {html_path}", file=sys.stderr)
        sys.exit(1)
    if not nvd_path.exists():
        print(f"Error: NVD file not found: {nvd_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output filename
    if args.output:
        output_path = Path(args.output)
    else:
        # Default: {nvd_stem}--{html_filename}
        output_path = Path(f"{nvd_path.stem}--{html_path.name}")

    if args.verbose:
        print(f"HTML input: {html_path} ({html_path.stat().st_size:,} bytes)", file=sys.stderr)
        print(f"NVD input: {nvd_path} ({nvd_path.stat().st_size:,} bytes)", file=sys.stderr)

    # Read input files
    html_content = html_path.read_text(encoding='utf-8')
    nvd_content = nvd_path.read_text(encoding='utf-8')

    # Embed NVD into HTML
    try:
        output_content = embed_nvd(
            html_content, nvd_content, apply_layers=not args.no_apply_layers
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write output
    output_path.write_text(output_content, encoding='utf-8')

    if args.verbose:
        print(f"Output: {output_path} ({len(output_content):,} bytes)", file=sys.stderr)
    else:
        print(output_path)


if __name__ == "__main__":
    main()
