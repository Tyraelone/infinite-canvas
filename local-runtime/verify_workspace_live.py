"""Read-only acceptance of the migrated disk workspace; no generation is submitted."""
import hashlib
import io
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import av
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3000/api/local-runtime/workspace"


def get(path, headers=None):
    return urllib.request.urlopen(urllib.request.Request(BASE + path, headers=headers or {}), timeout=60)


def api(path):
    with get(path) as response:
        payload = json.load(response)
    assert payload["code"] == 0, payload
    return payload["data"]


def inspect_media(path, mime):
    if mime.startswith("image/"):
        with Image.open(path) as image:
            image.load()
            return {"width": image.width, "height": image.height}
    if not mime.startswith("video/"):
        return {}
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        video = {"width": stream.width, "height": stream.height, "fps": float(stream.average_rate),
                 "frames": sum(1 for _ in container.decode(video=0))}
    with av.open(str(path)) as container:
        audio = container.streams.audio
        video["hasAudio"] = bool(audio)
        video["audioPeak"] = max((float(np.max(np.abs(frame.to_ndarray().astype(np.float32)))) for frame in container.decode(audio=0)), default=0) if audio else 0
    return video


def main():
    state = api("/state/canvas")
    projects = json.loads(state["value"])["state"]["projects"]
    archives = sorted((ROOT / "data/workspace/migrations").glob("*/manifest.json"))
    legacy = []
    for path in archives:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        raw = manifest.get("rawStates", {}).get("canvas")
        if raw:
            legacy.extend(json.loads(raw)["state"]["projects"])
    original = next(project for project in legacy if project["id"] == "itBPxeskMe509A2X_I2Gx" and len(project["nodes"]) == 4)
    current = next(project for project in projects if project["id"] == original["id"])
    assert {node["id"] for node in original["nodes"]} <= {node["id"] for node in current["nodes"]}
    assert all(link in current["connections"] for link in original["connections"])
    assert current["chatSessions"] == original["chatSessions"]
    assert current["agentConfig"] == original["agentConfig"]
    for node in original["nodes"]:
        migrated = next(item for item in current["nodes"] if item["id"] == node["id"])
        assert node["type"] == migrated["type"]
        for field in ("prompt", "model", "videoTaskId"):
            assert node.get("metadata", {}).get(field) == migrated.get("metadata", {}).get(field)
        if node["type"] == "text":
            assert node["metadata"]["content"] == migrated["metadata"]["content"]
    connection = sqlite3.connect(f"file:{(ROOT / 'data/infinite-canvas.db').as_posix()}?mode=ro", uri=True)
    files = connection.execute("select key,sha256,bytes,mime_type from local_workspace_files").fetchall()
    connection.close()
    report_files = []
    for key, digest, size, mime in files:
        path = ROOT / "data/workspace/files" / digest[:2] / digest
        data = path.read_bytes()
        assert len(data) == size and hashlib.sha256(data).hexdigest() == digest
        with get("/files/" + urllib.parse.quote(key, safe="")) as response:
            assert hashlib.sha256(response.read()).hexdigest() == digest
        with get("/files/" + urllib.parse.quote(key, safe=""), {"Range": "bytes=0-99"}) as response:
            assert response.status == 206 and response.read() == data[:100]
            assert response.headers["Content-Length"] == "100"
        report_files.append({"key": key, "sha256": digest, "bytes": size, "mimeType": mime,
                             "path": str(path), **inspect_media(path, mime)})
    original_video = next(item for item in report_files if item.get("width") == 864 and item.get("height") == 480)
    upscale = next(item for item in report_files if item.get("width") == 1728 and item.get("height") == 960)
    assert original_video["frames"] == upscale["frames"] == 124
    assert original_video["fps"] == upscale["fps"] == 24
    assert upscale["hasAudio"] and upscale["audioPeak"] > 0
    assert (ROOT / "data/workspace/pre-migration/infinite-canvas-before.sqlite").is_file()
    report = {"verifiedAt": datetime.now(timezone.utc).isoformat(), "status": api("/status"),
              "canvasId": current["id"], "originalNodes": len(original["nodes"]), "currentNodes": len(current["nodes"]),
              "originalConnections": len(original["connections"]), "currentConnections": len(current["connections"]),
              "chatSessionsPreserved": True, "agentConfigPreserved": True, "migrationArchives": len(archives),
              "files": report_files}
    output = ROOT / "data/local-runtime/shared-workspace-verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "nodes": report["currentNodes"], "filesVerified": len(files),
                      "upscale": {key: upscale[key] for key in ("width", "height", "frames", "fps", "hasAudio", "audioPeak")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
