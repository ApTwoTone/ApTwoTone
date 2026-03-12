from __future__ import annotations
"""
AI Video Ad Production Pipeline — Zoar Bathroom Rentals
- FFmpeg-based video manipulation (Ken Burns, slideshows, text overlays, etc.)
- AI video prompt library for luxury restroom trailer marketing
- Batch ad generation from image × headline combinations
"""
import subprocess
import shlex
import shutil
import json
from pathlib import Path
from datetime import datetime

# ── AI Video Prompt Library ──────────────────────────────────────────────────

AI_VIDEO_PROMPTS = {
    "luxury_interior_walkthrough": {
        "prompt": (
            "Smooth steadicam walkthrough inside a luxury portable restroom trailer, "
            "camera glides past marble countertops with vessel sinks, brushed gold faucets, "
            "LED-backlit mirrors, individual private stalls with premium doors, fresh flower "
            "arrangements, soft warm ambient lighting, pristine white towels on display, "
            "high-end real estate video style, 4K photorealistic"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["kling", "sora", "hailuo"],
        "duration": "5-8 seconds",
        "camera": "slow dolly push-in",
    },
    "exterior_at_wedding": {
        "prompt": (
            "Luxury white restroom trailer parked at an elegant outdoor wedding venue, "
            "string lights bokeh in background, golden hour backlighting, guests in formal "
            "attire walking nearby, manicured lawn, floral arrangements, cinematic shallow "
            "depth of field, shot on 35mm lens"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["kling", "sora", "luma"],
        "duration": "5 seconds",
        "camera": "slow pan right to left",
    },
    "contrast_portapotty_to_luxury": {
        "prompt": (
            "Close-up of a blue plastic portable toilet door opening to reveal a dark, "
            "cramped interior, HARD CUT to luxury restroom trailer door opening to reveal "
            "marble countertops, LED lighting, elegant mirrors, and a fresh flower arrangement, "
            "dramatic lighting contrast"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["kling", "runway"],
        "duration": "6-8 seconds",
        "camera": "static then push-in",
    },
    "asmr_details": {
        "prompt": (
            "Extreme close-up ASMR-style shots inside luxury restroom: chrome faucet turning "
            "on with water flowing, soap dispenser pressing, towel being pulled from holder, "
            "mirror reflection showing elegant interior, all in slow motion with soft ambient lighting"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark, fast motion",
        "tools": ["kling", "hailuo"],
        "duration": "8-10 seconds",
        "camera": "macro lens, shallow DOF",
    },
    "golden_hour_exterior": {
        "prompt": (
            "Beautiful golden hour shot of a luxury portable restroom trailer at an outdoor "
            "event, warm backlighting, string lights above, guests in elegant attire nearby, "
            "manicured grass, cinematic color grading, shot on Arri Alexa"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["sora", "kling", "luma"],
        "duration": "5 seconds",
        "camera": "slow dolly",
    },
    "quinceanera_setup": {
        "prompt": (
            "Colorful quincea\u00f1era celebration setup with luxury restroom trailer decorated "
            "with pink and white flowers, teen girls in formal dresses walking past, festive "
            "atmosphere, warm lighting, confetti, cultural celebration feel"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["kling", "hailuo"],
        "duration": "5-6 seconds",
        "camera": "tracking shot",
    },
    "night_event_glow": {
        "prompt": (
            "Luxury restroom trailer at night event, warm interior light glowing through "
            "frosted windows, exterior pathway LED lights, guests silhouettes, string lights "
            "and lanterns, upscale evening atmosphere, cinematic night photography"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark, daytime",
        "tools": ["kling", "sora"],
        "duration": "5 seconds",
        "camera": "slow push-in",
    },
    "before_after_split": {
        "prompt": (
            "Split-screen comparison: left side shows standard porta potty at dusty outdoor "
            "event, right side shows luxury restroom trailer interior with marble, flowers, "
            "and LED lighting. Dramatic side-by-side reveal, clean dividing line"
        ),
        "negative": "blurry, distorted, cartoon, anime, text, watermark",
        "tools": ["kling", "runway"],
        "duration": "5-6 seconds",
        "camera": "static with zoom",
    },
}


# ── FFmpeg Video Generator ───────────────────────────────────────────────────

class VideoGenerator:
    """FFmpeg-based video ad production pipeline for Zoar Bathroom Rentals."""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir or Path.home() / "nexus" / "media" / "generated")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._check_deps()

    def _check_deps(self):
        """Check if ffmpeg and ffprobe are available on PATH."""
        self._has_ffmpeg = shutil.which("ffmpeg") is not None
        self._has_ffprobe = shutil.which("ffprobe") is not None
        if self._has_ffmpeg:
            print("[VideoGen] ffmpeg found")
        else:
            print("[VideoGen] WARNING: ffmpeg not found on PATH - video functions will fail")
        if self._has_ffprobe:
            print("[VideoGen] ffprobe found")
        else:
            print("[VideoGen] WARNING: ffprobe not found on PATH - info functions will fail")

    def _require_ffmpeg(self) -> dict | None:
        """Return error dict if ffmpeg is missing, else None."""
        if not self._has_ffmpeg:
            return {"ok": False, "error": "ffmpeg not found on PATH. Install with: brew install ffmpeg"}
        return None

    def _require_ffprobe(self) -> dict | None:
        """Return error dict if ffprobe is missing, else None."""
        if not self._has_ffprobe:
            return {"ok": False, "error": "ffprobe not found on PATH. Install with: brew install ffmpeg"}
        return None

    def _run(self, cmd: list[str], desc: str = "") -> dict:
        """Run a subprocess command and return result dict."""
        cmd_str = " ".join(cmd)
        print(f"[VideoGen] Running: {cmd_str[:200]}{'...' if len(cmd_str) > 200 else ''}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                err = result.stderr.strip()[-500:] if result.stderr else "Unknown error"
                print(f"[VideoGen] FAILED ({desc}): {err[:200]}")
                return {"ok": False, "error": err}
            print(f"[VideoGen] OK: {desc}")
            return {"ok": True}
        except subprocess.TimeoutExpired:
            print(f"[VideoGen] TIMEOUT ({desc})")
            return {"ok": False, "error": f"Command timed out after 300s: {desc}"}
        except FileNotFoundError as e:
            print(f"[VideoGen] NOT FOUND: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            print(f"[VideoGen] ERROR ({desc}): {e}")
            return {"ok": False, "error": str(e)}

    def _output_path(self, output_path: str | None, suffix: str) -> str:
        """Generate output path if not specified."""
        if output_path:
            return output_path
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(self.output_dir / f"{suffix}_{ts}.mp4")

    # ── Core FFmpeg Functions ────────────────────────────────────────────────

    def create_kenburns_video(self, image_path: str, output_path: str = None,
                              duration: int = 10, zoom_speed: float = 0.0015,
                              resolution: str = "1080x1920") -> dict:
        """Create Ken Burns zoom-in effect on still image (9:16 vertical for Reels/Stories).

        Args:
            image_path: Path to source image
            output_path: Destination video path (auto-generated if None)
            duration: Video duration in seconds
            zoom_speed: Zoom increment per frame (0.001 = subtle, 0.003 = dramatic)
            resolution: Output resolution (default 1080x1920 for 9:16 vertical)
        """
        err = self._require_ffmpeg()
        if err:
            return err

        if not Path(image_path).exists():
            return {"ok": False, "error": f"Image not found: {image_path}"}

        out = self._output_path(output_path, "kenburns")
        total_frames = duration * 30

        zoompan_filter = (
            f"zoompan=z='min(zoom+{zoom_speed},{1 + zoom_speed * total_frames * 0.8})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={resolution}:fps=30"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-vf", zoompan_filter,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out,
        ]

        result = self._run(cmd, f"Ken Burns {duration}s from {Path(image_path).name}")
        if result["ok"]:
            result["output_path"] = out
            result["details"] = f"Ken Burns {resolution} {duration}s zoom_speed={zoom_speed}"
        return result

    def add_text_overlay(self, input_path: str, output_path: str = None, text: str = "",
                         position: str = "bottom", fontsize: int = 64,
                         fontcolor: str = "white", bg_opacity: float = 0.5,
                         font: str = "Arial") -> dict:
        """Add text overlay to video with semi-transparent background.

        Args:
            input_path: Source video path
            output_path: Destination path (auto-generated if None)
            text: Text to overlay
            position: "top", "center", or "bottom"
            fontsize: Font size in pixels
            fontcolor: Font color name or hex
            bg_opacity: Background box opacity (0-1)
            font: Font family name
        """
        err = self._require_ffmpeg()
        if err:
            return err

        if not Path(input_path).exists():
            return {"ok": False, "error": f"Video not found: {input_path}"}

        out = self._output_path(output_path, "text_overlay")

        # Escape text for drawtext filter (colons, single quotes, backslashes)
        escaped_text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

        # Position mapping
        y_positions = {
            "top": "h*0.08",
            "center": "(h-text_h)/2",
            "bottom": "h*0.85-text_h",
        }
        y_expr = y_positions.get(position, y_positions["bottom"])

        drawtext = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={fontsize}:fontcolor={fontcolor}:"
            f"font='{font}':"
            f"x=(w-text_w)/2:y={y_expr}:"
            f"box=1:boxcolor=black@{bg_opacity}:boxborderw=20"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", drawtext,
            "-c:v", "libx264",
            "-preset", "medium",
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out,
        ]

        result = self._run(cmd, f"Text overlay: {text[:40]}")
        if result["ok"]:
            result["output_path"] = out
            result["details"] = f"Text overlay '{text[:60]}' at {position}"
        return result

    def create_slideshow(self, image_paths: list, output_path: str = None,
                         duration_per_image: int = 3, transition_duration: float = 1.0,
                         audio_path: str = None, resolution: str = "1080x1920") -> dict:
        """Create slideshow with crossfade transitions between images.

        Args:
            image_paths: List of image file paths
            output_path: Destination path
            duration_per_image: Seconds each image is shown
            transition_duration: Crossfade duration between images
            audio_path: Optional background audio
            resolution: Output resolution
        """
        err = self._require_ffmpeg()
        if err:
            return err

        if not image_paths or len(image_paths) < 2:
            return {"ok": False, "error": "Need at least 2 images for a slideshow"}

        for p in image_paths:
            if not Path(p).exists():
                return {"ok": False, "error": f"Image not found: {p}"}

        out = self._output_path(output_path, "slideshow")
        w, h = resolution.split("x")
        n = len(image_paths)

        # Build inputs
        inputs = []
        for p in image_paths:
            inputs.extend(["-loop", "1", "-t", str(duration_per_image), "-i", p])

        if audio_path and Path(audio_path).exists():
            inputs.extend(["-i", audio_path])

        # Build filter_complex: scale each input, then xfade between them
        filters = []
        for i in range(n):
            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[v{i}]"
            )

        # Chain xfade transitions
        prev = "v0"
        td = transition_duration
        for i in range(1, n):
            offset = i * duration_per_image - i * td
            if offset < 0:
                offset = 0.5
            out_label = f"xf{i}"
            filters.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={td}:offset={offset}[{out_label}]"
            )
            prev = out_label

        filter_complex = ";".join(filters)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{prev}]",
        ]

        if audio_path and Path(audio_path).exists():
            audio_idx = n
            cmd.extend(["-map", f"{audio_idx}:a", "-shortest"])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out,
        ])

        result = self._run(cmd, f"Slideshow {n} images, {duration_per_image}s each")
        if result["ok"]:
            result["output_path"] = out
            result["details"] = f"Slideshow: {n} images x {duration_per_image}s with {td}s crossfade"
        return result

    def optimize_for_reels(self, input_path: str, output_path: str = None) -> dict:
        """Optimize any video for Instagram Reels / Facebook Stories (9:16, 1080x1920).

        Scales, pads to 9:16, re-encodes with h264+aac, and adds faststart for streaming.
        """
        err = self._require_ffmpeg()
        if err:
            return err

        if not Path(input_path).exists():
            return {"ok": False, "error": f"Video not found: {input_path}"}

        out = self._output_path(output_path, "reels_optimized")

        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-t", "90",  # Reels max 90s
            out,
        ]

        result = self._run(cmd, f"Reels optimize: {Path(input_path).name}")
        if result["ok"]:
            result["output_path"] = out
            result["details"] = "Optimized for Reels: 1080x1920 h264+aac faststart (max 90s)"
        return result

    def add_music(self, video_path: str, music_path: str, output_path: str = None,
                  music_volume: float = 0.3) -> dict:
        """Mix background music into video at specified volume.

        Args:
            video_path: Source video
            music_path: Background music audio file
            output_path: Destination
            music_volume: Music volume (0.0-1.0, default 0.3 for background)
        """
        err = self._require_ffmpeg()
        if err:
            return err

        for p, name in [(video_path, "Video"), (music_path, "Music")]:
            if not Path(p).exists():
                return {"ok": False, "error": f"{name} not found: {p}"}

        out = self._output_path(output_path, "with_music")

        # Mix: keep original audio (if any) + music at reduced volume
        filter_complex = (
            f"[1:a]volume={music_volume}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        # First try with original audio mixing
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            out,
        ]

        result = self._run(cmd, "Add music (with original audio mix)")

        # If failed (maybe no original audio), try just adding music track
        if not result["ok"]:
            print("[VideoGen] Retrying without original audio mix...")
            cmd_simple = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", music_path,
                "-filter_complex", f"[1:a]volume={music_volume}[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                out,
            ]
            result = self._run(cmd_simple, "Add music (music only)")

        if result["ok"]:
            result["output_path"] = out
            result["details"] = f"Added music at volume {music_volume}"
        return result

    def crossfade_clips(self, clip_paths: list, output_path: str = None,
                        transition_duration: float = 1.0) -> dict:
        """Crossfade between multiple video clips.

        Args:
            clip_paths: List of video file paths
            output_path: Destination
            transition_duration: Crossfade duration between clips
        """
        err = self._require_ffmpeg()
        if err:
            return err

        if not clip_paths or len(clip_paths) < 2:
            return {"ok": False, "error": "Need at least 2 clips for crossfade"}

        for p in clip_paths:
            if not Path(p).exists():
                return {"ok": False, "error": f"Clip not found: {p}"}

        out = self._output_path(output_path, "crossfade")

        # Get durations for offset calculation
        durations = []
        for p in clip_paths:
            info = self.get_video_info(p)
            if not info.get("ok"):
                return {"ok": False, "error": f"Cannot read clip info: {p}"}
            durations.append(info.get("duration", 5.0))

        # Build inputs
        inputs = []
        for p in clip_paths:
            inputs.extend(["-i", p])

        # Build xfade filter chain
        n = len(clip_paths)
        filters = []
        prev = "0:v"
        cumulative_offset = 0.0
        td = transition_duration

        for i in range(1, n):
            cumulative_offset += durations[i - 1] - td
            if cumulative_offset < 0:
                cumulative_offset = 0.5
            out_label = f"xf{i}"
            filters.append(
                f"[{prev}][{i}:v]xfade=transition=fade:duration={td}:offset={cumulative_offset}[{out_label}]"
            )
            prev = out_label

        # Audio crossfade
        audio_prev = "0:a"
        for i in range(1, n):
            a_offset = sum(durations[:i]) - i * td
            if a_offset < 0:
                a_offset = 0.5
            a_out = f"af{i}"
            filters.append(
                f"[{audio_prev}][{i}:a]acrossfade=d={td}:c1=tri:c2=tri[{a_out}]"
            )
            audio_prev = a_out

        filter_complex = ";".join(filters)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{prev}]",
            "-map", f"[{audio_prev}]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out,
        ]

        # If audio crossfade fails, try video-only
        result = self._run(cmd, f"Crossfade {n} clips")
        if not result["ok"]:
            print("[VideoGen] Retrying crossfade without audio...")
            video_filters = [f for f in filters if "acrossfade" not in f]
            filter_complex_v = ";".join(video_filters)
            cmd_v = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_complex_v,
                "-map", f"[{prev}]",
                "-an",
                "-c:v", "libx264",
                "-preset", "medium",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                out,
            ]
            result = self._run(cmd_v, f"Crossfade {n} clips (video only)")

        if result["ok"]:
            result["output_path"] = out
            result["details"] = f"Crossfaded {n} clips with {td}s transitions"
        return result

    def batch_generate_ads(self, images_dir: str, headlines: list,
                           output_dir: str = None) -> list[dict]:
        """Generate multiple ad variations from images x headlines.

        Creates Ken Burns + text overlay for each image/headline combination.
        Returns list of result dicts for each variation.

        Args:
            images_dir: Directory containing source images
            headlines: List of headline text strings
            output_dir: Output directory (default: self.output_dir / "batch_{timestamp}")
        """
        err = self._require_ffmpeg()
        if err:
            return [err]

        img_dir = Path(images_dir)
        if not img_dir.exists():
            return [{"ok": False, "error": f"Images directory not found: {images_dir}"}]

        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
        images = sorted([
            p for p in img_dir.iterdir()
            if p.suffix.lower() in image_exts
        ])

        if not images:
            return [{"ok": False, "error": f"No images found in {images_dir}"}]

        if not headlines:
            return [{"ok": False, "error": "No headlines provided"}]

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = Path(output_dir or self.output_dir / f"batch_{ts}")
        batch_dir.mkdir(parents=True, exist_ok=True)

        results = []
        total = len(images) * len(headlines)
        count = 0

        print(f"[VideoGen] Batch: {len(images)} images x {len(headlines)} headlines = {total} variations")

        for img in images:
            for headline in headlines:
                count += 1
                safe_name = img.stem[:30] + "_" + "".join(
                    c if c.isalnum() else "_" for c in headline[:30]
                )
                kb_path = str(batch_dir / f"{safe_name}_kb.mp4")
                final_path = str(batch_dir / f"{safe_name}_final.mp4")

                print(f"[VideoGen] Batch [{count}/{total}]: {img.name} + '{headline[:40]}'")

                # Step 1: Ken Burns
                kb_result = self.create_kenburns_video(
                    str(img), kb_path, duration=8, zoom_speed=0.0015
                )
                if not kb_result["ok"]:
                    results.append({
                        "ok": False,
                        "image": str(img),
                        "headline": headline,
                        "error": f"Ken Burns failed: {kb_result['error']}",
                    })
                    continue

                # Step 2: Text overlay
                text_result = self.add_text_overlay(
                    kb_path, final_path, headline,
                    position="bottom", fontsize=56, fontcolor="white"
                )

                # Clean up intermediate Ken Burns file
                try:
                    Path(kb_path).unlink(missing_ok=True)
                except Exception:
                    pass

                if text_result["ok"]:
                    results.append({
                        "ok": True,
                        "image": str(img),
                        "headline": headline,
                        "output_path": final_path,
                        "details": f"Ken Burns + overlay: {headline[:60]}",
                    })
                else:
                    results.append({
                        "ok": False,
                        "image": str(img),
                        "headline": headline,
                        "error": f"Text overlay failed: {text_result['error']}",
                    })

        succeeded = sum(1 for r in results if r["ok"])
        print(f"[VideoGen] Batch complete: {succeeded}/{total} succeeded")
        return results

    def get_video_info(self, video_path: str) -> dict:
        """Get video metadata: duration, resolution, codec, file size.

        Returns dict with ok, duration, width, height, codec, file_size_mb.
        """
        err = self._require_ffprobe()
        if err:
            return err

        if not Path(video_path).exists():
            return {"ok": False, "error": f"File not found: {video_path}"}

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"ok": False, "error": result.stderr.strip()[:300]}

            data = json.loads(result.stdout)

            # Extract video stream info
            video_stream = None
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    video_stream = s
                    break

            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0))
            file_size = int(fmt.get("size", 0))

            info = {
                "ok": True,
                "duration": round(duration, 2),
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "format": fmt.get("format_name", "unknown"),
            }

            if video_stream:
                info["width"] = int(video_stream.get("width", 0))
                info["height"] = int(video_stream.get("height", 0))
                info["codec"] = video_stream.get("codec_name", "unknown")
                info["fps"] = video_stream.get("r_frame_rate", "unknown")
                info["resolution"] = f"{info['width']}x{info['height']}"

            print(f"[VideoGen] Info: {Path(video_path).name} = {info.get('resolution', '?')} "
                  f"{info['duration']}s {info['file_size_mb']}MB")
            return info

        except json.JSONDecodeError:
            return {"ok": False, "error": "Failed to parse ffprobe output"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ffprobe timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── AI Video Prompt Functions ────────────────────────────────────────────

    def get_prompt(self, prompt_name: str) -> dict:
        """Get a specific AI video prompt by name.

        Returns the prompt dict or {"ok": False, "error": "..."}.
        """
        if prompt_name in AI_VIDEO_PROMPTS:
            return {"ok": True, "name": prompt_name, **AI_VIDEO_PROMPTS[prompt_name]}
        return {
            "ok": False,
            "error": f"Prompt '{prompt_name}' not found. Available: {', '.join(AI_VIDEO_PROMPTS.keys())}",
        }

    def list_prompts(self) -> list[str]:
        """List all available AI video prompt names."""
        return list(AI_VIDEO_PROMPTS.keys())

    def generate_prompt_guide(self) -> str:
        """Generate a formatted guide of all prompts for copy-paste into AI video tools.

        Returns a formatted string with all prompts organized for easy use.
        """
        lines = [
            "=" * 70,
            "ZOAR BATHROOM RENTALS - AI VIDEO AD PROMPT GUIDE",
            "Luxury Portable Restroom Trailer | SoCal | $1,100/event",
            "=" * 70,
            "",
        ]

        for i, (name, data) in enumerate(AI_VIDEO_PROMPTS.items(), 1):
            title = name.replace("_", " ").title()
            lines.append(f"--- [{i}] {title} ---")
            lines.append("")
            lines.append(f"PROMPT:")
            lines.append(data["prompt"])
            lines.append("")
            if data.get("negative"):
                lines.append(f"NEGATIVE PROMPT:")
                lines.append(data["negative"])
                lines.append("")
            lines.append(f"RECOMMENDED TOOLS: {', '.join(data.get('tools', []))}")
            lines.append(f"DURATION: {data.get('duration', 'N/A')}")
            lines.append(f"CAMERA: {data.get('camera', 'N/A')}")
            lines.append("")
            lines.append("-" * 50)
            lines.append("")

        lines.append("=" * 70)
        lines.append("USAGE TIPS:")
        lines.append("1. Copy prompt into Kling / Sora / Hailuo / Runway")
        lines.append("2. Set duration and camera motion as indicated")
        lines.append("3. Use negative prompt to avoid common artifacts")
        lines.append("4. Generate 3-5 variations, pick best for editing")
        lines.append("5. Use VideoGenerator.optimize_for_reels() on final cut")
        lines.append("=" * 70)

        guide = "\n".join(lines)
        print(f"[VideoGen] Generated prompt guide ({len(AI_VIDEO_PROMPTS)} prompts)")
        return guide
