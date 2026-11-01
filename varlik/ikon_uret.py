"""logo.svg'den ikon.png ve ikon.ico üretir. Geliştirme aracı: python varlik/ikon_uret.py"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

KOK = Path(__file__).resolve().parent.parent
svg = (KOK / "varlik" / "logo.svg").read_text("utf-8")

with sync_playwright() as p:
    b = p.chromium.launch()
    s = b.new_page(viewport={"width": 1024, "height": 1024})
    s.set_content(f"<style>*{{margin:0}}svg{{width:1024px;height:1024px;display:block}}body{{background:transparent}}</style>{svg}")
    s.screenshot(path=str(KOK / "varlik" / "logo-1024.png"), omit_background=True)
    b.close()

ana = Image.open(KOK / "varlik" / "logo-1024.png").convert("RGBA")
ana.resize((180, 180), Image.LANCZOS).save(KOK / "ikon.png")
ana.resize((256, 256), Image.LANCZOS).save(KOK / "ikon.ico", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
for n in (16, 32, 64, 180):
    print(n, "ok")
