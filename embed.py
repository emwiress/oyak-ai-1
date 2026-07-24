import json, re, math, os, pickle

VOCAB_FILE = "vocab.pkl"

def tokenize(text):
    text = text.lower()
    tr = {'ı':'i','İ':'i','ğ':'g','Ğ':'g','ü':'u','Ü':'u','ş':'s','Ş':'s','ö':'o','Ö':'o','ç':'c','Ç':'c'}
    for k,v in tr.items(): text = text.replace(k,v)
    return re.findall(r'[a-z0-9]+', text)

def build_and_save(docs, size=2000):
    freq = {}
    for d in docs:
        for t in set(tokenize(d)):
            freq[t] = freq.get(t,0) + 1
    vocab = {w:i for i,w in enumerate(sorted(freq, key=lambda x:-freq[x])[:size])}
    N = len(docs)
    idf = {w: math.log((N+1)/(sum(1 for d in docs if w in tokenize(d))+1))+1 for w in vocab}
    with open(VOCAB_FILE,'wb') as f: pickle.dump({'vocab':vocab,'idf':idf},f)
    return vocab, idf

_state = {}
def _load():
    if not _state and os.path.exists(VOCAB_FILE):
        with open(VOCAB_FILE,'rb') as f: _state.update(pickle.load(f))
    return _state

def embed(text):
    s = _load()
    if not s: raise RuntimeError("vocab.pkl bulunamadı")
    vocab, idf = s['vocab'], s['idf']
    tokens = tokenize(text)
    tf = {}
    for t in tokens: tf[t] = tf.get(t,0)+1
    n = len(tokens) or 1
    vec = [0.0]*len(vocab)
    for t,cnt in tf.items():
        if t in vocab: vec[vocab[t]] = (cnt/n)*idf.get(t,1.0)
    norm = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x/norm for x in vec]
