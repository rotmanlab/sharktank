#!/usr/bin/env python3
"""
compress_videos.py

Re-encode the largest MP4 files in this Shark Tank project to save disk
space. Originals are overwritten in place, but only after the new file is
verified to be smaller AND ffmpeg returns a successful exit code.

Examples:
    python3 compress_videos.py            # compress every eligible file
    python3 compress_videos.py 20         # only the 20 largest eligible files
    python3 compress_videos.py --workers 2 # 2 parallel ffmpeg jobs (default 4)
    python3 compress_videos.py -y         # skip the y/N preview prompt

Files smaller than ~30 MB are skipped automatically because re-encoding
small files is rarely worth the time/quality cost.

The veditor cache folders (veditor/uploads, veditor/clips) and node_modules
are skipped so this never touches working files.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# Directory names to skip wherever they appear.
SKIP_DIRNAMES = {"node_modules", ".git", "__pycache__"}

# Path suffixes (relative to project root) that we never compress, because
# they're working caches managed by the V editor server.
SKIP_RELATIVE_PATHS = {
    Path("veditor") / "uploads",
    Path("veditor") / "clips",
}

# Don't bother re-encoding files smaller than this. Re-encoding tiny files
# rarely saves anything meaningful and risks degrading already-clean clips.
MIN_SIZE_BYTES = 30 * 1024 * 1024  # 30 MB

# Output filename used while ffmpeg is writing the new file.
TEMP_PREFIX = "._compressing_"


def get_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def format_bytes(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def find_candidates(project_root: Path):
    """Walk the project and return [(Path, size_bytes)] for every MP4 above
    the size threshold, sorted from largest to smallest."""
    matches = []
    skip_paths = {(project_root / p).resolve() for p in SKIP_RELATIVE_PATHS}

    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune skipped directory names in place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRNAMES]

        # Prune skipped relative paths (veditor/uploads, veditor/clips, ...).
        try:
            dirpath_resolved = Path(dirpath).resolve()
        except OSError:
            continue
        if any(skip == dirpath_resolved or skip in dirpath_resolved.parents
               for skip in skip_paths):
            dirnames[:] = []
            continue

        for name in filenames:
            if not name.lower().endswith(".mp4"):
                continue
            if name.startswith(TEMP_PREFIX):
                # Leftover from an interrupted previous run; ignore.
                continue
            full = Path(dirpath) / name
            size = get_size(full)
            if size < MIN_SIZE_BYTES:
                continue
            matches.append((full, size))

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def compress_one(path: Path) -> dict:
    """Re-encode a single MP4 in place. Returns a dict with results."""
    temp = path.with_name(TEMP_PREFIX + path.name)

    # Clean up any leftover temp from an interrupted previous run.
    if temp.exists():
        try:
            temp.unlink()
        except OSError:
            pass

    cmd = [
        "ffmpeg", "-y",
        "-i", str(path),
        "-c:v", "libx264",
        "-crf", "28",          # aggressive but visually fine for survey playback
        "-preset", "veryfast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-loglevel", "error",
        str(temp),
    ]

    t0 = time.time()
    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        return {
            "path": path, "ok": False, "elapsed": time.time() - t0,
            "error": (e.stderr or b"").decode("utf-8", errors="ignore")[-300:],
        }
    except FileNotFoundError:
        return {
            "path": path, "ok": False, "elapsed": time.time() - t0,
            "error": "ffmpeg not found on PATH",
        }

    old = get_size(path)
    new = get_size(temp)
    elapsed = time.time() - t0

    if new == 0 or new >= old:
        # New file isn't actually smaller -- discard it, keep original.
        try:
            temp.unlink()
        except OSError:
            pass
        return {
            "path": path, "ok": True, "skipped": True,
            "old": old, "new": new, "elapsed": elapsed,
        }

    try:
        os.replace(temp, path)
    except OSError as e:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        return {"path": path, "ok": False, "elapsed": elapsed, "error": str(e)}

    return {
        "path": path, "ok": True, "skipped": False,
        "old": old, "new": new, "elapsed": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compress the largest MP4s in the project to save disk space.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("count", nargs="?", type=int, default=None,
                        help="Max number of files to compress (default: all eligible)")
    parser.add_argument("--workers", "-w", type=int, default=4,
                        help="Parallel ffmpeg jobs (default: 4)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg is not on your PATH.")
        print("       Install with: brew install ffmpeg")
        sys.exit(1)

    project_root = Path(__file__).resolve().parent
    print(f"Scanning {project_root} ...")
    candidates = find_candidates(project_root)

    if args.count is not None:
        candidates = candidates[:args.count]

    if not candidates:
        print(f"No MP4 files larger than {format_bytes(MIN_SIZE_BYTES)} found. "
              f"Nothing to do.")
        return

    total_before = sum(size for _, size in candidates)

    print()
    print(f"Found {len(candidates)} file(s) to compress, total "
          f"{format_bytes(total_before)}:")
    print("-" * 78)
    for i, (path, size) in enumerate(candidates, 1):
        try:
            rel = path.relative_to(project_root)
        except ValueError:
            rel = path
        print(f"  {i:>3}. {format_bytes(size):>10}  {rel}")
    print("-" * 78)
    print(f"Settings: CRF 28, preset veryfast, audio AAC 128k")
    print(f"Workers (parallel ffmpeg jobs): {args.workers}")
    print()

    if not args.yes:
        try:
            ans = input("Proceed and overwrite these files? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if ans not in ("y", "yes"):
            print("Aborted.")
            return

    print()
    t0 = time.time()
    results = []
    files = [p for p, _ in candidates]
    total_n = len(files)
    done_n = 0

    # ThreadPoolExecutor is correct here: each thread spends ~all its time
    # blocked in subprocess.run, releasing the GIL while ffmpeg runs as a
    # separate OS process. So `workers` threads = `workers` ffmpeg processes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(compress_one, p): p for p in files}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            done_n += 1
            results.append(r)
            try:
                rel = r["path"].relative_to(project_root)
            except ValueError:
                rel = r["path"]
            if not r["ok"]:
                print(f"  [{done_n}/{total_n}] FAILED  {rel}: "
                      f"{r.get('error', 'unknown error')[:160]}")
            elif r.get("skipped"):
                print(f"  [{done_n}/{total_n}] SKIP    {rel} "
                      f"(re-encoded file was not smaller)")
            else:
                pct = (1 - r["new"] / r["old"]) * 100
                print(
                    f"  [{done_n}/{total_n}] OK      {rel}: "
                    f"{format_bytes(r['old'])} -> {format_bytes(r['new'])} "
                    f"(-{pct:.0f}%, {r['elapsed']:.1f}s)"
                )

    elapsed = time.time() - t0
    successful = [r for r in results if r["ok"] and not r.get("skipped")]
    skipped = [r for r in results if r["ok"] and r.get("skipped")]
    failed = [r for r in results if not r["ok"]]
    bytes_saved = sum(r["old"] - r["new"] for r in successful)

    print()
    print("=" * 78)
    print(f"Done in {elapsed:.1f}s")
    print(f"  Compressed:  {len(successful)}")
    print(f"  Skipped:     {len(skipped)}")
    print(f"  Failed:      {len(failed)}")
    print(f"  Disk freed:  {format_bytes(bytes_saved)}")


if __name__ == "__main__":
    main()
