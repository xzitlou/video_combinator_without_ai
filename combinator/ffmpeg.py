"""Thin wrappers around the ffmpeg/ffprobe CLIs. Everything works on local paths."""

import json
import subprocess
from pathlib import Path

from django.conf import settings

WIDTH, HEIGHT, FPS = 1080, 1920, 30
AUDIO_RATE = 48000
# Fixed so every normalized clip shares the same timebase and concat can stream-copy.
VIDEO_TIMESCALE = 15360


class FFmpegError(Exception):
    pass


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip()[-2000:])
    return result.stdout


def probe(path):
    """Return (duration_seconds, has_audio)."""
    out = _run([
        settings.FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    streams = [s.get("codec_type") for s in data.get("streams", [])]
    if "video" not in streams:
        raise FFmpegError("El archivo no contiene video.")
    duration = float(data.get("format", {}).get("duration") or 0)
    return duration, "audio" in streams


def normalize_command(src, dst, has_audio):
    """Re-encode to the canonical format: 1080x1920@30, H.264 yuv420p, AAC 48 kHz stereo.

    Clips without audio get a silent track, otherwise concat would drop/desync audio.
    """
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}"
    )
    cmd = [settings.FFMPEG_BIN, "-y", "-v", "error", "-i", str(src)]
    if has_audio:
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        cmd += [
            "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}",
            "-map", "0:v:0", "-map", "1:a:0",
        ]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-video_track_timescale", str(VIDEO_TIMESCALE),
        "-shortest", "-movflags", "+faststart",
        str(dst),
    ]
    return cmd


def normalize(src, dst):
    _, has_audio = probe(src)
    _run(normalize_command(src, dst, has_audio))
    duration, _ = probe(dst)
    return duration


def concat(paths, dst, workdir):
    """Stream-copy already-normalized clips into one MP4 (no re-encode)."""
    list_file = Path(workdir) / "concat.txt"
    # Callers pass paths inside workdir with safe names, so no quote escaping is needed.
    list_file.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in paths))
    _run([
        settings.FFMPEG_BIN, "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart",
        str(dst),
    ])
