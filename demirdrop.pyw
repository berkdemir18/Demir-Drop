"""Demir Drop PC penceresi. Argümanla açılırsa dosyaları rafa koyar (Gönder menüsü)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import queue
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

KOK = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(KOK))

import pystray
import webview
from PIL import Image
from webview.dom import DOMEventHandler

import kurulum
import server

BASLIK = "Demir Drop"


def calisana_gonder(token: str, yollar: list[str]) -> bool:
    """Çalışan kopya varsa ona devreder. Başarılıysa bu kopya kapanır."""
    istek = urllib.request.Request(
        f"http://127.0.0.1:{server.PORT}/k/{token}/api/yerel",
        data=json.dumps({"yollar": yollar}).encode(), method="POST")
    try:
        with urllib.request.urlopen(istek, timeout=3) as y:
            return y.status == 200
    except OSError:
        return False


def panoya_yaz(metin: str) -> None:
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = w.HGLOBAL
    k32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [w.HGLOBAL]
    k32.GlobalUnlock.argtypes = [w.HGLOBAL]
    u32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
    u32.SetClipboardData.restype = w.HANDLE
    veri = metin.encode("utf-16-le") + b"\0\0"
    for _ in range(10):  # pano başkasında olabilir, kısa dene
        if u32.OpenClipboard(None):
            break
        time.sleep(.05)
    else:
        raise OSError("pano meşgul")
    try:
        u32.EmptyClipboard()
        tutamak = k32.GlobalAlloc(0x0002, len(veri))
        hedef = k32.GlobalLock(tutamak)
        ctypes.memmove(hedef, veri, len(veri))
        k32.GlobalUnlock(tutamak)
        u32.SetClipboardData(13, tutamak)  # CF_UNICODETEXT
    finally:
        u32.CloseClipboard()


class Arayuz:
    """Sayfadaki düğmelerin çağırdığı işlemler (window.pywebview.api)."""

    def __init__(self, uygulama: "Uygulama"):
        self._u = uygulama

    def dosya_sec(self):
        secilen = self._u.pencere.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        for yol in secilen or ():
            self._u.merkez.dosya_ekle(yol)

    def klasor_ac(self):
        self._u.klasor.mkdir(parents=True, exist_ok=True)
        os.startfile(self._u.klasor)

    def ac(self, gelen_id):
        k = self._u.merkez.gelen(gelen_id)
        if k and Path(k["yol"]).exists():
            os.startfile(k["yol"])

    def metin_at(self, metin):
        self._u.merkez.metin_ekle(metin)

    def raf_bosalt(self):
        self._u.merkez.temizle()

    def baslangic(self, acik):
        kurulum.baslangic_ayarla(bool(acik))
        self._u.baslangicta = bool(acik)

    def kural_iste(self):
        self._u.kural_iste()


class Uygulama:
    def __init__(self, token: str, ilk_yollar: list[str], gizli: bool):
        self.token = token
        self.gizli = gizli
        self.ilk_yollar = ilk_yollar
        self.olaylar: queue.Queue = queue.Queue()
        self.klasor = kurulum.gelen_klasoru()
        self.merkez = server.Merkez(token, self.klasor, lambda t, v: self.olaylar.put((t, v)),
                                   pc_adi=kurulum.ayarlar().get("pc_adi", "Bilgisayarım"))
        self.kural = kurulum.kural_var_mi()
        self.baslangicta = kurulum.baslangicta_mi()
        self.sunucu = server.baslat(self.merkez, pc_durumu=self._durum)
        self.bekleyen_bildirim: list[str] = []
        self.son_bildirim_istegi = 0.0
        self.cikiliyor = False
        self.pencere = webview.create_window(
            BASLIK, f"http://127.0.0.1:{server.PORT}/k/{token}/pc/", js_api=Arayuz(self),
            width=960, height=640, min_size=(820, 540), hidden=gizli, background_color="#111113")
        self.pencere.events.closing += self._kapatiliyor
        self.pencere.events.loaded += self._yuklendi
        self.pencere.events.shown += self._gosterildi

    def _durum(self) -> dict:
        return {"ip": server.lan_ip(), "port": server.PORT, "kural": self.kural, "baslangic": self.baslangicta}

    # ---- pencere yaşamı
    def _kapatiliyor(self):
        if self.cikiliyor:
            return True
        self.pencere.hide()  # kapatınca tepside çalışmaya devam eder
        return False

    def _gosterildi(self):
        hwnd = ctypes.windll.user32.FindWindowW(None, BASLIK)
        if not hwnd:
            return
        ikon = ctypes.windll.user32.LoadImageW(None, str(KOK / "ikon.ico"), 1, 0, 0, 0x10 | 0x40)
        for tur in (0, 1):
            ctypes.windll.user32.SendMessageW(hwnd, 0x80, tur, ikon)
        koyu = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(koyu), ctypes.sizeof(koyu))

    def _yuklendi(self):
        belge = self.pencere.dom.document
        belge.events.dragenter += DOMEventHandler(lambda e: None, True, True)
        belge.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
        belge.events.drop += DOMEventHandler(self._birakildi, True, True)

    def _birakildi(self, olay):
        for dosya in olay.get("dataTransfer", {}).get("files", []):
            yol = dosya.get("pywebviewFullPath")
            if yol:
                self.merkez.dosya_ekle(yol)

    def _goster(self):
        self.pencere.show()
        self.pencere.restore()
        hwnd = ctypes.windll.user32.FindWindowW(None, BASLIK)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    # ---- tepsi
    def _tepsi_kur(self):
        kuyruk = lambda *a: self.olaylar.put(a)
        menu = pystray.Menu(
            pystray.MenuItem("Aç", lambda: kuyruk("goster", None), default=True),
            pystray.MenuItem("Gelenler klasörü", lambda: kuyruk("klasor", None)),
            pystray.MenuItem("Çık", lambda: kuyruk("cik", None)),
        )
        self.tepsi = pystray.Icon("DemirDrop", Image.open(KOK / "ikon.png"), BASLIK, menu)
        self.tepsi.run_detached()

    def _bildir(self, baslik: str, mesaj: str):
        try:
            self.tepsi.notify(mesaj[:200], baslik)
        except Exception:
            traceback.print_exc()

    def _gelen_bildirimi(self):
        """Peş peşe gelen 20 fotoğraf için 20 bildirim atmasın."""
        if not self.bekleyen_bildirim or time.time() - self.son_bildirim_istegi < 1.5:
            return
        adlar, self.bekleyen_bildirim = self.bekleyen_bildirim, []
        self._bildir("iPhone'dan geldi", adlar[0] if len(adlar) == 1 else f"{len(adlar)} dosya")

    # ---- olay döngüsü (arka iş parçacığı)
    def _olay_dongusu(self):
        while True:
            try:
                self._olay(*self.olaylar.get(timeout=.25))
            except queue.Empty:
                pass
            except Exception:
                traceback.print_exc()
            self._gelen_bildirimi()
            if self.cikiliyor:
                return

    def _olay(self, tur, veri):
        if tur == "geldi":
            self.bekleyen_bildirim.append(veri["ad"])
            self.son_bildirim_istegi = time.time()
        elif tur == "metin":
            panoya_yaz(veri)
            self._bildir("Metin panoya kopyalandı", veri)
        elif tur == "hata":
            self._bildir(BASLIK, veri)
        elif tur == "goster":
            self._goster()
        elif tur == "klasor":
            self.klasor.mkdir(parents=True, exist_ok=True)
            os.startfile(self.klasor)
        elif tur == "cik":
            self._cik()

    def kural_iste(self):
        kurulum.kural_ekle(server.PORT)
        threading.Thread(target=self._kural_bekle, daemon=True).start()

    def _kural_bekle(self):
        for _ in range(30):
            time.sleep(1)
            if kurulum.kural_var_mi():
                self.kural = True
                return

    def _cik(self):
        self.cikiliyor = True
        self.tepsi.stop()
        self.sunucu.shutdown()
        self.merkez.kapat()
        self.pencere.destroy()

    # ---- başlangıç (pencere döngüsü açıldıktan sonra çalışır)
    def _ilk_is(self):
        self._tepsi_kur()
        threading.Thread(target=self._olay_dongusu, daemon=True).start()
        for yol in self.ilk_yollar:
            self.merkez.dosya_ekle(yol)
        if not self.kural:
            self.kural_iste()

    def calistir(self):
        webview.start(self._ilk_is, gui="edgechromium")


def main():
    kurulum.AYAR.mkdir(parents=True, exist_ok=True)
    sys.stderr = sys.stdout = open(kurulum.AYAR / "hata.log", "a", encoding="utf-8", buffering=1)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DemirDrop")

    argumanlar = sys.argv[1:]
    gizli = "--tepsi" in argumanlar
    yollar = [a for a in argumanlar if a != "--tepsi"]
    token = kurulum.ayarlar()["token"]
    if calisana_gonder(token, yollar):
        return
    kurulum.kur()
    try:
        Uygulama(token, yollar, gizli).calistir()
    except OSError as e:
        ctypes.windll.user32.MessageBoxW(None, f"{server.PORT} portu açılamadı: {e}", BASLIK, 0x10)
        raise


if __name__ == "__main__":
    main()
