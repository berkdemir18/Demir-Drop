"""Tanıtım görsellerini gerçek uygulamadan çeker, ama sahte token ve boş klasörle:
gerçek adres, gerçek dosya ya da kişisel veri görüntüye girmez."""
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image
from playwright.sync_api import sync_playwright

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
sys.dont_write_bytecode = True
import server

MASKOT = Path(sys.argv[1])  # Demir Maskot/paket-v1/tekli/his-life
PORT, TOKEN = 18800, "ornek-adres"
server.lan_ip = lambda: "192.168.1.20"
d = Path(tempfile.mkdtemp(prefix="dd-vitrin-"))
cikti = KOK / "docs" / "img"
kok = f"http://127.0.0.1:{PORT}/k/{TOKEN}/"

merkez = server.Merkez(TOKEN, d / "gelen", pc_adi="Bilgisayarım")
sunucu = server.baslat(merkez, PORT, lambda: {"ip": "192.168.1.20", "port": 8765, "kural": True, "baslangic": False})

# Galeri, Demir'in kendi sahneleriyle dolsun
for ad, kaynak in (("IMG_2041.jpg", "07-cay.png"), ("IMG_2042.jpg", "08-gezegen.png"), ("IMG_2043.jpg", "02-pit-stop.png"), ("IMG_2044.jpg", "04-gece-03.png")):
    g = Image.open(MASKOT / kaynak).convert("RGB")
    yol = d / ad
    g.save(yol, quality=95)
    urlopen(Request(kok + "api/yukle?ad=" + ad, data=yol.read_bytes(), method="POST")).read()

with sync_playwright() as p:
    c = p.chromium.launch()
    s = c.new_page(viewport={"width": 1120, "height": 720}, device_scale_factor=2, color_scheme="dark")
    s.goto(kok + "pc/")
    s.wait_for_selector(".kart img")
    merkez.dosya_ekle(d / "IMG_2041.jpg")
    time.sleep(2.2)
    s.screenshot(path=str(cikti / "pc.png"))
    c.close()
    merkez.temizle()

    w = p.webkit.launch()
    t = w.new_context(**p.devices["iPhone 15"], color_scheme="dark").new_page()
    t.goto(kok)
    t.wait_for_function("document.getElementById('durum').textContent.includes('bağlı')")
    time.sleep(.6)
    t.screenshot(path=str(cikti / "telefon-ana.png"))
    merkez.dosya_ekle(d / "IMG_2042.jpg")
    t.wait_for_selector("#onay.acik")
    time.sleep(.6)
    t.screenshot(path=str(cikti / "telefon-kart.png"))
    w.close()

sunucu.shutdown()
sunucu.server_close()
merkez.kapat()
shutil.rmtree(d, ignore_errors=True)

for ad in ("pc", "telefon-ana", "telefon-kart"):
    y = cikti / f"{ad}.png"
    g = Image.open(y).convert("RGB")
    if ad == "pc":
        g.thumbnail((1680, 1680))
    else:
        g.thumbnail((780, 1700))
    g.save(y.with_suffix(".webp"), quality=86, method=6)
    if ad != "pc":
        y.unlink()
print("hazır")
