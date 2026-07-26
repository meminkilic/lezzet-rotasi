# -*- coding: utf-8 -*-
"""
Rotaste — Proxy Sunucusu
Geliştirici: Mehmet Emin KILIÇ — V1.12.23
"""
import os, math, requests
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
    "places.servesBeer","places.servesWine","places.servesCocktails",
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

# ============================================================
# ROTA KORİDOR ANALİZİ — geometri yardımcıları
# Türkiye enlemlerinde (36-42°) equirectangular projeksiyon
# segment başına ihmal edilebilir hata verir; koridor filtresi
# için gereğinden fazla hassas.
# ============================================================
_DEG_LAT_M = 111132.0   # 1° enlem ≈ metre

def _haversine(lat1, lng1, lat2, lng2):
    """İki nokta arası büyük daire mesafesi (metre)."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _projeksiyon_kur(noktalar):
    """Polyline'ı yerel düzleme taşır. Ekvatoral düzeltme: Δlng·cos(φ).
    Döner: (xy_listesi, lat0) — xy metre cinsinden."""
    lat0 = sum(n[0] for n in noktalar) / len(noktalar)
    k = math.cos(math.radians(lat0)) * _DEG_LAT_M   # 1° boylam ≈ metre (bu enlemde)
    return [(n[1] * k, n[0] * _DEG_LAT_M) for n in noktalar], lat0, k

def _nokta_segment_mesafe(px, py, ax, ay, bx, by):
    """Noktadan doğru parçasına en kısa mesafe (düzlemde, metre)."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    # Projeksiyon parametresi t, [0,1] aralığına kırpılır (segment dışına taşmasın)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx*dx + dy*dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t*dx), py - (ay + t*dy))

def _polyline_mesafe(lat, lng, xy, k):
    """Noktadan polyline'a en kısa mesafe (metre). Klasik point-to-line distance:
    tüm segmentlere dik mesafenin minimumu."""
    px, py = lng * k, lat * _DEG_LAT_M
    en_kisa = float("inf")
    for i in range(len(xy) - 1):
        ax, ay = xy[i]
        bx, by = xy[i+1]
        # Ucuz ön eleme: segment bbox'ı zaten çok uzaksa hesaplama yapma
        if min(ax, bx) - en_kisa > px or max(ax, bx) + en_kisa < px:
            if min(ay, by) - en_kisa > py or max(ay, by) + en_kisa < py:
                continue
        d = _nokta_segment_mesafe(px, py, ax, ay, bx, by)
        if d < en_kisa:
            en_kisa = d
            if en_kisa == 0.0:
                break
    return en_kisa

def _mesafe_bazli_ornekle(noktalar, adim_m, maks_nokta):
    """Polyline'ı kümülatif mesafeye göre örnekler (nokta sayısına göre DEĞİL).
    Böylece uzun rotalarda örnekler arası boşluk kalmaz."""
    if len(noktalar) < 2:
        return list(noktalar), 0.0
    # Kümülatif mesafe
    kum = [0.0]
    for i in range(1, len(noktalar)):
        kum.append(kum[-1] + _haversine(noktalar[i-1][0], noktalar[i-1][1],
                                        noktalar[i][0],   noktalar[i][1]))
    toplam = kum[-1]
    # Maksimum nokta sınırını aşmamak için adımı adaptif büyüt
    if toplam / adim_m > maks_nokta - 1:
        adim_m = toplam / (maks_nokta - 1)
    ornekler, hedef, j = [noktalar[0]], adim_m, 1
    while hedef < toplam and len(ornekler) < maks_nokta:
        while j < len(kum) and kum[j] < hedef:
            j += 1
        if j >= len(kum):
            break
        ornekler.append(noktalar[j])
        hedef += adim_m
    if len(ornekler) < maks_nokta and noktalar[-1] not in ornekler:
        ornekler.append(noktalar[-1])
    return ornekler, toplam

def _alkol_durumu(p):
    bira  = p.get("servesBeer")
    sarap = p.get("servesWine")
    kokteyl = p.get("servesCocktails")
    if bira is None and sarap is None and kokteyl is None:
        return None        # bilgi yok
    elif bira or sarap or kokteyl:
        return True         # alkol servisi var
    else:
        return False        # alkol servisi yok

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
        "alkol":   _alkol_durumu(p),
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

@app.route("/mail-logo.png")
def mail_logo():
    return _serve_file("mail-logo.png","image/png")

@app.route("/google78e2bcd15de4f807.html")
def google_site_verification():
    return _serve_file("google78e2bcd15de4f807.html","text/html")

@app.route("/gizlilik")
def gizlilik():
    try:
        with open(os.path.join(BASE_DIR,"gizlilik.html"),encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Sayfa bulunamadı.", 404

@app.route("/privacy")
def privacy_redirect():
    return gizlilik()

@app.route("/sartlar")
def sartlar():
    try:
        with open(os.path.join(BASE_DIR,"sartlar.html"),encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Sayfa bulunamadı.", 404

@app.route("/terms")
def terms_redirect():
    return sartlar()

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
            gövde = {"textQuery":sorgu,"maxResultCount":20,"languageCode":"tr","regionCode":"TR","includedType":"restaurant"}
            if lat and lng:
                try: yaricap = float(request.args.get("yaricap", 15000))
                except: yaricap = 15000.0
                yaricap = max(50.0, min(yaricap, 50000.0))
                gövde["locationBias"] = {"circle":{"center":{"latitude":float(lat),"longitude":float(lng)},"radius":yaricap}}
            r = requests.post(f"{PLACES_BASE}:searchText",
                headers=_headers(FIELD_MASK_LIST),
                json=gövde,
                timeout=12)
        elif lat and lng:
            try: yaricap = float(request.args.get("yaricap",15000))
            except: yaricap = 15000.0
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
        ham = veri.get("noktalar") or []   # [[lat,lng], [lat,lng], ...]
        tur = (veri.get("tur") or "").strip()
        yaricap = float(veri.get("yaricap", 2500))
        yaricap = max(500.0, min(yaricap, 3000.0))  # 0.5-3 km arası (arama yarıçapı)
        # Koridor yarı genişliği: rotanın sağına/soluna kaç metre bakılacak
        koridor = float(veri.get("koridor", 2000))
        koridor = max(250.0, min(koridor, yaricap))  # arama yarıçapını aşamaz

        # Geçersiz/bozuk noktaları ayıkla
        noktalar = []
        for n in ham:
            try:
                noktalar.append((float(n[0]), float(n[1])))
            except (ValueError, IndexError, TypeError):
                continue
        if len(noktalar) < 2:
            return _hata("En az 2 rota noktası gerekli.")

        # --- Mesafe bazlı örnekleme ---
        # Yarıçapı R olan daireler, yarı genişliği w olan koridoru boşluksuz
        # kaplasın istiyorsak merkezler arası mesafe <= 2*sqrt(R^2 - w^2) olmalı.
        # (R=2500, w=2000 -> 3000 m). Nokta sayısı MAX_NOKTA ile sınırlı.
        MAX_NOKTA = 24
        if yaricap > koridor:
            ideal_adim = 2.0 * math.sqrt(yaricap*yaricap - koridor*koridor)
        else:
            ideal_adim = yaricap
        ideal_adim = max(1000.0, ideal_adim)
        ornek_noktalar, rota_uzunluk = _mesafe_bazli_ornekle(noktalar, ideal_adim, MAX_NOKTA)

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

        # --- KORİDOR FİLTRESİ ---
        # searchText'in locationBias'ı bir KISIT değil, sadece eğilimdir; bu yüzden
        # rotadan onlarca km uzaktaki güçlü metin eşleşmeleri de dönebiliyor.
        # Burada her sonucun polyline'a gerçek dik mesafesini (point-to-line distance)
        # hesaplayıp koridor dışında kalanları eliyoruz.
        xy, _lat0, k = _projeksiyon_kur(noktalar)
        esik = koridor * 1.15   # sınırdaki iyi mekânlar kıl payı elenmesin diye tolerans

        liste = []
        elenen = 0
        for fp in bulunanlar.values():
            lat, lng = fp.get("lat"), fp.get("lng")
            if lat is None or lng is None:
                elenen += 1
                continue
            d = _polyline_mesafe(float(lat), float(lng), xy, k)
            if d > esik:
                elenen += 1
                continue
            fp["rota_mesafe_m"] = int(round(d))
            liste.append(fp)

        # Rotaya en yakın önce (frontend istediğinde puana göre yeniden sıralayabilir)
        liste.sort(key=lambda x: x.get("rota_mesafe_m", 999999))

        # --- GİZLİ KALİTE EŞİĞİ ---
        # Kullanıcıya gösterilmez; düşük puanlı / güvenilmez yerler listeye hiç girmez.
        # Kademeli emniyet ağı: filtre sonrası çok az sonuç kalırsa eşik gevşer,
        # gerekirse tamamen kalkar. Tenha güzergâhlarda boş ekran çıkmasın diye.
        MIN_SONUC = 5
        kademeler = [(4.0, 10), (3.5, 5), (0.0, 0)]
        secilen = liste
        for puan_esik, yorum_esik in kademeler:
            aday = [r for r in liste
                    if (r.get("rating") or 0) >= puan_esik
                    and (r.get("reviews") or 0) >= yorum_esik]
            if len(aday) >= MIN_SONUC or (puan_esik == 0.0 and yorum_esik == 0):
                secilen = aday
                break
        kalite_elenen = len(liste) - len(secilen)
        liste = secilen

        return jsonify({"kaynak": "google", "sayi": len(liste), "restoranlar": liste,
                        "arama_noktasi": len(ornek_noktalar),
                        "koridor_m": int(koridor), "elenen": elenen,
                        "kalite_elenen": kalite_elenen,
                        "rota_uzunluk_m": int(rota_uzunluk)})
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
        result["alkol"] = _alkol_durumu(p)
        return jsonify(result)
    except requests.exceptions.Timeout:
        return _hata("Zaman aşımı.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)

# ============================================================
# HESAP SİLME (Apple Guideline 5.1.1 gereği)
# Kullanıcı kendi hesabını siler. Güvenlik: kullanıcının access
# token'ı doğrulanır, sonra service_role ile auth kaydı silinir.
# service_role anahtarı SADECE burada (backend) kullanılır.
# ============================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rtkwqemvbfezywitiacx.supabase.co").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

@app.route("/hesap-sil", methods=["POST"])
def hesap_sil():
    if not SUPABASE_SERVICE_KEY:
        return _hata("Hesap silme şu an kullanılamıyor.", 503)
    # 1) Kullanıcının access token'ını al (Authorization: Bearer <token>)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _hata("Oturum bulunamadı.", 401)
    token = auth_header.split("Bearer ", 1)[1].strip()
    if not token:
        return _hata("Geçersiz oturum.", 401)
    try:
        # 2) Token'ı doğrula: bu token gerçekten geçerli bir kullanıcıya mı ait?
        me = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_SERVICE_KEY},
            timeout=10
        )
        if me.status_code != 200:
            return _hata("Oturum doğrulanamadı.", 401)
        user_id = me.json().get("id")
        if not user_id:
            return _hata("Kullanıcı bulunamadı.", 404)
        # 3) service_role ile kullanıcıyı kalıcı olarak sil
        dele = requests.delete(
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "apikey": SUPABASE_SERVICE_KEY},
            timeout=10
        )
        if dele.status_code in (200, 204):
            return jsonify({"ok": True, "mesaj": "Hesap silindi"})
        return _hata("Hesap silinemedi, tekrar deneyin.", 502)
    except requests.exceptions.Timeout:
        return _hata("Zaman aşımı, tekrar deneyin.", 504)
    except requests.exceptions.RequestException as e:
        return _hata(f"Bağlantı hatası: {e}", 502)


if __name__ == "__main__":
    if not API_KEY:
        print("\n[UYARI] GOOGLE_API_KEY tanımlı değil!\n")
    else:
        print("\n[OK] API anahtarı yüklendi.\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
