# CC Tweaked YouTube Bridge Starter

Tiny public bridge API for CC:Tweaked.

## Local test

```bat
py -m pip install -r requirements.txt
py -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000/ping
http://127.0.0.1:8000/search?q=shrek trailer
```

## Render deploy settings

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Environment variable:

```text
YOUTUBE_API_KEY=your_key_here
```

Without the key, `/search` returns a demo result so you can test CC HTTP first.

## CC test program

Upload `cc_search_test.lua` to a CC computer, set `BASE` to your Render URL, then run it.
