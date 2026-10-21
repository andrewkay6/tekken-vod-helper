"""Streaming and vision primitives for VOD boundary detection."""
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ffmpeg_tools import FfmpegError, probe_duration, require_tool

ALLOWED_STATES = frozenset([
    "stage_select", "character_select", "controller_prompt", "controller_assignment", "vs_screen",
    "stage_intro", "gameplay", "post_match_menu", "controller_disconnected",
    "unknown",
])
_FMT = "bestvideo[height<=360][vcodec^=avc1][protocol=m3u8_native]/bestvideo[height<=360][vcodec^=avc1]/bestvideo[height<=360][ext=mp4]/worstvideo"
_POLICY = Path(__file__).resolve().parents[1] / "docs" / "cutting-policy.md"


def _validate_duration(d: float) -> None:
    if not math.isfinite(d) or d <= 0:
        raise ValueError("Invalid duration: {!r}".format(d))


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://[^\s\"']+", "[url]", text or "")


def _parse_pts(stderr: str) -> List[float]:
    pts = []
    for line in stderr.splitlines():
        if "pts_time" not in line:
            continue
        m = re.search(r"pts_time:([\-\d.eE+]+)", line)
        if m:
            try:
                pts.append(float(m.group(1)))
            except ValueError:
                pass
    return pts


def _write_atomic(path: Path, content: str) -> None:
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.rename(tmp, str(path))


def _strip_b64(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_b64(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_b64(v) for v in obj]
    if isinstance(obj, str) and "base64," in obj and len(obj) > 200:
        return "[base64_omitted]"
    return obj


def resolve_source(source: str, yt_dlp: str = "", ffprobe_path: str = "") -> Dict[str, Any]:
    source = (source or "").strip()
    if not source:
        raise ValueError("source is empty")
    if source.startswith(("http://", "https://")):
        return _resolve_remote(source, yt_dlp)
    path = Path(source)
    if not path.is_file():
        raise ValueError("Local file not found")
    duration = probe_duration(str(path), ffprobe_path)
    _validate_duration(duration)
    return {"uri": str(path), "duration": duration, "source": str(path), "http_headers": {}}


def _resolve_remote(url: str, yt_dlp: str) -> Dict[str, Any]:
    yt = (yt_dlp or "").strip().strip('"')
    if yt:
        if not Path(yt).exists():
            raise ValueError("yt-dlp path not found")
    else:
        yt = shutil.which("yt-dlp")
        if not yt:
            raise ValueError("yt-dlp not found; provide explicit path")
    cmd1 = [yt, "--skip-download", "--no-playlist", "--no-cookies", "--no-warnings",
            "--socket-timeout", "15", "--retries", "1", "-f", _FMT,
            "--dump-single-json", url]
    if shutil.which("node"):
        cmd1[1:1] = ["--js-runtimes", "node"]
    try:
        r = subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("YouTube stream resolution timed out") from exc
    if r.returncode != 0:
        raise ValueError(_strip_urls((r.stderr or "yt-dlp metadata failed").strip()))
    meta = json.loads(r.stdout or "{}")
    duration = float(meta.get("duration") or 0.0)
    _validate_duration(duration)
    uri = meta.get("url")
    if not isinstance(uri, str) or not uri.startswith(("http://", "https://")):
        raise ValueError("yt-dlp returned no stream URL")
    vid = meta.get("id", "")
    src = {
        "uri": uri,
        "duration": duration,
        "source": "yt:{}".format(vid) if vid else url,
        "http_headers": meta.get("http_headers", {}),
        "format_id": meta.get("format_id"),
        "protocol": meta.get("protocol"),
        "vcodec": meta.get("vcodec"),
        "height": meta.get("height"),
    }
    return src


def sample_frames(
    source_dict: Dict[str, Any],
    output_dir: str,
    start: float = 0,
    end: Optional[float] = None,
    interval: float = 5,
    width: int = 640,
    max_frames: int = 2000,
    ffmpeg_path: str = "",
) -> List[Dict[str, Any]]:
    uri = source_dict["uri"]
    duration = source_dict["duration"]
    source_id = source_dict.get("source", uri)
    start = max(float(start), 0.0)
    if end is None:
        end = duration
    end = min(float(end), duration)
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError("Invalid time range")
    if interval <= 0:
        raise ValueError("interval must be positive")
    span = end - start
    expected = int(math.ceil(span / interval))
    if expected > max_frames:
        raise ValueError("Expected ~{} frames exceeds max_frames={}".format(expected, max_frames))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.glob("frame-*.jpg")):
        raise ValueError("Existing frame files in output directory")
    ffmpeg = require_tool(ffmpeg_path, "ffmpeg")
    stage = Path(tempfile.mkdtemp(prefix=".stage_", dir=str(out_dir)))
    try:
        sel = "isnan(prev_selected_t)+gte(t-prev_selected_t\\,{:.4f})".format(interval)
        # Trim before showinfo: output -t can discard a frame already logged by
        # the filter graph, especially with fractional-rate sources.
        vf = "select='{}',trim=duration={:.6f}:end_frame={},showinfo,scale={}:-2".format(sel, span, expected, width)
        input_options = ["-rw_timeout", "20000000"]
        headers = source_dict.get("http_headers", {})
        if headers:
            input_options += ["-headers", "".join("{}: {}\r\n".format(k, v) for k, v in headers.items())]
        seek_options = ["-ss", "{:.3f}".format(start)] if start > 0 else []
        cmd = [ffmpeg, "-y", "-v", "info"] + input_options + seek_options + [
               "-i", uri, "-t", "{:.3f}".format(span), "-vf", vf,
               "-frames:v", str(expected), "-vsync", "vfr", "-q:v", "2", "-f", "image2",
               str(stage / "frame-%04d.jpg")]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=min(900, max(90, span * 2)))
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Frame extraction timed out") from exc
        if r.returncode != 0:
            raise ValueError(_strip_urls(r.stderr or "ffmpeg failed"))
        pts_list = _parse_pts(r.stderr or "")
        produced = sorted(stage.glob("frame-*.jpg"))
        if not produced:
            raise ValueError("No frames extracted")
        # showinfo can log a final decoded frame that output -t omits. Only
        # discard a trailing timestamp demonstrably outside the requested span;
        # unexplained mismatches remain errors.
        while len(pts_list) > len(produced) and pts_list[-1] >= span - 0.000001:
            pts_list.pop()
        if len(pts_list) != len(produced):
            raise ValueError("Frame/pts mismatch: {} files, {} pts".format(len(produced), len(pts_list)))
        frames = []
        for fpath, pts in zip(produced, pts_list):
            dest = out_dir / fpath.name
            shutil.move(str(fpath), str(dest))
            frames.append({"id": len(frames) + 1, "timestamp": round(pts + start, 6), "path": str(dest)})
        manifest = {"source": source_id, "start": start, "end": end,
                    "interval": interval, "frames": frames}
        _write_atomic(out_dir / "sample-manifest.json", json.dumps(manifest, indent=2))
        return frames
    finally:
        if stage.exists():
            shutil.rmtree(str(stage), ignore_errors=True)


class VisionClient:
    def __init__(self, endpoint: str = "http://127.0.0.1:8010", model: str = "qwen3.8-27b",
                 max_requests: int = 300, timeout: int = 180, output_dir: Optional[str] = None,
                 policy_path: Optional[str] = None,
                 reference_paths: Optional[List[str]] = None,
                 crop_regions: bool = False,
                 enable_thinking: bool = True):
        self.endpoint = endpoint.rstrip("/")
        self.crop_regions = crop_regions
        self.model = model
        self.max_requests = max_requests
        self.timeout = timeout
        self.output_dir = Path(output_dir) if output_dir else None
        self.policy_path = Path(policy_path) if policy_path else _POLICY
        self.reference_paths = list(reference_paths) if reference_paths else []
        self.enable_thinking = enable_thinking
        self.calls = 0
        self.usage: List[Dict[str, Any]] = []
        
        # Snapshot policy ONCE
        self._policy_snapshot = self.policy_path.read_text(encoding="utf-8") if self.policy_path.exists() else ""
        self._policy_hash = hashlib.sha256(self._policy_snapshot.encode("utf-8")).hexdigest()[:16]
        
        # Reference label map
        self._ref_labels = {
            "stage_select_ref.png": "stage_select",
            "character_select_ref.png": "character_select",
            "controller_overlay_ref.png": "controller_prompt",
            "vs_screen_ref.png": "vs_screen",
            "gameplay_ref.png": "gameplay",
        }
        self._processed_refs: List[Dict[str, str]] = []
        for ref_path in self.reference_paths:
            p = Path(ref_path)
            if p.exists():
                label = self._ref_labels.get(p.stem + ".png", "appearance_reference")
                self._processed_refs.append({"path": str(p), "label": label})

    def classify(self, frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not frames:
            return []
        if self.calls >= self.max_requests:
            raise ValueError("Budget exhausted: {}/{}".format(self.calls, self.max_requests))
        self.calls += 1
        
        content = self._build_content(self._policy_snapshot, frames, crop_regions=self.crop_regions)
        # Include the actual instructions and references, not just field names.
        fingerprint = self._prompt_fingerprint()

        body = {"model": self.model, "max_tokens": 6000, "reasoning_effort": "low",
                "messages": [{"role": "user", "content": content}]}
        if not self.enable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        import urllib.request
        req = urllib.request.Request(
            self.endpoint + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + os.environ.get("QWEN_API_KEY", "")},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as e:
            raise ValueError("API request failed: {}".format(_strip_urls(str(e))))
        try:
            api_resp = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("API returned invalid JSON")
        try:
            text = api_resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ValueError("Malformed API response")
        try:
            results = self._parse_output(text, frames)
        except ValueError:
            if self.output_dir:
                self._save_provenance(fingerprint, body, api_resp, [], frames)
            raise
        if self.output_dir:
            self._save_provenance(fingerprint, body, api_resp, results, frames)
        self.usage.append({"call": self.calls, "frame_ids": [f["id"] for f in frames],
                           "policy_hash": self._policy_hash, "fingerprint": fingerprint, "tokens": api_resp.get("usage", {})})
        return results

    def _prompt_fingerprint(self) -> str:
        template = {"model": self.model, "content": self._build_content(self._policy_snapshot, [], crop_regions=self.crop_regions), "enable_thinking": self.enable_thinking}
        return hashlib.sha256(json.dumps(template, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def _build_content(self, policy_text: str, frames: List[Dict[str, Any]], crop_regions: Optional[bool] = None) -> List[Dict[str, Any]]:
        if crop_regions is None:
            crop_regions = self.crop_regions
            
        parts = []
        if policy_text:
            parts.append("## Cutting Policy\n" + policy_text)
        
        ref_parts = []
        for i, ref in enumerate(self._processed_refs, 1):
            ref_parts.append("Reference #{} ({})".format(i, ref["label"]))
        ref_section = "\n".join(ref_parts) if ref_parts else ""
        
        region_instructions = ""
        if crop_regions:
            region_instructions = (
                "\n[REGION CROP INSTRUCTIONS]\n"
                "The following cropped regions are provided alongside the full frame:\n"
                "- 'broadcast-scoreboard' (top, y0..0.12): Use strictly for broadcast names/set score. Do NOT infer from in-game HUD.\n"
                "- 'controller-overlay' (center, x0.15..0.85, y0.28..0.72): Use for state priority. Actual 'Press [button]' controller overlay beats background ordinary rematch menu.\n"
                "- Distinguish OS 'Who's using this controller?' prompt as 'controller_assignment'.\n"
                "[END REGION CROP INSTRUCTIONS]\n"
            )
            
        content_text = (
            'Classify each frame. Return a JSON array, one object per frame id.\n'
            'Fields: id(int), state(str), confidence(float 0-1), player1(str), player2(str), '
            'character1(str), character2(str), score1(int|null), score2(int|null), evidence(str). '
            'score1/score2: TOP BROADCAST SET scoreboard ONLY. Do NOT infer from in-game round HUD. '
            'Missing fields -> null. Reject bool/negative/non-integer for scores. Blank names preferable to invented OCR.\n'
            'State precedence: Prioritize actual central Player 1/Player 2 Press [button] controller overlay '
            'even when rematch/character background exists behind. Do NOT promote ordinary side menu without overlay.\n'
            'Allowed states: ' + ", ".join(sorted(ALLOWED_STATES)) + "\n"
            + (ref_section + "\n" if ref_section else "") +
            region_instructions +
            "Do NOT include timestamps.\nFrames to classify:\n" +
            "\n".join("id={} t={}s".format(f["id"], f["timestamp"]) for f in frames)
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": content_text}]
        for ref in self._processed_refs:
            p = Path(ref["path"])
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            content.append({"type": "text", "text": "Ref [{}]".format(ref["label"])})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}})
            
        for f in frames:
            img = Path(f["path"]).read_bytes()
            b64 = base64.b64encode(img).decode("ascii")
            mime = "image/png" if f["path"].lower().endswith(".png") else "image/jpeg"
            content.append({"type": "text", "text": "Frame id={}: ".format(f["id"])})
            content.append({"type": "image_url",
                            "image_url": {"url": "data:{};base64,".format(mime) + b64}})
            
        if crop_regions:
            try:
                from PIL import Image
            except ImportError:
                raise RuntimeError("Pillow is required for crop_regions=True")
            
            for f in frames:
                try:
                    img = Image.open(f["path"])
                except Exception as e:
                    raise ValueError("Failed to open image {}: {}".format(f["path"], e))
                
                w, h = img.size
                if w < 100 or h < 100:
                    raise ValueError("Image too small for region crops: {}x{}".format(w, h))
                    
                crops = [
                    ("broadcast-scoreboard", 0, 0, w, int(h * 0.12)),
                    ("controller-overlay", int(w * 0.15), int(h * 0.28), int(w * 0.85), int(h * 0.72)),
                ]
                
                for label, x0, y0, x1, y1 in crops:
                    x1 = max(x1, x0 + 10)
                    y1 = max(y1, y0 + 10)
                    box = (x0, y0, x1, y1)
                    try:
                        crop_img = img.crop(box)
                    except Exception as e:
                        raise ValueError("Crop failed for {}: {}".format(label, e))
                    
                    max_dim = 1536
                    scale = max(1.0, min(3.0, max_dim / max(crop_img.width, crop_img.height)))
                    new_w = int(crop_img.width * scale)
                    new_h = int(crop_img.height * scale)
                    resample = getattr(Image, "Resampling", Image).LANCZOS
                    crop_img = crop_img.resize((new_w, new_h), resample)
                    
                    buf = io.BytesIO()
                    crop_img.convert("RGB").save(buf, format="JPEG", quality=95)
                    b64_crop = base64.b64encode(buf.getvalue()).decode("ascii")
                    
                    content.append({"type": "text", "text": "Region [{}]".format(label)})
                    content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64_crop}})
                    
        return content

    def _parse_output(self, text: str, frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                raise ValueError("Model output is not valid JSON")
            data = json.loads(m.group(0))
        if not isinstance(data, list):
            raise ValueError("Model output must be a JSON array")
        frame_ids = {f["id"] for f in frames}
        frame_map = {f["id"]: f for f in frames}
        if len(data) != len(frames):
            raise ValueError("Expected {} results, got {}".format(len(frames), len(data)))
        seen: set = set()
        results = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ValueError("Each result must be an object")
            fid = entry.get("id")
            if fid in seen:
                raise ValueError("Duplicate id: {}".format(fid))
            if fid not in frame_ids:
                raise ValueError("Unknown frame id: {}".format(fid))
            seen.add(fid)
            state = entry.get("state", "")
            if state not in ALLOWED_STATES:
                raise ValueError("Invalid state: {!r}".format(state))
            conf = entry.get("confidence")
            if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not math.isfinite(float(conf)):
                raise ValueError("Invalid confidence: {!r}".format(conf))
            conf = float(conf)
            if not 0.0 <= conf <= 1.0:
                raise ValueError("Confidence out of [0,1]: {}".format(conf))
            
            score1 = entry.get("score1")
            score2 = entry.get("score2")
            
            def _validate_score(val):
                if (isinstance(val, str) and 1 <= len(val) <= 2
                        and val.isascii() and val.isdecimal()
                        and (val == '0' or not val.startswith('0'))):
                    val = int(val)
                if val is None:
                    return None
                if isinstance(val, bool):
                    raise ValueError("Score must not be bool: {!r}".format(val))
                if not isinstance(val, int):
                    raise ValueError("Score must be int or null: {!r}".format(val))
                if val < 0:
                    raise ValueError("Score must be non-negative: {!r}".format(val))
                return val

            score1 = _validate_score(score1)
            score2 = _validate_score(score2)

            src = frame_map[fid]
            def _normalize_str(val):
                if val is None:
                    return ""
                if isinstance(val, (list, dict)):
                    raise ValueError("Metadata field must be string or null: {!r}".format(val))
                s = str(val).strip()
                if s.lower() in ("none", "null", "unknown"):
                    return ""
                return s
            results.append({
                "id": fid, "timestamp": src["timestamp"], "path": src["path"],
                "state": state, "confidence": conf,
                "player1": _normalize_str(entry.get("player1", "")), "player2": _normalize_str(entry.get("player2", "")),
                "character1": _normalize_str(entry.get("character1", "")), "character2": _normalize_str(entry.get("character2", "")),
                "score1": score1, "score2": score2,
                "evidence": str(entry.get("evidence", "")),
            })
        if seen != frame_ids:
            raise ValueError("Missing ids: {}".format(frame_ids - seen))
        return results

    def _save_provenance(self, fingerprint: str, body: Dict, api_resp: Dict, results: List, frames=None) -> None:
        if not self.output_dir:
            return
        bdir = self.output_dir / "batches"
        bdir.mkdir(parents=True, exist_ok=True)
        record = {"call": self.calls, "policy_hash": self._policy_hash, "fingerprint": fingerprint,
                  "model": self.model, "frame_ids": [r["id"] for r in (frames if frames is not None else results)],
                  "request": _strip_b64(body), "response": api_resp, "results": results}
        (bdir / "batch-{:04d}.json".format(self.calls)).write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8")
