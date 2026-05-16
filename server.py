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

# 16-color test palette. This will become per-frame dynamic palette later.
TEST_PALETTE = [
    (8, 10, 14),       # 0 dark bg
    (28, 32, 42),      # 1 dark blue gray
    (55, 62, 78),      # 2 gray blue
    (91, 101, 124),    # 3 soft gray
    (140, 151, 170),   # 4 light gray
    (220, 224, 232),   # 5 white text
    (40, 80, 180),     # 6 blue
    (60, 145, 230),    # 7 light blue
    (40, 170, 120),    # 8 green
    (120, 220, 130),   # 9 lime
    (230, 200, 70),    # a yellow
    (230, 145, 50),    # b orange
    (210, 60, 60),     # c red
    (180, 70, 210),    # d purple
    (30, 30, 30),      # e near black
    (0, 0, 0),         # f black
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
# 2x3 subpixel encoder
# -----------------------------

PIXEL_FONT = {
    "A":["01110","10001","10001","11111","10001","10001","10001"],
    "B":["11110","10001","10001","11110","10001","10001","11110"],
    "C":["01111","10000","10000","10000","10000","10000","01111"],
    "D":["11110","10001","10001","10001","10001","10001","11110"],
    "E":["11111","10000","10000","11110","10000","10000","11111"],
    "F":["11111","10000","10000","11110","10000","10000","10000"],
    "G":["01111","10000","10000","10011","10001","10001","01111"],
    "H":["10001","10001","10001","11111","10001","10001","10001"],
    "I":["11111","00100","00100","00100","00100","00100","11111"],
    "J":["00111","00010","00010","00010","10010","10010","01100"],
    "K":["10001","10010","10100","11000","10100","10010","10001"],
    "L":["10000","10000","10000","10000","10000","10000","11111"],
    "M":["10001","11011","10101","10101","10001","10001","10001"],
    "N":["10001","11001","10101","10011","10001","10001","10001"],
    "O":["01110","10001","10001","10001","10001","10001","01110"],
    "P":["11110","10001","10001","11110","10000","10000","10000"],
    "Q":["01110","10001","10001","10001","10101","10010","01101"],
    "R":["11110","10001","10001","11110","10100","10010","10001"],
    "S":["01111","10000","10000","01110","00001","00001","11110"],
    "T":["11111","00100","00100","00100","00100","00100","00100"],
    "U":["10001","10001","10001","10001","10001","10001","01110"],
    "V":["10001","10001","10001","10001","10001","01010","00100"],
    "W":["10001","10001","10001","10101","10101","10101","01010"],
    "X":["10001","10001","01010","00100","01010","10001","10001"],
    "Y":["10001","10001","01010","00100","00100","00100","00100"],
    "Z":["11111","00001","00010","00100","01000","10000","11111"],
    "0":["01110","10001","10011","10101","11001","10001","01110"],
    "1":["00100","01100","00100","00100","00100","00100","01110"],
    "2":["01110","10001","00001","00010","00100","01000","11111"],
    "3":["11110","00001","00001","01110","00001","00001","11110"],
    "4":["00010","00110","01010","10010","11111","00010","00010"],
    "5":["11111","10000","10000","11110","00001","00001","11110"],
    "6":["01110","10000","10000","11110","10001","10001","01110"],
    "7":["11111","00001","00010","00100","01000","01000","01000"],
    "8":["01110","10001","10001","01110","10001","10001","01110"],
    "9":["01110","10001","10001","01111","00001","00001","01110"],
    "-":["00000","00000","00000","11111","00000","00000","00000"],
    ":":["00000","00100","00100","00000","00100","00100","00000"],
    ".":["00000","00000","00000","00000","00000","00100","00100"],
    " ":["00000","00000","00000","00000","00000","00000","00000"],
}


def new_indexed_canvas(w: int, h: int, bg: int = 0):
    return [[bg for _ in range(w)] for _ in range(h)]


def set_px(canvas, x, y, c):
    h = len(canvas)
    w = len(canvas[0])
    if 0 <= x < w and 0 <= y < h:
        canvas[y][x] = c


def fill_rect(canvas, x, y, w, h, c):
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            set_px(canvas, xx, yy, c)


def draw_line(canvas, x0, y0, x1, y1, c):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        set_px(canvas, x, y, c)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def draw_text(canvas, x, y, text, color=5, scale=2):
    text = (text or "").upper()
    cursor = x
    for ch in text:
        glyph = PIXEL_FONT.get(ch, PIXEL_FONT[" "])
        for gy, row in enumerate(glyph):
            for gx, v in enumerate(row):
                if v == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            set_px(canvas, cursor + gx * scale + sx, y + gy * scale + sy, color)
        cursor += (5 * scale) + scale


def make_test_frame(job: str, video_id: str, title: str, w=164, h=81):
    """
    Creates a 328x243 indexed subpixel image.
    Every 2x3 cell uses max 2 colors by construction because each cell is filled
    with one bg color plus optional fg pixels.
    """
    sub_w = w * 2
    sub_h = h * 3
    c = new_indexed_canvas(sub_w, sub_h, 0)

    # Background blocky gradient by whole 2x3 cells.
    for cy in range(h):
        for cx in range(w):
            bg = 0
            if cy > h * 0.62:
                bg = 1
            if (cx // 8 + cy // 6) % 7 == 0:
                bg = 1
            if (cx // 13 + cy // 9) % 11 == 0:
                bg = 2
            fill_rect(c, cx * 2, cy * 3, 2, 3, bg)

    # Header and panels, aligned to cell grid mostly.
    fill_rect(c, 8, 9, sub_w - 16, 36, 1)
    fill_rect(c, 12, 13, sub_w - 24, 28, 2)
    fill_rect(c, 16, 17, sub_w - 32, 20, 6)

    draw_text(c, 24, 20, "CC VIDEO BRIDGE", 5, 2)

    fill_rect(c, 20, 60, sub_w - 40, 54, 1)
    fill_rect(c, 24, 64, sub_w - 48, 46, 2)
    draw_text(c, 32, 72, "REAL FRAME CHUNK", 9, 2)
    draw_text(c, 32, 94, "328X243 SUBPIXELS", 5, 1)

    fill_rect(c, 20, 128, sub_w - 40, 68, 1)
    fill_rect(c, 24, 132, sub_w - 48, 60, 2)
    draw_text(c, 34, 142, "VIDEO ID", 10, 1)
    draw_text(c, 34, 158, video_id[:11], 5, 2)

    safe_title = re.sub(r"[^A-Za-z0-9 .:-]", " ", title or "SELECTED VIDEO")
    safe_title = " ".join(safe_title.split())[:28]
    draw_text(c, 34, 202, safe_title, 5, 1)

    # Decorative diagonals.
    for i in range(0, sub_w, 18):
        draw_line(c, i, sub_h - 1, min(sub_w - 1, i + 45), sub_h - 36, 7)
    for i in range(0, sub_w, 35):
        draw_line(c, i, 50, min(sub_w - 1, i + 20), 88, 13)

    # Re-enforce two-color-per-cell. If any cell has >2 colors due to drawing overlap,
    # keep most common as bg and strongest non-bg as fg.
    for cy in range(h):
        for cx in range(w):
            pixels = []
            for yy in range(cy * 3, cy * 3 + 3):
                for xx in range(cx * 2, cx * 2 + 2):
                    pixels.append(c[yy][xx])
            unique = sorted(set(pixels))
            if len(unique) > 2:
                counts = {}
                for p in pixels:
                    counts[p] = counts.get(p, 0) + 1
                ordered = sorted(counts.keys(), key=lambda k: -counts[k])
                keep = set(ordered[:2])
                bg = ordered[0]
                fg = ordered[1]
                for yy in range(cy * 3, cy * 3 + 3):
                    for xx in range(cx * 2, cx * 2 + 2):
                        if c[yy][xx] not in keep:
                            c[yy][xx] = fg if c[yy][xx] != bg else bg

    return c


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


def make_real_frame_chunk(job: str, video_id: str, title: str) -> str:
    w, h = 164, 81
    indexed = make_test_frame(job, video_id, title, w, h)
    rows = pack_rows(indexed, w, h)

    lines = []
    lines.append("CCV1")
    lines.append(f"job={job}")
    lines.append(f"video_id={video_id}")
    lines.append(f"title={title.replace(chr(10), ' ')[:120]}")
    lines.append("fps=10")
    lines.append(f"w={w}")
    lines.append(f"h={h}")
    lines.append(f"subpixel_w={w * 2}")
    lines.append(f"subpixel_h={h * 3}")
    lines.append("frames=1")
    lines.append("FRAME 1")
    lines.append("PALETTE")
    for i, (r, g, b) in enumerate(TEST_PALETTE):
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
        "chunk_mode": "real_one_frame",
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

    real_chunk = make_real_frame_chunk(job, video_id, title)
    chunk_path.write_text(real_chunk, encoding="utf-8")

    now = int(time.time())

    manifest = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "one_frame_ready",
        "ready": True,
        "created_at": now,
        "storage": "render_temp_local",
        "fps": 10,
        "w": 164,
        "h": 81,
        "subpixel_w": 328,
        "subpixel_h": 243,
        "chunk_seconds": 0.1,
        "chunk_count": 1,
        "audio_ready": False,
        "audio_url": "",
        "chunks": [
            {
                "part": 1,
                "frames": 1,
                "url": f"{base_url()}/files/{chunk_rel}",
                "path": chunk_rel,
            }
        ],
        "message": "Real one-frame CCV chunk is ready.",
    }

    job_info = {
        "ok": True,
        "job": job,
        "video_id": video_id,
        "title": title,
        "status": "one_frame_ready",
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
