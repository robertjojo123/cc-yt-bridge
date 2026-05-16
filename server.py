import os
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(title="CC Tweaked YouTube Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")


@app.get("/")
def root():
    return {
        "ok": True,
        "name": "CC Tweaked YouTube Bridge",
        "try": ["/ping", "/search?q=shrek trailer"],
    }


@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "pong"


@app.get("/search")
def search(q: str = Query(..., min_length=1), max_results: int = 5):
    """
    Tiny JSON response meant for CC:Tweaked.

    Keep your YouTube API key on the server, never inside the CC computer.
    """
    max_results = max(1, min(int(max_results), 10))

    if not YOUTUBE_API_KEY:
        # Safe fake response so you can test CC HTTP before configuring an API key.
        return {
            "ok": True,
            "mode": "demo_no_api_key",
            "query": q,
            "results": [
                {
                    "id": "dQw4w9WgXcQ",
                    "title": f"Demo result for: {q}",
                    "channel": "Demo Channel",
                    "description": "Set YOUTUBE_API_KEY on your host to get real YouTube results.",
                    "thumbnail": "",
                }
            ],
        }

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "type": "video",
        "q": q,
        "maxResults": max_results,
        "key": YOUTUBE_API_KEY,
        "safeSearch": "none",
    }

    r = requests.get(url, params=params, timeout=12)
    if not r.ok:
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "youtube_api_failed",
                "status": r.status_code,
                "body": r.text[:500],
            },
        )

    data = r.json()
    results = []

    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        snip = item.get("snippet", {})
        thumbs = snip.get("thumbnails", {})
        thumb = (
            thumbs.get("default", {}).get("url")
            or thumbs.get("medium", {}).get("url")
            or ""
        )

        if vid:
            results.append({
                "id": vid,
                "title": snip.get("title", ""),
                "channel": snip.get("channelTitle", ""),
                "description": snip.get("description", ""),
                "thumbnail": thumb,
            })

    return {
        "ok": True,
        "query": q,
        "results": results,
    }


@app.get("/prepare")
def prepare(id: str):
    """
    Placeholder for the next phase.

    Later this endpoint will:
      1. fetch or accept an allowed video source
      2. extract frames
      3. run the 328x243 encoder
      4. run your v39 DFPWM tape converter
      5. produce chunk files
    """
    return {
        "ok": True,
        "video_id": id,
        "status": "not_implemented_yet",
        "next": "Add frame/audio processing job system here.",
    }
