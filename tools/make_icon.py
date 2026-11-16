"""Draws gazehop's icon (an eye whose iris is a focused window) and writes PNG + ICO files.
Run from the project root:  python tools/make_icon.py   (needs Pillow)"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "gazehop", "assets")


def draw(size=1024, paused=False):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (28, 32, 48, 255) if not paused else (70, 72, 80, 255)
    accent = (72, 214, 150, 255) if not paused else (160, 162, 170, 255)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=bg)
    # the eye: two arcs meeting at the corners
    cx, cy, half, lift = s / 2, s / 2, s * 0.36, s * 0.23
    lens = Image.new("L", (s, s), 0)
    ld = ImageDraw.Draw(lens)
    r = (half * half + lift * lift) / (2 * lift)
    ld.ellipse((cx - r, cy + lift - 2 * r, cx + r, cy + lift), fill=255)
    lower = Image.new("L", (s, s), 0)
    ImageDraw.Draw(lower).ellipse((cx - r, cy - lift, cx + r, cy - lift + 2 * r), fill=255)
    from PIL import ImageChops
    eye = ImageChops.multiply(lens, lower)
    img.paste(Image.new("RGBA", (s, s), (245, 247, 250, 255)), (0, 0), eye)
    # the iris is a little window: title bar + body
    w, h = s * 0.26, s * 0.22
    x0, y0 = cx - w / 2, cy - h / 2
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=int(s * 0.035), fill=accent)
    d.rounded_rectangle((x0 + s * 0.03, y0 + s * 0.075, x0 + w - s * 0.03, y0 + h - s * 0.03),
                        radius=int(s * 0.015), fill=bg)
    for k in range(2):
        d.ellipse((x0 + s * (0.03 + 0.04 * k), y0 + s * 0.025, x0 + s * (0.055 + 0.04 * k), y0 + s * 0.05), fill=bg)
    return img


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, "tray.png"))
    draw(paused=True).resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, "tray-paused.png"))
    big.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    print("icons written to", os.path.normpath(OUT))
