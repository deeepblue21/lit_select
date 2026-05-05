import requests
from bs4 import BeautifulSoup
import time
import re
from logic_engine import supabase, create_vibe_for_scraper

UA_HEADER = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
}

def is_already_in_db(titel, autor):
    res = supabase.table("buecher").select("id").eq("title", titel).eq("author", autor).execute()
    return len(res.data) > 0

def get_book_data(titel, autor):
    try:
        query = f"intitle:{titel} inauthor:{autor}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de&hl=de"
        res = requests.get(url, timeout=5).json()
        if "items" in res:
            vol = res["items"][0].get("volumeInfo", {})
            img = vol.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            pub_date = vol.get("publishedDate", "2026")
            year = int(pub_date[:4]) if pub_date else 2026
            description = vol.get("description", "")
            return img, True, description, year
    except: pass
    return None, False, "", 2026

def start_deep_scan():
    print("🚀 Starte Vibe-synchronisierten Scan...")
    candidates = []
    count = 0
    try:
        session = requests.Session()
        r = session.get("https://www.perlentaucher.de/buecherschau", headers=UA_HEADER, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = [ "https://www.perlentaucher.de" + a['href'] for a in soup.find_all('a', href=True) if "/buecherschau/20" in a['href'] ]
        
        for day_url in list(dict.fromkeys(links))[:5]:
            dr = session.get(day_url, headers=UA_HEADER, timeout=15)
            dsoup = BeautifulSoup(dr.text, 'html.parser')
            for entry in dsoup.find_all(['strong', 'b', 'a']):
                text = entry.get_text().strip()
                if ":" in text and 10 < len(text) < 120:
                    if any(x in text.lower() for x in ["notiz", "mehr", "archiv"]): continue
                    parts = text.split(":", 1)
                    candidates.append((parts[1].strip(), parts[0].strip()))
    except Exception as e: print(f"❌ Fehler: {e}")

    candidates = list(set(candidates))
    for titel, autor in candidates:
        autor_clean = re.sub(r'\(.*?\)|Hg\.|von', '', autor).strip()
        if "," in autor_clean:
            p = autor_clean.split(",")
            autor_clean = f"{p[1].strip()} {p[0].strip()}"

        if is_already_in_db(titel, autor_clean): continue

        img, ok, tags, year = get_book_data(titel, autor_clean)
        
        if ok:
            print(f" 🧬 Erzeuge Vibe & Vektor für: {titel}")
            # HIER PASSIERT DIE MAGIE: Vibe & Originaltext werden kombiniert!
            combined_tags, vector = create_vibe_for_scraper(titel, autor_clean, tags)
            
            if vector:
                buch = {
                    "title": titel[:200], 
                    "author": autor_clean[:100], 
                    "cover_url": img,
                    "year": year,
                    "tags": combined_tags[:3000], 
                    "embedding": vector
                }
                supabase.table("buecher").insert(buch).execute()
                print(f"   ✅ Gespeichert: {titel}")
                count += 1
            time.sleep(1)
    print(f"\n🏁 Fertig! {count} neue Bücher hinzugefügt.")

if __name__ == "__main__":
    start_deep_scan()