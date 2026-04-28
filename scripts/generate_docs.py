"""Generate docs/PicoPhone-Py.odt from project metadata + git log.

Run:  python scripts/generate_docs.py
Output: docs/PicoPhone-Py.odt
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from odf.opendocument import OpenDocumentText
from odf.style import (
    FontFace, ParagraphProperties, Style, TableCellProperties,
    TableColumnProperties, TableProperties, TextProperties,
)
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import H, LineBreak, ListItem, List, P, Span

ROOT = Path(__file__).resolve().parent.parent
OUT  = ROOT / "docs" / "PicoPhone-Py.odt"

# ---------------------------------------------------------------- styles ----
doc = OpenDocumentText()

# Fonts
doc.fontfacedecls.addElement(FontFace(name="Sans", fontfamily="Liberation Sans"))
doc.fontfacedecls.addElement(FontFace(name="Mono", fontfamily="Liberation Mono",
                                      fontpitch="fixed"))

def add_style(name, family, **props):
    s = Style(name=name, family=family)
    if family == "paragraph":
        para_props = {k: v for k, v in props.items() if k.startswith(("margin", "textalign", "breakbefore"))}
        if para_props:
            s.addElement(ParagraphProperties(**para_props))
    text_props = {k: v for k, v in props.items()
                  if not k.startswith(("margin", "textalign", "breakbefore"))}
    if text_props:
        s.addElement(TextProperties(**text_props))
    doc.styles.addElement(s)
    return s

add_style("Title",   "paragraph", fontsize="22pt", fontweight="bold", fontfamily="Sans",
          margintop="0cm", marginbottom="0.6cm", textalign="center")
add_style("H1",      "paragraph", fontsize="16pt", fontweight="bold", fontfamily="Sans",
          margintop="0.8cm", marginbottom="0.3cm", breakbefore="page")
add_style("H1NoPB",  "paragraph", fontsize="16pt", fontweight="bold", fontfamily="Sans",
          margintop="0.8cm", marginbottom="0.3cm")
add_style("H2",      "paragraph", fontsize="13pt", fontweight="bold", fontfamily="Sans",
          margintop="0.5cm", marginbottom="0.2cm")
add_style("H3",      "paragraph", fontsize="11pt", fontweight="bold", fontfamily="Sans",
          margintop="0.3cm", marginbottom="0.15cm")
add_style("Body",    "paragraph", fontsize="11pt", fontfamily="Sans",
          marginbottom="0.2cm")
add_style("Code",    "paragraph", fontsize="9.5pt", fontfamily="Mono",
          margintop="0.1cm", marginbottom="0.2cm")
add_style("CodeSpan","text",      fontsize="10pt", fontfamily="Mono")
add_style("Bold",    "text",      fontweight="bold")
add_style("Muted",   "text",      color="#666666", fontsize="9.5pt")
add_style("CenterMuted", "paragraph", fontfamily="Sans", fontsize="10pt",
          color="#666666", textalign="center", marginbottom="0.4cm")

# table styles
ts = Style(name="Tbl", family="table")
ts.addElement(TableProperties(width="17cm", align="margins"))
doc.automaticstyles.addElement(ts)

tch = Style(name="TCellH", family="table-cell")
tch.addElement(TableCellProperties(backgroundcolor="#dde4f0", padding="0.1cm",
                                   border="0.05pt solid #888"))
doc.automaticstyles.addElement(tch)

tcb = Style(name="TCellB", family="table-cell")
tcb.addElement(TableCellProperties(padding="0.1cm", border="0.05pt solid #888"))
doc.automaticstyles.addElement(tcb)

# ---------------------------------------------------------------- helpers ----
def p(style, text=""):
    el = P(stylename=style)
    if text:
        el.addText(text)
    doc.text.addElement(el)
    return el

def h(level, style, text):
    el = H(outlinelevel=level, stylename=style)
    el.addText(text)
    doc.text.addElement(el)

def code_block(text):
    para = P(stylename="Code")
    lines = text.strip("\n").split("\n")
    for i, line in enumerate(lines):
        if i:
            para.addElement(LineBreak())
        para.addText(line)
    doc.text.addElement(para)

def bullets(items):
    lst = List()
    for it in items:
        li = ListItem()
        para = P(stylename="Body")
        para.addText(it)
        li.addElement(para)
        lst.addElement(li)
    doc.text.addElement(lst)

def table(header, rows, col_widths=None):
    tbl = Table(stylename="Tbl")
    n = len(header)
    widths = col_widths or [str(17 / n) + "cm"] * n
    for w in widths:
        col_st = Style(name=f"col_{id(tbl)}_{w}", family="table-column")
        col_st.addElement(TableColumnProperties(columnwidth=w))
        doc.automaticstyles.addElement(col_st)
        tbl.addElement(TableColumn(stylename=col_st))
    # header row
    tr = TableRow()
    for cell in header:
        tc = TableCell(stylename="TCellH")
        para = P(stylename="Body")
        para.addElement(Span(stylename="Bold", text=cell))
        tc.addElement(para)
        tr.addElement(tc)
    tbl.addElement(tr)
    for row in rows:
        tr = TableRow()
        for cell in row:
            tc = TableCell(stylename="TCellB")
            tc.addElement(P(stylename="Body", text=str(cell)))
            tr.addElement(tc)
        tbl.addElement(tr)
    doc.text.addElement(tbl)


def git_log():
    try:
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "log", "--pretty=format:%h\t%ad\t%s", "--date=short"],
            text=True, stderr=subprocess.DEVNULL,
        )
        rows = []
        for line in out.splitlines():
            sha, day, msg = line.split("\t", 2)
            rows.append((sha, day, msg))
        return rows
    except subprocess.CalledProcessError:
        return []


# =====================================================================  body
p("Title", "PicoPhone-Py")
p("CenterMuted",
  f"Modern multiplatform reimplementation of PicoPhone (Aldazabal, 2009) — "
  f"generated {date.today().isoformat()}")

# 1. About
h(1, "H1NoPB", "1. About")
p("Body",
  "PicoPhone-Py is a single-file portable softphone for Windows and Linux. "
  "It revives the original PicoPhone 1.65 (Javier Aldazabal, 2009) on a modern "
  "stack: PySide6 GUI, Opus codec, AES-128-GCM encrypted media, IPv6 dual-stack "
  "signaling, mDNS-style LAN auto-discovery, and an optional DeepFilterNet3 "
  "neural noise / dereverb module on the AI-mode track.")
p("Body",
  "The Windows binary is a true onefile .exe; the Linux binary is a single ELF. "
  "Both unpack their bundled Python runtime and Qt libraries to a private "
  "temporary directory on launch and clean up on exit. No installer, no admin "
  "rights, no sibling DLLs.")

# 2. Features
h(1, "H1", "2. Features")
table(
    ["Feature", "Status"],
    [
        ("Opus 48 kHz mono media",                  "core"),
        ("AEC: FDAF + Wiener post-filter + Geigel DTD", "core"),
        ("AEC: DeepFilterNet3 (AI mode)",           "optional"),
        ("AES-128-GCM media encryption",            "core, HKDF per call"),
        ("IPv4 + IPv6 dual-stack signaling",        "core"),
        ("mDNS-style LAN auto-discovery",           "core, multicast UDP"),
        ("Single UDP port for signaling + media",   "first-byte dispatch"),
        ("PING/PONG keepalive (caller and callee)", "core"),
        ("Ringtone (original ringin.wav)",          "QSoundEffect, looped"),
        ("Pop-up dialogs for call events",          "hung up / lost / no answer"),
        ("Single incoming-call dialog",             "auto-rejects further INVITEs"),
        ("Mute mic / mute speaker (clickable LED)", "PicoPhone-style"),
        ("MIC / SPK gain sliders",                  "linear, 0..1.0"),
        ("Live TX/RX kbps in status bar",           "diagnostic"),
        ("System-tray minimize",                    "Win/Linux"),
        ("Autostart with Windows",                  "registry HKCU\\\\...\\\\Run"),
        ("Optional log file",                       "off by default"),
    ],
    col_widths=["10cm", "7cm"],
)

# 3. Build
h(1, "H1", "3. Build")
h(2, "H2", "3.1 Windows")
p("Body", "Run from the project root:")
code_block("scripts\\build_windows.bat")
p("Body",
  "Output: dist\\nuitka\\PicoPhone-Py.exe. Requires Python 3.12 + Nuitka with "
  "auto-MinGW64; DeepFilterNet bundling triggers automatically when the "
  "deepfilternet pip package is installed in that interpreter.")
h(2, "H2", "3.2 Linux (Mageia 9 / Fedora 39+ / RHEL 9+)")
p("Body",
  "On a real Mageia desktop:")
code_block("scripts/build_mageia.sh")
p("Body",
  "On any other host, use the WSL2-Mageia pipeline (bundled "
  "instructions in README.md). Output: dist/nuitka/PicoPhone-Py — single ELF, "
  "glibc 2.36 link target, runs on Mageia 9, Mageia 10/cauldron, "
  "Fedora 39+, RHEL 9+, Ubuntu 24.04+.")
h(2, "H2", "3.3 Slim torch list")
p("Body",
  "The DeepFilterNet bundle drags in PyTorch CPU. Without filtering, Nuitka "
  "tries to compile 2 700+ C modules (including 50 000-line "
  "torch.testing._internal.common_methods_invocations.c) and gcc OOMs.")
p("Body",
  "Both build scripts therefore pass --nofollow-import-to for subtrees we "
  "never reach during inference: torch.testing, torch.distributed, "
  "torch.fx/jit/onnx/optim/profiler, torch._inductor, torch._dynamo, "
  "torch.utils.{tensorboard,benchmark}, torch.nn.{qat,quantized,intrinsic}, "
  "torch.ao, torch.quantization, sympy, networkx. Module count drops to ~600, "
  "build wallclock to ~15 min on 24 cores.")

# 4. Configuration
h(1, "H1", "4. Configuration")
p("Body",
  "Settings are stored in TOML at ~/.picophone/picophone.toml; the file is "
  "auto-migrated from the original PicoPhone.ini if found in the working dir. "
  "Most options are exposed in the in-app PREF dialog (Network / Audio / "
  "Security tabs).")
table(
    ["Section", "Key", "Default", "Notes"],
    [
        ("[net]",    "identity",      "anon",  "shown in title bar / chat / mDNS"),
        ("[net]",    "port",          "11676", "single UDP port — signaling + media"),
        ("[net]",    "bind_v6",       "true",  "dual-stack v6 + v4-mapped"),
        ("[net]",    "autoanswer",    "false", "auto-accept incoming INVITE"),
        ("[net]",    "mdns",          "true",  "LAN auto-discovery"),
        ("[net]",    "encrypt",       "true",  "AES-128-GCM with PSK"),
        ("[net]",    "password",      "",      "PSK; both sides must match"),
        ("[audio]",  "sample_rate_hz","48000", "Opus expects 48 kHz internally"),
        ("[audio]",  "frame_ms",      "20",    "Opus frame; 10/20/40 valid"),
        ("[audio]",  "opus_bitrate_bps", "24000", "VoIP profile"),
        ("[audio]",  "aec",           "true",  "FDAF + Wiener (mutually exclusive with dfn)"),
        ("[audio]",  "dfn",           "false", "DeepFilterNet3 AI mode"),
        ("[audio]",  "vad",           "true",  "silence threshold"),
        ("[audio]",  "rec_level",     "1000",  "mic gain (0..1000 -> 0..1.0)"),
        ("[audio]",  "play_volume",   "1000",  "spk gain (0..1000 -> 0..1.0)"),
        ("[ui]",     "minimize_to_tray", "false", "X / minimize hides to tray"),
        ("[ui]",     "autostart",     "false", "Windows-only registry Run entry"),
        ("[ui]",     "generate_log",  "false", "~/.picophone/picophone.log"),
    ],
    col_widths=["2.5cm", "3.5cm", "2cm", "9cm"],
)

# 5. Network protocol
h(1, "H1", "5. Network protocol")
p("Body",
  "PicoPhone-Py uses a tiny custom protocol on a single UDP port (default "
  "11676). The first byte of every datagram tells the dispatcher which lane "
  "the packet belongs to:")
table(
    ["First byte", "Lane", "Format"],
    [
        ("0x7B  '{'", "Signaling", "JSON datagram"),
        ("0xEF",      "Media",     "RTP-like header (8 B) + AES-GCM payload"),
    ],
    col_widths=["3cm", "4cm", "10cm"],
)
h(2, "H2", "5.1 Signaling messages (JSON)")
table(
    ["t",      "Direction", "Purpose"],
    [
        ("INVITE", "caller -> callee", "open a call, carries nonce_a"),
        ("ACCEPT", "callee -> caller", "accept, carries nonce_b"),
        ("REJECT", "callee -> caller", "decline with reason"),
        ("BYE",    "either",            "hang up / cancel"),
        ("PING",   "either, every 3 s", "keepalive — auto-replied with PONG"),
        ("PONG",   "either",            "keepalive reply"),
        ("MSG",    "either",            "free-form chat text"),
    ],
    col_widths=["2.5cm", "4.5cm", "10cm"],
)
h(2, "H2", "5.2 Media packet")
code_block(
    "+---------+---------+---------+---------+---------+\n"
    "|0xEF| flg|     seq |       timestamp     |  ssrc  |\n"
    "+---------+---------+---------+---------+---------+\n"
    "|         AES-128-GCM(opus_payload, aad=hdr)        |\n"
    "+--------------------------------------------------+\n"
    "  AAD = 8-byte header   nonce = 12 B prepended to ciphertext"
)
h(2, "H2", "5.3 Per-call key derivation")
p("Body",
  "On INVITE the caller sends a 16-byte random nonce_a. The callee replies "
  "with nonce_b in ACCEPT. Both sides then derive a 16-byte AES key:")
code_block(
    "K = HKDF-SHA256(\n"
    "        ikm  = PSK,                       # net.password\n"
    "        salt = nonce_a || nonce_b,        # 32 bytes\n"
    "        info = b'picophone-media/v1',\n"
    "        length = 16)"
)
p("Body",
  "An empty PSK yields an empty key, in which case both sides skip "
  "AES-GCM entirely and exchange plaintext Opus. With matching PSKs each "
  "call has a unique key.")

# 6. Echo cancellation
h(1, "H1", "6. Echo cancellation")
h(2, "H2", "6.1 Classic AEC — FDAF")
p("Body",
  "When PREF -> Audio -> Classic AEC is on, picophone runs a frequency-domain "
  "adaptive filter (overlap-save NLMS):")
bullets([
    "block size 960 samples (20 ms at 48 kHz), FFT size 1920",
    "filter adaptation in the frequency domain with diagonal-loaded power "
    "normalisation (avoids divide-by-zero on quiet bins, prevents narrowband "
    "explosion)",
    "Geigel double-talk detector (max|d| > 1.5 * max|x|) freezes the filter "
    "during near-end speech",
    "Wiener post-filter: per-bin gain = 1 - |Y_hat|^2 / |D|^2, clipped to "
    "[0.02, 1.0] and IIR-smoothed; lets the speaker pass through during "
    "double-talk instead of crushing them",
    "synthetic test ERLE 2-3 s tail = 44.6 dB, near-end-only loss < 1 dB, "
    "double-talk speaker correlation 0.97",
])
h(2, "H2", "6.2 AI mode — DeepFilterNet3")
p("Body",
  "PREF -> Audio -> AI mode swaps the entire AEC stage out for "
  "DeepFilterNet3, the same neural enhancer family used by Krisp / "
  "Microsoft Teams. The capture frame goes raw mic -> DFN3 -> Opus. AI mode "
  "is mutually exclusive with the classic AEC because both stages would "
  "otherwise fight over the same residual.")
p("Body",
  "DFN3 runs on CPU via PyTorch (~1.3 GB) at real-time-factor ~0.03 on a "
  "modern x86 desktop (Ryzen 7900X3D). Latency ~30 ms (one DFN frame).")

# 7. Smoke tests
h(1, "H1", "7. Smoke tests")
p("Body", "All under scripts/, runnable as plain Python:")
table(
    ["Script", "What it covers"],
    [
        ("smoke_keyx.py",            "HKDF derivation, AES-GCM roundtrip, wrong-key reject"),
        ("smoke_signaling.py",       "INVITE/ACCEPT/BYE roundtrip on ::1 with nonce exchange"),
        ("smoke_media.py",           "50 RTP-like packets through MediaSession.feed/make_packet"),
        ("smoke_discovery.py",       "Two pure-Python multicast peers find each other"),
        ("smoke_aec.py",             "FDAF echo cancellation >= 15 dB ERLE on synthetic echo"),
        ("smoke_aec_doubletalk.py",  "Double-talk: near-end voice survives the post-filter"),
        ("smoke_chat.py",            "MSG signaling roundtrip"),
        ("smoke_call_localhost.py",  "Two CallControllers on ::1, full handshake + media"),
        ("smoke_engine_to_engine.py","Multiplexed signaling+media end-to-end (synthetic engine)"),
        ("smoke_gui.py",             "Headless Qt launch + clean teardown (offscreen)"),
    ],
    col_widths=["6cm", "11cm"],
)

# 8. Architecture overview
h(1, "H1", "8. Architecture")
code_block(
    "+---------------------------------------------------+\n"
    "|                 MainWindow (Qt 6)                  |\n"
    "|  CALL/DISC/MSG/OFF/CHAT/LOG/CONF  +  PREF dialog   |\n"
    "+----+-----------------+----------------+-----+------+\n"
    "     |                 |                |     |\n"
    "     |  signals        |  Qt timer      |     |  user actions\n"
    "     v                 v                v     v\n"
    "+---------------------------------------------------+\n"
    "|             CallController (asyncio thread)        |\n"
    "|  state machine: idle/calling/ringing/in-call       |\n"
    "|  keepalive (PING/PONG)                             |\n"
    "+---------+----------------+---------------+---------+\n"
    "          |                |               |\n"
    "          v                v               v\n"
    "  SignalingServer    MediaSession    AudioEngine\n"
    "  (one UDP socket)   (RTP-like+GCM)  (PortAudio + Opus + AEC/DFN)\n"
)

# 9. Version history
h(1, "H1", "9. Version history (git log)")
log = git_log()
if log:
    table(["Hash", "Date", "Subject"],
          [(h_, d, m) for h_, d, m in log[:60]],
          col_widths=["1.8cm", "2.2cm", "13cm"])
else:
    p("Body", "(git not available)")

# 10. Licenses / credits
h(1, "H1", "10. Credits")
bullets([
    "Original PicoPhone 1.65 — Javier Aldazabal, 2009 (vitez.it/picophone)",
    "Opus codec — Xiph.Org / IETF",
    "DeepFilterNet3 — Hendrik Schroeter et al. (Rikorose/DeepFilterNet)",
    "Qt 6 / PySide6 — The Qt Company",
    "PortAudio (sounddevice) — Ross Bencina, Phil Burk",
    "Nuitka — Kay Hayen",
])

# Save
OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(str(OUT))
print(f"wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")
