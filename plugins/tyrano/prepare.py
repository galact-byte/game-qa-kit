"""Allow only the authored scene and the recorded TyranoScript components."""
import json
from pathlib import Path

from gameqakit.admission import file_digest, fingerprint, no_links

SCRIPT = "gameqa-tyrano-fixture.ks"
SCRIPT_SHA256 = "e4f19687f147f3c29b8db4a03a216f11361d493abc8b18b098a6c13fc03cd4c4"
EXECUTABLE = "engine-runtime.exe"
RUNTIME_SHA256 = "1a02b95a335ddae82bbc2447c22f14ed03353a51dc4ea53162fa456769ce43bc"
PLUGIN = Path(__file__).resolve().parent
APP = "resources/app/"
BRIDGES = {"Fixture.js": "main.js", "MainBridge.js": "MainBridge.js", "Observer.js": "Observer.js"}
CONFIG = APP + "data/system/Config.tjs"
ROOT_FILES = (EXECUTABLE, "LICENSE", "LICENSES.chromium.html", "chrome_100_percent.pak",
              "chrome_200_percent.pak", "d3dcompiler_47.dll", "ffmpeg.dll", "icudtl.dat",
              "libEGL.dll", "libGLESv2.dll", "natives_blob.bin", "resources.pak",
              "snapshot_blob.bin", "v8_context_snapshot.bin")


def tree_files(root):
    no_links(root)
    for path in root.iterdir():
        no_links(path)
        if path.is_dir():
            yield from tree_files(path)
        elif path.is_file():
            yield path
        else:
            raise ValueError("Unexpected runtime entry: " + path.name)


def source_digests(source):
    root = Path(source)
    no_links(root / SCRIPT)
    if {p.name for p in root.iterdir()} != {SCRIPT} or file_digest(root / SCRIPT) != SCRIPT_SHA256:
        raise ValueError("仅接受未经修改的 TyranoScript 自写场景目录")
    return {SCRIPT: SCRIPT_SHA256}


def runtime_files(artifacts):
    root = Path(artifacts)
    files = [root / name for name in (*ROOT_FILES, APP + "index.html", CONFIG)]
    for directory in ("locales", APP + "tyrano"):
        files.extend(tree_files(root / directory))
    result = {}
    for path in files:
        no_links(path)
        result[path.relative_to(root).as_posix()] = file_digest(path)
    if fingerprint(result) != RUNTIME_SHA256:
        raise ValueError("运行组件不匹配已登记的 TyranoScript Ver5 / Electron 7.1.2")
    return result


def artifact_digests(artifacts):
    runtime_files(artifacts)
    result = {"runtime": RUNTIME_SHA256}
    for name in BRIDGES:
        no_links(PLUGIN / name)
        result[name] = file_digest(PLUGIN / name)
    return result


def authored_files():
    return {
        APP + "package.json": json.dumps({"name": "gameqa-tyrano-fixture", "version": "1.0.0", "main": "main.js"}).encode(),
        APP + "data/scenario/make.ks": b"[return]\n",
        APP + "data/system/KeyConfig.js": b"var __tyrano_key_config = { key: {13: function() { TYRANO.kag.key_mouse.next(); }}, mouse: {}, gesture: {}, system_key_event: false };\n",
    }


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
        ("source", source_digests, source, "选择只含仓库自写场景的目录。"),
        ("artifacts", artifact_digests, artifacts, "选择已登记的本机 TyranoScript 运行组件目录。"),
    ):
        try:
            if not path:
                raise ValueError("尚未选择固定构建目录")
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + "_not_ready", message=str(exc), field=field, recovery=recovery))
    return blockers
