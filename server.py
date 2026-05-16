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
from PIL import Image
from io import BytesIO


app = FastAPI(title="CC Tweaked YouTube Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

DATA_ROOT = Path(os.environ.get("CCVID_DATA_ROOT", "/tmp/ccvid"))
CURRENT_DIR = DATA_ROOT / "current"
CURRENT_VIDEO_DIR = CURRENT_DIR / "video"

HEX = "0123456789abcdef"

CC_COLOR_CONSTS = [
    "white", "orange", "magenta", "lightBlue",
    "yellow", "lime", "pink", "gray",
    "lightGray", "cyan", "purple", "blue",
    "brown", "green", "red", "black",
]


# -----------------------------
# Basic helpers
# -----------------------------

def base_url() -> str:
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
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
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
    thumbs = snip.get("thumbnails", {})
    thumb = (
        thumbs.get("maxres", {}).get("url")
        or thumbs.get("standard", {}).get("url")
        or thumbs.get("high", {}).get("url")
        or thumbs.get("medium", {}).get("url")
        or thumbs.get("default", {}).get("url")
        or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    )

    return {
        "id": video_id,
        "title": compact_title(snip.get("title", "")),
        "channel": compact_title(snip.get("channelTitle", ""), 40),
        "duration": details.get("duration", ""),
        "thumbnail": thumb,
        "direct": True,
    }, None


# -----------------------------
# Image -> CCV encoder
# -----------------------------

def resize_cover(img: Image.Image, out_w: int, out_h: int) -> Image.Image:
    in_w, in_h = img.size
    scale = max(out_w / in_w, out_h / in_h)
    new_w = max(1, round(in_w * scale))
    new_h = max(1, round(in_h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - out_w) // 2
    top = (new_h - out_h) // 2
    return img.crop((left, top, left + out_w, top + out_h))


def quantize_16(img: Image.Image):
    pal_img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=16)
    raw_palette = pal_img.getpalette()[:16 * 3]

    palette = []
    for i in range(16):
        j = i * 3
        if j + 2 < len(raw_palette):
            palette.append((int(raw_palette[j]), int(raw_palette[j + 1]), int(raw_palette[j + 2])))
        else:
            palette.append((0, 0, 0))

    indexed = []
    px = list(pal_img.getdata())
    w, h = pal_img.size
    for y in range(h):
        row = []
        for x in range(w):
            v = px[y * w + x]
            row.append(max(0, min(15, int(v))))
        indexed.append(row)

    return indexed, palette


def rgb_distance2(a, b):
    return (
        (a[0] - b[0]) * (a[0] - b[0])
        + (a[1] - b[1]) * (a[1] - b[1])
        + (a[2] - b[2]) * (a[2] - b[2])
    )


def enforce_two_colors_per_cell(img: Image.Image, palette, mon_w=164, mon_h=81):
    """
    Best-pair enforce:
    for each 2x3 cell, try every pair of palette colors and assign each of the 6
    subpixels to the closer of the two.
    """
    sub_w = mon_w * 2
    sub_h = mon_h * 3
    rgb = list(img.getdata())

    out = [[0 for _ in range(sub_w)] for _ in range(sub_h)]
    pairs = [(a, b) for a in range(16) for b in range(a, 16)]

    for cy in range(mon_h):
        for cx in range(mon_w):
            coords = []
            colors_rgb = []
            for dy in range(3):
                for dx in range(2):
                    x = cx * 2 + dx
                    y = cy * 3 + dy
                    coords.append((x, y))
                    colors_rgb.append(rgb[y * sub_w + x])

            best_err = None
            best_assign = None
            best_pair = (0, 0)

            for a, b in pairs:
                ca = palette[a]
                cb = palette[b]
                err = 0
                assign = []
                for col in colors_rgb:
                    da = rgb_distance2(col, ca)
                    db = rgb_distance2(col, cb)
                    if db < da:
                        assign.append(b)
                        err += db
                    else:
                        assign.append(a)
                        err += da

                if best_err is None or err < best_err:
                    best_err = err
                    best_assign = assign
                    best_pair = (a, b)

            for i, (x, y) in enumerate(coords):
                out[y][x] = best_assign[i]

    return out


def sixel_char(mask: int):
    if mask > 31:
        return 0x80 + (63 - mask), True
    return 0x80 + mask, False


def pack_rows(indexed, mon_w=164, mon_h=81):
    rows = []
    bit_values = [1, 2, 4, 8, 16, 32]

    for cy in range(mon_h):
        text_bytes = []
        fg_chars = []
        bg_chars = []

        for cx in range(mon_w):
            pts = [
                indexed[cy * 3 + 0][cx * 2 + 0],
                indexed[cy * 3 + 0][cx * 2 + 1],
                indexed[cy * 3 + 1][cx * 2 + 0],
                indexed[cy * 3 + 1][cx * 2 + 1],
                indexed[cy * 3 + 2][cx * 2 + 0],
                indexed[cy * 3 + 2][cx * 2 + 1],
            ]

            counts = {}
            for p in pts:
                counts[p] = counts.get(p, 0) + 1
            ordered = sorted(counts.keys(), key=lambda k: -counts[k])

            bg = int(ordered[0])
            fg = bg
            if len(ordered) > 1:
                fg = int(ordered[1])

            if fg == bg:
                text_bytes.append(ord(" "))
                fg_chars.append(HEX[bg])
                bg_chars.append(HEX[bg])
                continue

            mask = 0
            for i, p in enumerate(pts):
                if p == fg:
                    mask += bit_values[i]

            ch, inverted = sixel_char(mask)
            text_bytes.append(ch)

            if inverted:
                fg_chars.append(HEX[bg])
                bg_chars.append(HEX[fg])
            else:
                fg_chars.append(HEX[fg])
                bg_chars.append(HEX[bg])

        rows.append((text_bytes, "".join(fg_chars), "".join(bg_chars)))

    return rows


def bytes_to_hex(byte_values):
    return "".join(f"{b:02x}" for b in byte_values)


def download_thumbnail(video_id: str, thumb_url: str = "") -> Image.Image:
    urls = []
    if thumb_url:
        urls.append(thumb_url)

    # fallback common YouTube thumbnail URLs
    urls.extend([
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
    ])

    last_err = None
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            if r.ok and len(r.content) > 1000:
                return Image.open(BytesIO(r.content)).convert("RGB")
            last_err = f"{url}: HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{url}: {e}"

    raise RuntimeError("Could not download thumbnail: " + str(last_err))


def make_thumbnail_frame_chunk(job: str, video_id: str, title: str, thumb_url: str = "") -> str:
    w, h = 164, 81
    sub_w, sub_h = w * 2, h * 3

    img = download_thumbnail(video_id, thumb_url)
    img = resize_cover(img, sub_w, sub_h)

    # Save debug preview locally so you can open it from the browser.
    try:
        img.save(CURRENT_DIR / "thumbnail_source_328x243.jpg")
    except Exception:
        pass

    _initial_indexed, palette = quantize_16(img)
    indexed = enforce_two_colors_per_cell(img, palette, w, h)
    rows = pack_rows(indexed, w, h)

    lines = []
    lines.append("CCV1")
    lines.append(f"job={job}")
    lines.append(f"video_id={video_id}")
    lines.append(f"title={title.replace(chr(10), ' ')[:120]}")
    lines.append("source=thumbnail")
    lines.append("fps=10")
    lines.append(f"w={w}")
    lines.append(f"h={h}")
    lines.append(f"subpixel_w={sub_w}")
    lines.append(f"subpixel_h={sub_h}")
    lines.append("frames=1")
    lines.append("FRAME 1")
    lines.append("PALETTE")
    for i, (r, g, b) in enumerate(palette):
        lines.append(f"P {HEX[i]} {r:02x}{g:02x}{b:02x}")
    lines.append("ROWS")
    for y, (text_bytes, fg, bg) in enumerate(rows, start=1):
        lines.append(f"R {y} {bytes_to_hex(text_bytes)} {fg} {bg}")
    lines.append("END_FRAME")
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
        "chunk_mode": "youtube_thumbnail_one_frame",
        "try": [
            "/ping",
            "/search?q=shrek trailer",
            "/prepare?id=dQw4w9WgXcQ",
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
                    "thumbnail": f"https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
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
        thumbs = snip.get("thumbnails", {})
        thumb = (
            thumbs.get("high", {}).get("url")
            or thumbs.get("medium", {}).get("url")
            or thumbs.get("default", {}).get("url")
            or f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
        )
        if vid:
            video_ids.append(vid)
            results.append({
                "id": vid,
                "title": compact_title(snip.get("title", "")),
                "channel": compact_title(snip.get("channelTitle", ""), 40),
                "duration": "",
                "thumbnail": thumb,
                "direct": False,
            })

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
def prepare(id: str, title: str = "", thumbnail: str = ""):
    video_id = extract_youtube_id(id) or id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return JSONResponse(status_code=400, content={
            "ok": False,
            "error": "bad_video_id",
            "id": id,
        })

    # Get better metadata/thumbnail if not provided.
    if not title or not thumbnail:
        details, err = get_video_details(video_id)
        if details:
            title = title or details.get("title", "")
            thumbnail = thumbnail or details.get("thumbnail", "")

    title = compact_title(title, 100)
    seed = f"{video_id}-{int(time.time())}"
    job = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    wipe_current()

    chunk_name = "chunk_0001.ccv"
    chunk_rel = f"current/video/{chunk_name}"
    chunk_path = CURRENT_VIDEO_DIR / chunk_name

    try:
        ccv_chunk = make_thumbnail_frame_chunk(job, video_id, title, thumbnail)
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "ok": False,
            "error": "thumbnail_encode_failed",
            "message": str(e),
        })

    chunk_path.write_text(ccv_chunk, encoding="utf-8")

    now = int(time.time())

    manifest = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "thumbnail_frame_ready",
        "ready": True,
        "created_at": now,
        "storage": "render_temp_local",
        "source": "youtube_thumbnail",
        "fps": 10,
        "w": 164,
        "h": 81,
        "subpixel_w": 328,
        "subpixel_h": 243,
        "chunk_seconds": 0.1,
        "chunk_count": 1,
        "audio_ready": False,
        "audio_url": "",
        "thumbnail_url": thumbnail,
        "debug_preview_url": f"{base_url()}/files/current/thumbnail_source_328x243.jpg",
        "chunks": [
            {
                "part": 1,
                "frames": 1,
                "url": f"{base_url()}/files/{chunk_rel}",
                "path": chunk_rel,
            }
        ],
        "message": "Thumbnail image was encoded into one real CCV frame.",
    }

    job_info = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "thumbnail_frame_ready",
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
