#!/usr/bin/env python3
"""
Thumbnail generator for the media gallery.

    python3 _scripts/make-thumbs.py ~/Desktop/lab-dinner/
    python3 _scripts/make-thumbs.py photo1.jpg photo2.jpg --append
    python3 _scripts/make-thumbs.py ~/Photos/*.jpg --dry-run

For each source image it:

  1. Copies the untouched original into  images/media/full/
     (gitignored staging area -- upload these to Drive, then paste the
     share links into media/index.html)
  2. Writes a web-sized copy into      images/media/thumb/
  3. Prints the YAML entries to paste into media/index.html,
     or writes them straight in with --append

Sizing rule: NEVER upscale. A photo whose long edge is already under the
limit, and whose file is already small, is copied byte for byte -- no
re-encode, no generation loss. Only genuinely large photos get resampled.

Naming: files are renamed by the date the photo was taken, read from EXIF
where present and otherwise guessed from the filename (20240726_1224.jpg,
IMG-20240716-WA0013.jpg and similar). Use --name keep to keep the original
filenames instead.

Needs Pillow (pip3 install pillow). Without it the script falls back to
macOS `sips`, which works but handles rotation less predictably.
"""

import argparse
import datetime as dt
import hashlib
import pathlib
import re
import shutil
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FULL_DIR = ROOT / "images" / "media" / "full"
THUMB_DIR = ROOT / "images" / "media" / "thumb"
PAGE = ROOT / "media" / "index.html"
INSERT_BEFORE = "  # BEGIN IMPORTED"

SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".JPG", ".JPEG", ".PNG", ".HEIC"}

try:
    from PIL import Image, ImageOps
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


# ---------------------------------------------------------------- dimensions

def dims_from_header(path):
    """(width, height) straight out of the file header, no dependencies."""
    data = path.read_bytes()[:256 * 1024]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def dimensions(path):
    if HAVE_PIL:
        try:
            with Image.open(path) as im:
                return im.size
        except Exception:
            pass
    return dims_from_header(path)


# ---------------------------------------------------------------------- date

FILENAME_DATE_PATTERNS = [
    r"(?:^|[^0-9])(20\d{2})(\d{2})(\d{2})(?:[^0-9]|$)",   # 20240726_122409
    r"(?:^|[^0-9])(20\d{2})-(\d{2})-(\d{2})(?:[^0-9]|$)",  # 2024-07-26
]


def exif_date(path):
    if not HAVE_PIL:
        return None
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return None
            from PIL import ExifTags
            tags = {ExifTags.TAGS.get(k, k): v for k, v in ex.items()}
            raw = tags.get("DateTimeOriginal") or tags.get("DateTime")
            if not raw:
                return None
            return dt.datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").date()
    except Exception:
        return None


def filename_date(path):
    for pat in FILENAME_DATE_PATTERNS:
        m = re.search(pat, path.stem)
        if m:
            try:
                d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if dt.date(2000, 1, 1) <= d <= dt.date.today():
                return d
    return None


def photo_date(path):
    """Filename first: WhatsApp and camera filenames survive the transfers
    that strip or rewrite EXIF, so they are usually closer to the truth."""
    d = filename_date(path)
    if d:
        return d, "filename"
    d = exif_date(path)
    if d:
        return d, "exif"
    stamp = dt.date.fromtimestamp(path.stat().st_mtime)
    return stamp, "file mtime"


def target_stem(path, strategy, taken):
    if strategy == "keep":
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-").lower()
        return stem or "photo"
    return taken.isoformat()


def file_hash(path):
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def staged_hashes():
    """Content hashes of everything already staged in images/media/full/."""
    out = {}
    if FULL_DIR.exists():
        for p in FULL_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                out[file_hash(p)] = p.name
    return out


def unique(stem, used):
    if stem not in used:
        used.add(stem)
        return stem
    n = 2
    while f"{stem}-{n}" in used:
        n += 1
    used.add(f"{stem}-{n}")
    return f"{stem}-{n}"


# ----------------------------------------------------------------- resizing

def resize_pil(src, dest, max_edge, quality):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)      # bake rotation in before stripping
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        im.convert("RGB").save(dest, "JPEG", quality=quality,
                               optimize=True, progressive=True)


def resize_sips(src, dest, max_edge, quality):
    shutil.copy2(src, dest)
    subprocess.run(
        ["sips", "-Z", str(max_edge), "-s", "format", "jpeg",
         "-s", "formatOptions", str(quality), str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_thumb(src, dest, args):
    """Returns a short description of what happened.

    Two guarantees:
      * a photo that is already small enough to serve is copied byte for
        byte -- no re-encode, no generation loss
      * the thumbnail is never bigger than the source. Re-encoding a photo
        that was already compressed for the web can easily inflate it, so
        if that happens the resized copy is thrown away.
    """
    size = src.stat().st_size
    wh = dimensions(src)
    edge = max(wh) if wh else None
    shape = f"{wh[0]}x{wh[1]}" if wh else "?"
    is_jpeg = src.suffix.lower() in (".jpg", ".jpeg")

    # A little slack above --max: a 1600px photo that is already 200 KB
    # costs the page nothing, and re-encoding it can only make it worse.
    small_enough = (is_jpeg and size <= args.keep_under * 1024
                    and edge is not None and edge <= args.max * 1.25)

    if small_enough:
        if not args.dry_run:
            shutil.copy2(src, dest)
        return f"{shape} {size // 1024} KB -> kept as-is"

    if args.dry_run:
        return f"{shape} {size // 1024} KB -> would resize to {args.max}px"

    if HAVE_PIL:
        resize_pil(src, dest, args.max, args.quality)
    elif shutil.which("sips"):
        resize_sips(src, dest, args.max, args.quality)
    else:
        shutil.copy2(src, dest)
        return f"{shape} -> copied (no Pillow, no sips)"

    new_size = dest.stat().st_size
    if is_jpeg and new_size >= size:
        shutil.copy2(src, dest)
        return (f"{shape} {size // 1024} KB -> kept as-is "
                f"(resize would have grown it to {new_size // 1024} KB)")

    new_wh = dimensions(dest)
    new_shape = f"{new_wh[0]}x{new_wh[1]}" if new_wh else "?"
    return f"{shape} {size // 1024} KB -> {new_shape} {new_size // 1024} KB"


# -------------------------------------------------------------------- output

def yaml_for(entries):
    out = []
    for e in entries:
        out.append(f"  - thumb: /images/media/thumb/{e['name']}")
        out.append("    # full: PASTE DRIVE LINK")
        out.append(f"    date: {e['date']}")
        out.append("    # TODO: caption")
        out.append("")
    return "\n".join(out)


def append_to_page(entries):
    if not PAGE.exists():
        print(f"!! {PAGE} not found, not appending")
        return False
    text = PAGE.read_text(encoding="utf-8")

    # images/media/full/ is gitignored, so a fresh clone has no staged
    # originals to compare against and the content check above cannot fire.
    # Refuse to list the same thumbnail twice.
    fresh = [e for e in entries
             if f"/images/media/thumb/{e['name']}" not in text]
    if len(fresh) != len(entries):
        dupes = len(entries) - len(fresh)
        print(f"   {dupes} entr{'y' if dupes == 1 else 'ies'} already listed "
              f"in media/index.html, not adding again")
    if not fresh:
        return False
    entries = fresh

    block = yaml_for(entries)
    if INSERT_BEFORE in text:
        text = text.replace(INSERT_BEFORE, block + INSERT_BEFORE, 1)
    else:
        m = re.search(r"^items:\s*$", text, re.M)
        if not m:
            print("!! could not find 'items:' in media/index.html")
            return False
        text = text[:m.end()] + "\n\n" + block + text[m.end():]
    PAGE.write_text(text, encoding="utf-8")
    return True


def collect(paths):
    found = []
    for raw in paths:
        p = pathlib.Path(raw).expanduser()
        if p.is_dir():
            found += sorted(q for q in p.iterdir()
                            if q.is_file() and q.suffix in SUFFIXES)
        elif p.is_file():
            found.append(p)
        else:
            print(f"!! not found, skipping: {p}")
    return found


def main():
    ap = argparse.ArgumentParser(
        description="Build gallery thumbnails and the YAML to go with them.")
    ap.add_argument("sources", nargs="+",
                    help="image files, or folders of images")
    ap.add_argument("--max", type=int, default=1400, metavar="PX",
                    help="longest edge of the thumbnail (default 1400)")
    ap.add_argument("--quality", type=int, default=90,
                    help="JPEG quality when resizing (default 90)")
    ap.add_argument("--keep-under", type=int, default=600, metavar="KB",
                    help="JPEGs already under this size and under --max are "
                         "copied unchanged (default 600)")
    ap.add_argument("--name", choices=["date", "keep"], default="date",
                    help="'date' renames to YYYY-MM-DD (default), "
                         "'keep' keeps the original filename")
    ap.add_argument("--no-full", action="store_true",
                    help="skip copying originals into images/media/full/")
    ap.add_argument("--append", action="store_true",
                    help="write the YAML into media/index.html")
    ap.add_argument("--force", action="store_true",
                    help="overwrite thumbnails that already exist")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen, touch nothing")
    args = ap.parse_args()

    sources = collect(args.sources)
    if not sources:
        print("Nothing to do.")
        return 1

    if not HAVE_PIL:
        print("Pillow not installed; falling back to sips.")
        print("For better results: pip3 install pillow\n")

    if not args.dry_run:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        if not args.no_full:
            FULL_DIR.mkdir(parents=True, exist_ok=True)

    used = {p.stem for p in THUMB_DIR.glob("*.jpg")} if THUMB_DIR.exists() else set()
    staged = staged_hashes()
    entries, skipped = [], 0

    for src in sources:
        # Already imported? Match on content, so re-running on the same
        # folder is a no-op even if the source has since been renamed.
        digest = file_hash(src)
        if digest in staged and not args.force:
            print(f"  {src.name}  ->  already imported as "
                  f"{staged[digest]}, skipped")
            skipped += 1
            continue

        taken, how = photo_date(src)
        stem = unique(target_stem(src, args.name, taken), used)
        dest = THUMB_DIR / f"{stem}.jpg"

        if dest.exists() and not args.force and not args.dry_run:
            print(f"  {src.name}  ->  {dest.name}  (exists, skipped)")
            skipped += 1
            continue

        if not args.no_full and not args.dry_run:
            shutil.copy2(src, FULL_DIR / f"{stem}{src.suffix.lower()}")
            staged[digest] = f"{stem}{src.suffix.lower()}"

        note = make_thumb(src, dest, args)
        flag = "" if how == "exif" else f"  [date from {how}]"
        print(f"  {src.name}  ->  {dest.name}   {note}{flag}")
        entries.append({"name": dest.name, "date": taken.strftime("%B %Y")})

    if not entries:
        print(f"\nNothing new ({skipped} already present).")
        return 0

    if args.dry_run:
        print(f"\n{len(entries)} thumbnail(s) would be written"
              + (f", {skipped} skipped" if skipped else ""))
        print("\n(dry run -- nothing was written)\n")
        print(yaml_for(entries))
        return 0

    print(f"\n{len(entries)} thumbnail(s) written"
          + (f", {skipped} skipped" if skipped else ""))

    if args.append and append_to_page(entries):
        print(f"Added {len(entries)} entries to media/index.html.")
        print("Fill in the '# TODO: caption' lines, and check the dates.")
    else:
        print("\nPaste into the items: list in media/index.html --\n")
        print(yaml_for(entries))

    if not args.no_full:
        print(f"Originals staged in {FULL_DIR.relative_to(ROOT)}/ "
              f"for upload to Drive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
