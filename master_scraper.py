import requests
from bs4 import BeautifulSoup
import time
import re
import os
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

# --- Keys sicher aus der .env Datei laden ---
load_dotenv() 
O_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=O_KEY)

# --- SETUP SUPABASE ---
S_URL = "https://bmbpelhcjjguetenghjo.supabase.co/rest/v1/buecher"
S_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtYnBlbGhjampndWV0ZW5naGpvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUxNDUxMjIsImV4cCI6MjA5MDcyMTEyMn0.JYBzOCPofCUtgEEoPMzqAT4S9LYRXEfp0xvCq50YXIY"

HEADERS_SB = {
    "apikey": S_KEY,
    "Authorization": f"Bearer {S_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

UA_HEADER = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7'
}

def get_embedding(text):
    """Erstellt den Vektor identisch zur app.py."""
    if not text: return None
    try:
        res = client.embeddings.create(input=text, model="text-embedding-3-small")
        return res.data[0].embedding
    except Exception as e:
        print(f"⚠️ Vektor-Fehler: {e}")
        return None

def is_already_in_db(titel, autor):
    try:
        check_url = f"{S_URL}?title=eq.{titel}&author=eq.{autor}&select=id"
        res = requests.get(check_url, headers=HEADERS_SB)
        return len(res.json()) > 0
    except:
        return False

def get_book_data(titel, autor):
    """Holt Metadaten inkl. Spracheinschränkung (Punkt 9)."""
    try:
        query = f"intitle:{titel} inauthor:{autor}"
        # NEU: langRestrict=de und hl=de für deutsche Metadaten
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de&hl=de"
        res = requests.get(url, timeout=5).json()
        if "items" in res:
            vol = res["items"][0].get("volumeInfo", {})
            img = vol.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            
            # Echtes Jahr extrahieren (Punkt 7)
            pub_date = vol.get("publishedDate", "2026")
            year = int(pub_date[:4]) if pub_date else 2026
            
            description = vol.get("description", "")
            return img, True, description, year
    except: pass
    return None, False, "", 2026

def start_deep_scan():
    print("🚀 Starte synchronisierten Scan (Vektoren & DE-Metadaten)...")
    candidates = []
    count = 0
    try:
        session = requests.Session()
        r = session.get("https://www.perlentaucher.de/buecherschau", headers=UA_HEADER, timeout=15)
        if r.status_code != 200: return
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

        # NEU: Gibt jetzt auch das Jahr zurück
        img, ok, tags, year = get_book_data(titel, autor_clean)
        
        if ok:
            print(f" 🧬 Erzeuge Vektor für: {titel} ({year})")
            # Wir nutzen den Klappentext (tags) für den Vektor, wie in app.py
            # Falls tags leer ist, nehmen wir den Titel als Backup
            vector_input = tags if len(tags) > 10 else f"{titel} {autor_clean}"
            vector = get_embedding(vector_input) 
            
            buch = {
                "title": titel[:200], 
                "author": autor_clean[:100], 
                "cover_url": img,
                "year": year, # NEU: Dynamisches Jahr
                "tags": tags[:3000], 
                "embedding": vector
            }
            res = requests.post(S_URL, headers=HEADERS_SB, json=buch)
            if res.status_code in [200, 201]:
                print(f"   ✅ Gespeichert: {titel}")
                count += 1
            time.sleep(1)
    print(f"\n🏁 Fertig! {count} neue Bücher mit Vektoren hinzugefügt.")

if __name__ == "__main__":
    start_deep_scan()