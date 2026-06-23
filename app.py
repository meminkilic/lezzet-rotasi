# -*- coding: utf-8 -*-
"""
Lezzet Rotası — Proxy Sunucusu
Geliştirici: Mehmet Emin KILIÇ — V2.1

Bu küçük Flask sunucusu, tarayıcının doğrudan Google'a istek atarken
takıldığı CORS engelini aşar. Akış:  Tarayıcı  →  bu proxy  →  Google Places API

API anahtarı SADECE bu sunucuda tutulur; tarayıcıya / paylaşılan dosyaya hiç gitmez.

------------------------------------------------------------------
KURULUM (Windows / Mac):
  1) Python kurulu olmalı.
  2) Komut satırında:
         pip install flask flask-cors requests
  3) API anahtarını ortam değişkenine koy:
         Windows (kalıcı):   setx GOOGLE_API_KEY "AIza...senin_anahtarin"
                             (setx sonrası komut penceresini kapatıp yeniden aç)
         Windows (geçici):   set GOOGLE_API_KEY=AIza...senin_anahtarin
         Mac/Linux:          export GOOGLE_API_KEY="AIza...senin_anahtarin"
  4) Çalıştır:
         python app.py
  5) Tarayıcıda:  http://localhost:5000/saglik   →  {"durum":"ok"} görmelisin.

------------------------------------------------------------------
TELEFONDAN (iPhone) KULLANIM:
  - Telefon ve bilgisayar AYNI Wi-Fi ağında olmalı.
  - Bilgisayarın yerel IP'sini öğren:
         Windows:  ipconfig   → "IPv4 Address" (örn. 192.168.1.20)
  - HTML uygulamasındaki "Proxy adresi" alanına şunu yaz:
         http://192.168.1.20:5000      (kendi IP'ni koy)
  - Bilgisayardan kullanırken:  http://localhost:5000
------------------------------------------------------------------
"""

import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # HTML uygulamasının bu sunucuya erişebilmesi için

API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()

PLACES_BASE = "https://places.googleapis.com/v1/places"

# Yanıtta hangi alanları istediğimiz (field mask) — zorunlu.
FIELD_MASK = ",".join([
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.primaryTypeDisplayName",
    "places.location",
    "places.priceLevel",
])


def _restoran_listesi(places):
    """Google yanıtını HTML uygulamasının beklediği sade biçime çevirir."""
    sonuc = []
    for p in places or []:
        sonuc.append({
            "name": (p.get("displayName") or {}).get("text", "İsimsiz"),
            "cuisine": (p.get("primaryTypeDisplayName") or {}).get("text", "Restoran"),
            "addr": p.get("formattedAddress", ""),
            "rating": p.get("rating", 0) or 0,
            "reviews": p.get("userRatingCount", 0) or 0,
            "lat": (p.get("location") or {}).get("latitude"),
            "lng": (p.get("location") or {}).get("longitude"),
        })
    return sonuc


def _hata(mesaj, kod=400):
    return jsonify({"hata": mesaj}), kod


@app.route("/")
def anasayfa():
    """Uygulamanın kendisini sun (index.html aynı klasörde olmalı)."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ("index.html bulunamadı. HTML dosyasını app.py ile aynı klasöre "
                "'index.html' adıyla koy."), 404


@app.route("/saglik")
def saglik():
    """Sunucu ayakta mı + anahtar tanımlı mı kontrolü."""
    return jsonify({
        "durum": "ok",
        "anahtar_tanimli": bool(API_KEY),
    })


@app.route("/api/restoranlar")
def restoranlar():
    """
    İki kullanım:
      1) Konumdan:  /api/restoranlar?lat=39.96&lng=32.58&yaricap=2500
      2) Metinden:  /api/restoranlar?metin=Sincan Ankara restoran
    """
    if not API_KEY:
        return _hata("Sunucuda GOOGLE_API_KEY tanımlı değil. Kurulum adımlarına bak.", 500)

    metin = (request.args.get("metin") or "").strip()
    lat = request.args.get("lat")
    lng = request.args.get("lng")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    try:
        if metin:
            # ----- Metin araması (il/ilçe/mahalle) -----
            url = f"{PLACES_BASE}:searchText"
            body = {
                "textQuery": f"{metin} restoran",
                "maxResultCount": 20,
                "languageCode": "tr",
                "regionCode": "TR",
                "includedType": "restaurant",
            }
            r = requests.post(url, headers=headers, json=body, timeout=12)

        elif lat and lng:
            # ----- Konuma yakın arama -----
            try:
                yaricap = float(request.args.get("yaricap", 2500))
            except ValueError:
                yaricap = 2500.0
            yaricap = max(50.0, min(yaricap, 50000.0))  # Google sınırları

            url = f"{PLACES_BASE}:searchNearby"
            body = {
                "includedTypes": ["restaurant"],
                "maxResultCount": 20,
                "languageCode": "tr",
                "regionCode": "TR",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": float(lat), "longitude": float(lng)},
                        "radius": yaricap,
                    }
                },
            }
            r = requests.post(url, headers=headers, json=body, timeout=12)

        else:
            return _hata("Ya 'metin' ya da 'lat' & 'lng' parametresi gerekli.")

        # Google hata döndürdüyse anlamlı mesaj ver
        if r.status_code != 200:
            try:
                detay = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                detay = r.text[:300]
            return _hata(f"Google API hatası ({r.status_code}): {detay}", r.status_code)

        data = r.json()
        return jsonify({
            "kaynak": "google",
            "sayi": len(data.get("places", [])),
            "restoranlar": _restoran_listesi(data.get("places", [])),
        })

    except requests.exceptions.Timeout:
        return _hata("Google API zaman aşımına uğradı. Tekrar dene.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)


if __name__ == "__main__":
    if not API_KEY:
        print("\n[UYARI] GOOGLE_API_KEY tanımlı değil! Kurulum adımlarına bak.\n")
    else:
        print("\n[OK] API anahtarı yüklendi.\n")
    print("Sunucu çalışıyor:  http://localhost:5000")
    print("Sağlık kontrolü :  http://localhost:5000/saglik\n")
    # host=0.0.0.0  → aynı Wi-Fi'daki telefondan da erişilebilsin
    # PORT → Render gibi bulut servisleri portu kendi atar; yoksa 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
