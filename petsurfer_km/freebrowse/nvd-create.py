#!/usr/bin/env python3
"""Create standalone NiiVue documents (.nvd) with embedded imaging data.

Writes the niivue-mono document schema (``NVDocumentData``) at the version read
by FreeBrowse 2.5.x (``NVD_VERSION``), JSON-encoded.  Volumes are embedded
decoded, as ``volumes[i].data = {hdr, img, datatypeCode}``, which is how niivue
itself saves a self-contained document; binary payloads are tagged
``{"$ta": "Uint8Array", "b64": ...}`` (niivue-mono ``documentJson.ts``).

This script has no external dependencies (standard library only) and can be
copied and distributed independently to pipeline developers.

Example usage:
    # Basic: create NVD from image files (first image gray, the rest overlays)
    ./nvd-create.py -o output.nvd image1.nii.gz image2.nii.gz

    # With template (inherits settings and per-volume display options)
    ./nvd-create.py --template scene.nvd -o output.nvd image1.nii.gz

    # Output to stdout for piping
    ./nvd-create.py image.nii.gz > output.nvd
"""

from __future__ import annotations

import argparse
import base64
import copy
import gzip
import json
import math
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

# Document schema version written.  FreeBrowse 2.5.1 bundles a niivue that
# rejects documents newer than 9; newer niivue releases still read 9.
NVD_VERSION = 9

NIFTI1_HEADER_SIZE = 348

# NIfTI datatype codes niivue can view, with their element size in bytes (used
# for byte-swapping big-endian files).
SUPPORTED_DATATYPES = {
    2: 1,  # DT_UINT8
    4: 2,  # DT_INT16
    8: 4,  # DT_INT32
    16: 4,  # DT_FLOAT32
    64: 8,  # DT_FLOAT64
    128: 1,  # DT_RGB24
    256: 1,  # DT_INT8
    512: 2,  # DT_UINT16
    768: 4,  # DT_UINT32
    2304: 1,  # DT_RGBA32
}

# Display options for images not covered by a template: the first image is the
# background, the rest are overlays.
DEFAULT_BACKGROUND = {"colormap": "Gray", "opacity": 1}
DEFAULT_OVERLAY = {"colormap": "Nih", "opacity": 0.5}


def canonical_colormap(name):
    """Return niivue's stored form of a colormap name (first letter upper case).

    niivue keys colormaps as ``name[0].upper() + name[1:]`` and FreeBrowse's
    colormap picker matches that exact case.
    """
    if not isinstance(name, str) or not name:
        return name
    return name[0].upper() + name[1:]


# ---------------------------------------------------------------------------
# NIfTI-1
# ---------------------------------------------------------------------------


def read_image_bytes(path) -> bytes:
    """Read a file, transparently decompressing gzip (e.g. ``.nii.gz``)."""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def _finite(x: float) -> float:
    """JSON cannot carry NaN/Inf header floats; niivue's defaults for them are 0."""
    return x if math.isfinite(x) else 0.0


def _string_at(raw: bytes, start: int, end: int) -> str:
    """Mirror nifti-reader-js ``Utils.getStringAt``: drop NUL bytes."""
    return "".join(chr(b) for b in raw[start:end] if b != 0)


def _affine(qform_code, sform_code, pix_dims, quatern, qoffset, srow):
    """Voxel-to-world matrix, chosen exactly as nifti-reader-js does.

    Quaternion when ``qform_code > 0 and sform_code < qform_code``, else the
    ``srow`` rows when ``sform_code > 0``, else a diagonal of ``pixDims``.
    """
    affine = [[0.0] * 4 for _ in range(4)]
    affine[3][3] = 1.0
    if qform_code < 1 and sform_code < 1:
        for i in range(3):
            affine[i][i] = pix_dims[i + 1]
    if qform_code > 0 and sform_code < qform_code:
        b, c, d = quatern
        a = math.sqrt(max(0.0, 1.0 - (b * b + c * c + d * d)))
        qfac = 1.0 if pix_dims[0] == 0 else pix_dims[0]
        rot = [
            [a * a + b * b - c * c - d * d, 2 * b * c - 2 * a * d, 2 * b * d + 2 * a * c],
            [2 * b * c + 2 * a * d, a * a + c * c - b * b - d * d, 2 * c * d - 2 * a * b],
            [2 * b * d - 2 * a * c, 2 * c * d + 2 * a * b, a * a + d * d - c * c - b * b],
        ]
        for r in range(3):
            for col in range(3):
                affine[r][col] = rot[r][col] * pix_dims[col + 1]
            affine[r][2] *= qfac
            affine[r][3] = qoffset[r]
    elif sform_code > 0:
        for r in range(3):
            affine[r] = list(srow[r])
    return affine


def parse_nifti1(blob: bytes) -> dict:
    """Parse an uncompressed single-file NIfTI-1 into niivue's embedded volume data.

    Returns ``{"hdr": ..., "img": bytes, "datatypeCode": int}``.  ``hdr`` has the
    fields niivue's ``NVDocument.extractHeaderData`` copies from a
    nifti-reader-js header; ``img`` is the raw voxel bytes, little-endian.
    """
    if len(blob) < NIFTI1_HEADER_SIZE:
        raise ValueError(f"too short for a NIfTI-1 header ({len(blob)} bytes)")
    if struct.unpack_from("<i", blob, 0)[0] == NIFTI1_HEADER_SIZE:
        e, little = "<", True
    elif struct.unpack_from(">i", blob, 0)[0] == NIFTI1_HEADER_SIZE:
        e, little = ">", False
    else:
        raise ValueError("not a NIfTI-1 file (sizeof_hdr != 348)")
    magic = _string_at(blob, 344, 348)
    if magic != "n+1":
        raise ValueError(f"not a single-file NIfTI-1 (magic {magic!r})")

    def u(fmt, off):
        return struct.unpack_from(e + fmt, blob, off)

    dims = list(u("8h", 40))
    pix_dims = [_finite(x) for x in u("8f", 76)]
    datatype = u("h", 70)[0]
    bitpix = u("h", 72)[0]
    qform_code = u("h", 252)[0]
    sform_code = u("h", 254)[0]
    quatern = [_finite(x) for x in u("3f", 256)]
    qoffset = [_finite(x) for x in u("3f", 268)]
    srow = [[_finite(x) for x in u("4f", 280 + 16 * r)] for r in range(3)]

    hdr = {
        "littleEndian": True,  # img is emitted little-endian below, whatever the input
        "dim_info": blob[39],
        "dims": dims,
        "pixDims": pix_dims,
        "intent_p1": _finite(u("f", 56)[0]),
        "intent_p2": _finite(u("f", 60)[0]),
        "intent_p3": _finite(u("f", 64)[0]),
        "intent_code": u("h", 68)[0],
        "datatypeCode": datatype,
        "numBitsPerVoxel": bitpix,
        "slice_start": u("h", 74)[0],
        "vox_offset": _finite(u("f", 108)[0]),
        "scl_slope": _finite(u("f", 112)[0]),
        "scl_inter": _finite(u("f", 116)[0]),
        "slice_end": u("h", 120)[0],
        "slice_code": blob[122],
        "xyzt_units": blob[123],
        "cal_max": _finite(u("f", 124)[0]),
        "cal_min": _finite(u("f", 128)[0]),
        "slice_duration": _finite(u("f", 132)[0]),
        "toffset": _finite(u("f", 136)[0]),
        "description": _string_at(blob, 148, 228),
        "aux_file": _string_at(blob, 228, 252),
        "qform_code": qform_code,
        "sform_code": sform_code,
        "quatern_b": quatern[0],
        "quatern_c": quatern[1],
        "quatern_d": quatern[2],
        "qoffset_x": qoffset[0],
        "qoffset_y": qoffset[1],
        "qoffset_z": qoffset[2],
        "affine": _affine(qform_code, sform_code, pix_dims, quatern, qoffset, srow),
        "intent_name": _string_at(blob, 328, 344),
        "magic": magic,
    }

    if datatype not in SUPPORTED_DATATYPES:
        raise ValueError(f"unsupported NIfTI datatype code {datatype}")
    elem = SUPPORTED_DATATYPES[datatype]
    if bitpix != elem * 8 and datatype not in (128, 2304):
        raise ValueError(f"bitpix {bitpix} disagrees with datatype {datatype}")
    ndim = dims[0]
    if not 1 <= ndim <= 7:
        raise ValueError(f"invalid dim[0] = {ndim}")
    nvox = 1
    for i in range(1, ndim + 1):
        nvox *= max(dims[i], 1)
    nbytes = nvox * (bitpix // 8)
    start = int(hdr["vox_offset"])
    img = blob[start:start + nbytes]
    if len(img) != nbytes:
        raise ValueError(f"voxel data is {len(img)} bytes, header implies {nbytes}")
    if not little and elem > 1:
        swapped = bytearray(nbytes)
        for k in range(elem):
            swapped[k::elem] = img[elem - 1 - k::elem]
        img = bytes(swapped)
    return {"hdr": hdr, "img": img, "datatypeCode": datatype}


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


def nifti_volume(path, **options) -> dict:
    """Return a document volume entry with *path*'s data embedded.

    *options* are niivue volume display options (``colormap``, ``opacity``,
    ``calMin``, ``calMax``, ...); ``name`` defaults to the file name.
    """
    vol = {"name": Path(path).name}
    vol.update({k: v for k, v in options.items() if k not in ("url", "data")})
    if "colormap" in vol:
        vol["colormap"] = canonical_colormap(vol["colormap"])
    vol["data"] = parse_nifti1(read_image_bytes(path))
    return vol


def create_document(volumes=(), meshes=(), template=None) -> dict:
    """Assemble a document from volume/mesh entries.

    Settings groups (``scene``, ``layout``, ``ui``, ...) are taken from
    *template* (a document dict); its ``volumes``/``meshes`` are ignored here.
    """
    doc = {"version": NVD_VERSION, "created": datetime.now(timezone.utc).isoformat()}
    if template:
        for key, value in template.items():
            if key not in ("version", "created", "volumes", "meshes"):
                doc[key] = copy.deepcopy(value)
    doc["volumes"] = list(volumes)
    doc["meshes"] = list(meshes)
    return doc


def _load_template(template):
    if template is None or isinstance(template, dict):
        return template
    with open(template) as f:
        return json.load(f)


def create_nvd(image_paths, template=None) -> dict:
    """Create a document embedding *image_paths*.

    *template* (a path or a document dict) supplies the settings groups and,
    by position, each image's display options; images beyond the template's
    ``volumes`` get ``DEFAULT_BACKGROUND`` (first) or ``DEFAULT_OVERLAY``.
    """
    template = _load_template(template)
    template_volumes = (template or {}).get("volumes", [])
    volumes = []
    for i, image_path in enumerate(image_paths):
        if i < len(template_volumes):
            options = template_volumes[i]
        else:
            options = DEFAULT_BACKGROUND if i == 0 else DEFAULT_OVERLAY
        volumes.append(nifti_volume(image_path, **options))
    return create_document(volumes, (template or {}).get("meshes", []), template)


def _tag_binary(value):
    """JSON-safe copy of *value*: bytes become ``$ta``-tagged dicts, None keys drop."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$ta": "Uint8Array", "b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {k: _tag_binary(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_tag_binary(v) for v in value]
    return value


def to_json(doc: dict, indent=None) -> str:
    """Serialize a document to niivue's JSON encoding.

    Raises ``ValueError`` on NaN/Infinity, which the encoding cannot carry.
    """
    return json.dumps(_tag_binary(doc), indent=indent, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(
        description="Create standalone NiiVue documents (.nvd) with embedded imaging data.",
        epilog="Example: %(prog)s -o output.nvd T1.nii.gz segmentation.nii.gz"
    )
    parser.add_argument(
        "images",
        nargs="+",
        help="NIfTI-1 image files to embed (.nii or .nii.gz)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output .nvd file path (default: stdout)"
    )
    parser.add_argument(
        "-t", "--template",
        help="Template .nvd file to inherit settings from"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print info to stderr"
    )

    args = parser.parse_args()

    # Validate input files exist
    for image_path in args.images:
        if not Path(image_path).exists():
            print(f"Error: Image file not found: {image_path}", file=sys.stderr)
            sys.exit(1)

    # Validate template if provided
    if args.template and not Path(args.template).exists():
        print(f"Error: Template file not found: {args.template}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Creating NVD with {len(args.images)} image(s)...", file=sys.stderr)
        for img in args.images:
            size = Path(img).stat().st_size
            print(f"  - {img} ({size:,} bytes)", file=sys.stderr)
        if args.template:
            print(f"Using template: {args.template}", file=sys.stderr)

    # Create the NVD document
    try:
        nvd = create_nvd(args.images, args.template)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Output
    output_json = to_json(nvd)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        if args.verbose:
            print(f"Wrote {args.output} ({len(output_json):,} bytes)", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
