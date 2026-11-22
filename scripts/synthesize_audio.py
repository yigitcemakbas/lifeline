"""Generate the intercepted audio for every voice session in the scenarios.

Each call or TETRA transmission in `scenarios/*.json` has a `script` (what the caller says)
and an `audio` path. This renders the script with macOS text-to-speech, then shapes it like
the channel it arrives on: narrowband telephone audio (300-3400 Hz, 8 kHz) for calls, and a
narrower, compressed band with a light noise floor for TETRA radio. Output is Opus in Ogg,
which Whisper accepts directly. The engine never reads `script`; it only hears the audio.

Requires macOS (`say`) and ffmpeg. Usage: make audio   (or: python scripts/synthesize_audio.py)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scenarios"
CALLER_VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi", "Kathy", "Fred"]
RADIO_VOICES = ["Daniel", "Fred", "Rishi"]
PHONE = "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=3,aresample=8000"
RADIO = "highpass=f=400,lowpass=f=2800,acompressor=threshold=0.05:ratio=6,volume=1.4,aresample=8000"


def pick(voices: list[str], key: str) -> str:
    return voices[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(voices)]


def render(script: str, voice: str, shape: str, radio: bool, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.aiff"
        subprocess.run(["say", "-v", voice, "-r", "175", "-o", str(raw), script], check=True)
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw)]
        if radio:  # light background hiss mixed under the voice
            cmd += ["-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.015:sample_rate=22050"]
            cmd += [
                "-filter_complex",
                f"[0:a]{shape}[v];[1:a]aresample=8000[n];"
                "[v][n]amix=inputs=2:duration=first:weights=1 0.6",
            ]
        else:
            cmd += ["-af", shape]
        cmd += ["-ac", "1", "-c:a", "libopus", "-b:a", "16k", str(out)]
        subprocess.run(cmd, check=True)


def main(selected: list[str]) -> int:
    for path in sorted(ROOT.glob("*.json")):
        scenario = json.loads(path.read_text())
        if selected and scenario["meta"]["id"] not in selected:
            continue
        for ev in scenario["events"]:
            p = ev["payload"]
            if not p.get("audio"):
                continue
            radio = ev["channel"] == "tetra"
            voice = pick(RADIO_VOICES if radio else CALLER_VOICES, str(p["source_id"]))
            out = ROOT / p["audio"]
            render(str(p["script"]), voice, RADIO if radio else PHONE, radio, out)
            print(f"{out.relative_to(ROOT)}  ({voice}, {out.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
