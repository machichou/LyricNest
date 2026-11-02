"""Draws assets/icon.ico once; build.bat runs it so the icon lives in code rather than as an opaque binary."""
import os

from PIL import Image, ImageDraw

os.makedirs("assets", exist_ok=True)
S = 512
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((16, 16, S - 16, S - 16), radius=96, fill="#171717", outline="#404040", width=12)
white = "#ffffff"
d.ellipse((130, 318, 240, 410), fill=white)
d.rectangle((216, 130, 244, 366), fill=white)
d.polygon([(216, 110), (244, 110), (390, 158), (390, 214), (244, 170), (216, 170)], fill=white)
for y, x in ((270, 395), (330, 395), (390, 330)):
    d.rounded_rectangle((280, y, x, y + 26), radius=13, fill="#a3a3a3")
im.save("assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
