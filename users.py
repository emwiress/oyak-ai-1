# users.py — Kullanıcı yönetimi (JSON tabanlı, veritabanı gerekmez)
import json, os, uuid, hashlib, hmac

USERS_FILE = "users.json"

def _yukle():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE,'r',encoding='utf-8') as f: return json.load(f)
    return {}

def _kaydet(users):
    with open(USERS_FILE,'w',encoding='utf-8') as f: json.dump(users, f, ensure_ascii=False, indent=2)

def sifre_hash(sifre):
    return hashlib.sha256(sifre.encode()).hexdigest()

def kayit(email, sifre, isim, plan="ucretsiz"):
    users = _yukle()
    email = email.lower().strip()
    if email in users:
        return None, "Bu e-posta zaten kayıtlı"
    uid = str(uuid.uuid4())[:8]
    users[email] = {
        "id": uid,
        "isim": isim,
        "email": email,
        "sifre": sifre_hash(sifre),
        "plan": plan,
        "asistanlar": {}
    }
    _kaydet(users)
    return users[email], None

def giris(email, sifre):
    users = _yukle()
    email = email.lower().strip()
    user = users.get(email)
    if not user or user["sifre"] != sifre_hash(sifre):
        return None, "E-posta veya şifre hatalı"
    return user, None

def kullanici_al(email):
    return _yukle().get(email.lower().strip())

def asistan_ekle(email, asistan_id, isim, groq_key):
    users = _yukle()
    email = email.lower().strip()
    if email not in users: return None
    users[email]["asistanlar"][asistan_id] = {
        "id": asistan_id,
        "isim": isim,
        "groq_key": groq_key,
        "kayit_sayisi": 0,
        "soru_sayisi": 0
    }
    _kaydet(users)
    return users[email]["asistanlar"][asistan_id]

def asistan_guncelle(email, asistan_id, alan, deger):
    users = _yukle()
    email = email.lower().strip()
    if email in users and asistan_id in users[email]["asistanlar"]:
        users[email]["asistanlar"][asistan_id][alan] = deger
        _kaydet(users)

def soru_say(email, asistan_id):
    users = _yukle()
    email = email.lower().strip()
    if email in users and asistan_id in users[email]["asistanlar"]:
        users[email]["asistanlar"][asistan_id]["soru_sayisi"] = \
            users[email]["asistanlar"][asistan_id].get("soru_sayisi", 0) + 1
        _kaydet(users)
