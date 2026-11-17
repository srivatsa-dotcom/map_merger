// merge logic for the web version, same steps as map_merger/core.py
// works in the browser (window.MapMerge) and in node (require) for testing

(function (root) {
  "use strict";

  var UNKNOWN = -1, FREE = 0, OCC = 100;

  var COLORS = [[230, 57, 70], [37, 99, 235], [42, 157, 143], [244, 162, 97],
                [155, 93, 229], [233, 196, 106], [0, 187, 249], [241, 91, 181]];

  // ---- yaml (only the flat key: value format map_saver writes)

  function parseValue(v) {
    v = v.trim();
    if (v.charAt(0) === "[" && v.charAt(v.length - 1) === "]") {
      var inner = v.slice(1, -1).trim();
      return inner ? inner.split(",").map(parseValue) : [];
    }
    var q = v.charAt(0);
    if ((q === '"' || q === "'") && v.charAt(v.length - 1) === q) return v.slice(1, -1);
    if (v !== "" && !isNaN(Number(v))) return Number(v);
    return v;
  }

  function parseYaml(text) {
    var out = {}, last = null;
    text.split(/\r?\n/).forEach(function (line) {
      line = line.replace(/\s+#.*$/, "").replace(/^#.*$/, "");
      if (!line.trim()) return;
      var item = line.match(/^\s*-\s*(.+)$/);
      if (item && last) {  // block list like  origin:\n- 1.0\n- 2.0
        if (!Array.isArray(out[last])) out[last] = [];
        out[last].push(parseValue(item[1]));
        return;
      }
      var kv = line.match(/^\s*([A-Za-z_][\w]*)\s*:\s*(.*)$/);
      if (!kv) return;
      last = kv[1];
      out[last] = kv[2].trim() === "" ? [] : parseValue(kv[2]);
    });
    return out;
  }

  // ---- images

  function decodePGM(buf) {
    var bytes = new Uint8Array(buf);
    var pos = 0, tokens = [];
    function isSpace(c) { return c === 32 || c === 9 || c === 10 || c === 13; }
    while (tokens.length < 4 && pos < bytes.length) {
      while (pos < bytes.length && isSpace(bytes[pos])) pos++;
      if (bytes[pos] === 35) {  // # comment
        while (pos < bytes.length && bytes[pos] !== 10) pos++;
        continue;
      }
      var s = "";
      while (pos < bytes.length && !isSpace(bytes[pos])) s += String.fromCharCode(bytes[pos++]);
      tokens.push(s);
    }
    var magic = tokens[0], w = +tokens[1], h = +tokens[2], maxval = +tokens[3];
    if (magic !== "P5" && magic !== "P2") throw new Error("not a PGM file");
    var data = new Uint8Array(w * h);
    if (magic === "P5") {
      pos++;  // single whitespace after maxval
      if (maxval < 256) {
        for (var i = 0; i < w * h; i++) data[i] = bytes[pos + i];
      } else {
        for (var j = 0; j < w * h; j++) {
          data[j] = Math.round(((bytes[pos + 2 * j] << 8) | bytes[pos + 2 * j + 1]) * 255 / maxval);
        }
      }
    } else {
      var rest = new TextDecoder().decode(bytes.subarray(pos)).replace(/#.*$/gm, "").trim().split(/\s+/);
      for (var k = 0; k < w * h; k++) data[k] = Math.round(+rest[k] * 255 / maxval);
    }
    if (maxval !== 255 && magic === "P5" && maxval < 256) {
      for (var m = 0; m < w * h; m++) data[m] = Math.round(data[m] * 255 / maxval);
    }
    return { w: w, h: h, data: data };
  }

  function grayFromRGBA(rgba, w, h) {
    // same weights opencv uses for IMREAD_GRAYSCALE
    var data = new Uint8Array(w * h);
    for (var i = 0; i < w * h; i++) {
      data[i] = Math.round(0.299 * rgba[4 * i] + 0.587 * rgba[4 * i + 1] + 0.114 * rgba[4 * i + 2]);
    }
    return { w: w, h: h, data: data };
  }

  // ---- loading

  function loadMap(info, img, name) {
    var w = img.w, h = img.h;
    var occTh = info.occupied_thresh != null ? info.occupied_thresh : 0.65;
    var freeTh = info.free_thresh != null ? info.free_thresh : 0.25;
    var negate = !!info.negate;
    var grid = new Int8Array(w * h);
    for (var r = 0; r < h; r++) {
      for (var c = 0; c < w; c++) {
        var v = img.data[r * w + c];
        var p = v / 255.0;
        if (!negate) p = 1.0 - p;
        var g = UNKNOWN;
        if (p > occTh) g = OCC;
        if (p < freeTh) g = FREE;
        if (v === 205) g = UNKNOWN;  // map_saver unknown, sits below free_thresh
        grid[(h - 1 - r) * w + c] = g;  // row 0 = bottom, like world y
      }
    }
    var origin = (info.origin || [0, 0, 0]).map(Number);
    while (origin.length < 3) origin.push(0);
    if (!(info.resolution > 0)) throw new Error("resolution missing in yaml");
    return { name: name, grid: grid, w: w, h: h, res: Number(info.resolution), origin: origin, info: info };
  }

  // ---- geometry. affine = [a, b, c, d, e, f] meaning x' = a x + b y + c, y' = d x + e y + f

  function cellToWorld(m) {
    var r = m.res, cs = Math.cos(m.origin[2]), sn = Math.sin(m.origin[2]);
    return [r * cs, -r * sn, m.origin[0], r * sn, r * cs, m.origin[1]];
  }

  function compose(A, B) {  // A after B
    return [
      A[0] * B[0] + A[1] * B[3], A[0] * B[1] + A[1] * B[4], A[0] * B[2] + A[1] * B[5] + A[2],
      A[3] * B[0] + A[4] * B[3], A[3] * B[1] + A[4] * B[4], A[3] * B[2] + A[4] * B[5] + A[5],
    ];
  }

  function invert(M) {  // same steps as cv::invertAffineTransform
    var D = M[0] * M[4] - M[1] * M[3];
    D = D !== 0 ? 1.0 / D : 0;
    var a = M[4] * D, b = -M[1] * D, d = -M[3] * D, e = M[0] * D;
    return [a, b, -a * M[2] - b * M[5], d, e, -d * M[2] - e * M[5]];
  }

  function corners(m) {
    var A = cellToWorld(m), out = [];
    [[0, 0], [m.w, 0], [0, m.h], [m.w, m.h]].forEach(function (p) {
      out.push([A[0] * p[0] + A[1] * p[1] + A[2], A[3] * p[0] + A[4] * p[1] + A[5]]);
    });
    return out;
  }

  function rint(x) {  // round half to even, like cvRound
    var r = Math.round(x);
    if (Math.abs(x % 1) === 0.5 && r % 2 !== 0) r -= 1;
    return r;
  }

  // nearest neighbour warp, A maps source cell -> output cell (same rounding as cv2.warpAffine)
  function warpGrid(src, sw, sh, A, W, H) {
    var M = invert(A), out = new Int8Array(W * H).fill(UNKNOWN);
    var SCALE = 1024, HALF = 512;
    var ad = new Int32Array(W), bd = new Int32Array(W);
    for (var x = 0; x < W; x++) { ad[x] = rint(M[0] * x * SCALE); bd[x] = rint(M[3] * x * SCALE); }
    for (var y = 0; y < H; y++) {
      var X0 = rint((M[1] * y + M[2]) * SCALE) + HALF;
      var Y0 = rint((M[4] * y + M[5]) * SCALE) + HALF;
      for (x = 0; x < W; x++) {
        var sx = (X0 + ad[x]) >> 10, sy = (Y0 + bd[x]) >> 10;
        if (sx >= 0 && sx < sw && sy >= 0 && sy < sh) out[y * W + x] = src[sy * sw + sx];
      }
    }
    return out;
  }

  // ---- filters

  function dilateMask(mask, W, H, r) {
    var tmp = new Uint8Array(W * H), out = new Uint8Array(W * H), x, y, k;
    for (y = 0; y < H; y++) {
      for (x = 0; x < W; x++) {
        for (k = Math.max(0, x - r); k <= Math.min(W - 1, x + r); k++) {
          if (mask[y * W + k]) { tmp[y * W + x] = 1; break; }
        }
      }
    }
    for (y = 0; y < H; y++) {
      for (x = 0; x < W; x++) {
        for (k = Math.max(0, y - r); k <= Math.min(H - 1, y + r); k++) {
          if (tmp[k * W + x]) { out[y * W + x] = 1; break; }
        }
      }
    }
    return out;
  }

  function reflect101(i, n) {
    if (n === 1) return 0;
    while (i < 0 || i >= n) i = i < 0 ? -i : 2 * n - 2 - i;
    return i;
  }

  function boxBlur(src, W, H, win) {  // like cv2.blur, border reflect101
    var r = (win - 1) >> 1, tmp = new Float64Array(W * H), out = new Float64Array(W * H), x, y, k, s;
    for (y = 0; y < H; y++) {
      for (x = 0; x < W; x++) {
        s = 0;
        for (k = x - r; k <= x + r; k++) s += src[y * W + reflect101(k, W)];
        tmp[y * W + x] = s;
      }
    }
    for (y = 0; y < H; y++) {
      for (x = 0; x < W; x++) {
        s = 0;
        for (k = y - r; k <= y + r; k++) s += tmp[reflect101(k, H) * W + x];
        out[y * W + x] = s / (win * win);
      }
    }
    return out;
  }

  // cells this map is sure are open: free, no own wall nearby, mostly free around it
  function seesOpen(g, W, H, radius, win, minFree) {
    var n = W * H, occ = new Uint8Array(n), free = new Float64Array(n), i;
    for (i = 0; i < n; i++) { occ[i] = g[i] === OCC ? 1 : 0; free[i] = g[i] === FREE ? 1 : 0; }
    var near = dilateMask(occ, W, H, radius);
    var ratio = boxBlur(free, W, H, win);
    var out = new Uint8Array(n);
    for (i = 0; i < n; i++) out[i] = (g[i] === FREE && !near[i] && ratio[i] >= minFree - 1e-9) ? 1 : 0;
    return out;
  }

  function combine(grids, W, H, keepAllWalls, radius) {
    var n = W * H, out = new Int8Array(n).fill(UNKNOWN), removed = new Uint8Array(n), i, j;
    for (j = 0; j < grids.length; j++) for (i = 0; i < n; i++) if (grids[j][i] === FREE) out[i] = FREE;
    for (j = 0; j < grids.length; j++) for (i = 0; i < n; i++) if (grids[j][i] === OCC) out[i] = OCC;
    if (!keepAllWalls) {
      var open = new Uint8Array(n);
      for (j = 0; j < grids.length; j++) {
        var o = seesOpen(grids[j], W, H, radius, 7, 0.6);
        for (i = 0; i < n; i++) open[i] |= o[i];
      }
      for (i = 0; i < n; i++) {
        if (out[i] === OCC && open[i]) { out[i] = FREE; removed[i] = 1; }
      }
    }
    return { grid: out, removed: removed };
  }

  // ---- drift fix. brute force search of small shifts/rotations about the start point,
  // scoring how many walls of the moving map land on walls of the reference

  function findDrift(ref, mov, W, H, pivot, maxShift, maxRot) {
    var n = W * H, walls = new Float64Array(n), i;
    for (i = 0; i < n; i++) walls[i] = ref[i] === OCC ? 1 : 0;
    var field = boxBlur(boxBlur(walls, W, H, 3), W, H, 3);  // soft walls so near misses still count

    var px = [], py = [];
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      if (mov[y * W + x] === OCC) { px.push(x - pivot[0]); py.push(y - pivot[1]); }
    }
    if (px.length < 50) return { A: null, msg: "not enough walls, skipped" };

    function score(ang, dx, dy) {
      var cs = Math.cos(ang), sn = Math.sin(ang), s = 0;
      for (var k = 0; k < px.length; k++) {
        var X = Math.round(cs * px[k] - sn * py[k] + pivot[0] + dx);
        var Y = Math.round(sn * px[k] + cs * py[k] + pivot[1] + dy);
        if (X >= 0 && X < W && Y >= 0 && Y < H) s += field[Y * W + X];
      }
      return s;
    }

    var base = score(0, 0, 0), best = { s: base, a: 0, dx: 0, dy: 0 };
    for (var deg = -maxRot; deg <= maxRot + 1e-9; deg += 0.5) {
      var a = deg * Math.PI / 180;
      for (var dx = -maxShift; dx <= maxShift; dx++) {
        for (var dy = -maxShift; dy <= maxShift; dy++) {
          var s = score(a, dx, dy);
          if (s > best.s) best = { s: s, a: a, dx: dx, dy: dy };
        }
      }
    }
    if (best.s < base * 1.05 || (best.a === 0 && best.dx === 0 && best.dy === 0)) {
      return { A: null, msg: "already aligned, kept origin alignment" };
    }
    var cs = Math.cos(best.a), sn = Math.sin(best.a);
    var A = [cs, -sn, pivot[0] - cs * pivot[0] + sn * pivot[1] + best.dx,
             sn, cs, pivot[1] - sn * pivot[0] - cs * pivot[1] + best.dy];
    var msg = "dx=" + best.dx + " px  dy=" + best.dy + " px  rot=" + (best.a * 180 / Math.PI).toFixed(1) + " deg";
    return { A: A, msg: msg };
  }

  // ---- merge

  function merge(maps, opts) {
    opts = opts || {};
    if (maps.length < 2) throw new Error("need at least 2 maps");
    var res = opts.res || Math.min.apply(null, maps.map(function (m) { return m.res; }));
    var radius = opts.radius != null ? opts.radius : 3, pad = 5;

    var xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
    maps.forEach(function (m) {
      corners(m).forEach(function (p) {
        xmin = Math.min(xmin, p[0]); ymin = Math.min(ymin, p[1]);
        xmax = Math.max(xmax, p[0]); ymax = Math.max(ymax, p[1]);
      });
    });
    xmin -= pad * res; ymin -= pad * res; xmax += pad * res; ymax += pad * res;
    var W = Math.ceil((xmax - xmin) / res), H = Math.ceil((ymax - ymin) / res);
    var worldToCell = [1 / res, 0, -xmin / res, 0, 1 / res, -ymin / res];

    var grids = maps.map(function (m) {
      return warpGrid(m.grid, m.w, m.h, compose(worldToCell, cellToWorld(m)), W, H);
    });

    var notes = [];
    if (opts.refine) {
      var pivot = [-xmin / res, -ymin / res];  // the shared start point (world 0,0)
      var maxShift = Math.max(3, Math.round(0.5 / res));
      for (var i = 1; i < grids.length; i++) {
        var d = findDrift(grids[0], grids[i], W, H, pivot, maxShift, 3);
        if (d.A) grids[i] = warpGrid(grids[i], W, H, d.A, W, H);
        notes.push(maps[i].name + " -> " + maps[0].name + ": " + d.msg);
      }
    }

    var c = combine(grids, W, H, !!opts.keepAllWalls, radius);
    return { grid: c.grid, removed: c.removed, grids: grids, w: W, h: H,
             res: res, origin: [xmin, ymin, 0], notes: notes };
  }

  // ---- output

  function toGray(r) {  // top row first, like the pgm
    var W = r.w, H = r.h, out = new Uint8Array(W * H);
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      var g = r.grid[y * W + x];
      out[(H - 1 - y) * W + x] = g === FREE ? 254 : g === OCC ? 0 : 205;
    }
    return out;
  }

  function toRGBA(r, overlay) {
    var W = r.w, H = r.h, n = W * H, out = new Uint8ClampedArray(n * 4), i, j;
    var col = new Uint8Array(n * 3);
    if (!overlay) {
      for (i = 0; i < n; i++) {
        var v = r.grid[i] === FREE ? 254 : r.grid[i] === OCC ? 0 : 205;
        col[3 * i] = col[3 * i + 1] = col[3 * i + 2] = v;
      }
    } else {
      var count = new Uint8Array(n), anyFree = new Uint8Array(n);
      for (i = 0; i < n; i++) { col[3 * i] = col[3 * i + 1] = col[3 * i + 2] = 205; }
      r.grids.forEach(function (gr, k) {
        var c = COLORS[k % COLORS.length];
        for (i = 0; i < n; i++) {
          if (gr[i] === FREE) anyFree[i] = 1;
          if (gr[i] === OCC) { count[i]++; col[3 * i] = c[0]; col[3 * i + 1] = c[1]; col[3 * i + 2] = c[2]; }
        }
      });
      for (i = 0; i < n; i++) {
        if (count[i] === 0 && anyFree[i]) { col[3 * i] = col[3 * i + 1] = col[3 * i + 2] = 250; }
        if (count[i] >= 2) { col[3 * i] = col[3 * i + 1] = col[3 * i + 2] = 30; }
        if (r.removed[i]) { col[3 * i] = 46; col[3 * i + 1] = 204; col[3 * i + 2] = 113; }
      }
    }
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      i = y * W + x; j = (H - 1 - y) * W + x;  // flip to top row first
      out[4 * j] = col[3 * i]; out[4 * j + 1] = col[3 * i + 1]; out[4 * j + 2] = col[3 * i + 2]; out[4 * j + 3] = 255;
    }
    return out;
  }

  function toPGM(r) {
    var head = "P5\n" + r.w + " " + r.h + "\n255\n";
    var out = new Uint8Array(head.length + r.w * r.h);
    for (var i = 0; i < head.length; i++) out[i] = head.charCodeAt(i);
    out.set(toGray(r), head.length);
    return out;
  }

  function pyFloat(v) {  // print floats the way python's yaml does (0.0 not 0)
    var s = String(v);
    return /[.eE]/.test(s) || !isFinite(v) ? s : s + ".0";
  }

  function toYaml(r, imageName, refInfo) {
    refInfo = refInfo || {};
    var o = r.origin.map(function (v) { return pyFloat(Number(v.toFixed(4)) + 0); });
    return "image: " + imageName + "\n" +
      "mode: " + (refInfo.mode || "trinary") + "\n" +
      "resolution: " + pyFloat(r.res) + "\n" +
      "origin: [" + o.join(", ") + "]\n" +
      "negate: 0\n" +
      "occupied_thresh: " + (refInfo.occupied_thresh != null ? refInfo.occupied_thresh : 0.65) + "\n" +
      "free_thresh: " + (refInfo.free_thresh != null ? refInfo.free_thresh : 0.25) + "\n";
  }

  var api = {
    UNKNOWN: UNKNOWN, FREE: FREE, OCC: OCC, COLORS: COLORS,
    parseYaml: parseYaml, decodePGM: decodePGM, grayFromRGBA: grayFromRGBA,
    loadMap: loadMap, merge: merge, toRGBA: toRGBA, toPGM: toPGM, toYaml: toYaml,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MapMerge = api;
})(this);
