# VideoDownloader API

A small Flask API for previewing and downloading videos with `yt-dlp`.

## Features

- Preview video details before downloading
- Queue downloads and track job status
- Download finished files through the API
- Auto-delete old files after a configurable TTL
- Optional API token protection

## Requirements

- Python 3.10+
- `pip`

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create a local environment file from `.env.example`, or export the variables in your shell.

## Environment variables

- `API_TOKEN`: optional token used for `/api/*` endpoints
- `ALLOWED_ORIGINS`: optional CORS origin value such as `*` or your frontend URL
- `PORT`: app port, default `5000`
- `MAX_CONCURRENT`: max concurrent download workers, default `2`
- `MAX_QUEUE`: max queued jobs, default `50`
- `FILE_TTL_SECONDS`: how long completed/error jobs and files are kept, default `86400`

## Run locally

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

## API routes

- `GET /`
- `POST /api/preview`
- `POST /api/download`
- `GET /api/status/<job_id>`
- `GET /download/<job_id>`

## Deploy

This repo includes a `Procfile` and `requirements.txt`, so it is ready for platforms that run Gunicorn, including AWS setups.

Gunicorn example:

```bash
gunicorn --bind 0.0.0.0:5000 app:app
```

## Notes

- Some downloads may require `ffmpeg`, depending on the source and format handling.
- Be mindful of platform terms of service and copyright rules before using this publicly.
