# checks that the web version (docs/merge.js) makes exactly the same map as core.py
# skipped when node is not installed

import os
import shutil
import subprocess

import pytest

from map_merger import core

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
EX = os.path.join(ROOT, "examples")

JS = r"""
const M = require(process.argv[2]), fs = require('fs'), path = require('path');
const [out, keep, res, ...files] = process.argv.slice(3);
const maps = files.map(y => {
  const info = M.parseYaml(fs.readFileSync(y, 'utf8'));
  return M.loadMap(info, M.decodePGM(fs.readFileSync(path.join(path.dirname(y), info.image))), path.basename(y));
});
const r = M.merge(maps, {keepAllWalls: keep === '1', res: +res || null});
fs.writeFileSync(out, Buffer.from(M.toPGM(r)));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("keep,res,order", [(False, None, 0), (True, None, 0), (False, 0.1, 1)])
def test_js_matches_python(tmp_path, keep, res, order):
    files = [os.path.join(EX, "my_map.yaml"), os.path.join(EX, "my_map_2.yaml")]
    if order:
        files.reverse()

    r = core.merge([core.load_map(f) for f in files], res=res, keep_all_walls=keep)
    py_pgm, _ = core.save_map(r, str(tmp_path / "py"))

    script = tmp_path / "run.js"
    script.write_text(JS)
    js_pgm = str(tmp_path / "js.pgm")
    subprocess.run(["node", str(script), os.path.join(ROOT, "docs", "merge.js"), js_pgm,
                    "1" if keep else "0", str(res or 0)] + files, check=True)

    assert open(py_pgm, "rb").read() == open(js_pgm, "rb").read()
