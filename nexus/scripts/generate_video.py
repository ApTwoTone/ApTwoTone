"""
FFmpeg Video Generation Pipeline — Ken Burns Slideshow

Takes the 60+ photos from website/images/bathroom/ and creates a
professional Ken Burns-style slideshow video with:
  - Slow zoom/pan on each photo (4-5 seconds each)
  - Smooth crossfade transitions between photos
  - Royalty-free background music (if provided)
  - Text overlay with business info
  - 9:16 vertical (1080x1920) for Instagram/Facebook Reels
  - 16:9 horizontal (1920x1080) for Facebook/YouTube

Usage:
    python scripts/generate_video.py                       # Default: vertical, all photos
    python scripts/generate_video.py --format horizontal   # 16:9 landscape
    python scripts/generate_video.py --photos 15           # Only use 15 photos
    python scripts/generate_video.py --music path/to/song.mp3
    python scripts/generate_video.py --output my_video.mp4

Zero cost — uses FFmpeg (already installed) and local images.
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────

IMAGES_DIR = Path(__file__).parent.parent / "website" / "images" / "bathroom"
OUTPUT_DIR = Path.home() / ".nexus" / "generated_videos"

# Business info for text overlay
BIZ_PHONE = "(424) 235-8979"
BIZ_WEBSITE = "zoarbathroomrental.com"
BIZ_NAME = "Zoar Bathroom Rental"
TAGLINE = "Luxury Restroom Trailer  |  AC  |  Running Water  |  Hardwood Floors"

# Video settings
PHOTO_DURATION = 4.5   # seconds per photo
FADE_DURATION = 0.8    # crossfade between photos
FPS = 30

# Formats
FORMATS = {
    "vertical": {"width": 1080, "height": 1920, "label": "9:16 Vertical (Reels)"},
    "horizontal": {"width": 1920, "height": 1080, "label": "16:9 Horizontal"},
    "square": {"width": 1080, "height": 1080, "label": "1:1 Square"},
}


def get_images(count: int = 0, shuffle: bool = True) -> list[Path]:
    """Get photos from the images directory."""
    if not IMAGES_DIR.exists():
        print(f"❌ Images directory not found: {IMAGES_DIR}")
        sys.exit(1)

    images = sorted(IMAGES_DIR.glob("photo-*.jpg"))
    if not images:
        images = sorted(IMAGES_DIR.glob("*.jpg"))

    if not images:
        print(f"❌ No images found in {IMAGES_DIR}")
        sys.exit(1)

    if shuffle:
        random.shuffle(images)

    if count > 0:
        images = images[:count]

    print(f"📷 Using {len(images)} photos from {IMAGES_DIR}")
    return images


def generate_ken_burns_video(
    images: list[Path],
    output: Path,
    fmt: str = "vertical",
    music_path: str = "",
    with_text: bool = True,
) -> Path:
    """Generate a Ken Burns slideshow video using FFmpeg.

    Creates individual zoomed/panned clips for each photo, then concatenates
    them with crossfade transitions.
    """
    v = FORMATS[fmt]
    w, h = v["width"], v["height"]
    total_photos = len(images)
    total_duration = total_photos * PHOTO_DURATION

    print(f"🎬 Generating {v['label']} video ({w}x{h})")
    print(f"   {total_photos} photos × {PHOTO_DURATION}s = ~{total_duration:.0f}s total")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build FFmpeg filter chain
    # Each photo gets a Ken Burns effect (slow zoom from 100% to 110% or vice versa)
    # with crossfade between each pair

    inputs = []
    filter_parts = []

    for i, img in enumerate(images):
        inputs.extend(["-loop", "1", "-t", str(PHOTO_DURATION), "-i", str(img)])

        # Ken Burns: alternate between zoom-in and zoom-out
        if i % 2 == 0:
            # Zoom in: scale from 110% to 120%, slowly pan
            zoom_expr = f"zoom+0.0005"
            x_expr = f"iw/2-(iw/zoom/2)+{random.uniform(-0.3, 0.3)}*on"
            y_expr = f"ih/2-(ih/zoom/2)+{random.uniform(-0.2, 0.2)}*on"
        else:
            # Zoom out: start zoomed in, slowly zoom out
            zoom_expr = f"if(eq(on,0),1.15,zoom-0.0003)"
            x_expr = f"iw/2-(iw/zoom/2)+{random.uniform(-0.2, 0.2)}*on"
            y_expr = f"ih/2-(ih/zoom/2)+{random.uniform(-0.2, 0.2)}*on"

        # Apply zoompan (Ken Burns) + scale to target resolution
        filter_parts.append(
            f"[{i}:v]zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={int(PHOTO_DURATION * FPS)}:s={w}x{h}:fps={FPS},"
            f"setsar=1[v{i}]"
        )

    # Concatenate with crossfade
    if len(images) == 1:
        filter_chain = filter_parts[0].replace(f"[v0]", "[vout]")
    else:
        # Build crossfade chain
        xfade_parts = []
        prev = "v0"
        for i in range(1, len(images)):
            offset = i * PHOTO_DURATION - FADE_DURATION * i
            out_label = f"xf{i}" if i < len(images) - 1 else "vfaded"
            xfade_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={FADE_DURATION}"
                f":offset={offset:.2f}[{out_label}]"
            )
            prev = out_label

        filter_chain = ";".join(filter_parts + xfade_parts)

        # Add text overlay if requested
        if with_text:
            # Add business name at bottom
            text_filter = (
                f"[vfaded]drawtext=text='{BIZ_NAME}':"
                f"fontsize={int(w * 0.035)}:fontcolor=white:"
                f"borderw=2:bordercolor=black@0.6:"
                f"x=(w-tw)/2:y=h-{int(h * 0.08)},"
                f"drawtext=text='{BIZ_PHONE}  |  {BIZ_WEBSITE}':"
                f"fontsize={int(w * 0.022)}:fontcolor=white@0.9:"
                f"borderw=1:bordercolor=black@0.5:"
                f"x=(w-tw)/2:y=h-{int(h * 0.045)}[vout]"
            )
            filter_chain += ";" + text_filter
        else:
            filter_chain = filter_chain.replace("[vfaded]", "[vout]")

    # Build FFmpeg command
    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)

    # Add music if provided
    if music_path and Path(music_path).exists():
        cmd.extend(["-i", music_path])
        audio_idx = len(images)
        audio_filter = f"-map [{audio_idx}:a] -shortest"
    else:
        audio_filter = ""

    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[vout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ])

    if music_path and Path(music_path).exists():
        cmd.extend(["-map", f"{len(images)}:a", "-c:a", "aac", "-b:a", "192k", "-shortest"])

    cmd.append(str(output))

    print(f"\n🔧 Running FFmpeg...")
    print(f"   Output: {output}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout
        )
        if result.returncode != 0:
            print(f"❌ FFmpeg error:\n{result.stderr[-500:]}")
            # Try simpler approach without Ken Burns if complex filter fails
            return _generate_simple_slideshow(images, output, w, h, music_path, with_text)
        else:
            size_mb = output.stat().st_size / (1024 * 1024)
            print(f"✅ Video generated: {output} ({size_mb:.1f} MB)")
            return output
    except subprocess.TimeoutExpired:
        print("❌ FFmpeg timed out after 10 minutes")
        return _generate_simple_slideshow(images, output, w, h, music_path, with_text)


def _generate_simple_slideshow(
    images: list[Path],
    output: Path,
    w: int, h: int,
    music_path: str = "",
    with_text: bool = True,
) -> Path:
    """Fallback: simple slideshow without Ken Burns (more reliable)."""
    print("🔄 Falling back to simple slideshow...")

    # Create a concat demuxer file
    concat_file = Path(tempfile.mktemp(suffix=".txt"))
    with open(concat_file, "w") as f:
        for img in images:
            f.write(f"file '{img}'\n")
            f.write(f"duration {PHOTO_DURATION}\n")
        # Repeat last image for clean ending
        f.write(f"file '{images[-1]}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-movflags", "+faststart",
    ]

    if music_path and Path(music_path).exists():
        cmd.extend(["-i", music_path, "-c:a", "aac", "-b:a", "192k", "-shortest"])

    cmd.append(str(output))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        concat_file.unlink(missing_ok=True)

        if result.returncode != 0:
            print(f"❌ Simple slideshow also failed:\n{result.stderr[-500:]}")
            return output
        else:
            size_mb = output.stat().st_size / (1024 * 1024)
            print(f"✅ Simple slideshow generated: {output} ({size_mb:.1f} MB)")
            return output
    except Exception as e:
        concat_file.unlink(missing_ok=True)
        print(f"❌ Error: {e}")
        return output


def main():
    parser = argparse.ArgumentParser(description="Generate Ken Burns slideshow video")
    parser.add_argument("--format", choices=["vertical", "horizontal", "square"],
                        default="vertical", help="Video format (default: vertical)")
    parser.add_argument("--photos", type=int, default=0,
                        help="Number of photos to use (0 = all)")
    parser.add_argument("--music", type=str, default="",
                        help="Path to background music file")
    parser.add_argument("--output", type=str, default="",
                        help="Output filename")
    parser.add_argument("--no-text", action="store_true",
                        help="Disable text overlay")
    parser.add_argument("--no-shuffle", action="store_true",
                        help="Don't shuffle photos")

    args = parser.parse_args()

    images = get_images(count=args.photos, shuffle=not args.no_shuffle)

    if args.output:
        output = Path(args.output)
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = OUTPUT_DIR / f"zoar_{args.format}_{ts}.mp4"

    generate_ken_burns_video(
        images=images,
        output=output,
        fmt=args.format,
        music_path=args.music,
        with_text=not args.no_text,
    )


if __name__ == "__main__":
    main()
