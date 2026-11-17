# command line version
# map-merge map1.yaml map2.yaml [more.yaml ...] -o merged_map

import argparse
import sys

import cv2

from . import core


def main(argv=None):
    parser = argparse.ArgumentParser(prog="map-merge",
                                     description="merge occupancy grid maps that share a start point")
    parser.add_argument("maps", nargs="+", help="map yaml files (2 or more)")
    parser.add_argument("-o", "--output", default="merged_map", help="output name, writes .pgm and .yaml")
    parser.add_argument("--resolution", type=float, default=None, help="output resolution (default: finest input)")
    parser.add_argument("--refine", action="store_true", help="fix small drift against the first map")
    parser.add_argument("--keep-all-walls", action="store_true",
                        help="dont remove walls that another map sees through")
    parser.add_argument("--radius", type=int, default=3,
                        help="how far (cells) a map must be from its own wall to clear another map's wall")
    parser.add_argument("--debug", action="store_true", help="also save a coloured overlay png")
    args = parser.parse_args(argv)

    if len(args.maps) < 2:
        parser.error("need at least 2 maps")

    maps = []
    for f in args.maps:
        m = core.load_map(f)
        h, w = m["grid"].shape
        print("%-20s %dx%d  res=%s  origin=%s" % (m["name"], w, h, m["res"], m["origin"]))
        maps.append(m)

    r = core.merge(maps, args.resolution, args.refine, args.keep_all_walls, args.radius)
    for n in r["notes"]:
        print("refine", n)
    if not args.keep_all_walls:
        print("removed %d wall cells that another map sees through" % r["removed"].sum())

    pgm, yml = core.save_map(r, args.output, maps[0]["info"])
    h, w = r["grid"].shape
    print("saved %s and %s  (%dx%d, origin %s)" % (pgm, yml, w, h, [round(v, 3) for v in r["origin"]]))

    if args.debug:
        out = args.output + "_overlay.png"
        cv2.imwrite(out, cv2.cvtColor(core.overlay_image(r["grids"], r["removed"]), cv2.COLOR_RGB2BGR))
        print("overlay saved to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
