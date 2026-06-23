# -*- coding: utf-8 -*-
"""
Lezzet Rotası — Proxy Sunucusu
Geliştirici: Mehmet Emin KILIÇ — V3.0
"""

import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_KEY  = os.environ.get("GOOGLE_API_KEY", "").strip()
PLACES_BASE = "https://places.googleapis.com/v1/places"

# Liste araması için alan maskesi
FIELD_MASK_LIST = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.primaryTypeDisplayName",
    "places.location",
    "places.priceLevel",
    "places.editorialSummary",
    "places.photos",
])

# Detay (yorumlar dahil) için alan maskesi
FIELD_MASK_DETAIL = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "rating",
    "userRatingCount",
    "primaryTypeDisplayName",
    "location",
    "priceLevel",
    "editorialSummary",
    "reviews",
    "regularOpeningHours",
    "internationalPhoneNumber",
    "websiteUri",
    "photos",
])

PRICE_MAP = {
    "PRICE_LEVEL_FREE":          "Ücretsiz",
    "PRICE_LEVEL_INEXPENSIVE":   "₺",
    "PRICE_LEVEL_MODERATE":      "₺₺",
    "PRICE_LEVEL_EXPENSIVE":     "₺₺₺",
    "PRICE_LEVEL_VERY_EXPENSIVE":"₺₺₺₺",
}


def _fmt_place(p):
    return {
        "id":      p.get("id", ""),
        "name":    (p.get("displayName") or {}).get("text", "İsimsiz"),
        "cuisine": (p.get("primaryTypeDisplayName") or {}).get("text", "Restoran"),
        "addr":    p.get("formattedAddress", ""),
        "rating":  p.get("rating", 0) or 0,
        "reviews": p.get("userRatingCount", 0) or 0,
        "lat":     (p.get("location") or {}).get("latitude"),
        "lng":     (p.get("location") or {}).get("longitude"),
        "price":   PRICE_MAP.get(p.get("priceLevel", ""), ""),
        "summary": (p.get("editorialSummary") or {}).get("text", ""),
        "photo":   _first_photo_ref(p),
    }


def _first_photo_ref(p):
    photos = p.get("photos") or []
    if photos:
        return photos[0].get("name", "")
    return ""


def _fmt_reviews(reviews):
    out = []
    for r in (reviews or []):
        out.append({
            "author":  (r.get("authorAttribution") or {}).get("displayName", "Anonim"),
            "rating":  r.get("rating", 0),
            "text":    (r.get("text") or {}).get("text", ""),
            "time":    (r.get("relativePublishTimeDescription") or ""),
        })
    return out


def _hata(mesaj, kod=400):
    return jsonify({"hata": mesaj}), kod


def _headers(mask):
    return {
        "Content-Type":    "application/json",
        "X-Goog-Api-Key":  API_KEY,
        "X-Goog-FieldMask": mask,
    }


@app.route("/")
def anasayfa():
    try:
        with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html bulunamadı.", 404


@app.route("/saglik")
def saglik():
    return jsonify({"durum": "ok", "anahtar_tanimli": bool(API_KEY)})


@app.route("/api/restoranlar")
def restoranlar():
    """
    Kullanım:
      /api/restoranlar?metin=Sincan Ankara
      /api/restoranlar?lat=39.96&lng=32.58
      /api/restoranlar?lat=39.96&lng=32.58&tur=kebap   (filtre)
    """
    if not API_KEY:
        return _hata("Sunucuda GOOGLE_API_KEY tanımlı değil.", 500)

    metin  = (request.args.get("metin") or "").strip()
    lat    = request.args.get("lat")
    lng    = request.args.get("lng")
    tur    = (request.args.get("tur") or "").strip()   # mutfak türü filtresi

    try:
        if metin:
            sorgu = f"{metin} {tur} restoran" if tur else f"{metin} restoran"
            url   = f"{PLACES_BASE}:searchText"
            body  = {
                "textQuery":      sorgu,
                "maxResultCount": 20,
                "languageCode":   "tr",
                "regionCode":     "TR",
                "includedType":   "restaurant",
            }
            r = requests.post(url, headers=_headers(FIELD_MASK_LIST), json=body, timeout=12)

        elif lat and lng:
            try:
                yaricap = float(request.args.get("yaricap", 2500))
            except ValueError:
                yaricap = 2500.0
            yaricap = max(50.0, min(yaricap, 50000.0))

            if tur:
                # Konumlu arama + tür filtresi → metin aramasına dönüştür
                url  = f"{PLACES_BASE}:searchText"
                body = {
                    "textQuery":      f"{tur} restoran",
                    "maxResultCount": 20,
                    "languageCode":   "tr",
                    "regionCode":     "TR",
                    "includedType":   "restaurant",
                    "locationBias": {
                        "circle": {
                            "center": {"latitude": float(lat), "longitude": float(lng)},
                            "radius": yaricap,
                        }
                    },
                }
                r = requests.post(url, headers=_headers(FIELD_MASK_LIST), json=body, timeout=12)
            else:
                url  = f"{PLACES_BASE}:searchNearby"
                body = {
                    "includedTypes":    ["restaurant"],
                    "maxResultCount":   20,
                    "languageCode":     "tr",
                    "regionCode":       "TR",
                    "locationRestriction": {
                        "circle": {
                            "center": {"latitude": float(lat), "longitude": float(lng)},
                            "radius": yaricap,
                        }
                    },
                }
                r = requests.post(url, headers=_headers(FIELD_MASK_LIST), json=body, timeout=12)
        else:
            return _hata("Ya 'metin' ya da 'lat' & 'lng' parametresi gerekli.")

        if r.status_code != 200:
            try:
                detay = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                detay = r.text[:300]
            return _hata(f"Google API hatası ({r.status_code}): {detay}", r.status_code)

        data = r.json()
        return jsonify({
            "kaynak":      "google",
            "sayi":        len(data.get("places", [])),
            "restoranlar": [_fmt_place(p) for p in data.get("places", [])],
        })

    except requests.exceptions.Timeout:
        return _hata("Google API zaman aşımına uğradı.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)


@app.route("/api/detay/<place_id>")
def detay(place_id):
    """Tek restoran detayı + yorumlar."""
    if not API_KEY:
        return _hata("API anahtarı tanımlı değil.", 500)
    if not place_id or not place_id.startswith("ChI"):
        return _hata("Geçersiz place_id.", 400)
    try:
        url = f"{PLACES_BASE}/{place_id}"
        r   = requests.get(url, headers=_headers(FIELD_MASK_DETAIL), timeout=12)
        if r.status_code != 200:
            try:
                detay_msg = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                detay_msg = r.text[:300]
            return _hata(f"Google API hatası ({r.status_code}): {detay_msg}", r.status_code)
        p = r.json()
        result = _fmt_place(p)
        result["yorumlar"] = _fmt_reviews(p.get("reviews"))
        hours = p.get("regularOpeningHours", {})
        result["acik_mi"]  = hours.get("openNow")
        result["saatler"]  = hours.get("weekdayDescriptions", [])
        result["telefon"]  = p.get("internationalPhoneNumber", "")
        result["website"]  = p.get("websiteUri", "")
        return jsonify(result)
    except requests.exceptions.Timeout:
        return _hata("Zaman aşımı.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)


if __name__ == "__main__":
    if not API_KEY:
        print("\n[UYARI] GOOGLE_API_KEY tanımlı değil!\n")
    else:
        print("\n[OK] API anahtarı yüklendi.\n")
    port = int(os.environ.get("PORT", 5000))
    print(f"Sunucu: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
