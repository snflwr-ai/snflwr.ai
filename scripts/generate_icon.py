#!/usr/bin/env python3
"""One-time icon generator. Run: python scripts/generate_icon.py"""
import math, os, sys
try:
    from PIL import Image, ImageDraw
except ImportError:
    print("pip install Pillow  (needed only for icon generation)")
    sys.exit(1)

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
cx, cy = SIZE // 2, SIZE // 2 - 10

# Petals (golden yellow ellipses rotated around center)
petal_color = (255, 191, 0, 255)      # snflwr gold
for i in range(12):
    angle = math.radians(i * 30)
    px = cx + int(55 * math.cos(angle))
    py = cy + int(55 * math.sin(angle))
    # Draw petal as ellipse
    petal_w, petal_h = 30, 60
    bbox = [px - petal_w//2, py - petal_h//2, px + petal_w//2, py + petal_h//2]
    # Rotate by drawing multiple overlapping circles along the angle
    for t in range(0, 50, 3):
        tx = cx + int((35 + t) * math.cos(angle))
        ty = cy + int((35 + t) * math.sin(angle))
        r = max(4, 18 - t // 3)
        draw.ellipse([tx-r, ty-r, tx+r, ty+r], fill=petal_color)

# Center (brown disc)
draw.ellipse([cx-38, cy-38, cx+38, cy+38], fill=(101, 67, 33, 255))
draw.ellipse([cx-30, cy-30, cx+30, cy+30], fill=(139, 90, 43, 255))

# Small seeds pattern in center
seed_color = (80, 50, 20, 255)
for i in range(8):
    for j in range(3):
        angle = math.radians(i * 45 + j * 15)
        r = 8 + j * 8
        sx = cx + int(r * math.cos(angle))
        sy = cy + int(r * math.sin(angle))
        draw.ellipse([sx-2, sy-2, sx+2, sy+2], fill=seed_color)

# Short green stem
draw.rectangle([cx-8, cy+38, cx+8, cy+80], fill=(76, 153, 0, 255))
# Leaf
draw.ellipse([cx+8, cy+50, cx+40, cy+68], fill=(76, 153, 0, 255))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")

os.makedirs(ASSETS_DIR, exist_ok=True)
png_path = os.path.join(ASSETS_DIR, "icon.png")
ico_path = os.path.join(ASSETS_DIR, "icon.ico")

img.save(png_path, "PNG")
print(f"Saved {png_path}")

# Windows .ico (multiple sizes)
ico_sizes = [(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)]
img.save(ico_path, format="ICO", sizes=ico_sizes)
print(f"Saved {ico_path}")
