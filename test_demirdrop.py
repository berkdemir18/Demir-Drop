"""Gerçek soketle uçtan uca testler. Çalıştır: python -m unittest test_demirdrop -v"""
import http.client
import json
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image

import server

PORT = 18765
TOKEN = "test-token-abc"


class Temel(unittest.TestCase):
    def setUp(self):
        self.dizin = Path(tempfile.mkdtemp())
        self.gelen = self.dizin / "gelen"
        self.olaylar = []
        self.merkez = server.Merkez(TOKEN, self.gelen, lambda t, v: self.olaylar.append((t, v)))
        self.sunucu = server.baslat(self.merkez, PORT)

    def tearDown(self):
        self.sunucu.shutdown()
        self.sunucu.server_close()
        self.merkez.kapat()
        shutil.rmtree(self.dizin, ignore_errors=True)

    def istek(self, yontem, yol, govde=None, basliklar=None, host="127.0.0.1"):
        b = http.client.HTTPConnection(host, PORT, timeout=10)
        b.request(yontem, yol, body=govde, headers=basliklar or {})
        y = b.getresponse()
        veri = y.read()
        b.close()
        return y, veri

    def yol(self, ek):
        return f"/k/{TOKEN}/{ek}"

    # ---- yetki
    def test_tokensiz_hicbir_sey_yok(self):
        for yol in ("/", "/k/yanlis/", "/k/yanlis/api/ogeler", "/api/ogeler", "/k/yanlış/"):
            self.assertEqual(self.istek("GET", quote(yol, safe="/"))[0].status, 404, yol)
        self.assertEqual(self.istek("POST", "/k/yanlis/api/metin", b"x")[0].status, 404)
        self.assertEqual(self.olaylar, [])

    def test_yerel_yol_sadece_bu_pcden(self):
        gizli = self.dizin / "gizli.txt"
        gizli.write_text("sır")
        ip = server.lan_ip()
        if ip.startswith("127."):
            self.skipTest("LAN IP yok")
        govde = json.dumps({"yollar": [str(gizli)]}).encode()
        self.assertEqual(self.istek("POST", self.yol("api/yerel"), govde, host=ip)[0].status, 404)
        self.assertEqual(self.merkez.liste()["ogeler"], [])
        self.assertEqual(self.istek("POST", self.yol("api/yerel"), govde)[0].status, 200)
        self.assertEqual(len(self.merkez.liste()["ogeler"]), 1)

    # ---- telefondan PC'ye
    def test_yukleme_ve_turkce_ad(self):
        y, v = self.istek("POST", self.yol("api/yukle?ad=" + quote("tatil şğü.jpg")), b"veri")
        self.assertEqual(y.status, 200)
        self.assertEqual((self.gelen / "tatil şğü.jpg").read_bytes(), b"veri")
        self.assertEqual(json.loads(v)["ad"], "tatil şğü.jpg")

    def test_ayni_ad_ustune_yazmaz(self):
        for icerik in (b"bir", b"iki", b"uc"):
            self.istek("POST", self.yol("api/yukle?ad=image.jpg"), icerik)
        self.assertEqual((self.gelen / "image.jpg").read_bytes(), b"bir")
        self.assertEqual((self.gelen / "image (1).jpg").read_bytes(), b"iki")
        self.assertEqual((self.gelen / "image (2).jpg").read_bytes(), b"uc")

    def test_klasorden_kacamaz(self):
        for kotu in ("../../kacak.txt", "..\\..\\kacak.txt", "C:\\Windows\\kacak.txt", "/etc/kacak.txt"):
            self.istek("POST", self.yol("api/yukle?ad=" + quote(kotu)), b"x")
        self.assertFalse((self.dizin / "kacak.txt").exists())
        self.assertEqual({p.parent for p in self.gelen.iterdir()}, {self.gelen})

    def test_kotu_adlar(self):
        self.assertEqual(server.guvenli_ad("CON.txt"), "_CON.txt")
        self.assertEqual(server.guvenli_ad("a<b>:c?.jpg"), "abc.jpg")
        self.assertEqual(server.guvenli_ad("..."), "dosya")
        self.assertEqual(server.guvenli_ad(""), "dosya")
        self.assertLessEqual(len(server.guvenli_ad("x" * 500 + ".jpg")), 200)

    def test_yarim_kalan_iz_birakmaz(self):
        s = socket.create_connection(("127.0.0.1", PORT))
        s.sendall(f"POST {self.yol('api/yukle?ad=yarim.bin')} HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\nazcik".encode())
        s.close()
        for _ in range(100):
            if not any(self.gelen.glob("*")) if self.gelen.exists() else False:
                break
            import time
            time.sleep(.05)
        self.assertEqual(list(self.gelen.glob("*")) if self.gelen.exists() else [], [])
        self.assertFalse([o for o in self.olaylar if o[0] == "geldi"])

    def test_buyuk_dosya_butun(self):
        icerik = bytes(range(256)) * 40000  # ~10 MB
        y, _ = self.istek("POST", self.yol("api/yukle?ad=buyuk.bin"), icerik)
        self.assertEqual(y.status, 200)
        self.assertEqual((self.gelen / "buyuk.bin").read_bytes(), icerik)

    def test_metin_panoya_olay_atar(self):
        self.assertEqual(self.istek("POST", self.yol("api/metin"), "merhaba ş".encode())[0].status, 200)
        self.assertIn(("metin", "merhaba ş"), self.olaylar)
        self.assertEqual(self.istek("POST", self.yol("api/metin"), b"   ")[0].status, 400)

    # ---- PC'den telefona
    def _rafa(self, ad="rapor şu.pdf", icerik=b"0123456789"):
        f = self.dizin / ad
        f.write_bytes(icerik)
        self.merkez.dosya_ekle(f)
        return next(o for o in self.merkez.liste()["ogeler"] if o["ad"] == ad)

    def test_indirme_alindi_isaretler(self):
        o = self._rafa()
        y, v = self.istek("GET", self.yol(f"api/dosya/{o['id']}/x"))
        self.assertEqual((y.status, v), (200, b"0123456789"))
        self.assertIn("attachment", y.getheader("Content-Disposition"))
        self.assertIn("rapor%20%C5%9Fu.pdf", y.getheader("Content-Disposition"))
        self.assertTrue(self.merkez.oge(o["id"])["alindi"])

    def test_aralikli_indirme(self):
        o = self._rafa()
        adres = self.yol(f"api/dosya/{o['id']}/x")
        y, v = self.istek("GET", adres, basliklar={"Range": "bytes=2-4"})
        self.assertEqual((y.status, v, y.getheader("Content-Range")), (206, b"234", "bytes 2-4/10"))
        self.assertFalse(self.merkez.oge(o["id"])["alindi"])
        self.assertEqual(self.istek("GET", adres, basliklar={"Range": "bytes=-3"})[1], b"789")
        self.assertEqual(self.istek("GET", adres, basliklar={"Range": "bytes=50-"})[0].status, 416)

    def test_html_dosyasi_calistirilmaz(self):
        o = self._rafa("kotu.html", b"<script>1</script>")
        y, _ = self.istek("GET", self.yol(f"api/dosya/{o['id']}/x?onizleme"))
        self.assertIn("attachment", y.getheader("Content-Disposition"))
        self.assertEqual(y.getheader("Content-Type"), "application/octet-stream")

    def test_onizleme_alindi_saymaz(self):
        o = self._rafa("a.jpg", b"jpg")
        y, _ = self.istek("GET", self.yol(f"api/dosya/{o['id']}/a.jpg?onizleme"))
        self.assertEqual(y.getheader("Content-Type"), "image/jpeg")
        self.assertFalse(self.merkez.oge(o["id"])["alindi"])

    def test_klasor_ziplenir_kaldirinca_silinir(self):
        k = self.dizin / "klasorum"
        (k / "alt").mkdir(parents=True)
        (k / "alt" / "a.txt").write_text("x")
        self.merkez.dosya_ekle(k)
        for _ in range(100):
            if self.merkez.liste()["ogeler"]:
                break
            import time
            time.sleep(.05)
        o = self.merkez.liste()["ogeler"][0]
        self.assertEqual(o["ad"], "klasorum.zip")
        zip_yolu = Path(self.merkez.oge(o["id"])["yol"])
        self.assertTrue(zip_yolu.exists())
        self.istek("POST", self.yol(f"api/kaldir/{o['id']}"))
        self.assertFalse(zip_yolu.exists())
        self.assertTrue((k / "alt" / "a.txt").exists())

    def test_raftan_kaldirmak_asil_dosyayi_silmez(self):
        o = self._rafa()
        self.istek("POST", self.yol(f"api/kaldir/{o['id']}"))
        self.assertTrue((self.dizin / "rapor şu.pdf").exists())
        self.assertEqual(self.istek("GET", self.yol(f"api/dosya/{o['id']}/x"))[0].status, 404)

    def test_metin_listede(self):
        self.merkez.metin_ekle("https://ornek.com")
        self.assertEqual(self.merkez.liste()["ogeler"][0]["metin"], "https://ornek.com")

    # ---- Paylaş kestirmesi (multipart)
    def _cokparca(self, parcalar, sinir="----Sinir42"):
        govde = b""
        for baslik, icerik in parcalar:
            govde += f"--{sinir}\r\n{baslik}\r\n\r\n".encode() + icerik + b"\r\n"
        govde += f"--{sinir}--\r\n".encode()
        return govde, {"Content-Type": f"multipart/form-data; boundary={sinir}"}

    def test_paylas_baytlari_aynen_yazar(self):
        # Ayraca benzeyen bayt ve CRLF içeren ikili veri bozulmadan geçmeli
        ham = b"\r\n--Sinir4\r\n" + bytes(range(256)) * 9000 + b"\r\n--"
        govde, b = self._cokparca([('Content-Disposition: form-data; name="dosya"; filename="IMG_0001.HEIC"\r\nContent-Type: image/heic', ham)])
        y, v = self.istek("POST", self.yol("api/paylas"), govde, b)
        self.assertEqual(y.status, 200, v)
        self.assertEqual((self.gelen / "IMG_0001.HEIC").read_bytes(), ham)

    def test_paylas_coklu_ve_turkce(self):
        govde, b = self._cokparca([
            ('Content-Disposition: form-data; name="a"; filename="bir.jpg"', b"1"),
            ("Content-Disposition: form-data; name=\"b\"; filename*=UTF-8''%C5%9Fu.png", b"22"),
            ('Content-Disposition: form-data; name="c"; filename="uzantisiz"\r\nContent-Type: image/jpeg', b"333"),
        ])
        y, v = self.istek("POST", self.yol("api/paylas"), govde, b)
        self.assertEqual(y.status, 200, v)
        self.assertEqual((self.gelen / "bir.jpg").read_bytes(), b"1")
        self.assertEqual((self.gelen / "şu.png").read_bytes(), b"22")
        self.assertEqual(len(json.loads(v)["dosyalar"]), 3)
        self.assertTrue(list(self.gelen.glob("uzantisiz.*")))

    def test_paylas_metin_panoya(self):
        govde, b = self._cokparca([('Content-Disposition: form-data; name="dosya"', "https://ornek.com/ş".encode())])
        self.assertEqual(self.istek("POST", self.yol("api/paylas"), govde, b)[0].status, 200)
        self.assertIn(("metin", "https://ornek.com/ş"), self.olaylar)

    def test_paylas_bos_dosya_ve_yarim(self):
        govde, b = self._cokparca([('Content-Disposition: form-data; name="a"; filename="bos.txt"', b"")])
        self.assertEqual(self.istek("POST", self.yol("api/paylas"), govde, b)[0].status, 200)
        self.assertEqual((self.gelen / "bos.txt").read_bytes(), b"")
        s = socket.create_connection(("127.0.0.1", PORT))
        s.sendall(f"POST {self.yol('api/paylas')} HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=S\r\nContent-Length: 9999\r\n\r\n--S\r\nContent-Disposition: form-data; name=a; filename=\"yarim.bin\"\r\n\r\nazcik".encode())
        s.close()
        time.sleep(.5)
        self.assertFalse((self.gelen / "yarim.bin").exists())
        self.assertEqual(list(self.gelen.glob(".*")), [])

    # ---- Kayıpsızlık ve Fotoğraflar kestirmesi
    def test_bilgi_ve_kalite(self):
        kaynak = self.dizin / "kaynak.png"
        Image.effect_noise((1600, 1200), 80).convert("RGB").save(kaynak)
        ham = kaynak.read_bytes()
        y, v = self.istek("POST", self.yol("api/yukle?ad=foto.png"), ham)
        veri = json.loads(v)
        self.assertEqual((veri["cozunurluk"], veri["bicim"]), ("1600×1200", "PNG"))
        self.assertEqual((self.gelen / "foto.png").read_bytes(), ham)
        g = next(iter(self.merkez.gelenler()))
        self.assertEqual(g["cozunurluk"], "1600×1200")

    def test_iphone_dosya_bayt_bayt_ayni(self):
        ham = bytes(range(256)) * 5000
        o = self._rafa("IMG_1.HEIC", ham)
        y, v = self.istek("GET", self.yol(f"api/dosya/{o['id']}/x?kestirme"))
        self.assertEqual(v, ham)
        self.assertEqual(y.getheader("Content-Type"), "image/heic")

    def test_bekleyenler_sadece_alinmamis_medya(self):
        foto = self._rafa("a.jpg", b"j")
        self._rafa("b.pdf", b"p")
        video = self._rafa("c.MOV", b"v")
        self.merkez.metin_ekle("selam")
        y, v = self.istek("GET", self.yol("api/bekleyenler"))
        satirlar = v.decode().split("\n")
        self.assertEqual(len(satirlar), 2)
        self.assertTrue(all(s.startswith(f"http://127.0.0.1:{PORT}/k/{TOKEN}/api/dosya/") for s in satirlar))
        yol = satirlar[0].split(f":{PORT}", 1)[1]
        self.assertEqual(self.istek("GET", yol)[1], b"j")
        self.assertEqual(self.istek("GET", self.yol("api/bekleyenler"))[1].decode().count("\n"), 0)
        self.assertTrue(self.merkez.oge(foto["id"])["alindi"])
        self.assertFalse(self.merkez.oge(video["id"])["alindi"])

    def test_gelenler_yeniden_baslayinca_kalir(self):
        self.istek("POST", self.yol("api/yukle?ad=eski.txt"), b"x")
        m2 = server.Merkez("t", self.gelen)
        self.assertEqual([g["ad"] for g in m2.gelenler()], ["eski.txt"])
        m2.kapat()

    # ---- PC uçları telefondan kapalı
    def test_pc_uclari_sadece_bu_pcden(self):
        ip = server.lan_ip()
        if ip.startswith("127."):
            self.skipTest("LAN IP yok")
        for uc in ("pc/", "pc/durum", "pc/qr.svg"):
            self.assertEqual(self.istek("GET", self.yol(uc))[0].status, 200, uc)
            self.assertEqual(self.istek("GET", self.yol(uc), host=ip)[0].status, 404, uc)

    def test_telefon_bagli_durumu(self):
        self.assertFalse(self.merkez.telefon_bagli())
        self.istek("GET", self.yol("api/ogeler"))
        self.assertFalse(self.merkez.telefon_bagli(), "PC'nin kendi isteği telefon sayılmaz")
        ip = server.lan_ip()
        if not ip.startswith("127."):
            self.istek("GET", self.yol("api/ogeler"), host=ip)
            self.assertTrue(self.merkez.telefon_bagli())

    def test_sayfa_acilir(self):
        y, v = self.istek("GET", self.yol(""))
        self.assertEqual(y.status, 200)
        self.assertIn("Demir Drop".encode(), v)


if __name__ == "__main__":
    unittest.main()
