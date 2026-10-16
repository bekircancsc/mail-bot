"""Sponsorluk mail botu: mailler.csv'deki her markaya Gmail SMTP ile teker teker gönderir.

Kurulum: ayarlar.ornek.env dosyasını ayarlar.env olarak kopyala, mail ve uygulama şifresini yaz.

Kullanım:
  python gonder.py --kontrol               Mail göndermeden her şeyi kontrol eder (Gmail girişi dahil)
  python gonder.py --kuru                  Hiçbir şey göndermez, kime ne gideceğini listeler
  python gonder.py --test benim@mail.com   İlk maili sadece verilen adrese gönderir (deneme)
  python gonder.py                         Gerçek gönderim (onay ister)
  python gonder.py --limit 20              Bu çalıştırmada en fazla 20 mail gönderir

Gönderilenler gonderim_log.csv'ye yazılır ve tekrar gönderilmez;
bot yarıda kesilirse aynı komutla kaldığı yerden devam eder.
marka_listesi.csv, sablon.txt veya urun_gruplari.csv değiştiyse mailler.csv otomatik yeniden üretilir.
Aynı ürün grubundan (urun_gruplari.csv) sadece bir markaya gönderilir; olumsuz yanıt gelince
marka_listesi.csv'de o markanın durum sütununa "olumsuz yanıt" yaz, sıradaki marka otomatik gönderime girer.
"""
import argparse
import csv
import random
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

KLASOR = Path(__file__).parent
LOG = KLASOR / "gonderim_log.csv"
LOG_YEDEK = KLASOR / "gonderim_log_yedek.csv"
MAILLER = KLASOR / "mailler.csv"
KAYNAKLAR = [KLASOR / "marka_listesi.csv", KLASOR / "sablon.txt", KLASOR / "olustur.py", KLASOR / "urun_gruplari.csv"]
# "belirsiz": Gmail'e iletilirken bağlantı koptu, gitmiş olabilir; tekrar gönderilmez, Gönderilmiş klasöründen kontrol edilir
GIDEN_DURUMLAR = ("gönderildi", "belirsiz")
# Bu kodlar Gmail'in gönderimi geçici/günlük olarak durdurduğunu gösterir; devam etmek anlamsız
DURDURAN_KODLAR = (421, 454)


def ayarlari_oku():
    dosya = KLASOR / "ayarlar.env"
    if not dosya.exists():
        sys.exit("ayarlar.env bulunamadı. ayarlar.ornek.env dosyasını ayarlar.env olarak kopyalayıp doldur.")
    ham = dosya.read_bytes().removeprefix(b"\xef\xbb\xbf")
    try:
        metin = ham.decode("utf-8")
    except UnicodeDecodeError:
        metin = ham.decode("cp1254")  # Not Defteri'nde ANSI olarak kaydedilmişse
    ayar = {}
    for satir in metin.splitlines():
        if "=" in satir and not satir.strip().startswith("#"):
            k, v = satir.split("=", 1)
            ayar[k.strip().lstrip("﻿")] = v.strip().strip('"').strip("'")
    for k in ("GONDEREN_MAIL", "UYGULAMA_SIFRESI"):
        if not ayar.get(k):
            sys.exit(f"ayarlar.env içinde {k} boş.")
    ayar["UYGULAMA_SIFRESI"] = ayar["UYGULAMA_SIFRESI"].replace(" ", "")
    if len(ayar["UYGULAMA_SIFRESI"]) != 16:
        print(f"UYARI: Uygulama şifresi 16 karakter değil ({len(ayar['UYGULAMA_SIFRESI'])}). Yanlış kopyalanmış olabilir.")
    return ayar


def mailleri_guncelle():
    """Kaynak dosyalar mailler.csv'den yeniyse olustur.py'yi çalıştırır."""
    if MAILLER.exists() and MAILLER.stat().st_mtime >= max(k.stat().st_mtime for k in KAYNAKLAR):
        return
    print("marka_listesi.csv / sablon.txt değişmiş, mailler yeniden üretiliyor...")
    r = subprocess.run([sys.executable, str(KLASOR / "olustur.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode != 0:
        sys.exit(f"Mailler yeniden üretilemedi, gönderim durduruldu:\n{r.stderr.strip()}")
    print(r.stdout.strip())


def mailleri_oku():
    with open(MAILLER, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def gonderilenler():
    giden = set()
    for dosya in (LOG, LOG_YEDEK):
        if dosya.exists():
            with open(dosya, encoding="utf-8-sig", newline="") as f:
                giden |= {r["mail"].lower() for r in csv.DictReader(f, delimiter=";") if r["durum"] in GIDEN_DURUMLAR}
    return giden


def isaretli_markalar():
    """marka_listesi.csv'de durum sütunu dolu olanlar (elle gönderildi, iptal) hiç gönderilmez."""
    with open(KLASOR / "marka_listesi.csv", encoding="utf-8-sig", newline="") as f:
        return {r["marka"] for r in csv.DictReader(f, delimiter=";") if r.get("durum")}


def _log_satiri(dosya, satir):
    yeni = not dosya.exists()
    with open(dosya, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if yeni:
            w.writerow(["zaman", "marka", "mail", "durum", "hata"])
        w.writerow(satir)


def log_yaz(marka, mail, durum, hata=""):
    """Kayıt yazılamazsa (örn. OneDrive dosyayı kilitlediyse) bekleyip tekrar dener, olmazsa yedek dosyaya yazar."""
    satir = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), marka, mail, durum, hata]
    for _ in range(5):
        try:
            _log_satiri(LOG, satir)
            return True
        except OSError:
            time.sleep(2)
    try:
        _log_satiri(LOG_YEDEK, satir)
        print(f"UYARI: {LOG.name} kilitli, kayıt {LOG_YEDEK.name} dosyasına yazıldı.")
        return True
    except OSError:
        return False


def mesaj_olustur(ayar, alici, konu, govde):
    m = EmailMessage()
    m["From"] = formataddr((ayar.get("GONDEREN_AD", ""), ayar["GONDEREN_MAIL"]))
    m["To"] = alici
    m["Subject"] = konu
    m.set_content(govde)
    return m


def baglan(ayar):
    s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60)
    try:
        s.login(ayar["GONDEREN_MAIL"], ayar["UYGULAMA_SIFRESI"])
    except smtplib.SMTPAuthenticationError:
        sys.exit("Gmail girişi reddedildi. GONDEREN_MAIL ve UYGULAMA_SIFRESI'ni kontrol et "
                 "(normal şifre değil, 16 haneli uygulama şifresi olmalı; hesapta 2 Adımlı Doğrulama açık olmalı).")
    return s


def kapat(s):
    try:
        s.quit()
    except Exception:
        pass


def gonder_tek(s, ayar, mesaj):
    """(bağlantı, durum, hata) döner. durum: gönderildi | hata (kesin gitmedi) | belirsiz (gitmiş olabilir)."""
    try:
        s.send_message(mesaj)
        return s, "gönderildi", ""
    except smtplib.SMTPServerDisconnected:
        pass  # bağlantı önceden kopmuş, mesaj sunucuya hiç ulaşmadı; yeniden bağlanıp dene
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException) as e:
        return s, "hata", str(e)
    except Exception as e:
        return s, "belirsiz", f"{type(e).__name__}: {e}"
    try:
        s = baglan(ayar)
    except Exception as e:
        return s, "hata", f"yeniden bağlanılamadı: {e}"
    try:
        s.send_message(mesaj)
        return s, "gönderildi", ""
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException) as e:
        return s, "hata", str(e)
    except Exception as e:
        return s, "belirsiz", f"{type(e).__name__}: {e}"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser()
    p.add_argument("--kontrol", action="store_true", help="göndermeden ayarları ve Gmail girişini kontrol et")
    p.add_argument("--kuru", action="store_true", help="göndermeden listele")
    p.add_argument("--test", metavar="ADRES", help="ilk maili sadece bu adrese gönder")
    p.add_argument("--limit", type=int, default=50, help="bu çalıştırmada en fazla kaç mail (varsayılan 50)")
    p.add_argument("--min-bekleme", type=int, default=45, help="mailler arası en az saniye")
    p.add_argument("--max-bekleme", type=int, default=90, help="mailler arası en çok saniye")
    p.add_argument("--onay", action="store_true", help="onay sorusunu atla")
    a = p.parse_args()

    mailleri_guncelle()
    gidenler = gonderilenler()
    isaretli = isaretli_markalar()
    bekleyen = [m for m in mailleri_oku() if m["mail"].lower() not in gidenler and m["marka"] not in isaretli]
    print(f"Listede bekleyen: {len(bekleyen)} mail ({len(gidenler)} tanesi daha önce gönderilmiş)")

    if a.kuru:
        for i, m in enumerate(bekleyen[:a.limit], 1):
            print(f"{i:3}. {m['marka']:<30} -> {m['mail']:<40} [{m['guven']}]")
        print("\nKuru çalıştırma: hiçbir mail gönderilmedi.")
        return

    ayar = ayarlari_oku()

    if a.kontrol:
        s = baglan(ayar)
        kapat(s)
        print(f"Gmail girişi başarılı: {ayar['GONDEREN_MAIL']} (gönderen adı: {ayar.get('GONDEREN_AD') or '-'})")
        print(f"Kontrol tamam: {len(bekleyen)} mail gönderime hazır. Hiçbir mail gönderilmedi.")
        return

    if a.test:
        m = bekleyen[0] if bekleyen else mailleri_oku()[0]
        s = baglan(ayar)
        s.send_message(mesaj_olustur(ayar, a.test, f"[TEST: {m['marka']}] {m['konu']}", m["govde"]))
        kapat(s)
        print(f"Test maili ({m['marka']} metni) {a.test} adresine gönderildi. Log'a yazılmadı.")
        return

    sira = bekleyen[:a.limit]
    if not sira:
        print("Gönderilecek mail kalmadı.")
        return
    print(f"\nGönderen: {ayar['GONDEREN_MAIL']}")
    print(f"Bu çalıştırmada {len(sira)} mail gönderilecek, aralarında {a.min_bekleme}-{a.max_bekleme} sn beklenecek.")
    if not a.onay and input("Başlamak için EVET yaz: ").strip().upper() != "EVET":
        print("İptal edildi.")
        return

    s = baglan(ayar)
    sayac = {"gönderildi": 0, "hata": 0, "belirsiz": 0}
    i = 0
    try:
        for i, m in enumerate(sira, 1):
            s, durum, hata = gonder_tek(s, ayar, mesaj_olustur(ayar, m["mail"], m["konu"], m["govde"]))
            sayac[durum] += 1
            etiket = {"gönderildi": "OK  ", "hata": "HATA", "belirsiz": "??  "}[durum]
            print(f"[{i}/{len(sira)}] {etiket} {m['marka']} -> {m['mail']}" + (f": {hata}" if hata else ""))
            if not log_yaz(m["marka"], m["mail"], durum, hata):
                print("Gönderim kaydı hiçbir dosyaya yazılamadı; çift gönderim olmasın diye bot durduruldu.")
                break
            if durum == "hata" and any(f"{k} " in hata or f"({k}," in hata for k in DURDURAN_KODLAR) or "5.4.5" in hata:
                print("Gmail gönderimi geçici olarak durdurdu (limit/oturum). Bot durduruldu; birkaç saat sonra aynı komutla devam edin.")
                break
            if i < len(sira):
                time.sleep(random.randint(a.min_bekleme, a.max_bekleme))
    except KeyboardInterrupt:
        print(f"\nKullanıcı durdurdu ({i}. mailde). Gönderilenler kayıtlı, aynı komutla kaldığı yerden devam eder.")
    finally:
        kapat(s)
    print(f"\nBitti: {sayac['gönderildi']} gönderildi, {sayac['hata']} hata, {sayac['belirsiz']} belirsiz. Sonuçlar: {LOG.name}")
    if sayac["hata"]:
        print("Hatalı olanlar aynı komutu tekrar çalıştırınca yeniden denenir.")
    if sayac["belirsiz"]:
        print("Belirsiz olanlar gitmiş olabilir, tekrar denenmez. Gmail > Gönderilmiş klasöründen kontrol edin.")


if __name__ == "__main__":
    main()
