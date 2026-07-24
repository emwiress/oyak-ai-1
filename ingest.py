import json, chromadb
from embed import build_and_save, embed

def main():
    with open("knowledge_base.json","r",encoding="utf-8") as f:
        kb = json.load(f)
    print(f"{len(kb)} kayıt işleniyor...")
    docs = [item["baslik"]+": "+item["metin"] for item in kb]
    build_and_save(docs)
    client = chromadb.PersistentClient(path="./chroma_db")
    try: client.delete_collection("oyak_kb")
    except: pass
    col = client.create_collection("oyak_kb")
    for i,item in enumerate(kb):
        tam = item["baslik"]+": "+item["metin"]
        col.add(ids=[item["id"]], embeddings=[embed(tam)], documents=[tam], metadatas=[{"baslik":item["baslik"]}])
        print(f"  [{i+1}/{len(kb)}] {item['baslik']}")
    print("Veritabanı hazır.")

if __name__=="__main__": main()
