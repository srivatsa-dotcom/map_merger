# merge logic for occupancy grid maps (pgm/png + yaml, same format as map_server / nav2)
# all maps are expected to be built from the same start point, so they share one world frame

import math
import os

import cv2
import numpy as np
import yaml

UNKNOWN = -1
FREE = 0
OCC = 100


def load_map(yaml_file):
    with open(yaml_file) as f:
        info = yaml.safe_load(f)

    img_file = info["image"]
    if not os.path.isabs(img_file):
        img_file = os.path.join(os.path.dirname(os.path.abspath(yaml_file)), img_file)

    img = cv2.imread(img_file, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError("cant read map image: " + img_file)

    occ_th = info.get("occupied_thresh", 0.65)
    free_th = info.get("free_thresh", 0.25)

    p = img / 255.0
    if not info.get("negate", 0):
        p = 1.0 - p

    grid = np.full(img.shape, UNKNOWN, np.int8)
    grid[p > occ_th] = OCC
    grid[p < free_th] = FREE
    grid[img == 205] = UNKNOWN  # map_saver writes unknown as 205, which is below free_thresh

    # flip so row 0 is the bottom of the map (same direction as world y)
    grid = np.flipud(grid)

    origin = [float(v) for v in info["origin"]]
    while len(origin) < 3:
        origin.append(0.0)

    return {
        "name": os.path.splitext(os.path.basename(yaml_file))[0],
        "path": os.path.abspath(yaml_file),
        "grid": grid,
        "res": float(info["resolution"]),
        "origin": origin,
        "info": info,
    }


def cell_to_world(m):
    ox, oy, yaw = m["origin"]
    r = m["res"]
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[r * c, -r * s, ox],
                     [r * s, r * c, oy]])


def corners(m):
    h, w = m["grid"].shape
    pts = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], float).T
    return (cell_to_world(m) @ pts).T


def to_3x3(a):
    return np.vstack([a, [0, 0, 1]])


def warp_grid(grid, A, shape):
    # +2 so that 0 means "outside this map" after warping
    tmp = (grid.astype(np.int16) + 2).astype(np.uint8)
    tmp = cv2.warpAffine(tmp, A, (shape[1], shape[0]), flags=cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    out = np.full(shape, UNKNOWN, np.int8)
    out[tmp == 2] = FREE
    out[tmp == 102] = OCC
    return out


def find_drift(ref, other, max_shift=10, max_rot=5.0):
    # small rotation + shift between two maps using ECC
    # returns (2x3 warp, message). identity warp if it fails or the answer is too big
    # (maps share a start point, so a big correction means ECC locked onto the wrong thing)
    def prep(g):
        img = np.zeros(g.shape, np.float32)
        img[g == FREE] = 0.3
        img[g == OCC] = 1.0
        return cv2.GaussianBlur(img, (0, 0), 2.0)

    warp = np.eye(2, 3, dtype=np.float32)
    mask = ((ref != UNKNOWN) & (other != UNKNOWN)).astype(np.uint8)
    if mask.sum() < 500:
        return warp, "not enough overlap, skipped"

    try:
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
        _, warp = cv2.findTransformECC(prep(ref), prep(other), warp,
                                       cv2.MOTION_EUCLIDEAN, crit, mask, 5)
    except cv2.error:
        return np.eye(2, 3, dtype=np.float32), "did not converge, kept origin alignment"

    ang = math.degrees(math.atan2(warp[1, 0], warp[0, 0]))
    msg = "dx=%.2f px  dy=%.2f px  rot=%.2f deg" % (warp[0, 2], warp[1, 2], ang)
    if math.hypot(warp[0, 2], warp[1, 2]) > max_shift or abs(ang) > max_rot:
        return np.eye(2, 3, dtype=np.float32), "rejected (%s is too big), kept origin alignment" % msg
    return warp, msg


def sees_open(g, radius=3, win=7, min_free=0.6):
    # cells this map is sure are open:
    # free, no wall of its own nearby (handles 1-2px offset), and mostly free around it
    k = 2 * radius + 1
    wall_near = cv2.dilate((g == OCC).astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    free_ratio = cv2.blur((g == FREE).astype(np.float32), (win, win))
    return (g == FREE) & ~wall_near & (free_ratio >= min_free)


def combine(grids, keep_all_walls=False, radius=3):
    # returns (merged grid, mask of walls that got removed)
    stack = np.stack(grids)
    out = np.full(grids[0].shape, UNKNOWN, np.int8)
    out[(stack == FREE).any(axis=0)] = FREE
    out[(stack == OCC).any(axis=0)] = OCC

    removed = np.zeros(out.shape, bool)
    if not keep_all_walls:
        # wall in one map but another map clearly sees through it (door open in that run)
        open_somewhere = np.zeros(out.shape, bool)
        for g in grids:
            open_somewhere |= sees_open(g, radius=radius)
        removed = (out == OCC) & open_somewhere
        out[removed] = FREE

    return out, removed


def merge(maps, res=None, refine=False, keep_all_walls=False, radius=3, pad=5):
    """Merge a list of maps from load_map(). Returns a dict with the result."""
    if len(maps) < 2:
        raise ValueError("need at least 2 maps")
    if res is None:
        res = min(m["res"] for m in maps)

    pts = np.vstack([corners(m) for m in maps])
    xmin, ymin = pts.min(axis=0) - pad * res
    xmax, ymax = pts.max(axis=0) + pad * res
    w = int(math.ceil((xmax - xmin) / res))
    h = int(math.ceil((ymax - ymin) / res))

    world_to_cell = np.array([[1 / res, 0, -xmin / res],
                              [0, 1 / res, -ymin / res]])

    grids = []
    for m in maps:
        A = (to_3x3(world_to_cell) @ to_3x3(cell_to_world(m)))[:2]
        grids.append(warp_grid(m["grid"], A, (h, w)))

    notes = []
    if refine:
        # align everything to the first map
        for i in range(1, len(grids)):
            warp, msg = find_drift(grids[0], grids[i], max_shift=max(3, round(0.5 / res)))
            grids[i] = warp_grid(grids[i], cv2.invertAffineTransform(warp), (h, w))
            notes.append("%s -> %s: %s" % (maps[i]["name"], maps[0]["name"], msg))

    merged, removed = combine(grids, keep_all_walls, radius)
    return {
        "grid": merged,
        "removed": removed,
        "grids": grids,
        "res": res,
        "origin": [float(xmin), float(ymin), 0.0],
        "notes": notes,
    }


def to_image(grid):
    # grid -> pgm style image (top row first)
    img = np.full(grid.shape, 205, np.uint8)
    img[grid == FREE] = 254
    img[grid == OCC] = 0
    return np.flipud(img).copy()


COLORS = [(230, 57, 70), (37, 99, 235), (42, 157, 143), (244, 162, 97),
          (155, 93, 229), (233, 196, 106), (0, 187, 249), (241, 91, 181)]  # RGB


def overlay_image(grids, removed=None):
    # RGB image: each map's walls in its own colour, dark = walls 2+ maps agree on,
    # green = walls removed because another map sees through them
    stack = np.stack(grids)
    img = np.full(grids[0].shape + (3,), 205, np.uint8)
    img[(stack == FREE).any(axis=0)] = (250, 250, 250)
    for i, g in enumerate(grids):
        img[g == OCC] = COLORS[i % len(COLORS)]
    img[(stack == OCC).sum(axis=0) >= 2] = (30, 30, 30)
    if removed is not None:
        img[removed] = (46, 204, 113)
    return np.flipud(img).copy()


def save_map(result, name, ref_info=None):
    # writes name.pgm and name.yaml, returns both paths
    ref_info = ref_info or {}
    base = os.path.splitext(name)[0]
    pgm = base + ".pgm"
    cv2.imwrite(pgm, to_image(result["grid"]))

    info = {
        "image": os.path.basename(pgm),
        "mode": ref_info.get("mode", "trinary"),
        "resolution": result["res"],
        "origin": [round(v, 4) for v in result["origin"]],
        "negate": 0,
        "occupied_thresh": ref_info.get("occupied_thresh", 0.65),
        "free_thresh": ref_info.get("free_thresh", 0.25),
    }
    with open(base + ".yaml", "w") as f:
        yaml.safe_dump(info, f, default_flow_style=None, sort_keys=False)
    return pgm, base + ".yaml"
