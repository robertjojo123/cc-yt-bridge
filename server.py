import os
import re
import time
import hashlib
from urllib.parse import urlparse, parse_qs

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
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


# -----------------------------
# Basic helpers
# -----------------------------

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

    # Raw video IDs are usually 11 chars. Keep this permissive enough.
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
    """
    Returns one compact result object for a direct video ID.
    Falls back to ID-only if the API key is missing.
    """
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
# Endpoints
# -----------------------------

@app.get("/")
def root():
    return {
        "ok": True,
        "name": "CC Tweaked YouTube Bridge",
        "try": [
            "/ping",
            "/search?q=shrek trailer",
            "/search?q=https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "/prepare?id=dQw4w9WgXcQ",
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
    """
    If q is a YouTube URL or raw video ID:
      return that one specific video as result #1.

    Otherwise:
      do a normal YouTube search.

    Response is intentionally small for CC:Tweaked.
    """
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

    # Optional duration fill-in, still compact.
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
    Job placeholder.

    This now returns a stable fake job id, so the CC side can move forward.
    Later this endpoint will launch:
      video fetch/source validation
      10 fps frame extraction
      328x243 2x3-subpixel encoding
      DFPWM v39 audio conversion
      chunk creation
    """
    video_id = extract_youtube_id(id) or id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return JSONResponse(status_code=400, content={
            "ok": False,
            "error": "bad_video_id",
            "id": id,
        })

    seed = f"{video_id}-{int(time.time())}"
    job = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    return {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title[:80],
        "status": "queued_placeholder",
        "message": "Selection works. Next step is adding real frame/audio processing.",
        "pipeline": {
            "fps": 10,
            "monitor_cells": [164, 81],
            "subpixels": [328, 243],
            "chunk_seconds": 10,
        },
    }


@app.get("/status")
def status(job: str):
    return {
        "ok": True,
        "job": job,
        "status": "placeholder",
        "ready": False,
        "message": "Real job processing not implemented yet.",
    }
