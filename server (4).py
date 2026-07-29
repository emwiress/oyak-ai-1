from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
import os, json, uuid, re, requests as req_lib
from werkzeug.utils import secure_filename
from embed import embed, build_and_save, load_state, embed_with
from users import kayit, giris, kullanici_al, asistan_ekle, asistan_guncelle, soru_say
from trainer import dosya_ekle_kb, url_ekle_kb, metin_ekle_kb, egit, ara, kb_yukle, UPLOADS_DIR
import chromadb

app = Flask(__name__, static_folder="static")
CORS(app, origins="*")
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET", "oyak-platform-secret-2026")
jwt = JWTManager(app)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.1-8b-instant"
N_RESULTS    = 2

# ÖYAK veritabanı
try:
    client     = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_collection("oyak_kb")
    print("ÖYAK veritabanı bağlandı.")
except Exception as e:
    print(f"ÖYAK veritabanı hatası: {e}")
    client = None
    collection = None

OYAK_SYSTEM_PROMPT = """Sen ÖYAK (Kıbrıs Türk Öğretmenler Yardımlaşma Kooperatifi Ltd.) için çalışan resmi dijital asistansın.

Temel bilgiler:
- ÖYAK 5 Temmuz 1978'de kurulmuştur.
- İletişim: oyak@oyakkoop.com | WhatsApp: 0533 850 33 12 | Tel: 227 61 17
- Şubeler: Lefkoşa, Girne, Güzelyurt, Gazi Mağusa, İskele

Görevin:
- BAĞLAM bölümündeki bilgileri kullanarak soruları yanıtla.
- Bilgi yoksa: "Detaylı bilgi için oyak@oyakkoop.com veya WhatsApp 0533 850 33 12"
- Her zaman Türkçe, kısa, net ve profesyonel cevap ver.
- Asla bilgi uydurma, asla İngilizce yazma.
"""

def sayi_cikar(metin):
    metin = metin.lower()
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*milyon', metin)
    if m: return float(m.group(1).replace(',', '.')) * 1_000_000
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*bin', metin)
    if m: return float(m.group(1).replace(',', '.')) * 1_000
    m = re.search(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?', metin)
    if m:
        raw = m.group(0)
        if '.' in raw and ',' not in raw: raw = raw.replace('.', '')
        raw = raw.replace(',', '.')
        return float(raw)
    return None

def vade_cikar(metin):
    metin = metin.lower()
    m = re.search(r'(\d+)\s*(yil|sene|yıl)', metin)
    if m: return int(m.group(1)) * 12
    m = re.search(r'(\d+)\s*ay', metin)
    if m: return int(m.group(1))
    if '1 yıl' in metin or 'bir yıl' in metin or 'yıllık' in metin: return 12
    if '6 ay' in metin or 'altı ay' in metin: return 6
    if '3 ay' in metin or 'üç ay' in metin: return 3
    return None

def para_birimi_cikar(metin):
    metin = metin.lower()
    if any(k in metin for k in ['dolar','usd','$']): return 'USD'
    if any(k in metin for k in ['euro','eur','€']): return 'EUR'
    if any(k in metin for k in ['sterlin','gbp','£','stg']): return 'GBP'
    return 'TL'

def fmt(n, birim='TL'):
    return f"{n:,.2f}".replace(',','X').replace('.',',').replace('X','.') + f" {birim}"

def mevduat_hesapla(soru):
    tutar = sayi_cikar(soru)
    vade  = vade_cikar(soru)
    birim = para_birimi_cikar(soru)
    if not tutar or not vade: return None
    ORANLAR = {
        'TL': {
            1:  [(199999,29.50),(500000,31.00),(1000000,31.50),(3000000,34.00),(5000000,35.00),(10000000,36.00),(float('inf'),37.00)],
            3:  [(199999,28.00),(500000,29.50),(1000000,30.00),(3000000,32.50),(5000000,33.50),(10000000,34.50),(float('inf'),35.50)],
            6:  [(199999,27.00),(500000,28.50),(1000000,29.00),(3000000,31.50),(5000000,32.50),(10000000,33.50),(float('inf'),34.50)],
            12: [(199999,27.00),(500000,28.50),(1000000,29.00),(3000000,31.50),(5000000,32.50),(10000000,33.50),(float('inf'),34.50)],
        },
        'GBP': {1:[(49999,0.80),(100000,0.90),(150000,0.95),(float('inf'),1.00)],3:[(49999,1.10),(100000,1.20),(150000,1.25),(float('inf'),1.30)],6:[(49999,1.20),(100000,1.30),(150000,1.35),(float('inf'),1.40)],12:[(49999,1.30),(100000,1.40),(150000,1.45),(float('inf'),1.50)]},
        'USD': {1:[(49999,1.40),(100000,1.45),(150000,1.50),(float('inf'),1.55)],3:[(49999,1.60),(100000,1.65),(150000,1.70),(float('inf'),1.75)],6:[(49999,1.90),(100000,1.95),(150000,2.00),(float('inf'),2.05)],12:[(49999,2.45),(100000,2.50),(150000,2.55),(float('inf'),2.60)]},
        'EUR': {1:[(49999,0.45),(100000,0.50),(150000,0.52),(float('inf'),0.54)],3:[(49999,0.45),(100000,0.50),(150000,0.52),(float('inf'),0.54)],6:[(49999,0.45),(100000,0.50),(150000,0.52),(float('inf'),0.54)],12:[(49999,0.45),(100000,0.50),(150000,0.52),(float('inf'),0.54)]},
    }
    vadeler=[1,3,6,12]
    ev=min(vadeler,key=lambda x:abs(x-vade))
    tablo=ORANLAR.get(birim,ORANLAR['TL'])
    vt=tablo.get(ev,list(tablo.values())[0])
    oran=next(o for(s,o) in vt if tutar<=s)
    brut=tutar*(oran/100)*(vade/12)
    stopaj=brut*0.25
    net=brut-stopaj
    toplam=tutar+net
    vl=f"{vade//12} yıl" if vade%12==0 else f"{vade} ay"
    vn=f"\n⚠️ {vade} aylık vade için en yakın {ev} aylık oran uygulandı." if ev!=vade else ""
    return (f"📊 **Mevduat Faizi Hesabı**\n\n💰 Anapara: {fmt(tutar,birim)}\n📅 Vade: {vl}\n📈 Yıllık faiz oranı: %{oran}\n➕ Brüt faiz: {fmt(brut,birim)}\n➖ Stopaj (%25): {fmt(stopaj,birim)}\n✅ Net faiz getirisi: {fmt(net,birim)}\n🏦 Vade sonu toplam: {fmt(toplam,birim)}{vn}\n\n⚠️ Gerçek ÖYAK faiz oranlarıyla hesaplanmıştır.")

def kredi_hesapla(soru):
    tutar=sayi_cikar(soru)
    vade=vade_cikar(soru)
    birim=para_birimi_cikar(soru)
    if not tutar or not vade: return None
    KREDI={'TL':{12:47,24:49,48:52,60:55},'GBP':{12:4,24:4,48:4,60:4},'USD':{12:4,24:4,48:4,60:4},'EUR':{12:3.5,24:3.5,48:3.5,60:3.5}}
    vadeler=[12,24,48,60]
    ev=min(vadeler,key=lambda x:abs(x-vade))
    tablo=KREDI.get(birim,KREDI['TL'])
    oran=tablo.get(ev,list(tablo.values())[-1])
    ar=(oran/100)/12
    taksit=(tutar*ar)/(1-(1+ar)**-vade) if ar else tutar/vade
    toplam=taksit*vade
    tf=toplam-tutar
    vl=f"{vade//12} yıl" if vade%12==0 else f"{vade} ay"
    vn=f"\n⚠️ {vade} aylık vade için en yakın {ev} aylık oran uygulandı." if ev!=vade else ""
    return (f"📊 **Kredi Taksit Hesabı**\n\n💰 Kredi tutarı: {fmt(tutar,birim)}\n📅 Vade: {vl}\n📈 Yıllık faiz oranı: %{oran}\n💳 Aylık taksit: {fmt(taksit,birim)}\n💸 Toplam geri ödeme: {fmt(toplam,birim)}\n📌 Toplam faiz maliyeti: {fmt(tf,birim)}{vn}\n\n⚠️ Gerçek ÖYAK kredi oranlarıyla hesaplanmıştır.")

def niyet_tespit(soru):
    s=soru.lower()
    if any(k in s for k in ['faiz','mevduat','getiri','birik','yatır']): return 'mevduat'
    if any(k in s for k in ['kredi','taksit','borç','ödeme']): return 'kredi'
    return 'genel'

def groq_headers(key=None):
    return {"Authorization":f"Bearer {key or GROQ_API_KEY}","Content-Type":"application/json"}

def oyak_retrieve(question):
    if collection is None: return ""
    try:
        q_emb=embed(question)
        results=collection.query(query_embeddings=[q_emb],n_results=N_RESULTS)
        docs=results["documents"][0] if results["documents"] else []
        return "\n\n".join(docs)
    except: return ""

def groq_stream(soru, baglam, sistem, key):
    r=req_lib.post(GROQ_URL,headers=groq_headers(key),json={"model":GROQ_MODEL,"messages":[{"role":"system","content":sistem},{"role":"user","content":f"BAĞLAM:\n{baglam}\n\nSORU: {soru}"}],"max_tokens":400,"temperature":0.1,"stream":True},stream=True)
    for line in r.iter_lines():
        if line:
            line=line.decode("utf-8")
            if line.startswith("data: "):
                data=line[6:]
                if data=="[DONE]":
                    yield f"data: {json.dumps({'token':'','done':True})}\n\n"; break
                try:
                    chunk=json.loads(data)
                    token=chunk["choices"][0]["delta"].get("content","")
                    yield f"data: {json.dumps({'token':token,'done':False})}\n\n"
                except: pass

# ══ ÖYAK ENDPOINTS ══════════════════════════════════════════════

@app.route("/ask", methods=["POST"])
def ask():
    data=request.get_json(force=True)
    question=(data or {}).get("soru","").strip()
    if not question: return jsonify({"hata":"Soru boş"}),400
    if not GROQ_API_KEY: return jsonify({"hata":"GROQ_API_KEY eksik"}),500
    try:
        niyet=niyet_tespit(question)
        tutar=sayi_cikar(question)
        vade=vade_cikar(question)
        if niyet=='mevduat' and tutar and vade:
            s=mevduat_hesapla(question)
            if s: return jsonify({"cevap":s})
        if niyet=='kredi' and tutar and vade:
            s=kredi_hesapla(question)
            if s: return jsonify({"cevap":s})
        context=oyak_retrieve(question)
        r=req_lib.post(GROQ_URL,headers=groq_headers(),json={"model":GROQ_MODEL,"messages":[{"role":"system","content":OYAK_SYSTEM_PROMPT},{"role":"user","content":f"BAĞLAM:\n{context}\n\nSORU: {question}"}],"max_tokens":400,"temperature":0.1,"stream":False})
        r.raise_for_status()
        return jsonify({"cevap":r.json()["choices"][0]["message"]["content"].strip()})
    except Exception as e: return jsonify({"hata":str(e)}),500

@app.route("/ask-stream", methods=["POST"])
def ask_stream():
    data=request.get_json(force=True)
    question=(data or {}).get("soru","").strip()
    if not question: return jsonify({"hata":"Soru boş"}),400
    niyet=niyet_tespit(question)
    tutar=sayi_cikar(question)
    vade=vade_cikar(question)
    def tek(s):
        yield f"data: {json.dumps({'token':s,'done':True})}\n\n"
    if niyet=='mevduat' and tutar and vade:
        s=mevduat_hesapla(question)
        if s: return Response(stream_with_context(tek(s)),mimetype="text/event-stream",headers={"Cache-Control":"no-cache"})
    if niyet=='kredi' and tutar and vade:
        s=kredi_hesapla(question)
        if s: return Response(stream_with_context(tek(s)),mimetype="text/event-stream",headers={"Cache-Control":"no-cache"})
    context=oyak_retrieve(question)
    return Response(stream_with_context(groq_stream(question,context,OYAK_SYSTEM_PROMPT,GROQ_API_KEY)),mimetype="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

OYAK_WIDGET=r"""(function(){var SUNUCU="__SUNUCU__";var HIZLI=["Vezne saatleri nedir?","1 milyon TL 1 yıllık mevduat faizi","100.000 TL kredi 3 yıl taksit","Şube adresleri","Anlaşmalı iş yerleri"];var css=`#oyak-root,#oyak-root *{box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif}#oyak-bubble{position:fixed;bottom:24px;right:24px;z-index:99999;width:62px;height:62px;border-radius:50%;background:linear-gradient(135deg,#0b3d67,#1a6fa8);box-shadow:0 6px 24px rgba(11,61,103,.4);display:flex;align-items:center;justify-content:center;cursor:pointer;border:none;transition:transform .2s}#oyak-bubble:hover{transform:scale(1.08)}#oyak-bubble svg{width:30px;height:30px;fill:#fff}#oyak-badge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;background:#e53e3e;border-radius:50%;border:2px solid #fff;display:none}#oyak-panel{position:fixed;bottom:100px;right:24px;z-index:99999;width:370px;max-width:93vw;height:520px;max-height:80vh;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 16px 48px rgba(0,0,0,.2);display:none;flex-direction:column;border:1px solid #dce8f0}#oyak-panel.acik{display:flex}#oyak-header{background:linear-gradient(135deg,#0b3d67,#1a6fa8);padding:16px;display:flex;align-items:center;gap:12px;color:#fff}#oyak-header .avatar{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.15);display:flex;align-items:center;justify-content:center;flex-shrink:0}#oyak-header .avatar svg{width:22px;height:22px;fill:#fff}#oyak-header .isim{font-weight:700;font-size:14px}#oyak-header .durum{font-size:11px;opacity:.8;margin-top:2px;display:flex;align-items:center;gap:5px}#oyak-header .yesilnokta{width:7px;height:7px;background:#4ade80;border-radius:50%}#oyak-kapat{margin-left:auto;background:none;border:none;color:#fff;font-size:20px;cursor:pointer;opacity:.8;line-height:1;padding:0}#oyak-mesajlar{flex:1;overflow-y:auto;padding:16px;background:#f0f4f8;display:flex;flex-direction:column;gap:12px}.oyak-m{max-width:85%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.6}.oyak-m.bot{background:#fff;border:1px solid #dce8f0;align-self:flex-start;border-bottom-left-radius:3px;color:#1a2b3c}.oyak-m.user{background:linear-gradient(135deg,#0b3d67,#1a6fa8);color:#fff;align-self:flex-end;border-bottom-right-radius:3px}.oyak-zaman{font-size:10.5px;opacity:.5;margin-top:4px}.oyak-typing span{width:7px;height:7px;border-radius:50%;background:#a0b4c4;animation:oyak-ziplama 1.2s infinite ease-in-out;display:inline-block;margin:0 2px}.oyak-typing span:nth-child(2){animation-delay:.18s}.oyak-typing span:nth-child(3){animation-delay:.36s}@keyframes oyak-ziplama{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-5px);opacity:1}}#oyak-hizli{display:flex;flex-wrap:wrap;gap:6px;padding:0 16px 12px;background:#f0f4f8}.oyak-chip{background:#fff;border:1.5px solid #c5d8e8;color:#0b3d67;font-size:11.5px;padding:5px 11px;border-radius:20px;cursor:pointer}.oyak-chip:hover{background:#ddeeff}#oyak-form{display:flex;gap:8px;padding:12px;border-top:1px solid #dce8f0;background:#fff}#oyak-input{flex:1;border:1.5px solid #c5d8e8;border-radius:22px;padding:10px 16px;font-size:13.5px;outline:none}#oyak-input:focus{border-color:#1a6fa8}#oyak-gonder{background:linear-gradient(135deg,#0b3d67,#1a6fa8);border:none;color:#fff;width:42px;height:42px;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}#oyak-gonder svg{width:18px;height:18px;fill:#fff}.oyak-cursor{display:inline-block;width:2px;height:14px;background:#0b3d67;margin-left:2px;animation:blink .7s infinite}@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}`;var s=document.createElement('style');s.textContent=css;document.head.appendChild(s);var root=document.createElement('div');root.id='oyak-root';document.body.appendChild(root);root.innerHTML=`<button id="oyak-bubble"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.02 2 11c0 2.4 1.06 4.55 2.8 6.13L4 22l5.14-1.67C10.02 20.77 11 21 12 21c5.52 0 10-4.02 10-9S17.52 2 12 2z"/></svg><div id="oyak-badge"></div></button><div id="oyak-panel"><div id="oyak-header"><div class="avatar"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.02 2 11c0 2.4 1.06 4.55 2.8 6.13L4 22l5.14-1.67C10.02 20.77 11 21 12 21c5.52 0 10-4.02 10-9S17.52 2 12 2z"/></svg></div><div><div class="isim">ÖYAK Dijital Asistan</div><div class="durum"><span class="yesilnokta"></span>Çevrimiçi — Yapay Zeka Destekli</div></div><button id="oyak-kapat">✕</button></div><div id="oyak-mesajlar"></div><div id="oyak-hizli"></div><div id="oyak-form"><input id="oyak-input" type="text" placeholder="Soru sorun veya faiz hesaplatın…" autocomplete="off"/><button id="oyak-gonder"><svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg></button></div></div>`;var bubble=document.getElementById('oyak-bubble'),panel=document.getElementById('oyak-panel'),msgs=document.getElementById('oyak-mesajlar'),hizli=document.getElementById('oyak-hizli'),input=document.getElementById('oyak-input'),badge=document.getElementById('oyak-badge'),ilk=true,aktif=false;function saat(){return new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'});}function mesaj(html,kim){var d=document.createElement('div');d.className='oyak-m '+kim;d.innerHTML=html.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>')+'<div class="oyak-zaman">'+saat()+'</div>';msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d;}function hizliButonlar(){hizli.innerHTML='';HIZLI.forEach(function(q){var b=document.createElement('button');b.className='oyak-chip';b.textContent=q;b.onclick=function(){sor(q);};hizli.appendChild(b);});}function sor(soru){if(!soru.trim()||aktif)return;aktif=true;mesaj(soru.replace(/</g,'&lt;'),'user');input.value='';hizli.innerHTML='';var d=document.createElement('div');d.className='oyak-m bot';var ic=document.createElement('span'),cur=document.createElement('span');cur.className='oyak-cursor';var zaman=document.createElement('div');zaman.className='oyak-zaman';zaman.textContent=saat();d.appendChild(ic);d.appendChild(cur);d.appendChild(zaman);msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;var tam='';fetch(SUNUCU+'/ask-stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({soru:soru})}).then(function(r){var reader=r.body.getReader(),dec=new TextDecoder(),buf='';function oku(){reader.read().then(function(ref){if(ref.done){cur.remove();aktif=false;return;}buf+=dec.decode(ref.value,{stream:true});var satirlar=buf.split('\n');buf=satirlar.pop();satirlar.forEach(function(satir){if(satir.startsWith('data: ')){try{var v=JSON.parse(satir.slice(6));tam+=v.token||'';ic.innerHTML=tam.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>');msgs.scrollTop=msgs.scrollHeight;if(v.done){cur.remove();aktif=false;}}catch(e){}}});oku();});}oku();}).catch(function(){d.innerHTML='Sunucuya ulaşılamadı.<div class="oyak-zaman">'+saat()+'</div>';aktif=false;});}bubble.addEventListener('click',function(){panel.classList.toggle('acik');badge.style.display='none';if(ilk&&panel.classList.contains('acik')){ilk=false;mesaj('Merhaba, ÖYAK Dijital Asistanına hoş geldiniz! 👋\n\nMevduat faizi ve kredi taksiti hesaplayabilir, şube bilgileri, vezne saatleri ve üyelik konularında yardımcı olabilirim.','bot');hizliButonlar();}});document.getElementById('oyak-kapat').addEventListener('click',function(){panel.classList.remove('acik');});document.getElementById('oyak-gonder').addEventListener('click',function(){sor(input.value);});input.addEventListener('keydown',function(e){if(e.key==='Enter')sor(input.value);});setTimeout(function(){if(!panel.classList.contains('acik'))badge.style.display='block';},3000);})();"""

@app.route("/widget.js")
def widget_js():
    sunucu=request.host_url.rstrip('/')
    if request.headers.get('X-Forwarded-Proto')=='https': sunucu='https://'+request.host
    return Response(OYAK_WIDGET.replace('__SUNUCU__',sunucu),mimetype='application/javascript')

# ══ PLATFORM AUTH ENDPOINTS ═════════════════════════════════════

@app.route("/api/kayit",methods=["POST"])
def api_kayit():
    d=request.get_json(force=True) or {}
    email=d.get("email","").strip()
    sifre=d.get("sifre","").strip()
    isim=d.get("isim","").strip()
    if not email or not sifre or not isim: return jsonify({"hata":"Tüm alanlar zorunlu"}),400
    user,hata=kayit(email,sifre,isim)
    if hata: return jsonify({"hata":hata}),400
    token=create_access_token(identity=email)
    return jsonify({"token":token,"kullanici":{"isim":user["isim"],"email":user["email"],"plan":user["plan"]}})

@app.route("/api/giris",methods=["POST"])
def api_giris():
    d=request.get_json(force=True) or {}
    user,hata=giris(d.get("email",""),d.get("sifre",""))
    if hata: return jsonify({"hata":hata}),401
    token=create_access_token(identity=user["email"])
    return jsonify({"token":token,"kullanici":{"isim":user["isim"],"email":user["email"],"plan":user["plan"],"asistanlar":user.get("asistanlar",{})}})

@app.route("/api/profil",methods=["GET"])
@jwt_required()
def api_profil():
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user: return jsonify({"hata":"Bulunamadı"}),404
    return jsonify({"isim":user["isim"],"email":user["email"],"plan":user["plan"],"asistanlar":user.get("asistanlar",{})})

@app.route("/api/asistan/olustur",methods=["POST"])
@jwt_required()
def asistan_olustur():
    email=get_jwt_identity()
    d=request.get_json(force=True) or {}
    isim=d.get("isim","").strip()
    groq_key=d.get("groq_key","").strip()
    sistem=d.get("sistem_prompt","Sen yardımsever bir yapay zeka asistanısın. SADECE verilen bilgilere dayanarak Türkçe cevap ver.")
    if not isim or not groq_key: return jsonify({"hata":"Asistan adı ve Groq anahtarı zorunlu"}),400
    aid=str(uuid.uuid4())[:8]
    asistan_ekle(email,aid,isim,groq_key)
    asistan_guncelle(email,aid,"sistem_prompt",sistem)
    return jsonify({"asistan_id":aid,"mesaj":f"'{isim}' asistanı oluşturuldu"})

@app.route("/api/asistan/<aid>/bilgi",methods=["GET"])
@jwt_required()
def asistan_bilgi(aid):
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user or aid not in user.get("asistanlar",{}): return jsonify({"hata":"Bulunamadı"}),404
    a=user["asistanlar"][aid]
    kb=kb_yukle(aid)
    return jsonify({**a,"kayit_sayisi":len(kb),"kb":[{"id":k["id"],"baslik":k["baslik"]} for k in kb]})

@app.route("/api/asistan/<aid>/dosya-yukle",methods=["POST"])
@jwt_required()
def dosya_yukle(aid):
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user or aid not in user.get("asistanlar",{}): return jsonify({"hata":"Bulunamadı"}),404
    if "dosya" not in request.files: return jsonify({"hata":"Dosya seçilmedi"}),400
    dosya=request.files["dosya"]
    ad=secure_filename(dosya.filename)
    klasor=os.path.join(UPLOADS_DIR,aid)
    os.makedirs(klasor,exist_ok=True)
    yol=os.path.join(klasor,ad)
    dosya.save(yol)
    baslik=request.form.get("baslik",os.path.splitext(ad)[0])
    ok,mesaj=dosya_ekle_kb(aid,yol,baslik)
    if not ok: return jsonify({"hata":mesaj}),400
    kb=kb_yukle(aid)
    asistan_guncelle(email,aid,"kayit_sayisi",len(kb))
    return jsonify({"mesaj":mesaj,"toplam_kayit":len(kb)})

@app.route("/api/asistan/<aid>/url-ekle",methods=["POST"])
@jwt_required()
def url_ekle(aid):
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user or aid not in user.get("asistanlar",{}): return jsonify({"hata":"Bulunamadı"}),404
    d=request.get_json(force=True) or {}
    url=d.get("url","").strip()
    if not url: return jsonify({"hata":"URL boş"}),400
    ok,mesaj=url_ekle_kb(aid,url,d.get("baslik") or None)
    if not ok: return jsonify({"hata":mesaj}),400
    kb=kb_yukle(aid)
    asistan_guncelle(email,aid,"kayit_sayisi",len(kb))
    return jsonify({"mesaj":mesaj,"toplam_kayit":len(kb)})

@app.route("/api/asistan/<aid>/metin-ekle",methods=["POST"])
@jwt_required()
def metin_ekle_api(aid):
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user or aid not in user.get("asistanlar",{}): return jsonify({"hata":"Bulunamadı"}),404
    d=request.get_json(force=True) or {}
    metin=d.get("metin","").strip()
    baslik=d.get("baslik","Manuel Giriş").strip()
    if not metin: return jsonify({"hata":"Metin boş"}),400
    ok,mesaj=metin_ekle_kb(aid,metin,baslik)
    if not ok: return jsonify({"hata":mesaj}),400
    kb=kb_yukle(aid)
    asistan_guncelle(email,aid,"kayit_sayisi",len(kb))
    return jsonify({"mesaj":mesaj,"toplam_kayit":len(kb)})

@app.route("/api/asistan/<aid>/egit",methods=["POST"])
@jwt_required()
def egit_api(aid):
    email=get_jwt_identity()
    user=kullanici_al(email)
    if not user or aid not in user.get("asistanlar",{}): return jsonify({"hata":"Bulunamadı"}),404
    ok,mesaj=egit(aid)
    if not ok: return jsonify({"hata":mesaj}),400
    return jsonify({"mesaj":mesaj})

@app.route("/api/asistan/<aid>/sor",methods=["POST"])
def platform_sor(aid):
    d=request.get_json(force=True) or {}
    soru=d.get("soru","").strip()
    if not soru: return jsonify({"hata":"Soru boş"}),400
    import users as u
    users_data=u._yukle()
    asistan=None
    email_owner=None
    for em,usr in users_data.items():
        if aid in usr.get("asistanlar",{}):
            asistan=usr["asistanlar"][aid]; email_owner=em; break
    if not asistan: return jsonify({"hata":"Asistan bulunamadı"}),404
    groq_key=asistan.get("groq_key","")
    sistem=asistan.get("sistem_prompt","Sen yardımsever bir yapay zeka asistanısın.")
    baglam=ara(aid,soru)
    if not groq_key: return jsonify({"hata":"Groq anahtarı eksik"}),500
    try:
        r=req_lib.post(GROQ_URL,headers=groq_headers(groq_key),json={"model":GROQ_MODEL,"messages":[{"role":"system","content":sistem},{"role":"user","content":f"BAĞLAM:\n{baglam}\n\nSORU: {soru}"}],"max_tokens":500,"temperature":0.1,"stream":False})
        r.raise_for_status()
        if email_owner: soru_say(email_owner,aid)
        return jsonify({"cevap":r.json()["choices"][0]["message"]["content"].strip()})
    except Exception as e: return jsonify({"hata":str(e)}),500

@app.route("/api/asistan/<aid>/sor-stream",methods=["POST"])
def platform_sor_stream(aid):
    d=request.get_json(force=True) or {}
    soru=d.get("soru","").strip()
    if not soru: return jsonify({"hata":"Soru boş"}),400
    import users as u
    users_data=u._yukle()
    asistan=None
    email_owner=None
    for em,usr in users_data.items():
        if aid in usr.get("asistanlar",{}):
            asistan=usr["asistanlar"][aid]; email_owner=em; break
    if not asistan: return jsonify({"hata":"Asistan bulunamadı"}),404
    groq_key=asistan.get("groq_key","")
    sistem=asistan.get("sistem_prompt","Sen yardımsever bir yapay zeka asistanısın.")
    baglam=ara(aid,soru)
    if email_owner: soru_say(email_owner,aid)
    return Response(stream_with_context(groq_stream(soru,baglam,sistem,groq_key)),mimetype="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# Platform widget
PLATFORM_WIDGET=r"""(function(){var AID="__AID__";var SUNUCU="__SUNUCU__";var BASLIK="__BASLIK__";var HIZLI=__HIZLI__;var css=`#aiplt-root,#aiplt-root *{box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif}#aiplt-bubble{position:fixed;bottom:24px;right:24px;z-index:99999;width:60px;height:60px;border-radius:50%;background:linear-gradient(135deg,#2563eb,#1d4ed8);box-shadow:0 4px 20px rgba(37,99,235,.4);display:flex;align-items:center;justify-content:center;cursor:pointer;border:none;transition:transform .2s}#aiplt-bubble:hover{transform:scale(1.08)}#aiplt-bubble svg{width:28px;height:28px;fill:#fff}#aiplt-badge{position:absolute;top:-2px;right:-2px;width:16px;height:16px;background:#ef4444;border-radius:50%;border:2px solid #fff;display:none}#aiplt-panel{position:fixed;bottom:96px;right:24px;z-index:99999;width:360px;max-width:92vw;height:500px;max-height:78vh;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.18);display:none;flex-direction:column;border:1px solid #e5e7eb}#aiplt-panel.acik{display:flex}#aiplt-header{background:linear-gradient(135deg,#2563eb,#1d4ed8);padding:14px 16px;display:flex;align-items:center;gap:10px;color:#fff}#aiplt-header .av{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.15);display:flex;align-items:center;justify-content:center;flex-shrink:0}#aiplt-header .av svg{width:20px;height:20px;fill:#fff}#aiplt-header .isim{font-weight:600;font-size:14px}#aiplt-header .durum{font-size:11px;opacity:.8;display:flex;align-items:center;gap:4px;margin-top:2px}#aiplt-header .nd{width:6px;height:6px;background:#4ade80;border-radius:50%}#aiplt-kapat{margin-left:auto;background:none;border:none;color:#fff;font-size:18px;cursor:pointer;opacity:.8}#aiplt-msgs{flex:1;overflow-y:auto;padding:14px;background:#f9fafb;display:flex;flex-direction:column;gap:10px}.aiplt-m{max-width:84%;padding:9px 12px;border-radius:12px;font-size:13.5px;line-height:1.55}.aiplt-m.bot{background:#fff;border:1px solid #e5e7eb;align-self:flex-start;border-bottom-left-radius:3px;color:#111827}.aiplt-m.user{background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;align-self:flex-end;border-bottom-right-radius:3px}.aiplt-t{font-size:10px;opacity:.45;margin-top:3px}.aiplt-typing span{width:6px;height:6px;border-radius:50%;background:#9ca3af;animation:aiplt-b 1.2s infinite ease-in-out;display:inline-block;margin:0 2px}.aiplt-typing span:nth-child(2){animation-delay:.16s}.aiplt-typing span:nth-child(3){animation-delay:.32s}@keyframes aiplt-b{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}#aiplt-hizli{display:flex;flex-wrap:wrap;gap:5px;padding:0 14px 10px;background:#f9fafb}.aiplt-chip{background:#fff;border:1px solid #d1d5db;color:#2563eb;font-size:11.5px;padding:4px 10px;border-radius:20px;cursor:pointer}.aiplt-chip:hover{background:#eff6ff}#aiplt-form{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;background:#fff}#aiplt-input{flex:1;border:1px solid #d1d5db;border-radius:20px;padding:8px 14px;font-size:13px;outline:none}#aiplt-input:focus{border-color:#2563eb}#aiplt-gonder{background:linear-gradient(135deg,#2563eb,#1d4ed8);border:none;color:#fff;width:36px;height:36px;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}#aiplt-gonder svg{width:16px;height:16px;fill:#fff}.aiplt-cur{display:inline-block;width:2px;height:13px;background:#2563eb;margin-left:2px;animation:aiplt-cur .7s infinite}@keyframes aiplt-cur{0%,100%{opacity:1}50%{opacity:0}}`;var st=document.createElement('style');st.textContent=css;document.head.appendChild(st);var root=document.createElement('div');root.id='aiplt-root';document.body.appendChild(root);root.innerHTML=`<button id="aiplt-bubble"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.02 2 11c0 2.4 1.06 4.55 2.8 6.13L4 22l5.14-1.67C10.02 20.77 11 21 12 21c5.52 0 10-4.02 10-9S17.52 2 12 2z"/></svg><div id="aiplt-badge"></div></button><div id="aiplt-panel"><div id="aiplt-header"><div class="av"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.02 2 11c0 2.4 1.06 4.55 2.8 6.13L4 22l5.14-1.67C10.02 20.77 11 21 12 21c5.52 0 10-4.02 10-9S17.52 2 12 2z"/></svg></div><div><div class="isim">${BASLIK}</div><div class="durum"><span class="nd"></span>Çevrimiçi</div></div><button id="aiplt-kapat">✕</button></div><div id="aiplt-msgs"></div><div id="aiplt-hizli"></div><div id="aiplt-form"><input id="aiplt-input" type="text" placeholder="Bir soru sorun…" autocomplete="off"/><button id="aiplt-gonder"><svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg></button></div></div>`;var bubble=document.getElementById('aiplt-bubble'),panel=document.getElementById('aiplt-panel'),msgs=document.getElementById('aiplt-msgs'),hizli=document.getElementById('aiplt-hizli'),input=document.getElementById('aiplt-input'),badge=document.getElementById('aiplt-badge'),ilk=true,aktif=false;function saat(){return new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'});}function mesaj(html,kim){var d=document.createElement('div');d.className='aiplt-m '+kim;d.innerHTML=html.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>')+'<div class="aiplt-t">'+saat()+'</div>';msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d;}function hizliGoster(){hizli.innerHTML='';HIZLI.forEach(function(q){var b=document.createElement('button');b.className='aiplt-chip';b.textContent=q;b.onclick=function(){sor(q);};hizli.appendChild(b);});}function sor(soru){if(!soru.trim()||aktif)return;aktif=true;mesaj(soru.replace(/</g,'&lt;'),'user');input.value='';hizli.innerHTML='';var d=document.createElement('div');d.className='aiplt-m bot';var ic=document.createElement('span'),cur=document.createElement('span');cur.className='aiplt-cur';var t=document.createElement('div');t.className='aiplt-t';t.textContent=saat();d.appendChild(ic);d.appendChild(cur);d.appendChild(t);msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;var tam='';fetch(SUNUCU+'/api/asistan/'+AID+'/sor-stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({soru:soru})}).then(function(r){var reader=r.body.getReader(),dec=new TextDecoder(),buf='';function oku(){reader.read().then(function(ref){if(ref.done){cur.remove();aktif=false;return;}buf+=dec.decode(ref.value,{stream:true});var satirlar=buf.split('\n');buf=satirlar.pop();satirlar.forEach(function(s){if(s.startsWith('data: ')){try{var v=JSON.parse(s.slice(6));tam+=v.token||'';ic.innerHTML=tam.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>');msgs.scrollTop=msgs.scrollHeight;if(v.done){cur.remove();aktif=false;}}catch(e){}}});oku();});}oku();}).catch(function(){d.innerHTML='Bağlantı hatası.<div class="aiplt-t">'+saat()+'</div>';aktif=false;});}bubble.addEventListener('click',function(){panel.classList.toggle('acik');badge.style.display='none';if(ilk&&panel.classList.contains('acik')){ilk=false;mesaj('Merhaba! Size nasıl yardımcı olabilirim?','bot');hizliGoster();}});document.getElementById('aiplt-kapat').addEventListener('click',function(){panel.classList.remove('acik');});document.getElementById('aiplt-gonder').addEventListener('click',function(){sor(input.value);});input.addEventListener('keydown',function(e){if(e.key==='Enter')sor(input.value);});setTimeout(function(){if(!panel.classList.contains('acik'))badge.style.display='block';},3000);})();"""

@app.route("/widget/<aid>.js")
def platform_widget(aid):
    import users as u
    users_data=u._yukle()
    asistan=None
    for em,usr in users_data.items():
        if aid in usr.get("asistanlar",{}):
            asistan=usr["asistanlar"][aid]; break
    if not asistan: return "// Asistan bulunamadı",404,{"Content-Type":"application/javascript"}
    sunucu=request.host_url.rstrip('/')
    if request.headers.get('X-Forwarded-Proto')=='https': sunucu='https://'+request.host
    baslik=asistan.get("isim","AI Asistan")
    hizli=asistan.get("hizli_sorular",["Merhaba","Ne yapabilirsin?","Yardım"])
    js=PLATFORM_WIDGET.replace("__AID__",aid).replace("__SUNUCU__",sunucu).replace("__BASLIK__",baslik).replace("__HIZLI__",json.dumps(hizli,ensure_ascii=False))
    return Response(js,mimetype="application/javascript")

@app.route("/panel")
def panel():
    return send_from_directory("static","index.html")

@app.route("/saglik")
def saglik():
    return jsonify({"durum":"calisiyor","model":GROQ_MODEL,"db":"bagli" if collection else "yok"})

@app.route("/")
def index():
    return send_from_directory("static","index.html")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False,threaded=True)
