#!/usr/bin/env python3
"""Create standalone NiiVue documents (.nvd) with embedded imaging data.

Writes the niivue-mono document schema (``NVDocumentData``) at the version read
by FreeBrowse 2.5.x (``NVD_VERSION``), JSON-encoded.  Data is embedded decoded,
which is how niivue itself saves a self-contained document: volumes as
``volumes[i].data = {hdr, img, datatypeCode}``, surfaces as
``meshes[i].data = {positions, indices, colors}`` with per-vertex overlays in
``meshes[i].layers[j].data``.  Binary payloads are tagged
``{"$ta": "Uint8Array", "b64": ...}`` (niivue-mono ``documentJson.ts``).

niivue displays an embedded mesh with its stored ``colors`` and does not apply
the overlay layers on load; ``nvd-embed.py`` adds a step that asks FreeBrowse to
apply them once the document has loaded.

This script has no external dependencies (standard library only) and can be
copied and distributed independently to pipeline developers.

Example usage:
    # Basic: create NVD from image files (first image gray, the rest overlays)
    ./nvd-create.py -o output.nvd image1.nii.gz image2.nii.gz

    # With template (inherits settings and per-volume display options)
    ./nvd-create.py --template scene.nvd -o output.nvd image1.nii.gz

    # FreeSurfer surface with overlays (curv, GIFTI or 1D NIfTI values)
    ./nvd-create.py -o output.nvd --mesh lh.pial lh.sulc lh.thickness.func.gii

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
import xml.etree.ElementTree as ET
import zlib
from array import array
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
# ... and for surfaces and their overlay layers not covered by a template.
DEFAULT_MESH = {"color": [1, 1, 1, 1], "shaderType": "phong"}
DEFAULT_LAYER = {"colormap": "Nih", "opacity": 1}


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
    # Data in a single-file NIfTI-1 cannot start inside the header; FreeSurfer
    # writes vox_offset 0 (nibabel likewise reads such files from byte 352).
    vox_offset = max(_finite(u("f", 108)[0]), 352.0)

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
        "vox_offset": vox_offset,
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
# Surfaces and per-vertex values
# ---------------------------------------------------------------------------

FS_TRIANGLE_MAGIC = b"\xff\xff\xfe"
FS_CURV_MAGIC = b"\xff\xff\xff"

# array typecodes for NIfTI datatype codes / GIFTI DataType names.
_NIFTI_TYPECODES = {2: "B", 4: "h", 8: "i", 16: "f", 64: "d", 256: "b", 512: "H", 768: "I"}
_GIFTI_TYPECODES = {
    "NIFTI_TYPE_UINT8": "B",
    "NIFTI_TYPE_INT16": "h",
    "NIFTI_TYPE_INT32": "i",
    "NIFTI_TYPE_FLOAT32": "f",
    "NIFTI_TYPE_FLOAT64": "d",
}


def _typed_array(typecode: str, raw: bytes, big_endian: bool = False) -> array:
    """Native-order ``array`` from raw bytes stored in the given byte order."""
    arr = array(typecode)
    arr.frombytes(raw)
    if big_endian != (sys.byteorder == "big"):
        arr.byteswap()
    return arr


def _le_bytes(arr: array) -> bytes:
    """Little-endian bytes of a native-order ``array``."""
    if sys.byteorder == "big":
        arr = array(arr.typecode, arr)
        arr.byteswap()
    return arr.tobytes()


def read_fs_surface(path) -> tuple:
    """Read a FreeSurfer binary triangle surface (e.g. ``lh.pial``).

    Returns ``(positions, indices)``: float32 xyz triplets and uint32 vertex
    index triplets, as little-endian bytes.
    """
    raw = Path(path).read_bytes()
    if raw[:3] != FS_TRIANGLE_MAGIC:
        raise ValueError(f"{path}: not a FreeSurfer triangle surface")
    off = raw.index(b"\n\n", 3) + 2  # skip the "created by" comment line
    nvert, nface = struct.unpack_from(">ii", raw, off)
    off += 8
    coords = _typed_array("f", raw[off:off + 12 * nvert], big_endian=True)
    off += 12 * nvert
    faces = _typed_array("i", raw[off:off + 12 * nface], big_endian=True)
    if len(coords) != 3 * nvert or len(faces) != 3 * nface:
        raise ValueError(f"{path}: truncated surface")
    if nface and (min(faces) < 0 or max(faces) >= nvert):
        raise ValueError(f"{path}: face index out of range")
    return _le_bytes(coords), _le_bytes(array("I", faces))


def read_fs_curv(path) -> bytes:
    """Read a FreeSurfer (new format) per-vertex file such as ``lh.sulc``."""
    raw = Path(path).read_bytes()
    if raw[:3] != FS_CURV_MAGIC:
        raise ValueError(f"{path}: not a FreeSurfer curvature file")
    nvert, _nface, per_vertex = struct.unpack_from(">iii", raw, 3)
    if per_vertex != 1:
        raise ValueError(f"{path}: {per_vertex} values per vertex, expected 1")
    values = _typed_array("f", raw[15:15 + 4 * nvert], big_endian=True)
    if len(values) != nvert:
        raise ValueError(f"{path}: truncated curvature file")
    return _le_bytes(values)


def read_gifti_values(path) -> bytes:
    """Read the first data array of a GIFTI file (e.g. ``.func.gii``) as float32."""
    root = ET.fromstring(read_image_bytes(path))
    darray = root.find("DataArray")
    if darray is None:
        raise ValueError(f"{path}: no GIFTI DataArray")
    encoding = darray.get("Encoding")
    text = (darray.findtext("Data") or "").strip()
    if encoding == "ASCII":
        values = array("f", (float(x) for x in text.split()))
    elif encoding in ("Base64Binary", "GZipBase64Binary"):
        typecode = _GIFTI_TYPECODES.get(darray.get("DataType"))
        if typecode is None:
            raise ValueError(f"{path}: unsupported GIFTI DataType {darray.get('DataType')!r}")
        raw = base64.b64decode(text)
        if encoding == "GZipBase64Binary":
            raw = zlib.decompress(raw, 47)  # zlib or gzip stream
        values = _typed_array(typecode, raw, darray.get("Endian") == "BigEndian")
        if typecode != "f":
            values = array("f", values)
    else:
        raise ValueError(f"{path}: unsupported GIFTI encoding {encoding!r}")
    count = 1
    for i in range(int(darray.get("Dimensionality", "1"))):
        count *= int(darray.get(f"Dim{i}"))
    if len(values) != count:
        raise ValueError(f"{path}: {len(values)} values, header implies {count}")
    return _le_bytes(values)


def read_nifti_values(path) -> bytes:
    """Read every value of a NIfTI-1 file as float32, scaling applied.

    For FreeSurfer "1D NIfTI" surface overlays (N_vertices x 1 x 1, or the
    27307 x 1 x 6 layout FreeSurfer uses for 163842 vertices) the stored order
    is the vertex order.
    """
    vol = parse_nifti1(read_image_bytes(path))
    typecode = _NIFTI_TYPECODES.get(vol["datatypeCode"])
    if typecode is None:
        raise ValueError(f"{path}: datatype {vol['datatypeCode']} cannot hold scalar values")
    slope, inter = vol["hdr"]["scl_slope"], vol["hdr"]["scl_inter"]
    scaled = slope != 0 and (slope != 1 or inter != 0)
    if typecode == "f" and not scaled:
        return vol["img"]
    values = _typed_array(typecode, vol["img"])
    if scaled:
        values = array("f", (v * slope + inter for v in values))
    elif typecode != "f":
        values = array("f", values)
    return _le_bytes(values)


def read_surface_values(path) -> bytes:
    """Per-vertex float32 values from GIFTI, NIfTI-1 or a FreeSurfer curv file."""
    name = Path(path).name.lower()
    if name.endswith((".gii", ".gii.gz")):
        return read_gifti_values(path)
    if name.endswith((".nii", ".nii.gz")):
        return read_nifti_values(path)
    return read_fs_curv(path)


def _pack_rgba(r: int, g: int, b: int, a: int) -> int:
    """Pack a colour the way niivue stores per-vertex colours (ABGR uint32)."""
    return (a << 24) | (b << 16) | (g << 8) | r


def gray_colors(values: bytes, cal_min: float, cal_max: float, inverted: bool = False) -> bytes:
    """Per-vertex gray colours for *values* windowed to [cal_min, cal_max].

    Matches niivue's rendering of a ``Gray`` layer, for use as a mesh's stored
    ``colors`` (what is shown until the layers are applied).
    """
    span = cal_max - cal_min
    colors = array("I")
    for v in _typed_array("f", values):
        frac = min(max((v - cal_min) / span, 0.0), 1.0) if span > 0 else 0.0
        level = int(frac * 255 + 0.5)
        if inverted:
            level = 255 - level
        colors.append(_pack_rgba(level, level, level, 255))
    return _le_bytes(colors)


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


def mesh_layer(values: bytes, **options) -> dict:
    """Return a mesh layer (per-vertex overlay) entry for float32 *values*.

    *options* are niivue layer display options (``name``, ``colormap``,
    ``calMin``, ``calMax``, ``opacity``, ``colormapType``, ...).
    """
    layer = {k: v for k, v in options.items() if k not in ("url", "data")}
    if "colormap" in layer:
        layer["colormap"] = canonical_colormap(layer["colormap"])
    layer["data"] = values
    return layer


def mesh_entry(positions: bytes, indices: bytes, layers=(), colors=None, **options) -> dict:
    """Return a document mesh entry with embedded geometry.

    *positions* / *indices* are little-endian float32 xyz / uint32 triangle
    triplets (see ``read_fs_surface``), *layers* come from ``mesh_layer``.
    *colors* (uint32 per vertex, see ``gray_colors``) is what niivue shows until
    the layers are applied; it defaults to the uniform ``color`` option.
    """
    if len(positions) % 12 or len(indices) % 12:
        raise ValueError("positions/indices must hold whole xyz / triangle triplets")
    nvert = len(positions) // 12
    if indices and max(_typed_array("I", indices)) >= nvert:
        raise ValueError(f"triangle index out of range for {nvert} vertices")
    for layer in layers:
        if len(layer["data"]) != 4 * nvert:
            raise ValueError(
                f"layer {layer.get('name', '')!r} has {len(layer['data']) // 4} values, "
                f"mesh has {nvert} vertices"
            )
    if colors is None:
        rgba = [int(c * 255 + 0.5) for c in options.get("color", DEFAULT_MESH["color"])]
        colors = _le_bytes(array("I", [_pack_rgba(*rgba)])) * nvert
    elif len(colors) != 4 * nvert:
        raise ValueError(f"{len(colors) // 4} colours for {nvert} vertices")
    mesh = {k: v for k, v in options.items() if k not in ("url", "data", "layers")}
    mesh["data"] = {"positions": positions, "indices": indices, "colors": colors}
    if layers:
        mesh["layers"] = list(layers)
    return mesh


def surface_mesh(surface_path, value_paths=(), options=None) -> dict:
    """Return a mesh entry for a FreeSurfer surface with per-vertex overlays.

    *options* is a template mesh entry: its settings apply to the mesh and its
    ``layers[j]`` to ``value_paths[j]`` (default ``DEFAULT_MESH`` /
    ``DEFAULT_LAYER``).  Names default to the file names.
    """
    options = dict(options or DEFAULT_MESH)
    layer_options = options.pop("layers", None) or []
    positions, indices = read_fs_surface(surface_path)
    layers = []
    for j, value_path in enumerate(value_paths):
        opts = {"name": Path(value_path).name}
        opts.update(layer_options[j] if j < len(layer_options) else DEFAULT_LAYER)
        layers.append(mesh_layer(read_surface_values(value_path), **opts))
    return mesh_entry(positions, indices, layers, **{"name": Path(surface_path).name, **options})


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


def create_nvd(image_paths, template=None, meshes=()) -> dict:
    """Create a document embedding *image_paths* and *meshes*.

    *meshes* is a sequence of ``(surface_path, [value_path, ...])``.
    *template* (a path or a document dict) supplies the settings groups and,
    by position, each image's / mesh's display options; images beyond the
    template's ``volumes`` get ``DEFAULT_BACKGROUND`` (first) or
    ``DEFAULT_OVERLAY``, meshes beyond its ``meshes`` ``DEFAULT_MESH``.
    """
    template = _load_template(template) or {}
    template_volumes = template.get("volumes", [])
    template_meshes = template.get("meshes", [])
    volumes = []
    for i, image_path in enumerate(image_paths):
        if i < len(template_volumes):
            options = template_volumes[i]
        else:
            options = DEFAULT_BACKGROUND if i == 0 else DEFAULT_OVERLAY
        volumes.append(nifti_volume(image_path, **options))
    mesh_entries = [
        surface_mesh(surface, values, template_meshes[i] if i < len(template_meshes) else None)
        for i, (surface, values) in enumerate(meshes)
    ]
    return create_document(volumes, mesh_entries, template)


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
        nargs="*",
        help="NIfTI-1 image files to embed (.nii or .nii.gz)"
    )
    parser.add_argument(
        "-m", "--mesh",
        nargs="+",
        action="append",
        default=[],
        metavar=("SURFACE", "VALUES"),
        help="FreeSurfer surface followed by per-vertex overlay files "
             "(FreeSurfer curv, GIFTI or 1D NIfTI); may be repeated"
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
    if not args.images and not args.mesh:
        parser.error("give at least one image or --mesh")

    # Validate input files exist
    for image_path in args.images + [p for mesh in args.mesh for p in mesh]:
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
        nvd = create_nvd(
            args.images, args.template, [(mesh[0], mesh[1:]) for mesh in args.mesh]
        )
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
