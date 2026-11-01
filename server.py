"""Demir Drop çekirdeği: yerel ağda token korumalı aktarım sunucusu.

Her istek /k/<token>/ altından geçer; token'ı bilmeyen 404 görür.
Dosya baytlarına asla dokunmaz: ne gelirse diske o yazılır, ne varsa o gider.
Arayüzden bağımsızdır, olayları `on_event(tur, veri)` ile bildirir.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pillow_heif
import segno
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

PORT = 8765
KOK = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PARCA = 1024 * 1024
METIN_SINIRI = 1024 * 1024
BAGLI_SURESI = 7  # telefon bu kadar saniyedir sormadıysa "bağlı" sayma
GOSTERILEBILIR = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif"}
VIDEO = {"video/mp4", "video/quicktime", "video/x-m4v"}
MEDYA = GOSTERILEBILIR | VIDEO
GORSEL_UZANTI = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
AYRILMIS = {"CON", "PRN", "AUX", "NUL"} | {f"{a}{i}" for a in ("COM", "LPT") for i in range(1, 10)}
for _tur, _uz in (("image/heic", ".heic"), ("image/heif", ".heif"), ("image/webp", ".webp"),
                  ("video/quicktime", ".mov"), ("video/x-m4v", ".m4v")):
    mimetypes.add_type(_tur, _uz)


def guvenli_ad(ham: str) -> str:
    """Telefondan gelen dosya adını Windows'ta güvenli bir ada çevirir."""
    ad = unicodedata.normalize("NFC", ham).replace("\\", "/").rsplit("/", 1)[-1]
    ad = "".join(c for c in ad if c not in '<>:"|?*' and ord(c) >= 32).strip(" .")
    if not ad:
        return "dosya"
    kok, uzanti = os.path.splitext(ad)
    if kok.upper() in AYRILMIS:
        kok = f"_{kok}"
    return kok[:180] + uzanti[:20]


def lan_ip() -> str:
    """Varsayılan rotadaki yerel IP. UDP connect paket göndermez."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def medya_bilgisi(yol: Path) -> dict:
    """Çözünürlük ve biçim; görüntü bozuldu mu sorusunun kanıtı."""
    if yol.suffix.lower() not in GORSEL_UZANTI:
        return {"bicim": yol.suffix.lstrip(".").upper() or None, "cozunurluk": None}
    try:
        with Image.open(yol) as g:
            en, boy = ImageOps.exif_transpose(g).size if g.format in ("JPEG", "HEIF") else g.size
            return {"bicim": "HEIC" if g.format == "HEIF" else g.format, "cozunurluk": f"{en}×{boy}"}
    except Exception:
        return {"bicim": yol.suffix.lstrip(".").upper(), "cozunurluk": None}


def kucuk_resim(yol: Path, kenar: int = 360) -> bytes | None:
    """Yalnızca PC penceresindeki önizleme için; asıl dosyaya dokunmaz."""
    try:
        with Image.open(yol) as g:
            g = ImageOps.exif_transpose(g).convert("RGB")
            g.thumbnail((kenar, kenar))
            tampon = io.BytesIO()
            g.save(tampon, "JPEG", quality=82)
            return tampon.getvalue()
    except Exception:
        return None


class DiskDolu(Exception):
    pass


class EksikGovde(Exception):
    pass


class CokParca:
    """multipart/form-data'yı belleğe almadan akıtır (iPhone Kestirmeler böyle yollar)."""

    def __init__(self, kaynak, sinir: bytes, uzunluk: int):
        self.kaynak, self.kalan = kaynak, uzunluk
        self.ayrac = b"\r\n--" + sinir
        self.tampon = b"\r\n"
        self.parca_bitti = False

    def _doldur(self) -> bool:
        adet = min(PARCA, self.kalan)
        if not adet:
            return False
        veri = self.kaynak.read(adet)
        if not veri:
            raise EksikGovde()
        self.kalan -= len(veri)
        self.tampon += veri
        return True

    def read(self, _n: int = PARCA) -> bytes:
        """Geçerli parçanın gövdesi; ayraca gelince b'' döner."""
        while not self.parca_bitti:
            i = self.tampon.find(self.ayrac)
            if i >= 0:
                veri, self.tampon = self.tampon[:i], self.tampon[i:]
                self.parca_bitti = True
                if veri:
                    return veri
                break
            guvenli = len(self.tampon) - len(self.ayrac) + 1
            if guvenli > PARCA // 2:
                veri, self.tampon = self.tampon[:guvenli], self.tampon[guvenli:]
                return veri
            if not self._doldur():
                raise EksikGovde()
        return b""

    def parcalar(self):
        """Her parça için başlık sözlüğü verir; tüketilmeyen gövde atlanır."""
        while True:
            self.parca_bitti = False
            while self.read():
                pass
            self.tampon = self.tampon[len(self.ayrac):]
            while len(self.tampon) < 2:
                if not self._doldur():
                    raise EksikGovde()
            if self.tampon.startswith(b"--"):
                return
            self.tampon = self.tampon.split(b"\n", 1)[-1] if b"\n" in self.tampon[:4] else self.tampon
            while b"\r\n\r\n" not in self.tampon and not self.tampon.startswith(b"\r\n"):
                if len(self.tampon) > 32 * 1024 or not self._doldur():
                    raise EksikGovde()
            if self.tampon.startswith(b"\r\n"):
                ham, self.tampon = b"", self.tampon[2:]
            else:
                ham, self.tampon = self.tampon.split(b"\r\n\r\n", 1)
            basliklar = {}
            for satir in ham.decode("utf-8", "replace").split("\r\n"):
                if ":" in satir:
                    k, v = satir.split(":", 1)
                    basliklar[k.strip().lower()] = v.strip()
            self.parca_bitti = False
            yield basliklar


def parca_adi(basliklar: dict) -> str | None:
    cd = basliklar.get("content-disposition", "")
    m = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", cd)
    if m:
        return unquote(m[1])
    m = re.search(r'filename="([^"]*)"', cd) or re.search(r"filename=([^;]+)", cd)
    if not m:
        return None
    ad = m[1].strip()
    if "." not in ad:  # Kestirmeler bazen uzantısız yollar, türden tamamla
        ad += mimetypes.guess_extension(basliklar.get("content-type", "").split(";")[0].strip()) or ""
    return ad


class Merkez:
    """Raf (PC'den telefona bekleyenler) ve gelenler (telefondan PC'ye)."""

    def __init__(self, token: str, klasor: Path, on_event=None, pc_adi: str = "Bilgisayarım"):
        self.token = token
        self.pc_adi = pc_adi
        self.klasor = Path(klasor)
        self.on_event = on_event or (lambda tur, veri: None)
        self._kilit = threading.Lock()
        self._raf: dict[str, dict] = {}
        self._gelenler: OrderedDict[str, dict] = OrderedDict()
        self._kucukler: OrderedDict[str, bytes] = OrderedDict()
        self.surum = 1
        self.telefon_son = 0.0
        self._gecici = Path(tempfile.mkdtemp(prefix="demirdrop-"))
        self._eskileri_yukle()

    # ---- raf
    def _ekle(self, oge: dict) -> dict:
        oge.update(id=secrets.token_hex(8), zaman=time.time(), alindi=False)
        with self._kilit:
            self._raf[oge["id"]] = oge
            self.surum += 1
        return oge

    def dosya_ekle(self, yol: str | Path) -> None:
        yol = Path(yol)
        if yol.is_dir():
            threading.Thread(target=self._klasor_ekle, args=(yol,), daemon=True).start()
        elif yol.is_file():
            self._ekle({"tur": "dosya", "ad": yol.name, "boyut": yol.stat().st_size, "yol": str(yol), "gecici": False})
            self.on_event("rafa", yol.name)
        else:
            self.on_event("hata", f"Bulunamadı: {yol.name}")

    def _klasor_ekle(self, yol: Path) -> None:
        hedef = self._gecici / f"{secrets.token_hex(4)}.zip"
        try:
            with zipfile.ZipFile(hedef, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for f in yol.rglob("*"):
                    if f.is_file():
                        z.write(f, f.relative_to(yol.parent))
        except OSError as e:
            hedef.unlink(missing_ok=True)
            self.on_event("hata", f"{yol.name} paketlenemedi: {e}")
            return
        ad = f"{yol.name}.zip"
        self._ekle({"tur": "dosya", "ad": ad, "boyut": hedef.stat().st_size, "yol": str(hedef), "gecici": True})
        self.on_event("rafa", ad)

    def metin_ekle(self, metin: str) -> None:
        self._ekle({"tur": "metin", "ad": "Metin", "metin": metin})
        self.on_event("rafa", metin[:50])

    def kaldir(self, oge_id: str) -> bool:
        with self._kilit:
            oge = self._raf.pop(oge_id, None)
            if oge:
                self.surum += 1
        if oge and oge.get("gecici"):
            Path(oge["yol"]).unlink(missing_ok=True)
        return bool(oge)

    def temizle(self) -> None:
        for oge_id in list(self._raf):
            self.kaldir(oge_id)

    def kapat(self) -> None:
        shutil.rmtree(self._gecici, ignore_errors=True)

    def oge(self, oge_id: str) -> dict | None:
        with self._kilit:
            return self._raf.get(oge_id)

    def alindi(self, oge_id: str) -> None:
        with self._kilit:
            oge = self._raf.get(oge_id)
            if not oge or oge["alindi"]:
                return
            oge["alindi"] = True
            self.surum += 1
        self.on_event("alindi", oge["ad"])

    def liste(self) -> dict:
        with self._kilit:
            ogeler = sorted(self._raf.values(), key=lambda o: o["zaman"], reverse=True)
            cikti = []
            for o in ogeler:
                kayit = {k: o.get(k) for k in ("id", "tur", "ad", "boyut", "metin", "alindi")}
                tur = mimetypes.guess_type(o["ad"])[0] if o["tur"] == "dosya" else None
                kayit["gorsel"] = tur in GOSTERILEBILIR
                kayit["medya"] = tur in MEDYA
                cikti.append(kayit)
            return {"surum": self.surum, "pc": self.pc_adi, "ogeler": cikti}

    def bekleyen_medya(self) -> list[dict]:
        """Kestirmenin Fotoğraflar'a kaydedeceği, henüz alınmamış foto/videolar."""
        return [o for o in reversed(self.liste()["ogeler"]) if o["medya"] and not o["alindi"]]

    # ---- gelenler
    def _eskileri_yukle(self) -> None:
        if not self.klasor.exists():
            return
        dosyalar = [f for f in self.klasor.iterdir() if f.is_file() and not f.name.startswith(".")]
        for f in sorted(dosyalar, key=lambda p: p.stat().st_mtime)[-60:]:
            self._gelen_kaydi(f)

    def _gelen_kaydi(self, yol: Path) -> dict:
        kayit = {"id": secrets.token_hex(8), "ad": yol.name, "yol": str(yol), "boyut": yol.stat().st_size,
                 "zaman": yol.stat().st_mtime, **medya_bilgisi(yol)}
        kayit["gorsel"] = yol.suffix.lower() in GORSEL_UZANTI and kayit["cozunurluk"] is not None
        with self._kilit:
            self._gelenler[kayit["id"]] = kayit
            while len(self._gelenler) > 60:
                self._gelenler.popitem(last=False)
            self.surum += 1
        return kayit

    def gelenler(self) -> list[dict]:
        with self._kilit:
            kayitlar = list(self._gelenler.values())
        return [k for k in reversed(kayitlar) if Path(k["yol"]).exists()]

    def gelen(self, gelen_id: str) -> dict | None:
        with self._kilit:
            return self._gelenler.get(gelen_id)

    def kucuk(self, gelen_id: str) -> bytes | None:
        k = self.gelen(gelen_id)
        if not k or not k["gorsel"]:
            return None
        if gelen_id not in self._kucukler:
            veri = kucuk_resim(Path(k["yol"]))
            if veri is None:
                return None
            self._kucukler[gelen_id] = veri
            while len(self._kucukler) > 80:
                self._kucukler.popitem(last=False)
        return self._kucukler.get(gelen_id)

    def gelen_kaydet(self, ad: str, okuyucu, uzunluk: int | None) -> dict:
        """Akışı .part dosyasına yazar; eksik gelirse hiçbir iz bırakmaz.
        uzunluk None ise okuyucu b'' dönene kadar okur (multipart)."""
        self.klasor.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.klasor).free < (uzunluk or 0) + 50 * PARCA:
            raise DiskDolu()
        parca = self.klasor / f".{secrets.token_hex(8)}.part"
        try:
            kalan = uzunluk
            with open(parca, "xb") as f:
                while kalan is None or kalan > 0:
                    blok = okuyucu.read(PARCA if kalan is None else min(PARCA, kalan))
                    if not blok:
                        if kalan is None:
                            break
                        raise EksikGovde()
                    f.write(blok)
                    if kalan is not None:
                        kalan -= len(blok)
            hedef = self._yerlestir(parca, guvenli_ad(ad))
        finally:
            parca.unlink(missing_ok=True)
        kayit = self._gelen_kaydi(hedef)
        self.on_event("geldi", kayit)
        return kayit

    def _yerlestir(self, parca: Path, ad: str) -> Path:
        kok, uzanti = os.path.splitext(ad)
        n = 0
        while True:
            hedef = self.klasor / (ad if n == 0 else f"{kok} ({n}){uzanti}")
            try:
                os.rename(parca, hedef)  # Windows'ta var olanın üstüne yazmaz
                return hedef
            except FileExistsError:
                n += 1

    # ---- telefon durumu
    def telefon_gordu(self) -> None:
        self.telefon_son = time.time()

    def telefon_bagli(self) -> bool:
        return time.time() - self.telefon_son < BAGLI_SURESI


class Isleyici(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 90
    server_version = "DemirDrop"
    sys_version = ""
    merkez: Merkez
    pc_durumu = staticmethod(lambda: {})

    def log_message(self, *_):
        pass

    # ---- yanıt yardımcıları
    def _baslik(self, kod: int, tur: str, uzunluk: int, ek: dict | None = None) -> None:
        self.send_response(kod)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(uzunluk))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (ek or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _gonder(self, kod: int, govde: bytes, tur: str, ek: dict | None = None) -> None:
        self._baslik(kod, tur, len(govde), ek)
        if self.command != "HEAD":
            self.wfile.write(govde)

    def _json(self, veri, kod: int = 200) -> None:
        self._gonder(kod, json.dumps(veri, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _hata(self, kod: int, mesaj: str = "yok") -> None:
        self.close_connection = True
        self._gonder(kod, mesaj.encode(), "text/plain; charset=utf-8", {"Connection": "close"})

    def _yerelden_mi(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _rota(self) -> tuple[list[str], dict] | None:
        """Token doğruysa token sonrası yol parçalarını döner."""
        parca = urlsplit(self.path)
        yol = parca.path.split("/")
        if len(yol) < 3 or yol[1] != "k" or not secrets.compare_digest(yol[2].encode(), self.merkez.token.encode()):
            return None
        return yol[3:], parse_qs(parca.query, keep_blank_values=True)

    def _uzunluk(self) -> int | None:
        try:
            uzunluk = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._hata(411, "uzunluk yok")
            return None
        if uzunluk < 0:
            self._hata(400, "uzunluk bozuk")
            return None
        return uzunluk

    def _govdeyi_oku(self, sinir: int) -> bytes | None:
        uzunluk = self._uzunluk()
        if uzunluk is None:
            return None
        if uzunluk > sinir:
            self._hata(413, "çok büyük")
            return None
        govde = self.rfile.read(uzunluk)
        if len(govde) != uzunluk:
            self._hata(400, "eksik")
            return None
        return govde

    # ---- GET / HEAD
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        rota = self._rota()
        if rota is None:
            return self._hata(404)
        yol, sorgu = rota
        if not yol:
            return self._gonder(301, b"", "text/plain", {"Location": f"/k/{self.merkez.token}/"})
        if yol == [""]:
            return self._gonder(200, (KOK / "telefon.html").read_bytes(), "text/html; charset=utf-8")
        if yol == ["ikon.png"]:
            return self._gonder(200, (KOK / "ikon.png").read_bytes(), "image/png")
        if yol == ["api", "ogeler"]:
            if not self._yerelden_mi():
                self.merkez.telefon_gordu()
            return self._json(self.merkez.liste())
        if yol == ["api", "bekleyenler"]:
            return self._bekleyenler()
        if len(yol) >= 3 and yol[:2] == ["api", "dosya"]:
            return self._dosya_gonder(yol[2], "onizleme" in sorgu, "kestirme" in sorgu)
        if yol[0] == "pc":
            return self._pc_get(yol[1:])
        self._hata(404)

    def _bekleyenler(self) -> None:
        """Kestirme için: satır başına bir indirme adresi."""
        kok = f"http://{self.headers.get('Host', '')}/k/{self.merkez.token}/api/dosya"
        satirlar = [f"{kok}/{o['id']}/{quote(o['ad'])}?kestirme" for o in self.merkez.bekleyen_medya()]
        self._gonder(200, "\n".join(satirlar).encode(), "text/plain; charset=utf-8")

    def _pc_get(self, yol: list[str]) -> None:
        """PC penceresinin uçları; telefondan açılamaz."""
        if not self._yerelden_mi():
            return self._hata(404)
        if yol == [""]:
            return self._gonder(200, (KOK / "pc.html").read_bytes(), "text/html; charset=utf-8")
        if yol == ["durum"]:
            return self._json({
                "surum": self.merkez.surum,
                "bagli": self.merkez.telefon_bagli(),
                "raf": self.merkez.liste()["ogeler"],
                "gelenler": [{k: g[k] for k in ("id", "ad", "boyut", "bicim", "cozunurluk", "gorsel")}
                             for g in self.merkez.gelenler()],
                **self.pc_durumu(),
            })
        if yol == ["qr.svg"]:
            tampon = io.BytesIO()
            segno.make(adres(self.merkez.token), error="m").save(tampon, kind="svg", border=2, xmldecl=False, svgns=True)
            return self._gonder(200, tampon.getvalue(), "image/svg+xml")
        if len(yol) == 2 and yol[0] == "kucuk":
            veri = self.merkez.kucuk(yol[1])
            return self._gonder(200, veri, "image/jpeg") if veri else self._hata(404)
        self._hata(404)

    def _dosya_gonder(self, oge_id: str, onizleme: bool, kestirme: bool) -> None:
        oge = self.merkez.oge(oge_id)
        if not oge or oge["tur"] != "dosya":
            return self._hata(404)
        try:
            f = open(oge["yol"], "rb")
        except OSError:
            return self._hata(410, "dosya PC'de artık yok")
        with f:
            boyut = os.fstat(f.fileno()).st_size
            bas, son = 0, boyut - 1
            kod = 200
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if m and (m[1] or m[2]):
                if m[1]:
                    bas = int(m[1])
                    son = min(int(m[2]), boyut - 1) if m[2] else boyut - 1
                else:
                    bas = max(boyut - int(m[2]), 0)
                if bas >= boyut or bas > son:
                    return self._gonder(416, b"", "text/plain", {"Content-Range": f"bytes */{boyut}"})
                kod = 206
            tur = mimetypes.guess_type(oge["ad"])[0] or "application/octet-stream"
            satir_ici = onizleme and tur in GOSTERILEBILIR
            gercek_tur = satir_ici or (kestirme and tur in MEDYA)
            ek = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"{'inline' if satir_ici else 'attachment'}; filename*=UTF-8''{quote(oge['ad'])}",
            }
            if kod == 206:
                ek["Content-Range"] = f"bytes {bas}-{son}/{boyut}"
            self._baslik(kod, tur if gercek_tur else "application/octet-stream", son - bas + 1, ek)
            if self.command == "HEAD":
                return
            f.seek(bas)
            kalan = son - bas + 1
            while kalan > 0:
                blok = f.read(min(256 * 1024, kalan))
                if not blok:
                    break
                self.wfile.write(blok)
                kalan -= len(blok)
        if not onizleme and kalan == 0 and son == boyut - 1:
            self.merkez.alindi(oge_id)

    # ---- POST
    def do_POST(self):
        rota = self._rota()
        if rota is None:
            return self._hata(404)
        yol, sorgu = rota
        if yol == ["api", "yukle"]:
            return self._yukle(sorgu)
        if yol == ["api", "paylas"]:
            return self._paylas()
        if yol == ["api", "metin"]:
            return self._metin_al()
        if len(yol) == 3 and yol[:2] == ["api", "kaldir"]:
            self.merkez.kaldir(yol[2])
            return self._json({"tamam": True})
        if yol == ["api", "alindi"]:
            govde = self._govdeyi_oku(200)
            if govde is not None:
                self.merkez.alindi(govde.decode("ascii", "ignore"))
                self._json({"tamam": True})
            return None
        if yol == ["api", "yerel"]:
            return self._yerel()
        self._hata(404)

    def _kaydi_ozetle(self, kayit: dict) -> dict:
        return {k: kayit[k] for k in ("ad", "boyut", "bicim", "cozunurluk")}

    def _yukle(self, sorgu: dict) -> None:
        uzunluk = self._uzunluk()
        if uzunluk is None:
            return
        ad = sorgu.get("ad", ["dosya"])[0]
        try:
            kayit = self.merkez.gelen_kaydet(ad, self.rfile, uzunluk)
        except DiskDolu:
            self.merkez.on_event("hata", "Disk dolu, dosya alınamadı")
            return self._hata(507, "PC'de yer kalmadı")
        except (EksikGovde, OSError):
            self.close_connection = True
            return
        self._json(self._kaydi_ozetle(kayit))

    def _paylas(self) -> None:
        """iPhone Paylaş menüsündeki kestirmenin yazdığı yer."""
        uzunluk = self._uzunluk()
        if uzunluk is None:
            return
        m = re.search(r'boundary="?([^";]+)"?', self.headers.get("Content-Type", ""))
        if not m:
            return self._hata(415, "multipart bekleniyordu")
        akis = CokParca(self.rfile, m[1].encode(), uzunluk)
        kayitlar = []
        try:
            for basliklar in akis.parcalar():
                ad = parca_adi(basliklar)
                if ad is not None:
                    kayitlar.append(self.merkez.gelen_kaydet(ad, akis, None))
                    continue
                metin = bytearray()
                while parca := akis.read():
                    metin += parca[: METIN_SINIRI - len(metin)]
                if metin.strip():
                    self.merkez.on_event("metin", metin.decode("utf-8", "replace").strip())
        except DiskDolu:
            self.merkez.on_event("hata", "Disk dolu, dosya alınamadı")
            return self._hata(507, "PC'de yer kalmadı")
        except (EksikGovde, OSError):
            self.close_connection = True
            return
        self._json({"dosyalar": [self._kaydi_ozetle(k) for k in kayitlar]})

    def _metin_al(self) -> None:
        govde = self._govdeyi_oku(METIN_SINIRI)
        if govde is None:
            return
        metin = govde.decode("utf-8", "replace").strip()
        if not metin:
            return self._hata(400, "boş")
        self.merkez.on_event("metin", metin)
        self._json({"tamam": True})

    def _yerel(self) -> None:
        """Gönder menüsünden gelen ikinci kopya buraya yazar. Yalnızca bu PC'den:
        telefon token'ı bilse de PC'deki keyfi yolları rafa koyduramasın."""
        if not self._yerelden_mi():
            return self._hata(404)
        govde = self._govdeyi_oku(METIN_SINIRI)
        if govde is None:
            return
        veri = json.loads(govde)
        for yol in veri.get("yollar", []):
            self.merkez.dosya_ekle(yol)
        self.merkez.on_event("goster", None)
        self._json({"tamam": True})


class Sunucu(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # Windows'ta True olunca iki kopya aynı portu kapabilir

    def handle_error(self, request, client_address):
        pass  # telefon kilitlenince kopan bağlantı normal, gürültü yapmasın


def baslat(merkez: Merkez, port: int = PORT, pc_durumu=None) -> Sunucu:
    isleyici = type("BagliIsleyici", (Isleyici,),
                    {"merkez": merkez, "pc_durumu": staticmethod(pc_durumu or (lambda: {}))})
    sunucu = Sunucu(("0.0.0.0", port), isleyici)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    return sunucu


def adres(token: str, ip: str | None = None, port: int = PORT) -> str:
    return f"http://{ip or lan_ip()}:{port}/k/{token}/"
