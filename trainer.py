# trainer.py — Dosya işleme ve eğitim motoru
import os, json, re, time, requests, pickle
from embed import build_and_save, embed_with, load_state
import chromadb

UPLOADS_DIR = "uploads"
DBS_DIR     = "dbs"

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(DBS_DIR, exist_ok=True)

def temizle(metin):
    metin = re.sub(r'\n{3,}', '\n\n', metin)
    metin = re.sub(r'[ \t]{2,}', ' ', metin)
    return metin.strip()

def parcala(metin, boyut=2000, adim=1800):
    return [metin] if len(metin) <= boyut else [metin[i:i+boyut] for i in range(0, len(metin), adim)]

# ── Dosya okuyucular ──────────────────────────────────────────────

def pdf_oku(yol):
    try:
        import fitz
        doc = fitz.open(yol)
        t = "".join(s.get_text()+"\n" for s in doc)
        doc.close()
        return temizle(t) if len(t.strip()) > 50 else None
    except: return None

def word_oku(yol):
    try:
        from docx import Document
        m = "\n".join(p.text for p in Document(yol).paragraphs if p.text.strip())
        return temizle(m) if len(m) > 50 else None
    except: return None

def resim_oku(yol):
    try:
        import pytesseract
        from PIL import Image
        m = pytesseract.image_to_string(Image.open(yol), lang="tur+eng")
        return temizle(m) if len(m) > 20 else None
    except: return None

def url_oku(url):
    try:
        from bs4 import BeautifulSoup
        s = requests.Session(); s.trust_env = True
        r = s.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'lxml')
        for tag in soup(['nav','footer','script','style','header','form','button','img']): tag.decompose()
        ic = soup.find('article') or soup.find('main') or soup.find(class_='entry-content') or soup.find('body')
        if ic:
            m = temizle(ic.get_text(separator='\n'))
            return m if len(m) > 80 else None
    except: return None

def txt_oku(yol):
    try:
        c = open(yol,'r',encoding='utf-8',errors='ignore').read().strip()
        if c.startswith('http'): return url_oku(c.split()[0])
        return temizle(c) if len(c) > 20 else None
    except: return None

def dosya_isle(yol):
    uz = os.path.splitext(yol)[1].lower()
    if uz == '.pdf': return pdf_oku(yol)
    if uz == '.docx': return word_oku(yol)
    if uz in ['.jpg','.jpeg','.png','.bmp','.webp','.tiff']: return resim_oku(yol)
    if uz in ['.txt','.md']: return txt_oku(yol)
    return None

# ── Veritabanı işlemleri ─────────────────────────────────────────

def kb_yolu(asistan_id):
    return os.path.join(DBS_DIR, f"{asistan_id}_kb.json")

def vocab_yolu(asistan_id):
    return os.path.join(DBS_DIR, f"{asistan_id}_vocab.pkl")

def chroma_yolu(asistan_id):
    return os.path.join(DBS_DIR, f"{asistan_id}_chroma")

def kb_yukle(asistan_id):
    yol = kb_yolu(asistan_id)
    if os.path.exists(yol):
        with open(yol,'r',encoding='utf-8') as f: return json.load(f)
    return []

def kb_kaydet(asistan_id, kb):
    with open(kb_yolu(asistan_id),'w',encoding='utf-8') as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)

def egit(asistan_id):
    """Bilgi tabanını vektör veritabanına dönüştür"""
    kb = kb_yukle(asistan_id)
    if not kb: return False, "Bilgi tabanı boş"

    docs = [item["baslik"]+": "+item["metin"] for item in kb]

    # Vocab oluştur
    vocab, idf = build_and_save(docs, path=vocab_yolu(asistan_id))

    # Chroma'ya yükle
    client = chromadb.PersistentClient(path=chroma_yolu(asistan_id))
    try: client.delete_collection("kb")
    except: pass
    col = client.create_collection("kb")

    for item in kb:
        tam = item["baslik"]+": "+item["metin"]
        vec = embed_with(tam, vocab, idf)
        col.add(ids=[item["id"]], embeddings=[vec], documents=[tam], metadatas=[{"baslik":item["baslik"]}])

    return True, f"{len(kb)} kayıt eğitildi"

def ara(asistan_id, soru, n=2):
    """Soruya en yakın bilgi parçalarını bul"""
    vp = vocab_yolu(asistan_id)
    cp = chroma_yolu(asistan_id)
    if not os.path.exists(vp) or not os.path.exists(cp):
        return ""
    state = load_state(vp)
    if not state: return ""
    vec = embed_with(soru, state['vocab'], state['idf'])
    client = chromadb.PersistentClient(path=cp)
    try:
        col = client.get_collection("kb")
        res = col.query(query_embeddings=[vec], n_results=n)
        docs = res["documents"][0] if res["documents"] else []
        return "\n\n".join(docs)
    except: return ""

def dosya_ekle_kb(asistan_id, dosya_yolu, baslik=None):
    """Dosyayı işle ve bilgi tabanına ekle"""
    metin = dosya_isle(dosya_yolu)
    if not metin: return False, "İçerik çıkarılamadı"

    kb = kb_yukle(asistan_id)
    ad = baslik or os.path.splitext(os.path.basename(dosya_yolu))[0]
    kid = re.sub(r'[^a-z0-9]','_', ad.lower())

    # Eskiyi temizle
    kb = [k for k in kb if not k["id"].startswith(kid)]

    for i, p in enumerate(parcala(metin)):
        uid = f"{kid}_{i}" if len(parcala(metin)) > 1 else kid
        bl  = f"{ad} (Bölüm {i+1})" if len(parcala(metin)) > 1 else ad
        kb.append({"id": uid, "baslik": bl, "kaynak": dosya_yolu, "metin": p})

    kb_kaydet(asistan_id, kb)
    return True, f"{len(parcala(metin))} parça eklendi"

def url_ekle_kb(asistan_id, url, baslik=None):
    """URL'yi çek ve bilgi tabanına ekle"""
    metin = url_oku(url)
    if not metin: return False, "Sayfa içeriği alınamadı"

    kb = kb_yukle(asistan_id)
    ad = baslik or url.split('//')[-1].split('/')[0]
    kid = re.sub(r'[^a-z0-9]','_', ad.lower())
    kb = [k for k in kb if not k["id"].startswith(kid)]

    for i, p in enumerate(parcala(metin)):
        uid = f"{kid}_{i}" if len(parcala(metin)) > 1 else kid
        bl  = f"{ad} (Bölüm {i+1})" if len(parcala(metin)) > 1 else ad
        kb.append({"id": uid, "baslik": bl, "kaynak": url, "metin": p})

    kb_kaydet(asistan_id, kb)
    return True, f"{len(parcala(metin))} parça eklendi"

def metin_ekle_kb(asistan_id, metin, baslik):
    """Direkt metin ekle"""
    kb = kb_yukle(asistan_id)
    kid = re.sub(r'[^a-z0-9]','_', baslik.lower())
    kb = [k for k in kb if not k["id"].startswith(kid)]

    for i, p in enumerate(parcala(metin)):
        uid = f"{kid}_{i}" if len(parcala(metin)) > 1 else kid
        bl  = f"{baslik} (Bölüm {i+1})" if len(parcala(metin)) > 1 else baslik
        kb.append({"id": uid, "baslik": bl, "kaynak": "manuel", "metin": p})

    kb_kaydet(asistan_id, kb)
    return True, f"{len(parcala(metin))} parça eklendi"
