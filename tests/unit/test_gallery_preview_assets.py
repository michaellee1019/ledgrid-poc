"""Shipped Gallery images must cover the catalog and contain useful pixels."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from tools.generate_gallery_previews import OUTPUT, catalog_scene, render_thumbnail
from web.composer_final_preview import current_component_catalog


def test_every_catalog_animation_has_a_visible_distinct_shipped_preview():
    catalog = current_component_catalog()
    manifest = json.loads((OUTPUT / "manifest.json").read_text())
    expected = {d.component_id: d for d in catalog.descriptors if d.role.value == "animation"}
    assert set(manifest["entries"]) == set(expected)
    hashes = set()
    script = (OUTPUT / "previews.js").read_text()
    for name, entry in manifest["entries"].items():
        path = OUTPUT / entry["file"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert entry["sha256"][:16] in path.name
        assert entry["file"] in script
        assert entry["scene"] == catalog_scene(expected[name]), f"Regenerate changed catalog item {name}"
        with Image.open(path) as image:
            assert image.size == (33, 138)
            pixels = np.asarray(image)
            # Catch black assets and almost-empty placeholder frames; retain
            # genuinely sparse art, which is valid for fireflies and games.
            assert np.count_nonzero(pixels.max(axis=2) > 40) > 45, name
            assert pixels.std() > 5, name
        assert entry["sha256"] not in hashes, f"Duplicate catalog illustration: {name}"
        hashes.add(entry["sha256"])
    assert sum((OUTPUT / e["file"]).stat().st_size for e in manifest["entries"].values()) < 300_000


@pytest.mark.parametrize("component_id", ["cellular_tapestry", "gif_animation", "fireworks"])
def test_warmed_production_render_reproduces_shipped_image(component_id):
    catalog = current_component_catalog()
    descriptor = next(d for d in catalog.descriptors if d.component_id == component_id)
    data, _, elapsed = render_thumbnail(descriptor, catalog)
    entry = json.loads((OUTPUT / "manifest.json").read_text())["entries"][component_id]
    assert data == (OUTPUT / entry["file"]).read_bytes()
    assert elapsed == entry["elapsed"]
    if component_id == "cellular_tapestry":
        with Image.open(OUTPUT / entry["file"]) as image:
            # A first fixed-time sample used to contain only a nascent row.
            populated_rows = (np.asarray(image).max(axis=2) > 80).any(axis=1)
            assert populated_rows.sum() > 100


def test_gallery_assets_are_included_in_fast_deployments():
    from pathlib import PurePosixPath
    from tools.deployment.deploy_manifest import _include_fast

    root = Path(__file__).resolve().parents[2]
    for path in OUTPUT.iterdir():
        assert _include_fast(PurePosixPath(path.relative_to(root)))
