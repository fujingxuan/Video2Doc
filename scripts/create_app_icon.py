"""Render the code-native Video2Doc mark at Windows icon resolutions."""
from pathlib import Path

from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
size = 512
image = Image.new("RGBA", (size, size))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 511, 511), radius=136, fill="#16796d")
draw.rounded_rectangle((96, 136, 416, 376), radius=48, outline="white", width=24)
draw.line([(224, 192), (328, 256), (224, 320), (224, 192)], fill="white", width=24, joint="curve")
image.save(root / "frontend/public/video2doc.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
