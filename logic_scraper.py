import os
import requests
from bs4 import BeautifulSoup
import time
import re
# Die Verbindung geschieht hier über deine andere Datei
from logic_engine import supabase, create_vibe_for_scraper

UA_HEADER = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7'
}

def is_already_in_db(titel, autor):
    try:
        # Wir nutzen die exakt gleiche Logik wie im Master-Scraper
        res = supabase.table("buecher").select("id").eq("title", titel).eq("author", autor).execute()
        return len(res.data) > 0
    except:
        return False

def get_book_data(titel, autor):
    """Holt Metadaten identisch zum Master-Scraper."""
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
    print("🚀 Starte synchronisierten Vibe-Scan (Parameter wie Master-Scraper)...")
    candidates = []
    count = 0
    try:
        session = requests.Session()
        # Exakt die gleichen Suchparameter wie im Master-Scraper
        r = session.get("https://www.perlentaucher.de/buecherschau", headers=UA_HEADER, timeout=15)
        if r.status_code != 200: return
        
        soup = BeautifulSoup(r.text, 'html.parser')
        links = [ "https://www.perlentaucher.de" + a['href'] for a in soup.find_all('a', href=True) if "/buecherschau/20" in a['href'] ]
        
        # Die Top 5 Tage scannen
        for day_url in list(dict.fromkeys(links))[:5]:
            dr = session.get(day_url, headers=UA_HEADER, timeout=15)
            dsoup = BeautifulSoup(dr.text, 'html.parser')
            # Identische Selektoren wie im Master-Scraper
            for entry in dsoup.find_all(['strong', 'b', 'a']):
                text = entry.get_text().strip()
                if ":" in text and 10 < len(text) < 120:
                    if any(x in text.lower() for x in ["notiz", "mehr", "archiv"]): continue
                    parts = text.split(":", 1)
                    candidates.append((parts[1].strip(), parts[0].strip()))
    except Exception as e: 
        print(f"❌ Scraping-Fehler: {e}")

    candidates = list(set(candidates))
    for titel, autor in candidates:
        # Säuberung identisch zum Master-Scraper
        autor_clean = re.sub(r'\(.*?\)|Hg\.|von', '', autor).strip()
        if "," in autor_clean:
            p = autor_clean.split(",")
            autor_clean = f"{p[1].strip()} {p[0].strip()}"

        if is_already_in_db(titel, autor_clean): 
            continue

        img, ok, tags, year = get_book_data(titel, autor_clean)
        
        if ok:
            print(f" 🧬 Verarbeite: {titel}")
            
            # FALLBACK-LOGIK: 
            # Wir versuchen den Vibe zu erzeugen. Wenn es scheitert (vector is None),
            # nutzen wir die Original-Tags und lassen den Vektor leer (wie im alten Scraper).
            try:
                combined_tags, vector = create_vibe_for_scraper(titel, autor_clean, tags)
            except:
                combined_tags, vector = tags, None

            # Falls die KI-Analyse fehlschlug, nehmen wir die Basis-Daten (Master-Scraper Style)
            if not combined_tags:
                combined_tags = tags
            
            buch = {
                "title": titel[:200], 
                "author": autor_clean[:100], 
                "cover_url": img,
                "year": year,
                "tags": combined_tags[:3000], 
                "embedding": vector # Kann None sein, Supabase erlaubt das
            }
            
            try:
                supabase.table("buecher").insert(buch).execute()
                print(f"   ✅ Gespeichert: {titel} ({year})")
                count += 1
            except Exception as e:
                print(f"   ❌ Fehler beim Speichern von {titel}: {e}")
            
            time.sleep(1)
            
    print(f"\n🏁 Fertig! {count} neue Bücher hinzugefügt.")

if __name__ == "__main__":
    start_deep_scan()