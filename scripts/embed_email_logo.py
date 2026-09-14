import base64
from pathlib import Path

from PIL import Image

src = Image.open("pulse_hwm/assets/icons/pulse.png").convert("RGBA")
logo = src.resize((160, 160), Image.Resampling.NEAREST)
q = logo.quantize(colors=16, method=Image.FASTOCTREE).convert("RGBA")
q.save("branding_160c.png", optimize=True)
b64 = base64.b64encode(Path("branding_160c.png").read_bytes()).decode()
worker = Path("workers/src/worker.js")
text = worker.read_text(encoding="utf-8")
marker = "// ── email (Brevo HTTP API) ─────────────────────────────────────────────"
block = (
    "// ── email (Brevo HTTP API) ─────────────────────────────────────────────\n\n"
    "// the branded icon served from /branding/logo.png (16-color quantized,\n"
    "// 160px ≈ 4 KB) — embedded as base64 so the worker needs no external asset\n"
    "# fmt: off\n"
    f'EMAIL_LOGO_PNG_B64 =\n "{b64}";\n'
    "# fmt: on\n\n"
)
assert marker in text
worker.write_text(text.replace(marker, block, 1), encoding="utf-8")
print("embedded", len(b64), "b64 chars into worker.js")
