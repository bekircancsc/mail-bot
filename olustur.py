"""marka_listesi.csv + sablon.txt + urun_gruplari.csv -> mailler/ klasörü ve mailler.csv

Aynı ürünü veren markalara (urun_gruplari.csv) aynı anda mail atılmaz: her gruptan sadece
sıradaki ilk marka gönderime girer, diğerleri mailler/olumsuz_yanit_gelirse_denenecek/ klasörüne gider.
Gönderilen markadan olumsuz yanıt gelince marka_listesi.csv'de durumunu "olumsuz yanıt" yap;
bir sonraki çalıştırmada gruptaki sıradaki marka otomatik olarak gönderime girer.

Kullanım: python olustur.py
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

KLASOR = Path(__file__).parent
KONU = "DEÜ CS-AI Hackathon (7 Kasım 2026) – Sponsorluk İş Birliği Talebi"
# Bu durumlarla başlayan marka gruptaki yeri boşaltır, sıradaki marka denenir.
# Diğer dolu durumlar (elle gönderildi, kabul edildi...) yeri tutar: gruptan başka markaya mail gitmez.
YER_BOSALTAN = ("olumsuz", "cevap yok", "iptal")
YEDEK_ALT = "olumsuz_yanit_gelirse_denenecek"

sablon = (KLASOR / "sablon.txt").read_text(encoding="utf-8")
with open(KLASOR / "marka_listesi.csv", encoding="utf-8-sig", newline="") as f:
    markalar = list(csv.DictReader(f, delimiter=";"))
with open(KLASOR / "urun_gruplari.csv", encoding="utf-8-sig", newline="") as f:
    grup_satirlari = sorted(csv.DictReader(f, delimiter=";"), key=lambda r: (r["grup"], int(r["sira"])))

durumlar = {m["marka"]: m.get("durum") or "" for m in markalar}
gruplar = defaultdict(list)
for r in grup_satirlari:
    if r["marka"] not in durumlar:
        sys.exit(f"urun_gruplari.csv: '{r['marka']}' marka_listesi.csv'de yok (yazımı kontrol et).")
    gruplar[r["grup"]].append(r["marka"])
tekrar = [m for m in durumlar if sum(m in g for g in gruplar.values()) > 1]
if tekrar:
    sys.exit(f"urun_gruplari.csv: birden fazla grupta olan marka: {', '.join(tekrar)}")

def grup_sirasi(liste):
    """(yeri tutanlar, sıradaki yedekler).
    Elle gönderilmiş/kabul etmiş markalar cevap verene kadar yeri tutar; hiçbiri yoksa ilk boştaki marka gönderilir."""
    adaylar = [m for m in liste if not durumlar[m].lower().startswith(YER_BOSALTAN)]
    tutanlar = [m for m in adaylar if durumlar[m]]
    bostakiler = [m for m in adaylar if not durumlar[m]]
    if not tutanlar and bostakiler:
        tutanlar = bostakiler[:1]
        bostakiler = bostakiler[1:]
    return tutanlar, bostakiler


# yedek[marka] = (grup, gruptaki sırası, yeri tutan marka(lar))
yedek = {}
for grup, liste in gruplar.items():
    tutanlar, sıradakiler = grup_sirasi(liste)
    for sira, m in enumerate(sıradakiler, 1):
        yedek[m] = (grup, sira, ", ".join(tutanlar))

cikti = KLASOR / "mailler"
for alt in ("mail_adresi_var", "mail_adresi_yok", YEDEK_ALT):
    (cikti / alt).mkdir(parents=True, exist_ok=True)
    for eski in (cikti / alt).glob("*.txt"):
        eski.unlink()

satirlar = []
atlanan = 0
for i, m in enumerate(markalar, 1):
    if m.get("durum"):
        atlanan += 1
        continue
    govde = sablon.replace("{MARKA_IN}", m["marka_in"]).replace("{MARKA}", m["marka"])
    ad = re.sub(r'[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+', '_', m['marka']).strip('_')
    baslik = f"Kime: {m['mail'] or '(mail bulunamadı)'}\nKonu: {KONU}\n"
    if m["not"]:
        baslik += f"Not: {m['not']}\n"
    if m["marka"] in yedek:
        grup, sira, tutan = yedek[m["marka"]]
        alt = YEDEK_ALT
        dosya_adi = f"{re.sub(r'[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+', '_', grup)}_{sira}_{ad}.txt"
        baslik = (f"BEKLİYOR: {grup} grubunda {sira}. yedek. Şu an yeri tutan: {tutan}. Hepsinden olumsuz yanıt gelirse "
                  f"(ve önceki yedekler de olumsuzsa) gönderilecek.\n") + baslik
    else:
        alt = "mail_adresi_var" if m["mail"] else "mail_adresi_yok"
        dosya_adi = f"{i:02d}_{ad}.txt"
    (cikti / alt / dosya_adi).write_text(baslik + "\n" + govde, encoding="utf-8")
    if m["mail"] and m["marka"] not in yedek:
        satirlar.append({"kategori": m["kategori"], "marka": m["marka"], "mail": m["mail"],
                         "guven": m["guven"], "konu": KONU, "govde": govde, "durum": "hazır"})

with open(KLASOR / "mailler.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["kategori", "marka", "mail", "guven", "konu", "govde", "durum"], delimiter=";")
    w.writeheader()
    w.writerows(satirlar)

bekleyen_yedek = sum(1 for m in markalar if m["marka"] in yedek and not m.get("durum"))
hazir = len(markalar) - atlanan
print(f"{hazir} mail hazırlandı: {len(satirlar)} gönderime hazır, {bekleyen_yedek} olumsuz yanıt bekleyen yedek, "
      f"{hazir - len(satirlar) - bekleyen_yedek} adressiz ({atlanan} marka atlandı: elle gönderildi, iptal veya olumsuz)")
for grup, liste in sorted(gruplar.items()):
    tutanlar, sıradakiler = grup_sirasi(liste)
    if not tutanlar:
        print(f"  {grup}: denenecek marka kalmadı")
        continue
    tutan_yazi = ", ".join(f"{m} ({durumlar[m] or 'gönderime hazır'})" for m in tutanlar)
    print(f"  {grup}: {tutan_yazi} | sıradakiler: {', '.join(sıradakiler) or '-'}")
