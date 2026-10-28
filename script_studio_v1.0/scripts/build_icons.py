"""Rebuild icons matching static/app-icon.svg (requires Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw
root = Path(__file__).resolve().parents[1]
scale = 4
im = Image.new("RGBA", (256 * scale, 256 * scale))
draw = ImageDraw.Draw(im)
def box(coords):
    return tuple(v * scale for v in coords)
def polygon(points, fill):
    draw.polygon([(x * scale, y * scale) for x,y in points], fill=fill)
draw.rounded_rectangle(box((0,0,255,255)), radius=56*scale, fill="#252525")
draw.rounded_rectangle(box((60,48,180,200)), radius=16*scale, fill="#faf9f6")
polygon([(152,48),(180,76),(180,48)], "#252525")
polygon([(152,48),(152,76),(180,76)], "#d9d7d2")
for y,end in [(93,147),(115,135),(137,117)]:
    draw.line(box((85,y,end,y)), fill="#55534f", width=9*scale)
    for x in [85,end]:
        draw.ellipse(box((x-4.5,y-4.5,x+4.5,y+4.5)),fill="#55534f")
draw.ellipse(box((123,126,215,218)),fill="#252525")
draw.ellipse(box((131,134,207,210)),fill="#dd754e")
polygon([(158,151),(187,172),(158,193)],"#fff")
im = im.resize((256,256),Image.Resampling.LANCZOS)
im.save(root/"static"/"app-icon.png")
im.resize((180,180),Image.Resampling.LANCZOS).save(root/"static"/"apple-touch-icon.png")
im.save(root/"static"/"favicon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("Created app-icon.png, apple-touch-icon.png, favicon.ico")
