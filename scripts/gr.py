#!/usr/bin/env python3
"""Gold Reels: turns long Hebrew videos into vertical reels with Hebrew captions.

Commands (run through the studio wrapper, see SKILL.md):
  setup                         create the studio folders, fetch the Hebrew model, render a self-test
  doctor                        check that everything works
  probe   <video>               length, resolution, fps, audio
  sheet   <video> [--every S]   contact sheet of frames (to see who is on screen and where)
  transcribe <video> --project NAME [--prompt "names, terms"] [--fast]
  styles  <plan.json> [--only NAME]   the same moment in every caption style, numbered, to pick from
  preview <plan.json> [--only NAME]   frames from every cut, framed as they will render
  render  <plan.json> [--only NAME]
  stills  <video>               12-frame grid of a finished reel, for checking
  clean                         delete temporary files
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

STUDIO = Path(os.environ.get("GOLD_REELS_HOME", Path.home() / "GoldReels")).expanduser()
SKILL_DIR = Path(__file__).resolve().parent.parent
FONTS_SRC = SKILL_DIR / "assets" / "fonts"
MODEL = os.environ.get("GOLD_REELS_MODEL", "ivrit-ai/whisper-large-v3-turbo-ct2")

FPS = 30
FORMATS = {"9:16": (1080, 1920), "4:5": (1080, 1350)}
DEFAULT_ACCENT = "#F2B84B"
WHITE_ASS = "&H00FFFFFF"
BLACK_ASS = "&H00000000"
# Styles use Encoding -1 (libass picks the base direction from the first strong letter).
# Every line starts with an invisible right-to-left mark, so a line that opens with
# an English word or a number still reads right to left.
RLM = "‏"
FILLERS = {"אמ", "אממ", "אממם", "אמם", "אה", "אהה", "אההה", "מממ", "הממ", "אום", "um", "uh", "uhm", "hmm"}


# ---------------------------------------------------------------- helpers
def say(msg):
    print(msg, flush=True)


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        sys.exit("ffmpeg not found. Run: setup")


def run(args, cwd=None, quiet=True):
    cmd = [ffmpeg_exe(), "-hide_banner", "-y"] + [str(a) for a in args]
    if quiet:
        cmd[1:1] = ["-loglevel", "error"]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit("ffmpeg failed:\n" + (r.stderr or "")[-2500:])
    return r


def probe(path):
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    info = {"path": str(path)}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", r.stderr)
    if m:
        info["width"], info["height"] = int(m[1]), int(m[2])
    m = re.search(r"([\d.]+) fps", r.stderr)
    if m:
        info["fps"] = float(m[1])
    info["audio"] = "Audio:" in r.stderr
    info["video"] = "Video:" in r.stderr
    if "duration" not in info:
        sys.exit(f"Could not read the file: {path}")
    return info


def tc(sec):
    sec = max(0, sec)
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ass_time(sec):
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def ass_color(hex_color, alpha="00"):
    h = hex_color.lstrip("#")
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def resolve(p):
    p = Path(p).expanduser()
    if p.exists():
        return p.resolve()
    for base in (STUDIO, STUDIO / "input"):
        if (base / p).exists():
            return (base / p).resolve()
    sys.exit(f"File not found: {p}")


def prepare_fonts(tmp):
    fonts = Path(tmp) / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for f in FONTS_SRC.glob("*.ttf"):
        shutil.copy2(f, fonts / f.name)


def dims(reel):
    fmt = reel.get("format", "9:16")
    if fmt not in FORMATS:
        sys.exit(f"Unknown format {fmt}. Use one of: {', '.join(FORMATS)}")
    return FORMATS[fmt]


# ---------------------------------------------------------------- setup / doctor
def cmd_setup(a):
    for d in ("input", "projects", "output", "music", "sfx", "broll", ".tmp"):
        (STUDIO / d).mkdir(parents=True, exist_ok=True)
    say(f"[1/4] Studio folder: {STUDIO}")
    say(f"[2/4] Video engine: {ffmpeg_exe()}")
    say(f"[3/4] Hebrew speech model ({MODEL}), first time downloads ~1.6GB...")
    from faster_whisper import WhisperModel
    WhisperModel(MODEL, device="cpu", compute_type="int8")
    say("      model ready")
    say("[4/4] Self-test render...")
    out = self_test()
    say(f"DONE. Test video: {out}")


def self_test():
    W, H = FORMATS["9:16"]
    tmp = STUDIO / ".tmp" / "selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"color=c=0x4A414A:s={W}x{H}:r={FPS}:d=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", tmp / "base.mp4"])
    words = [{"s": 0.2, "e": 0.9, "w": "הסטודיו"}, {"s": 0.9, "e": 1.6, "w": "מוכן"},
             {"s": 1.8, "e": 2.6, "w": "בהצלחה!"}, {"s": 2.6, "e": 3.6, "w": "213 רילס"}]
    write_ass(tmp / "captions.ass", words, 4.0, W, H,
              {"hook": "בדיקה: עברית מימין לשמאל", "highlight": ["מוכן"], "progress": True})
    out = STUDIO / "output" / "selftest.mp4"
    prepare_fonts(tmp)
    run(["-i", "base.mp4", "-vf", "subtitles=captions.ass:fontsdir=fonts", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(out.resolve())], cwd=tmp)
    return out


def cmd_doctor(a):
    ok = True
    say(f"studio: {STUDIO} {'OK' if STUDIO.exists() else 'MISSING (run setup)'}")
    try:
        exe = ffmpeg_exe()
        conf = subprocess.run([exe, "-hide_banner", "-buildconf"], capture_output=True, text=True).stdout
        has_ass = "libass" in conf
        say(f"ffmpeg: {exe} | captions engine: {'OK' if has_ass else 'MISSING libass'}")
        ok &= has_ass
    except SystemExit as e:
        say(str(e)); ok = False
    try:
        import faster_whisper  # noqa
        say("speech-to-text: OK")
    except Exception:
        say("speech-to-text: MISSING (run setup)"); ok = False
    fonts = list(FONTS_SRC.glob("*.ttf"))
    say(f"fonts: {len(fonts)} found")
    ok &= bool(fonts)
    for d in ("music", "sfx", "broll"):
        n = len([f for f in (STUDIO / d).glob("*") if f.is_file()]) if (STUDIO / d).exists() else 0
        say(f"{d}/: {n} files")
    free = shutil.disk_usage(STUDIO if STUDIO.exists() else Path.home()).free / 1e9
    say(f"free disk: {free:.0f}GB {'OK' if free > 5 else 'LOW'}")
    say("ALL GOOD" if ok else "PROBLEMS FOUND")


# ---------------------------------------------------------------- look
def cmd_probe(a):
    say(json.dumps(probe(resolve(a.video)), ensure_ascii=False, indent=2))


def cmd_sheet(a):
    src = resolve(a.video)
    info = probe(src)
    dur = info["duration"]
    every = a.every or max(5.0, dur / 16)
    n = min(16, max(1, int(dur // every)))
    out = Path(a.out) if a.out else src.with_name(src.stem + "_sheet.jpg")
    tmp = STUDIO / ".tmp" / "sheet"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for i in range(n):
        t = min(dur - 0.5, (i + 0.5) * every)
        run(["-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
             "-vf", "scale=480:-2", tmp / f"f{i:02d}.jpg"])
    cols = 4
    rows = math.ceil(n / cols)
    run(["-framerate", "1", "-i", tmp / "f%02d.jpg",
         "-vf", f"tile={cols}x{rows}:padding=6:margin=6", "-frames:v", "1", out])
    say(f"sheet: {out}")
    say("frames at: " + ", ".join(tc(min(dur - 0.5, (i + 0.5) * every)) for i in range(n)))


# ---------------------------------------------------------------- transcribe
def cmd_transcribe(a):
    src = resolve(a.video)
    info = probe(src)
    proj = STUDIO / "projects" / a.project
    proj.mkdir(parents=True, exist_ok=True)
    wav = proj / "audio16k.wav"
    say(f"Extracting audio ({tc(info['duration'])})...")
    run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])

    from faster_whisper import WhisperModel
    say("Loading the Hebrew model...")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                         cpu_threads=max(1, (os.cpu_count() or 4)))
    t0 = time.time()
    segments, _ = model.transcribe(
        str(wav), language="he", word_timestamps=True, vad_filter=True,
        beam_size=1 if a.fast else 5, initial_prompt=a.prompt or None,
        condition_on_previous_text=False)

    words, lines, last_report = [], [], 0.0
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(f"[{tc(seg.start)} | {seg.start:.1f}-{seg.end:.1f}] {text}")
        for w in (seg.words or []):
            ww = w.word.strip()
            if ww:
                words.append({"s": round(w.start, 3), "e": round(w.end, 3), "w": ww})
        if seg.end - last_report > 60:
            last_report = seg.end
            pct = 100 * seg.end / info["duration"]
            say(f"  {pct:4.0f}%  ({tc(seg.end)} of {tc(info['duration'])}, {tc(time.time() - t0)} elapsed)")

    meta = {"source": str(src), "duration": info["duration"], "width": info.get("width"),
            "height": info.get("height"), "model": MODEL}
    (proj / "words.json").write_text(json.dumps({"meta": meta, "words": words}, ensure_ascii=False), encoding="utf-8")
    header = (f"# Transcript: {src.name}\n"
              f"length {tc(info['duration'])} | format: [time | start-end seconds] text\n\n")
    (proj / "transcript.md").write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    wav.unlink(missing_ok=True)
    say(f"DONE in {tc(time.time() - t0)}. {len(words)} words.")
    say(f"transcript: {proj / 'transcript.md'}")


# ---------------------------------------------------------------- captions
PUNCT_END = re.compile(r"[.,!?;:…]$")

# Caption looks. y = height of the caption line above the bottom, as a share of the frame.
CAPTION_STYLES = {
    "bold":    {"size": 104, "outline": 8, "shadow": 3, "words": 3, "chars": 16, "pop": True, "y": 0.29,
                "desc": "big white, keywords in color (default)"},
    "karaoke": {"size": 96, "outline": 8, "shadow": 3, "words": 4, "chars": 20, "pop": False, "y": 0.29,
                "desc": "a few words, the spoken word lights up"},
    "clean":   {"size": 70, "outline": 3, "shadow": 2, "words": 5, "chars": 26, "pop": False, "y": 0.20,
                "desc": "small and quiet, for emotional moments"},
    "box":     {"size": 88, "outline": 18, "shadow": 0, "words": 3, "chars": 16, "pop": True, "y": 0.29,
                "desc": "dark words on a colored box"},
}


def pages_from_words(words, max_words=3, max_chars=16, gap=0.45):
    pages, cur = [], []
    for w in words:
        if cur:
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if (len(cur) >= max_words or text_len > max_chars
                    or w["s"] - cur[-1]["e"] > gap or PUNCT_END.search(cur[-1]["w"])):
                pages.append(cur)
                cur = []
        cur.append(w)
    if cur:
        pages.append(cur)
    return pages


def clean_word(w):
    w = w.strip()
    w = re.sub(r"^[\"'״׳“”(\[]+|[\"'״׳“”)\],.;:]+$", "", w)
    return w


def norm(w):
    return re.sub(r"[^\w]", "", w)


def write_ass(path, words, duration, W, H, opts):
    """Captions + hook + CTA + brand tag + progress bar, all in one subtitle file."""
    style = opts.get("caption_style", "bold")
    if style not in CAPTION_STYLES:
        sys.exit(f"Unknown caption_style {style}. Use one of: {', '.join(CAPTION_STYLES)}")
    cs = CAPTION_STYLES[style]
    accent = ass_color(opts.get("accent") or DEFAULT_ACCENT)
    highlight = {norm(h) for h in (opts.get("highlight") or [])}
    cap_v = int(H * cs["y"])
    top_v = int(H * (0.156 if H > 1500 else 0.08))
    if style == "box":
        cap = f"Style: Cap,Heebo Black,{cs['size']},{BLACK_ASS},{BLACK_ASS},{accent},{accent},0,0,0,0,100,100,0,0,3,{cs['outline']},0,2,70,70,{cap_v},-1"
    else:
        cap = f"Style: Cap,Heebo Black,{cs['size']},{WHITE_ASS},{WHITE_ASS},{BLACK_ASS},&H96000000,0,0,0,0,100,100,0,0,1,{cs['outline']},{cs['shadow']},2,70,70,{cap_v},-1"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cap}
Style: Hook,Heebo Black,96,{BLACK_ASS},{BLACK_ASS},{WHITE_ASS},{WHITE_ASS},0,0,0,0,100,100,0,0,3,28,0,8,90,90,{top_v},-1
Style: Cta,Heebo Black,84,{BLACK_ASS},{BLACK_ASS},{accent},{accent},0,0,0,0,100,100,0,0,3,28,0,8,90,90,{top_v},-1
Style: Brand,Heebo Black,44,{WHITE_ASS},{WHITE_ASS},&H50000000,&H50000000,0,0,0,0,100,100,0,0,3,14,0,7,50,50,{int(H * 0.085)},-1
Style: Bar,Heebo Black,20,{accent},{accent},{BLACK_ASS},{BLACK_ASS},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,-1
Style: Label,Heebo Black,120,{BLACK_ASS},{BLACK_ASS},{WHITE_ASS},{WHITE_ASS},0,0,0,0,100,100,0,0,3,24,0,9,50,50,60,-1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    clean = [dict(w, w=clean_word(w["w"])) for w in words
             if clean_word(w["w"]) and norm(w["w"]) not in FILLERS]
    pages = pages_from_words(clean, cs["words"], cs["chars"])
    pop = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}" if cs["pop"] else ""
    if style == "box":
        hi_on, hi_off = "{\\3c&H00FFFFFF&\\4c&H00FFFFFF&}", "{\\3c" + accent + "&\\4c" + accent + "&}"
    else:
        hi_on, hi_off = "{\\c" + accent + "&}", "{\\c&H00FFFFFF&}"
    for i, p in enumerate(pages):
        start = p[0]["s"]
        end = p[-1]["e"] + 0.15
        if i + 1 < len(pages):
            nxt = pages[i + 1][0]["s"]
            end = nxt if nxt - p[-1]["e"] < 0.6 else min(end, nxt)
        end = min(end, duration)
        if end - start < 0.12:
            continue
        if style == "karaoke":
            # one event per spoken word: the whole page stays, the current word lights up
            for k, w in enumerate(p):
                ws = start if k == 0 else w["s"]
                we = end if k == len(p) - 1 else p[k + 1]["s"]
                if we - ws < 0.04:
                    continue
                txt = " ".join((hi_on + ass_escape(x["w"]) + hi_off) if j == k else ass_escape(x["w"])
                               for j, x in enumerate(p))
                ev.append(f"Dialogue: 1,{ass_time(ws)},{ass_time(we)},Cap,,0,0,0,,{RLM}{txt}")
            continue
        parts = [(hi_on + ass_escape(w["w"]) + hi_off) if norm(w["w"]) in highlight else ass_escape(w["w"]) for w in p]
        ev.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{pop}{RLM}{' '.join(parts)}")
    if opts.get("hook"):
        ev.append(f"Dialogue: 3,{ass_time(0)},{ass_time(min(opts.get('hook_secs', 3.0), duration))},Hook,,0,0,0,,{RLM}{ass_escape(opts['hook'])}")
    if opts.get("cta"):
        ev.append(f"Dialogue: 3,{ass_time(max(0, duration - opts.get('cta_secs', 3.0)))},{ass_time(duration)},Cta,,0,0,0,,{RLM}{ass_escape(opts['cta'])}")
    if opts.get("brand"):
        ev.append(f"Dialogue: 2,{ass_time(0)},{ass_time(duration)},Brand,,0,0,0,,{RLM}{ass_escape(opts['brand'])}")
    if opts.get("progress"):
        bh = 12
        ms = int(duration * 1000)
        ev.append(f"Dialogue: 4,{ass_time(0)},{ass_time(duration)},Bar,,0,0,0,,"
                  f"{{\\an7\\pos(0,0)\\p1\\clip(0,0,0,{bh})\\t(0,{ms},\\clip(0,0,{W},{bh}))}}m 0 0 l {W} 0 {W} {bh} 0 {bh}{{\\p0}}")
    if opts.get("label"):
        ev.append(f"Dialogue: 5,{ass_time(0)},{ass_time(duration)},Label,,0,0,0,,{opts['label']}")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- cutting
def snap(seg, words, pad_in=0.08, pad_out=0.18):
    """Move cut points into the silence just before / after the nearest words, so no word is clipped."""
    s, e = seg
    inside = [w for w in words if w["e"] > s and w["s"] < e]
    if inside:
        s = min(s, inside[0]["s"]) - pad_in
        e = max(e, inside[-1]["e"]) + pad_out
        before = [w for w in words if w["e"] <= inside[0]["s"]]
        after = [w for w in words if w["s"] >= inside[-1]["e"]]
        if before:
            s = max(s, before[-1]["e"] + 0.02)
        if after:
            e = min(e, after[0]["s"] - 0.02)
    return max(0.0, s), e


def tighten(s, e, words, gap=0.45, pad=0.10):
    """Split [s, e] into ranges that skip pauses longer than `gap` and filler words (אממ, אהה...)."""
    ws = [w for w in words if w["s"] >= s - 0.05 and w["e"] <= e + 0.05]
    if not ws:
        return [(s, e)]
    ranges, cur_s, cur_e, skipped = [], None, None, False
    for w in ws:
        if norm(w["w"]) in FILLERS:
            skipped = True
            continue
        if cur_s is None:
            cur_s, cur_e = max(s, w["s"] - pad), w["e"]
        elif w["s"] - cur_e > gap or (skipped and w["s"] - cur_e > 0.15):
            ranges.append((cur_s, min(e, cur_e + pad)))
            cur_s = max(ranges[-1][1], w["s"] - pad)
        cur_e = w["e"]
        skipped = False
    if cur_s is not None:
        ranges.append((cur_s, min(e, cur_e + pad)))
    return [(a, b) for a, b in ranges if b - a > 0.2] or [(s, e)]


def punch_split(a, b, words, every):
    """Split [a, b] at word gaps roughly every `every` seconds (continuous audio, new zoom each part)."""
    if not every or b - a < every * 1.6:
        return [(a, b)]
    ws = [w for w in words if w["s"] >= a and w["e"] <= b]
    gaps = [(w1["e"] + w2["s"]) / 2 for w1, w2 in zip(ws, ws[1:])]
    cuts, t = [], a + every
    while gaps and t < b - every * 0.6:
        m = min(gaps, key=lambda g: abs(g - t))
        if (cuts and m <= cuts[-1] + 1.0) or m - a < 1.0 or b - m < 1.0:
            break
        cuts.append(m)
        t = m + every
    bounds = [a] + cuts + [b]
    return list(zip(bounds, bounds[1:]))


def build_pieces(reel, words):
    """Turn the plan's segments into the actual pieces that get rendered, with zoom and fades."""
    segs = [snap(s, words) for s in reel["segments"]] if words else [tuple(s) for s in reel["segments"]]
    crops, zooms = reel.get("crop_x", 0.5), reel.get("zoom")
    tight = reel.get("tighten", False)
    gap = tight if isinstance(tight, (int, float)) and not isinstance(tight, bool) else 0.45
    punch = reel.get("punch", 0)
    pieces, n = [], 0
    for i, (s, e) in enumerate(segs):
        cx = crops[i] if isinstance(crops, list) else crops
        ranges = tighten(s, e, words, gap) if (tight and words) else [(s, e)]
        for ra, rb in ranges:
            subs = punch_split(ra, rb, words, punch) if words else [(ra, rb)]
            for k, (pa, pb) in enumerate(subs):
                if isinstance(zooms, list):
                    z = zooms[i] * (1.12 if k % 2 else 1.0)
                else:
                    z = 1.0 if n % 2 == 0 else 1.1
                pieces.append({"s": pa, "e": pb, "cx": cx, "zoom": z, "seg": i,
                               "fade_in": k == 0, "fade_out": k == len(subs) - 1})
                n += 1
    if len(pieces) == 1 and not isinstance(zooms, list):
        pieces[0]["zoom"] = 1.0
    return pieces


def crop_filter(info, crop_x, zoom, layout, W, H):
    iw, ih = info.get("width", 1920), info.get("height", 1080)
    if layout == "fit":
        # whole frame centered on a blurred copy (screen recordings, slides, wide group shots)
        return (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    ch = ih / zoom
    cw = ch * W / H
    if cw > iw:
        cw, ch = iw / zoom, iw / zoom * H / W
    cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
    x = max(0, min(iw - cw, int((iw * crop_x) - cw / 2)))
    y = max(0, int((ih - ch) * 0.35))
    return f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1"


def to_out_time(t, pieces):
    """Source time -> time in the finished reel."""
    off = 0.0
    for p in pieces:
        if t < p["s"]:
            return off
        if t <= p["e"]:
            return off + t - p["s"]
        off += p["e"] - p["s"]
    return off


# ---------------------------------------------------------------- render
def render_parts(tmp, src, info, pieces, layout, W, H):
    listing = []
    for i, p in enumerate(pieces):
        d = p["e"] - p["s"]
        fades = []
        if p["fade_in"]:
            fades.append("afade=t=in:d=0.03")
        if p["fade_out"]:
            fades.append(f"afade=t=out:st={max(0, d - 0.03):.3f}:d=0.03")
        af = ",".join(fades) or "anull"
        part = tmp / f"part{i:03d}.mp4"
        run(["-ss", f"{p['s']:.3f}", "-i", src, "-t", f"{d:.3f}",
             "-filter_complex", f"[0:v]{crop_filter(info, p['cx'], p['zoom'], layout, W, H)}[v];[0:a:0]{af}[a]",
             "-map", "[v]", "-map", "[a]",
             "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", part])
        listing.append(f"file '{part.name}'")
    (tmp / "list.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=tmp)


def finish(tmp, out, reel, pieces, dur, W, H):
    """Final pass: B-roll overlays, captions on top, music with ducking, sound effects, loudness."""
    prepare_fonts(tmp)
    inputs = ["-i", "joined.mp4"]
    vf, af = [], []
    cur, idx = "0:v", 1

    for k, b in enumerate(reel.get("broll", [])):
        f = resolve(b["file"])
        bd = float(b.get("dur", 2.0))
        at = to_out_time(float(b["at"]), pieces) if "at" in b else float(b.get("out_at", 0))
        at = min(max(0.0, at), max(0.0, dur - bd))
        is_img = f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        inputs += (["-loop", "1", "-t", f"{bd:.2f}", "-i", str(f)] if is_img else ["-t", f"{bd:.2f}", "-i", str(f)])
        if b.get("mode", "card") == "full":
            fit, pos = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "0:0"
        else:
            cw = int(W * 0.84)
            fit = f"scale={cw}:-2,pad=iw+16:ih+16:8:8:white"
            pos = f"(W-w)/2:{int(H * 0.2)}"
        vf.append(f"[{idx}:v]{fit},format=yuva420p,fade=t=in:st=0:d=0.15:alpha=1,"
                  f"fade=t=out:st={max(0, bd - 0.15):.2f}:d=0.15:alpha=1,setpts=PTS-STARTPTS+{at:.3f}/TB[b{k}]")
        vf.append(f"[{cur}][b{k}]overlay={pos}:eof_action=pass:enable='between(t,{at:.3f},{at + bd:.3f})'[v{k}]")
        cur, idx = f"v{k}", idx + 1
    vf.append(f"[{cur}]subtitles=captions.ass:fontsdir=fonts[vout]")

    speech = "0:a"
    if reel.get("music"):
        m = resolve(reel["music"])
        inputs += ["-stream_loop", "-1", "-i", str(m)]
        vol = float(reel.get("music_volume", 0.18))
        af.append("[0:a]asplit=2[sp][sc]")
        af.append(f"[{idx}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={vol},"
                  f"afade=t=in:d=1,afade=t=out:st={max(0, dur - 1.5):.3f}:d=1.5[mus]")
        af.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck]")
        af.append("[sp][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        speech, idx = "mix", idx + 1
    if reel.get("sfx"):
        sfx = resolve(reel["sfx"])
        inputs += ["-i", str(sfx)]
        times = [0.0] + sorted({round(to_out_time(pieces[j]["s"], pieces), 3)
                               for j in range(1, len(pieces)) if pieces[j]["seg"] != pieces[j - 1]["seg"]})
        vol = float(reel.get("sfx_volume", 0.5))
        labels = [f"x{j}" for j in range(len(times))]
        af.append(f"[{idx}:a]volume={vol},asplit={len(times)}" + "".join(f"[r{j}]" for j in range(len(times))))
        for j, t in enumerate(times):
            ms = int(t * 1000)
            af.append(f"[r{j}]adelay={ms}|{ms}[{labels[j]}]")
        af.append(f"[{speech}]" + "".join(f"[{l}]" for l in labels) +
                  f"amix=inputs={len(times) + 1}:duration=first:normalize=0[sfxmix]")
        speech, idx = "sfxmix", idx + 1
    af.append(f"[{speech}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    run(inputs + ["-filter_complex", ";".join(vf + af), "-map", "[vout]", "-map", "[aout]",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
                  "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", "-movflags", "+faststart",
                  str(Path(out).resolve())], cwd=tmp)


def make_cover(tmp, reel, pieces, dur, W, H, out):
    """A cover image for the reel: a strong frame + the hook in big letters."""
    text = reel.get("cover_text", reel.get("hook", ""))
    t = to_out_time(float(reel["cover_at"]), pieces) if "cover_at" in reel else min(0.6, dur / 2)
    accent = ass_color(reel.get("accent") or DEFAULT_ACCENT)
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cover,Heebo Black,118,{BLACK_ASS},{BLACK_ASS},{accent},{accent},0,0,0,0,100,100,0,0,3,34,0,2,80,80,{int(H * 0.2)},-1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:00:00.00,Cover,,0,0,0,,{RLM}{ass_escape(text)}
"""
    (tmp / "cover.ass").write_text(ass, encoding="utf-8")
    prepare_fonts(tmp)
    vf = "subtitles=cover.ass:fontsdir=fonts" if text else "null"
    run(["-ss", f"{t:.2f}", "-i", "joined.mp4", "-frames:v", "1", "-vf", vf, "-q:v", "2", str(Path(out).resolve())], cwd=tmp)


def caption_opts(reel):
    return {"caption_style": reel.get("caption_style", "bold"), "accent": reel.get("accent"),
            "highlight": reel.get("highlight", []), "hook": reel.get("hook", ""), "cta": reel.get("cta", ""),
            "hook_secs": reel.get("hook_secs", 3.0), "cta_secs": reel.get("cta_secs", 3.0),
            "brand": reel.get("brand", ""), "progress": reel.get("progress", False)}


def load_plan(plan_arg):
    plan_path = resolve(plan_arg)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    wf = plan_path.parent / "words.json"
    words = json.loads(wf.read_text(encoding="utf-8"))["words"] if wf.exists() else []
    src = resolve(plan["source"])
    return plan_path, plan, words, src, probe(src)


def cmd_render(a):
    plan_path, plan, all_words, src, info = load_plan(a.plan)
    if not info["audio"]:
        sys.exit("This video has no sound track, so there is nothing to caption.")
    for reel in plan["reels"]:
        if a.only and reel["name"] != a.only:
            continue
        name = reel["name"]
        W, H = dims(reel)
        say(f"== {name}  ({reel.get('format', '9:16')}, captions: {reel.get('caption_style', 'bold')})")
        tmp = STUDIO / ".tmp" / name
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        pieces = build_pieces(reel, all_words)
        src_len = sum(e - s for s, e in (snap(x, all_words) if all_words else x for x in reel["segments"]))
        say(f"  {len(pieces)} pieces" + (f" (tightened {src_len:.1f}s -> {sum(p['e'] - p['s'] for p in pieces):.1f}s)"
                                         if reel.get("tighten") else ""))
        render_parts(tmp, src, info, pieces, reel.get("layout", "crop"), W, H)
        out_words, off = [], 0.0
        for p in pieces:
            for w in all_words:
                if w["s"] >= p["s"] - 0.01 and w["e"] <= p["e"] + 0.01:
                    out_words.append({"s": w["s"] - p["s"] + off, "e": w["e"] - p["s"] + off, "w": w["w"]})
            off += p["e"] - p["s"]
        dur = probe(tmp / "joined.mp4")["duration"]
        write_ass(tmp / "captions.ass", out_words, dur, W, H, caption_opts(reel))
        out_dir = STUDIO / "output"
        out_dir.mkdir(exist_ok=True)
        version = 1
        while (out_dir / f"{name}_v{version}.mp4").exists():
            version += 1
        out = out_dir / f"{name}_v{version}.mp4"
        say("  b-roll + captions + sound + final encode...")
        finish(tmp, out, reel, pieces, dur, W, H)
        cover = None
        if reel.get("cover", True):
            cover = out.with_name(out.stem + "_cover.jpg")
            make_cover(tmp, reel, pieces, dur, W, H, cover)
        shutil.rmtree(tmp, ignore_errors=True)
        check = probe(out)
        problems = []
        if (check.get("width"), check.get("height")) != (W, H):
            problems.append(f"size {check.get('width')}x{check.get('height')}")
        if not check.get("audio"):
            problems.append("no audio")
        if abs(check["duration"] - dur) > 0.5:
            problems.append(f"length {check['duration']:.1f}s instead of {dur:.1f}s")
        say(f"  READY: {out}  ({dur:.0f}s)" + (f"  CHECK FAILED: {', '.join(problems)}" if problems
                                              else f"  [checked: {W}x{H}, audio OK]"))
        if cover:
            say(f"  cover: {cover}")


def cmd_styles(a):
    """The same few seconds in every caption style, numbered 1-4, so the creator can pick by number."""
    plan_path, plan, words, src, info = load_plan(a.plan)
    reel = next((r for r in plan["reels"] if not a.only or r["name"] == a.only), None)
    if not reel:
        sys.exit("No such reel in the plan.")
    W, H = dims(reel)
    s, e = snap(reel["segments"][0], words) if words else reel["segments"][0]
    seg_words = [w for w in words if w["s"] >= s and w["e"] <= e]
    if len(seg_words) < 3:
        sys.exit("Not enough words in the first segment to show caption styles.")
    t0 = max(s, seg_words[0]["s"] - 0.1)
    t1 = min(e, t0 + 4.0)
    tmp = STUDIO / ".tmp" / ("styles-" + reel["name"])
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    prepare_fonts(tmp)
    cx = reel.get("crop_x", 0.5)
    cx = cx[0] if isinstance(cx, list) else cx
    run(["-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}", "-an", "-vf",
         crop_filter(info, cx, 1.0, reel.get("layout", "crop"), W, H), "-c:v", "libx264", "-crf", "16",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", tmp / "sample.mp4"])
    local = [{"s": w["s"] - t0, "e": w["e"] - t0, "w": w["w"]} for w in seg_words if w["e"] <= t1]
    grab = local[min(1, len(local) - 1)]["s"] + 0.15
    for k, name in enumerate(CAPTION_STYLES, 1):
        opts = caption_opts(reel)
        opts.update({"caption_style": name, "hook": "", "cta": "", "label": str(k)})
        write_ass(tmp / "captions.ass", local, t1 - t0, W, H, opts)
        run(["-i", "sample.mp4", "-vf", "subtitles=captions.ass:fontsdir=fonts,scale=360:-2",
             "-ss", f"{grab:.2f}", "-frames:v", "1", f"s{k}.jpg"], cwd=tmp)
    out = plan_path.parent / f"{reel['name']}_styles.jpg"
    run(["-framerate", "1", "-i", "s%d.jpg", "-vf", "tile=4x1:padding=6:margin=6",
         "-frames:v", "1", str(out.resolve())], cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    say(f"styles: {out}")
    for k, (name, cs) in enumerate(CAPTION_STYLES.items(), 1):
        say(f"  {k} = {name}: {cs['desc']}")


def scene_changes(src, s, e, threshold=0.3):
    """Times (in source seconds) where the picture jumps to a different shot inside [s, e]."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(src),
           "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [round(s + float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def frange(a, b, step):
    while a < b:
        yield a
        a += step


def cmd_preview(a):
    """Before rendering: a frame every 2 seconds of every cut, framed as the reel will be,
    plus the times where the camera switches shot inside a cut."""
    plan_path, plan, words, src, info = load_plan(a.plan)
    for reel in plan["reels"]:
        if a.only and reel["name"] != a.only:
            continue
        W, H = dims(reel)
        tmp = STUDIO / ".tmp" / ("preview-" + reel["name"])
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        crops, layout = reel.get("crop_x", 0.5), reel.get("layout", "crop")
        n, labels = 0, []
        for i, (s, e) in enumerate(reel["segments"]):
            cx = crops[i] if isinstance(crops, list) else crops
            times = [s + 0.05] + [t for t in frange(s + 2.0, e - 1.0, 2.0)] + [e - 0.1]
            switches = scene_changes(src, s, e)
            times = sorted(set(times + [min(e - 0.05, t + 0.15) for t in switches]))
            if switches:
                say(f"  cut{i + 1}: camera/shot changes at " + ", ".join(f"{t:.1f}s ({tc(t)})" for t in switches))
            for t in times:
                run(["-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-filter_complex",
                     f"[0:v]{crop_filter(info, cx, 1.0, layout, W, H)},scale=270:-2[v]", "-map", "[v]",
                     tmp / f"p{n:03d}.jpg"])
                labels.append(f"cut{i + 1}@{tc(t)}")
                n += 1
        cols = 6
        out = plan_path.parent / f"{reel['name']}_preview.jpg"
        run(["-framerate", "1", "-i", tmp / "p%03d.jpg", "-vf",
             f"tile={cols}x{math.ceil(n / cols)}:padding=4:margin=4", "-frames:v", "1", out])
        shutil.rmtree(tmp, ignore_errors=True)
        say(f"preview: {out}")
        say("order: " + " | ".join(labels))
        if reel.get("tighten") and words:
            pieces = build_pieces(reel, words)
            src_len = sum(e - s for s, e in (snap(x, words) for x in reel["segments"]))
            say(f"  tighten: {len(pieces)} pieces, {src_len:.1f}s -> {sum(p['e'] - p['s'] for p in pieces):.1f}s")


def cmd_stills(a):
    src = resolve(a.video)
    dur = probe(src)["duration"]
    out = src.with_name(src.stem + "_check.jpg")
    run(["-i", src, "-vf", f"fps=12/{dur:.3f},scale=270:-2,tile=6x2:padding=4:margin=4",
         "-frames:v", "1", out])
    say(f"check sheet: {out}")


def cmd_clean(a):
    tmp = STUDIO / ".tmp"
    size = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file()) if tmp.exists() else 0
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    say(f"cleaned {size / 1e9:.2f}GB of temporary files")


def main():
    p = argparse.ArgumentParser(prog="gr")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("probe"); s.add_argument("video"); s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("sheet"); s.add_argument("video"); s.add_argument("--every", type=float)
    s.add_argument("--out"); s.set_defaults(fn=cmd_sheet)
    s = sub.add_parser("transcribe"); s.add_argument("video"); s.add_argument("--project", required=True)
    s.add_argument("--prompt"); s.add_argument("--fast", action="store_true"); s.set_defaults(fn=cmd_transcribe)
    for name, fn in (("styles", cmd_styles), ("preview", cmd_preview), ("render", cmd_render)):
        s = sub.add_parser(name); s.add_argument("plan"); s.add_argument("--only"); s.set_defaults(fn=fn)
    s = sub.add_parser("stills"); s.add_argument("video"); s.set_defaults(fn=cmd_stills)
    sub.add_parser("clean").set_defaults(fn=cmd_clean)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
