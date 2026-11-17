import os

import cv2
import numpy as np
import yaml

from map_merger import core

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
MAP1 = os.path.join(EX, "my_map.yaml")
MAP2 = os.path.join(EX, "my_map_2.yaml")


def crop_map(src_yaml, box, out_dir, name):
    # cut a piece out of a map and fix the origin, like a smaller run from the same start
    m = yaml.safe_load(open(src_yaml))
    img = cv2.imread(os.path.join(EX, m["image"]), cv2.IMREAD_GRAYSCALE)
    x0, y0, x1, y1 = box
    h = img.shape[0]
    r = m["resolution"]
    m["origin"] = [m["origin"][0] + x0 * r, m["origin"][1] + (h - y1) * r, 0]
    m["image"] = name + ".pgm"
    cv2.imwrite(os.path.join(out_dir, name + ".pgm"), img[y0:y1, x0:x1])
    path = os.path.join(out_dir, name + ".yaml")
    yaml.safe_dump(m, open(path, "w"))
    return path


def test_load_keeps_unknown():
    m = core.load_map(MAP1)
    vals = set(np.unique(m["grid"]).tolist())
    assert vals == {core.UNKNOWN, core.FREE, core.OCC}
    # grey 205 must stay unknown, not become free
    img = cv2.imread(os.path.join(EX, "my_map.pgm"), cv2.IMREAD_GRAYSCALE)
    assert (np.flipud(m["grid"])[img == 205] == core.UNKNOWN).all()


def test_merge_basic():
    r = core.merge([core.load_map(MAP1), core.load_map(MAP2)])
    assert r["grid"].shape == (234, 385)
    assert r["res"] == 0.05
    assert r["removed"].sum() > 0  # the doorway that is closed in map 1 and open in map 2


def test_keep_all_walls():
    maps = [core.load_map(MAP1), core.load_map(MAP2)]
    a = core.merge(maps)["grid"]
    b = core.merge(maps, keep_all_walls=True)["grid"]
    assert (b == core.OCC).sum() > (a == core.OCC).sum()


def test_cropped_maps_line_up(tmp_path):
    maps = [core.load_map(MAP1), core.load_map(MAP2)]
    ref = core.merge(maps)["grid"]
    extra = [crop_map(MAP1, (100, 20, 300, 200), str(tmp_path), "c1"),
             crop_map(MAP2, (0, 40, 200, 223), str(tmp_path), "c2")]
    maps += [core.load_map(p) for p in extra]
    out = core.merge(maps)["grid"]
    assert (out == ref).all()


def test_refine_rejects_big_jumps():
    maps = [core.load_map(MAP2), core.load_map(MAP1)]
    r = core.merge(maps, refine=True)
    assert len(r["notes"]) == 1
    # whatever ecc said, the result must be close to the plain merge
    plain = core.merge(maps)["grid"]
    assert (r["grid"] != plain).mean() < 0.02


def test_save_roundtrip(tmp_path):
    r = core.merge([core.load_map(MAP1), core.load_map(MAP2)])
    pgm, yml = core.save_map(r, str(tmp_path / "out"))
    back = core.load_map(yml)
    assert (back["grid"] == r["grid"]).all()
    assert back["origin"][:2] == [round(v, 4) for v in r["origin"][:2]]
