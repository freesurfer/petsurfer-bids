"""Tests for the FreeBrowse document writer (``freebrowse/nvd-create.py``),
the HTML embedder (``freebrowse/nvd-embed.py``) and the report helpers that
combine them.

The NIfTI-1, GIFTI and FreeSurfer readers are checked against nibabel, which
writes the fixtures.
"""

from __future__ import annotations

import base64
import gzip
import json
import struct
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from nibabel.freesurfer import read_geometry, write_geometry, write_morph_data

from petsurfer_km import report_helpers
from petsurfer_km.report_helpers import (
    FREEBROWSE_HTML,
    _FREEBROWSE_DIR,
    _get_freebrowse_modules,
    generate_freebrowse_surface_viewer,
    generate_freebrowse_viewer,
    robust_vlim,
)

nvd_create, nvd_embed = _get_freebrowse_modules()

# A rotated, anisotropic, left-handed (det < 0) voxel-to-world matrix.
_THETA = 0.3
_AFFINE = np.array([
    [-2.0 * np.cos(_THETA), -3.0 * np.sin(_THETA), 0.0, 90.0],
    [-2.0 * np.sin(_THETA), 3.0 * np.cos(_THETA), 0.0, -126.0],
    [0.0, 0.0, 4.0, -72.0],
    [0.0, 0.0, 0.0, 1.0],
])


def _write_nifti(
    path: Path,
    data: np.ndarray,
    *,
    qform: np.ndarray | None = None,
    qform_code: int = 0,
    sform: np.ndarray | None = None,
    sform_code: int = 0,
    endianness: str = "<",
) -> Path:
    hdr = nib.Nifti1Header(endianness=endianness)
    hdr.set_data_dtype(data.dtype)
    img = nib.Nifti1Image(data, sform if sform is not None else qform, hdr)
    img.set_qform(qform, code=qform_code)
    img.set_sform(sform, code=sform_code)
    nib.save(img, str(path))
    return path


def _parse(path: Path) -> dict:
    return nvd_create.parse_nifti1(nvd_create.read_image_bytes(path))


def _decode_json(text: str):
    """Mirror niivue's decodeDocumentJSON for the ``$ta`` tag."""
    def hook(obj):
        if set(obj) == {"$ta", "b64"}:
            return base64.b64decode(obj["b64"])
        return obj
    return json.loads(text, object_hook=hook)


def _embedded_json(html: str) -> str:
    start_tag = '<script type="application/json" id="embedded-nvd-data">'
    start = html.index(start_tag) + len(start_tag)
    end = html.index("</script>", start)
    return html[start:end]


# --- NIfTI-1 parsing ------------------------------------------------------------

def test_parse_sform_gz_matches_nibabel(tmp_path: Path) -> None:
    data = np.arange(4 * 5 * 6, dtype=np.int16).reshape(4, 5, 6) - 50
    path = _write_nifti(tmp_path / "v.nii.gz", data, sform=_AFFINE, sform_code=4)

    vol = _parse(path)
    hdr = vol["hdr"]

    assert hdr["dims"][:4] == [3, 4, 5, 6]
    assert hdr["datatypeCode"] == vol["datatypeCode"] == 4
    assert hdr["numBitsPerVoxel"] == 16
    assert hdr["sform_code"] == 4 and hdr["qform_code"] == 0
    assert hdr["magic"] == "n+1"
    assert hdr["littleEndian"] is True
    np.testing.assert_allclose(hdr["pixDims"][1:4], [2.0, 3.0, 4.0], rtol=1e-6)
    np.testing.assert_allclose(hdr["affine"], nib.load(path).header.get_sform(), atol=1e-5)
    assert vol["img"] == data.astype("<i2").tobytes(order="F")


def test_parse_qform_left_handed_matches_nibabel(tmp_path: Path) -> None:
    data = np.zeros((3, 4, 5), dtype=np.float32)
    path = _write_nifti(tmp_path / "q.nii", data, qform=_AFFINE, qform_code=1)

    hdr = _parse(path)["hdr"]

    assert hdr["pixDims"][0] == -1  # qfac
    np.testing.assert_allclose(hdr["affine"], nib.load(path).header.get_qform(), atol=1e-5)


def test_parse_prefers_qform_when_its_code_is_higher(tmp_path: Path) -> None:
    other = np.diag([1.0, 1.0, 1.0, 1.0])
    path = _write_nifti(
        tmp_path / "qs.nii", np.zeros((2, 2, 2), np.uint8),
        qform=_AFFINE, qform_code=2, sform=other, sform_code=1,
    )

    hdr = _parse(path)["hdr"]

    np.testing.assert_allclose(hdr["affine"], nib.load(path).header.get_qform(), atol=1e-5)


def test_parse_without_codes_uses_pixdims(tmp_path: Path) -> None:
    path = tmp_path / "n.nii"
    img = nib.Nifti1Image(np.zeros((2, 2, 2), np.uint8), np.diag([2.0, 3.0, 4.0, 1.0]))
    img.set_qform(None, code=0)
    img.set_sform(None, code=0)
    nib.save(img, str(path))

    hdr = _parse(path)["hdr"]

    np.testing.assert_allclose(np.array(hdr["affine"])[:3, :3], np.diag([2.0, 3.0, 4.0]))


def test_parse_big_endian_emits_little_endian_voxels(tmp_path: Path) -> None:
    data = np.linspace(-1, 1, 24, dtype=np.float32).reshape(2, 3, 4)
    path = _write_nifti(tmp_path / "be.nii", data, sform=_AFFINE, sform_code=1, endianness=">")
    assert nib.load(path).header.endianness == ">"

    vol = _parse(path)

    assert vol["img"] == data.astype("<f4").tobytes(order="F")
    assert vol["hdr"]["dims"][:4] == [3, 2, 3, 4]


def test_parse_keeps_scaling(tmp_path: Path) -> None:
    img = nib.Nifti1Image(np.ones((2, 2, 2), np.int16), _AFFINE)
    img.header.set_slope_inter(2.5, -1.0)
    path = tmp_path / "s.nii"
    nib.save(img, str(path))

    hdr = _parse(path)["hdr"]

    assert hdr["scl_slope"] == pytest.approx(2.5)
    assert hdr["scl_inter"] == pytest.approx(-1.0)


def test_parse_rejects_bad_input(tmp_path: Path) -> None:
    path = _write_nifti(tmp_path / "c.nii", np.zeros((2, 2, 2), np.complex64))
    with pytest.raises(ValueError, match="datatype"):
        _parse(path)
    with pytest.raises(ValueError, match="NIfTI-1"):
        nvd_create.parse_nifti1(b"\0" * 400)
    good = _write_nifti(tmp_path / "t.nii", np.zeros((4, 4, 4), np.float32))
    with pytest.raises(ValueError, match="voxel data"):
        nvd_create.parse_nifti1(good.read_bytes()[:-8])


# --- document assembly / JSON ---------------------------------------------------

def test_create_nvd_uses_template_options_then_defaults(tmp_path: Path) -> None:
    paths = [
        _write_nifti(tmp_path / f"i{i}.nii.gz", np.zeros((2, 2, 2), np.float32))
        for i in range(3)
    ]
    template = {
        "version": 9,
        "ui": {"crosshairColor": [1, 0, 0, 0.5]},
        "volumes": [{"name": "bg", "colormap": "gray", "calMin": 1, "url": "x.nii"}],
    }

    doc = nvd_create.create_nvd([str(p) for p in paths], template=template)

    assert doc["version"] == nvd_create.NVD_VERSION == 9
    assert doc["ui"] == {"crosshairColor": [1, 0, 0, 0.5]}
    assert doc["meshes"] == []
    v0, v1, v2 = doc["volumes"]
    assert (v0["name"], v0["colormap"], v0["calMin"]) == ("bg", "Gray", 1)
    assert "url" not in v0
    assert (v1["name"], v1["colormap"], v1["opacity"]) == ("i1.nii.gz", "Nih", 0.5)
    assert v2["name"] == "i2.nii.gz"
    assert all(set(v["data"]) == {"hdr", "img", "datatypeCode"} for v in doc["volumes"])


def test_to_json_tags_bytes_and_rejects_nan() -> None:
    doc = {"version": 9, "a": b"\x00\x01\xff", "b": None, "c": [b"\x02"]}

    text = nvd_create.to_json(doc)

    assert json.loads(text)["a"] == {"$ta": "Uint8Array", "b64": "AAH/"}
    assert "b" not in json.loads(text)
    assert _decode_json(text) == {"version": 9, "a": b"\x00\x01\xff", "c": [b"\x02"]}
    with pytest.raises(ValueError):
        nvd_create.to_json({"calMin": float("nan")})


def test_template_is_a_v9_document() -> None:
    with open(_FREEBROWSE_DIR / "templates" / "petsurfer-km-template.nvd") as f:
        template = json.load(f)

    assert template["version"] == nvd_create.NVD_VERSION
    assert not {"imageOptionsArray", "encodedImageBlobs", "opts"} & set(template)
    assert [v["colormap"] for v in template["volumes"]] == ["Gray", "Nih"]


# --- embedding ------------------------------------------------------------------

def test_embed_is_script_safe_and_round_trips() -> None:
    html = '<html><body><script>var s = "</body>";</script></body></html>'
    doc = {"version": 9, "volumes": [{"name": "a</script><!--b", "data": {"img": b"\x01"}}]}

    out = nvd_embed.embed_nvd(html, nvd_create.to_json(doc))

    assert out.startswith('<html><body><script>var s = "</body>";</script>')
    assert out.endswith("</body></html>")
    embedded = _embedded_json(out)
    assert "<" not in embedded
    assert _decode_json(embedded) == {"version": 9, "volumes": [{"name": "a</script><!--b", "data": {"img": b"\x01"}}]}
    assert "new TextEncoder().encode(dataElement.textContent)" in out


def test_vendored_freebrowse_has_embed_hooks() -> None:
    html = (_FREEBROWSE_DIR / FREEBROWSE_HTML).read_text(encoding="utf-8")

    assert "loadEmbeddedNvd" in html
    assert "__EMBEDDED_NVD_DATA__" in html
    assert "window.freebrowse={nv:" in html
    assert "async setMeshLayerProperty(" in html


def test_generate_freebrowse_viewer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report_helpers, "_freebrowse_html_cache", "<html><body></body></html>")
    template = _write_nifti(tmp_path / "tpl.nii.gz", np.full((3, 3, 3), 4000, np.int16), sform=_AFFINE, sform_code=4)
    mimap = _write_nifti(tmp_path / "sub-01_mimap.nii.gz", np.ones((2, 2, 2), np.float32), sform=_AFFINE, sform_code=4)
    out = tmp_path / "figures" / "viewer.html"

    generate_freebrowse_viewer(mimap, template, (0.5, 2.5), out, tmp_path)

    doc = _decode_json(_embedded_json(out.read_text(encoding="utf-8")))
    assert doc["version"] == 9
    bg, overlay = doc["volumes"]
    assert (bg["colormap"], bg["calMin"], bg["calMax"]) == ("Gray", 2000, 8500)
    assert (overlay["name"], overlay["colormap"]) == ("sub-01_mimap.nii.gz", "Nih")
    assert (overlay["calMin"], overlay["calMax"]) == (0.5, 2.5)
    assert overlay["data"]["img"] == np.ones(8, "<f4").tobytes()
    assert gzip.decompress(template.read_bytes())[352:] == bg["data"]["img"]


# --- surfaces -------------------------------------------------------------------

# A tetrahedron: 4 vertices, 4 triangles.
_COORDS = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], np.float32)
_FACES = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], np.int32)


def _write_fs_surface(path: Path) -> Path:
    write_geometry(str(path), _COORDS, _FACES)
    return path


def _write_func_gii(path: Path, values: np.ndarray, encoding: str = "GZipBase64Binary") -> Path:
    darray = nib.gifti.GiftiDataArray(values, intent="NIFTI_INTENT_NONE", encoding=encoding)
    nib.save(nib.GiftiImage(darrays=[darray]), str(path))
    return path


def test_read_fs_surface_matches_nibabel(tmp_path: Path) -> None:
    path = _write_fs_surface(tmp_path / "lh.pial")

    positions, indices = nvd_create.read_fs_surface(path)

    coords, faces = read_geometry(str(path))
    assert positions == coords.astype("<f4").tobytes()
    assert indices == faces.astype("<u4").tobytes()


def test_read_fs_curv_matches_nibabel(tmp_path: Path) -> None:
    values = np.array([-1.5, 0, 2.25, 7], np.float32)
    write_morph_data(str(tmp_path / "lh.sulc"), values)

    assert nvd_create.read_surface_values(tmp_path / "lh.sulc") == values.astype("<f4").tobytes()


@pytest.mark.parametrize("encoding", ["ASCII", "Base64Binary", "GZipBase64Binary"])
@pytest.mark.parametrize("dtype", [np.float32, np.int32])
def test_read_gifti_values(tmp_path: Path, encoding: str, dtype) -> None:
    values = np.array([-2, 0, 3, 7], dtype)
    path = _write_func_gii(tmp_path / "x.func.gii", values, encoding)

    assert nvd_create.read_surface_values(path) == values.astype("<f4").tobytes()


def test_read_nifti_values_freesurfer_layouts(tmp_path: Path) -> None:
    values = np.arange(12, dtype=np.float32) - 3
    nii = _write_nifti(tmp_path / "a.nii.gz", values.reshape(12, 1, 1))
    gii = _write_func_gii(tmp_path / "a.func.gii", values)
    assert nvd_create.read_surface_values(nii) == nvd_create.read_surface_values(gii) == values.tobytes()

    # FreeSurfer writes vox_offset 0 and the 27307 x 1 x 6 style layout.
    raw = bytearray(gzip.decompress(nii.read_bytes()))
    struct.pack_into("<f", raw, 108, 0.0)
    struct.pack_into("<4h", raw, 40, 3, 2, 1, 6)
    (tmp_path / "b.nii").write_bytes(raw)
    assert nvd_create.read_surface_values(tmp_path / "b.nii") == values.tobytes()

    # Scaled integers.
    ints = _write_nifti(tmp_path / "c.nii", np.arange(12, dtype=np.int16).reshape(12, 1, 1))
    raw = bytearray(ints.read_bytes())
    struct.pack_into("<ff", raw, 112, 0.5, 1.0)
    ints.write_bytes(raw)
    expected = np.asarray(nib.load(ints).dataobj, np.float32).ravel()
    assert np.frombuffer(nvd_create.read_surface_values(ints), "<f4").tolist() == expected.tolist()


def test_gray_colors() -> None:
    values = np.array([-10, -3, 0, 3, 10], "<f4").tobytes()

    colors = np.frombuffer(nvd_create.gray_colors(values, -3, 3), "<u4")
    inverted = np.frombuffer(nvd_create.gray_colors(values, -3, 3, inverted=True), "<u4")

    assert (colors & 0xFF).tolist() == [0, 0, 128, 255, 255]
    assert (inverted & 0xFF).tolist() == [255, 255, 127, 0, 0]
    for c in colors.tolist():
        assert c & 0xFF == (c >> 8) & 0xFF == (c >> 16) & 0xFF and c >> 24 == 255


def test_mesh_entry_validates_and_defaults_colors(tmp_path: Path) -> None:
    positions, indices = nvd_create.read_fs_surface(_write_fs_surface(tmp_path / "s"))
    layer = nvd_create.mesh_layer(np.zeros(4, "<f4").tobytes(), name="m", colormap="nih")

    mesh = nvd_create.mesh_entry(positions, indices, [layer], name="s", color=[1, 0, 0, 1])

    assert mesh["layers"][0]["colormap"] == "Nih"
    assert np.frombuffer(mesh["data"]["colors"], "<u4").tolist() == [0xFF0000FF] * 4
    short = nvd_create.mesh_layer(np.zeros(3, "<f4").tobytes(), name="short")
    with pytest.raises(ValueError, match="3 values, mesh has 4 vertices"):
        nvd_create.mesh_entry(positions, indices, [short])
    with pytest.raises(ValueError, match="out of range"):
        nvd_create.mesh_entry(positions, np.array([0, 1, 4], "<u4").tobytes())
    with pytest.raises(ValueError, match="triplets"):
        nvd_create.mesh_entry(positions[:-4], indices)
    with pytest.raises(ValueError, match="colours"):
        nvd_create.mesh_entry(positions, indices, colors=b"\0" * 4)


def test_create_nvd_with_mesh_uses_template_options(tmp_path: Path) -> None:
    surface = _write_fs_surface(tmp_path / "lh.pial")
    write_morph_data(str(tmp_path / "lh.sulc"), np.arange(4, dtype=np.float32))
    template = {"meshes": [{"shaderType": "matte", "layers": [{"colormap": "gray", "calMin": 0}]}]}

    doc = nvd_create.create_nvd([], template, [(surface, [tmp_path / "lh.sulc"] * 2)])

    (mesh,) = doc["meshes"]
    assert (mesh["name"], mesh["shaderType"]) == ("lh.pial", "matte")
    assert [layer["name"] for layer in mesh["layers"]] == ["lh.sulc", "lh.sulc"]
    assert [layer["colormap"] for layer in mesh["layers"]] == ["Gray", "Nih"]
    assert doc["volumes"] == []


def test_embed_adds_layer_script_only_for_meshes_with_layers() -> None:
    html = "<html><body></body></html>"
    with_layers = nvd_create.to_json({"version": 9, "meshes": [{"layers": [{"data": b""}]}, {}]})

    out = nvd_embed.embed_nvd(html, with_layers)

    assert "nv.setMeshLayerProperty(i, 0, {})" in out
    assert "var expectedMeshes = 2;" in out
    assert "setMeshLayerProperty" not in nvd_embed.embed_nvd(html, with_layers, apply_layers=False)
    assert "setMeshLayerProperty" not in nvd_embed.embed_nvd(html, nvd_create.to_json({"version": 9}))


def _fake_fsaverage(tmp_path: Path, monkeypatch) -> None:
    surf = tmp_path / "fs" / "subjects" / "fsaverage" / "surf"
    surf.mkdir(parents=True)
    for hemi in ("lh", "rh"):
        _write_fs_surface(surf / f"{hemi}.pial")
        write_morph_data(str(surf / f"{hemi}.sulc"), np.array([-3, 0, 3, 0], np.float32))
    monkeypatch.setenv("FREESURFER_HOME", str(tmp_path / "fs"))
    monkeypatch.setattr(report_helpers, "_fsaverage_cache", {})
    monkeypatch.setattr(report_helpers, "_freebrowse_html_cache", "<html><body></body></html>")


def test_generate_freebrowse_surface_viewer(tmp_path: Path, monkeypatch) -> None:
    _fake_fsaverage(tmp_path, monkeypatch)
    lh_values = np.array([0, 1, 2, 3], np.float32)
    rh_values = np.array([4, 5, 6, 0], np.float32)
    lh = _write_func_gii(tmp_path / "sub-01_hemi-L_mimap.func.gii", lh_values)
    rh = _write_nifti(tmp_path / "sub-01_hemi-R_mimap.nii.gz", rh_values.reshape(4, 1, 1))
    out = tmp_path / "figures" / "surf.html"

    generate_freebrowse_surface_viewer({"lh": lh, "rh": rh}, out)

    html = out.read_text(encoding="utf-8")
    doc = _decode_json(_embedded_json(html))
    assert "nv.setMeshLayerProperty" in html
    assert doc["layout"] == {"sliceType": 4} and doc["volumes"] == []
    lh_mesh, rh_mesh = doc["meshes"]
    assert (lh_mesh["name"], rh_mesh["name"]) == ("lh.pial", "rh.pial")
    sulc, overlay = rh_mesh["layers"]
    assert (sulc["name"], sulc["colormap"]) == ("rh.sulc", "Gray")
    assert (overlay["name"], overlay["colormap"]) == ("sub-01_hemi-R_mimap.nii.gz", "Nih")
    assert overlay["data"] == rh_values.astype("<f4").tobytes()
    vlim = robust_vlim(np.concatenate([lh_values, rh_values]))
    assert [overlay["calMin"], overlay["calMax"]] == pytest.approx(list(vlim))
    assert lh_mesh["layers"][1]["calMin"] == overlay["calMin"]
    # Stored colours: inverted gray sulc over [-3, 3]
    assert (np.frombuffer(lh_mesh["data"]["colors"], "<u4") & 0xFF).tolist() == [255, 127, 0, 127]


def test_generate_freebrowse_surface_viewer_one_hemi_explicit_vlim(tmp_path: Path, monkeypatch) -> None:
    _fake_fsaverage(tmp_path, monkeypatch)
    rh = _write_func_gii(tmp_path / "rh.func.gii", np.array([4, 5, 6, 0], np.float32))
    out = tmp_path / "surf.html"

    generate_freebrowse_surface_viewer({"rh": rh}, out, vlim=(1.0, 9.0))

    (mesh,) = _decode_json(_embedded_json(out.read_text(encoding="utf-8")))["meshes"]
    assert mesh["name"] == "rh.pial"
    assert (mesh["layers"][1]["calMin"], mesh["layers"][1]["calMax"]) == (1.0, 9.0)
