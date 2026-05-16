import os
import re
import time
import json
import shutil
import hashlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="CC Tweaked YouTube Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# Render temp storage. This is intentionally temporary.
# New jobs wipe CURRENT_DIR and reuse it.
DATA_ROOT = Path(os.environ.get("CCVID_DATA_ROOT", "/tmp/ccvid"))
CURRENT_DIR = DATA_ROOT / "current"
CURRENT_VIDEO_DIR = CURRENT_DIR / "video"


# -----------------------------
# Basic helpers
# -----------------------------

def base_url() -> str:
    # Set this in Render if needed:
    # PUBLIC_BASE=https://cc-yt-bridge.onrender.com
    return os.environ.get("PUBLIC_BASE", "https://cc-yt-bridge.onrender.com").rstrip("/")


def ensure_dirs():
    CURRENT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def wipe_current():
    if CURRENT_DIR.exists():
        shutil.rmtree(CURRENT_DIR)
    ensure_dirs()


def write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_youtube_id(text: str) -> str | None:
    """
    Accepts:
      https://www.youtube.com/watch?v=VIDEOID
      https://youtu.be/VIDEOID
      https://www.youtube.com/shorts/VIDEOID
      https://www.youtube.com/embed/VIDEOID
      raw VIDEOID
    """
    s = (text or "").strip()

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s

    try:
        u = urlparse(s)
    except Exception:
        return None

    host = (u.netloc or "").lower()
    path = u.path or ""

    if "youtu.be" in host:
        vid = path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
            return vid

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(u.query or "")
        if "v" in qs and qs["v"]:
            vid = qs["v"][0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
                return vid

        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
            vid = parts[1]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
                return vid

    return None


def youtube_get(endpoint: str, params: dict):
    if not YOUTUBE_API_KEY:
        return None, {
            "ok": False,
            "error": "missing_api_key",
            "message": "Set YOUTUBE_API_KEY on Render.",
        }

    params = dict(params)
    params["key"] = YOUTUBE_API_KEY

    url = "https://www.googleapis.com/youtube/v3/" + endpoint
    r = requests.get(url, params=params, timeout=12)

    if not r.ok:
        return None, {
            "ok": False,
            "error": "youtube_api_failed",
            "status": r.status_code,
            "body": r.text[:800],
        }

    return r.json(), None


def compact_title(s: str, n: int = 78) -> str:
    s = " ".join((s or "").replace("\n", " ").split())
    return s[:n]


def get_video_details(video_id: str):
    if not YOUTUBE_API_KEY:
        return {
            "id": video_id,
            "title": "Direct YouTube video",
            "channel": "Unknown",
            "duration": "",
            "direct": True,
        }, None

    data, err = youtube_get("videos", {
        "part": "snippet,contentDetails",
        "id": video_id,
        "maxResults": 1,
    })

    if err:
        return None, err

    items = data.get("items", [])
    if not items:
        return None, {
            "ok": False,
            "error": "video_not_found",
            "id": video_id,
        }

    item = items[0]
    snip = item.get("snippet", {})
    details = item.get("contentDetails", {})

    return {
        "id": video_id,
        "title": compact_title(snip.get("title", "")),
        "channel": compact_title(snip.get("channelTitle", ""), 40),
        "duration": details.get("duration", ""),
        "direct": True,
    }, None


# -----------------------------
# Fake CCV chunk generator
# -----------------------------

def make_fake_chunk(job: str, video_id: str, title: str) -> str:
    """
    Temporary fake chunk.

    This is NOT the final video format yet.
    It gives CC something small and real to download from the bridge.
    Next update replaces this with actual 328x243 encoded frame chunks.
    """
    lines = []
    lines.append("CCV1")
    lines.append("job=" + job)
    lines.append("video_id=" + video_id)
    lines.append("title=" + title.replace("\n", " ")[:120])
    lines.append("fps=10")
    lines.append("w=164")
    lines.append("h=81")
    lines.append("subpixel_w=328")
    lines.append("subpixel_h=243")
    lines.append("chunk_seconds=10")
    lines.append("frames=0")
    lines.append("note=placeholder chunk; real frame data comes next")
    lines.append("END")
    return "\n".join(lines)


# -----------------------------
# Endpoints
# -----------------------------

@app.get("/")
def root():
    return {
        "ok": True,
        "name": "CC Tweaked YouTube Bridge",
        "storage": "render_temp_local",
        "try": [
            "/ping",
            "/search?q=shrek trailer",
            "/search?q=https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "/prepare?id=dQw4w9WgXcQ",
            "/status",
            "/manifest",
        ],
    }


@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "pong"


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    max_results: int = 5,
    page_token: str = "",
):
    q = (q or "").strip()
    max_results = max(1, min(int(max_results), 10))

    direct_id = extract_youtube_id(q)
    if direct_id:
        item, err = get_video_details(direct_id)
        if err:
            return JSONResponse(status_code=502, content=err)
        return {
            "ok": True,
            "mode": "direct_url",
            "query": q,
            "results": [item],
            "nextPageToken": "",
        }

    if not YOUTUBE_API_KEY:
        return {
            "ok": True,
            "mode": "demo_no_api_key",
            "query": q,
            "results": [
                {
                    "id": "dQw4w9WgXcQ",
                    "title": f"Demo result for: {compact_title(q, 50)}",
                    "channel": "Demo Channel",
                    "duration": "",
                    "direct": False,
                }
            ],
            "nextPageToken": "",
        }

    params = {
        "part": "snippet",
        "type": "video",
        "q": q,
        "maxResults": max_results,
        "safeSearch": "none",
    }
    if page_token:
        params["pageToken"] = page_token

    data, err = youtube_get("search", params)
    if err:
        return JSONResponse(status_code=502, content=err)

    results = []
    video_ids = []

    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        snip = item.get("snippet", {})
        if vid:
            video_ids.append(vid)
            results.append({
                "id": vid,
                "title": compact_title(snip.get("title", "")),
                "channel": compact_title(snip.get("channelTitle", ""), 40),
                "duration": "",
                "direct": False,
            })

    # Optional duration fill-in.
    if video_ids:
        detail_data, detail_err = youtube_get("videos", {
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "maxResults": len(video_ids),
        })
        if not detail_err:
            durations = {}
            for item in detail_data.get("items", []):
                durations[item.get("id")] = item.get("contentDetails", {}).get("duration", "")
            for r in results:
                r["duration"] = durations.get(r["id"], "")

    return {
        "ok": True,
        "mode": "search",
        "query": q,
        "results": results,
        "nextPageToken": data.get("nextPageToken", ""),
    }


@app.get("/prepare")
def prepare(id: str, title: str = ""):
    """
    Current behavior:
      1. Validate selected YouTube ID/URL.
      2. Wipe old temp storage.
      3. Create current/job.json.
      4. Create current/manifest.json.
      5. Create current/video/chunk_0001.ccv placeholder.
      6. Return URLs that CC can download immediately.

    Next behavior:
      Replace placeholder chunk with actual encoded video/audio outputs.
    """
    video_id = extract_youtube_id(id) or id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return JSONResponse(status_code=400, content={
            "ok": False,
            "error": "bad_video_id",
            "id": id,
        })

    title = compact_title(title, 100)
    seed = f"{video_id}-{int(time.time())}"
    job = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    wipe_current()

    chunk_name = "chunk_0001.ccv"
    chunk_rel = f"current/video/{chunk_name}"
    chunk_path = CURRENT_VIDEO_DIR / chunk_name

    fake_chunk = make_fake_chunk(job, video_id, title)
    chunk_path.write_text(fake_chunk, encoding="utf-8")

    now = int(time.time())

    manifest = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "placeholder_ready",
        "ready": True,
        "created_at": now,
        "storage": "render_temp_local",
        "fps": 10,
        "w": 164,
        "h": 81,
        "subpixel_w": 328,
        "subpixel_h": 243,
        "chunk_seconds": 10,
        "chunk_count": 1,
        "audio_ready": False,
        "audio_url": "",
        "chunks": [
            {
                "part": 1,
                "frames": 0,
                "url": f"{base_url()}/files/{chunk_rel}",
                "path": chunk_rel,
            }
        ],
        "message": "Temporary storage is working. This is a placeholder chunk; real frame/audio processing comes next.",
    }

    job_info = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "placeholder_ready",
        "ready": True,
        "manifest_url": f"{base_url()}/files/current/manifest.json",
        "status_url": f"{base_url()}/status",
        "created_at": now,
    }

    write_json(CURRENT_DIR / "manifest.json", manifest)
    write_json(CURRENT_DIR / "job.json", job_info)

    return {
        **job_info,
        "manifest": manifest,
    }


@app.get("/status")
def status(job: str = ""):
    job_info = read_json(CURRENT_DIR / "job.json")
    if not job_info:
        return {
            "ok": False,
            "status": "no_current_job",
            "ready": False,
            "message": "No current temp job exists. Call /prepare first.",
        }

    if job and job_info.get("job") != job:
        return {
            "ok": False,
            "status": "job_not_current",
            "ready": False,
            "requested": job,
            "current": job_info.get("job"),
            "message": "This temp bridge only stores one active job at a time.",
        }

    manifest_exists = (CURRENT_DIR / "manifest.json").exists()
    return {
        **job_info,
        "manifest_exists": manifest_exists,
    }


@app.get("/manifest")
def manifest(job: str = ""):
    m = read_json(CURRENT_DIR / "manifest.json")
    if not m:
        return JSONResponse(status_code=404, content={
            "ok": False,
            "error": "no_manifest",
            "message": "No current manifest exists. Call /prepare first.",
        })

    if job and m.get("job") != job:
        return JSONResponse(status_code=404, content={
            "ok": False,
            "error": "job_not_current",
            "requested": job,
            "current": m.get("job"),
        })

    return m


@app.get("/files/{file_path:path}")
def files(file_path: str):
    """
    Public temp file serving endpoint.

    Examples:
      /files/current/manifest.json
      /files/current/job.json
      /files/current/video/chunk_0001.ccv

    Safety:
      Only serves files under DATA_ROOT.
    """
    requested = (DATA_ROOT / file_path).resolve()
    root = DATA_ROOT.resolve()

    try:
        requested.relative_to(root)
    except ValueError:
        return JSONResponse(status_code=403, content={
            "ok": False,
            "error": "forbidden",
        })

    if not requested.exists() or not requested.is_file():
        return JSONResponse(status_code=404, content={
            "ok": False,
            "error": "file_not_found",
            "path": file_path,
        })

    return FileResponse(str(requested))
