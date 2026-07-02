# -*- coding: utf-8 -*-
"""
Rotaste — Proxy Sunucusu
Geliştirici: Mehmet Emin KILIÇ — V1.5.0
"""
import os, requests
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
PLACES_BASE = "https://places.googleapis.com/v1/places"

FIELD_MASK_LIST = ",".join([
    "places.id","places.displayName","places.formattedAddress",
    "places.rating","places.userRatingCount","places.primaryTypeDisplayName",
    "places.location","places.priceLevel","places.editorialSummary","places.photos",
])
FIELD_MASK_DETAIL = ",".join([
    "id","displayName","formattedAddress","rating","userRatingCount",
    "primaryTypeDisplayName","location","priceLevel","editorialSummary",
    "reviews","regularOpeningHours","internationalPhoneNumber","websiteUri","photos",
    "servesBeer","servesWine","servesCocktails",
])
PRICE_MAP = {
    "PRICE_LEVEL_FREE":"Ücretsiz","PRICE_LEVEL_INEXPENSIVE":"₺",
    "PRICE_LEVEL_MODERATE":"₺₺","PRICE_LEVEL_EXPENSIVE":"₺₺₺",
    "PRICE_LEVEL_VERY_EXPENSIVE":"₺₺₺₺",
}

def _headers(mask):
    return {"Content-Type":"application/json","X-Goog-Api-Key":API_KEY,"X-Goog-FieldMask":mask}

def _fmt_place(p):
    photos = p.get("photos") or []
    return {
        "id":      p.get("id",""),
        "name":    (p.get("displayName") or {}).get("text","İsimsiz"),
        "cuisine": (p.get("primaryTypeDisplayName") or {}).get("text","Restoran"),
        "addr":    p.get("formattedAddress",""),
        "rating":  p.get("rating",0) or 0,
        "reviews": p.get("userRatingCount",0) or 0,
        "lat":     (p.get("location") or {}).get("latitude"),
        "lng":     (p.get("location") or {}).get("longitude"),
        "price":   PRICE_MAP.get(p.get("priceLevel",""),""),
        "summary": (p.get("editorialSummary") or {}).get("text",""),
        "photoRef": photos[0].get("name","") if photos else "",
    }

def _fmt_reviews(reviews):
    out = []
    for r in (reviews or []):
        metin = ""
        texts = r.get("text", {})
        if isinstance(texts, dict):
            metin = texts.get("text", "")
        elif isinstance(texts, str):
            metin = texts
        out.append({
            "author": (r.get("authorAttribution") or {}).get("displayName","Anonim"),
            "rating": r.get("rating",0),
            "text":   metin,
            "time":   r.get("relativePublishTimeDescription",""),
        })
    return out

def _hata(mesaj, kod=400):
    return jsonify({"hata": mesaj}), kod

BASE_DIR = os.path.dirname(__file__)

def _serve_file(filename, mimetype):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        return send_file(path, mimetype=mimetype)
    return "", 404

@app.route("/")
def anasayfa():
    try:
        with open(os.path.join(BASE_DIR,"index.html"),encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html bulunamadı.", 404

@app.route("/manifest.json")
def manifest():
    return _serve_file("manifest.json","application/manifest+json")

@app.route("/sw.js")
def sw():
    path = os.path.join(BASE_DIR, "sw.js")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return content, 200, {"Content-Type":"application/javascript","Service-Worker-Allowed":"/"}
    return "", 404

@app.route("/icon-192.png")
def icon192():
    return _serve_file("icon-192.png","image/png")

@app.route("/icon-512.png")
def icon512():
    return _serve_file("icon-512.png","image/png")

@app.route("/apple-touch-icon.png")
def apple_icon():
    return _serve_file("apple-touch-icon.png","image/png")

@app.route("/test")
def test():
    """Google bağlantı testi."""
    import socket
    results = {}
    # DNS çözümleme testi
    for host in ["places.googleapis.com", "maps.googleapis.com", "google.com"]:
        try:
            ip = socket.gethostbyname(host)
            results[host] = f"DNS OK: {ip}"
        except Exception as e:
            results[f"{host}_dns"] = f"DNS HATA: {e}"
    # HTTP bağlantı testi
    try:
        r = requests.get("https://google.com", timeout=5)
        results["google_http"] = f"HTTP OK: {r.status_code}"
    except Exception as e:
        results["google_http"] = f"HTTP HATA: {e}"
    try:
        r = requests.get("https://places.googleapis.com", timeout=5)
        results["places_http"] = f"HTTP: {r.status_code}"
    except Exception as e:
        results["places_http"] = f"HTTP HATA: {e}"
    return jsonify(results)

@app.route("/saglik")
def saglik():
    return jsonify({"durum":"ok","anahtar_tanimli":bool(API_KEY)})

@app.route("/api/foto/<path:photo_name>")
def foto(photo_name):
    if not API_KEY:
        return _hata("API anahtarı yok.", 500)
    try:
        url = f"https://places.googleapis.com/v1/{photo_name}/media"
        r = requests.get(url, params={"maxWidthPx":400,"key":API_KEY}, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            return Response(r.content, content_type=r.headers.get("content-type","image/jpeg"))
        return _hata("Fotoğraf alınamadı.", r.status_code)
    except Exception as e:
        return _hata(str(e), 502)

@app.route("/api/restoranlar")
def restoranlar():
    if not API_KEY:
        return _hata("Sunucuda GOOGLE_API_KEY tanımlı değil.", 500)
    metin = (request.args.get("metin") or "").strip()
    lat   = request.args.get("lat")
    lng   = request.args.get("lng")
    tur   = (request.args.get("tur") or "").strip()
    try:
        if metin:
            sorgu = f"{metin} {tur} restoran" if tur else f"{metin} restoran"
            r = requests.post(f"{PLACES_BASE}:searchText",
                headers=_headers(FIELD_MASK_LIST),
                json={"textQuery":sorgu,"maxResultCount":20,"languageCode":"tr","regionCode":"TR","includedType":"restaurant"},
                timeout=12)
        elif lat and lng:
            try: yaricap = float(request.args.get("yaricap",2500))
            except: yaricap = 2500.0
            yaricap = max(50.0, min(yaricap, 50000.0))
            if tur:
                r = requests.post(f"{PLACES_BASE}:searchText",
                    headers=_headers(FIELD_MASK_LIST),
                    json={"textQuery":f"{tur} restoran","maxResultCount":20,"languageCode":"tr","regionCode":"TR","includedType":"restaurant",
                          "locationBias":{"circle":{"center":{"latitude":float(lat),"longitude":float(lng)},"radius":yaricap}}},
                    timeout=12)
            else:
                r = requests.post(f"{PLACES_BASE}:searchNearby",
                    headers=_headers(FIELD_MASK_LIST),
                    json={"includedTypes":["restaurant"],"maxResultCount":20,"languageCode":"tr","regionCode":"TR",
                          "locationRestriction":{"circle":{"center":{"latitude":float(lat),"longitude":float(lng)},"radius":yaricap}}},
                    timeout=12)
        else:
            return _hata("'metin' veya 'lat'+'lng' gerekli.")
        if r.status_code != 200:
            try: detay = r.json().get("error",{}).get("message",r.text[:300])
            except: detay = r.text[:300]
            return _hata(f"Google API hatası ({r.status_code}): {detay}", r.status_code)
        data = r.json()
        return jsonify({"kaynak":"google","sayi":len(data.get("places",[])),"restoranlar":[_fmt_place(p) for p in data.get("places",[])]})
    except requests.exceptions.Timeout:
        return _hata("Zaman aşımı.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)

@app.route("/api/rota-restoranlar", methods=["POST"])
def rota_restoranlar():
    """Rota üzerindeki restoranları bulur.
    Frontend'den gelen rota noktaları (polyline) boyunca örnekleme yapıp,
    her örnekleme noktasının çevresinde restoran arar, tekilleştirir."""
    if not API_KEY:
        return _hata("Sunucuda GOOGLE_API_KEY tanımlı değil.", 500)
    try:
        veri = request.get_json(force=True) or {}
        noktalar = veri.get("noktalar") or []   # [[lat,lng], [lat,lng], ...]
        tur = (veri.get("tur") or "").strip()
        yaricap = float(veri.get("yaricap", 2500))
        yaricap = max(500.0, min(yaricap, 3000.0))  # 0.5-3 km arası
        if len(noktalar) < 2:
            return _hata("En az 2 rota noktası gerekli.")

        # Rota çok uzunsa örnekleme yap: en fazla 12 arama noktası
        # (Google API maliyeti ve süre için sınır)
        MAX_NOKTA = 12
        adim = max(1, len(noktalar) // MAX_NOKTA)
        ornek_noktalar = noktalar[::adim][:MAX_NOKTA]

        bulunanlar = {}  # place_id -> restoran (tekilleştirme)
        for nk in ornek_noktalar:
            try:
                lat, lng = float(nk[0]), float(nk[1])
            except (ValueError, IndexError, TypeError):
                continue
            try:
                if tur:
                    r = requests.post(f"{PLACES_BASE}:searchText",
                        headers=_headers(FIELD_MASK_LIST),
                        json={"textQuery": f"{tur} restoran", "maxResultCount": 10,
                              "languageCode": "tr", "regionCode": "TR", "includedType": "restaurant",
                              "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": yaricap}}},
                        timeout=10)
                else:
                    r = requests.post(f"{PLACES_BASE}:searchNearby",
                        headers=_headers(FIELD_MASK_LIST),
                        json={"includedTypes": ["restaurant"], "maxResultCount": 10,
                              "languageCode": "tr", "regionCode": "TR",
                              "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": yaricap}}},
                        timeout=10)
                if r.status_code == 200:
                    for p in r.json().get("places", []):
                        fp = _fmt_place(p)
                        if fp.get("id") and fp["id"] not in bulunanlar:
                            bulunanlar[fp["id"]] = fp
            except requests.exceptions.RequestException:
                continue  # bir nokta hata verirse diğerlerine devam

        liste = list(bulunanlar.values())
        return jsonify({"kaynak": "google", "sayi": len(liste), "restoranlar": liste,
                        "arama_noktasi": len(ornek_noktalar)})
    except Exception as e:
        return _hata(f"Rota arama hatası: {e}", 500)


@app.route("/api/detay/<place_id>")
def detay(place_id):
    if not API_KEY:
        return _hata("API anahtarı yok.", 500)
    if not place_id or not place_id.startswith("ChI"):
        return _hata("Geçersiz place_id.", 400)
    try:
        headers = _headers(FIELD_MASK_DETAIL)
        headers["Accept-Language"] = "tr"
        r = requests.get(f"{PLACES_BASE}/{place_id}",
            headers=headers, params={"languageCode":"tr"}, timeout=12)
        if r.status_code != 200:
            try: msg = r.json().get("error",{}).get("message",r.text[:300])
            except: msg = r.text[:300]
            return _hata(f"Google API hatası ({r.status_code}): {msg}", r.status_code)
        p = r.json()
        result = _fmt_place(p)
        result["yorumlar"] = _fmt_reviews(p.get("reviews"))
        hours = p.get("regularOpeningHours",{})
        result["acik_mi"]  = hours.get("openNow")
        result["saatler"]  = hours.get("weekdayDescriptions",[])
        result["telefon"]  = p.get("internationalPhoneNumber","")
        result["website"]  = p.get("websiteUri","")
        # Alkol servisi (Google'ın resmi verisi)
        bira  = p.get("servesBeer")
        sarap = p.get("servesWine")
        kokteyl = p.get("servesCocktails")
        # Üçü de None ise Google bu mekan için bilgi vermemiş = belirsiz
        if bira is None and sarap is None and kokteyl is None:
            result["alkol"] = None        # bilgi yok
        elif bira or sarap or kokteyl:
            result["alkol"] = True         # alkol servisi var
        else:
            result["alkol"] = False        # alkol servisi yok
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
    app.run(host="0.0.0.0", port=port, debug=False)
