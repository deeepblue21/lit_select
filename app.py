import os
import requests
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from tavily import TavilyClient

# --- 1. SETUP & KONFIGURATION ---
load_dotenv()
app = Flask(__name__)
CORS(app)  # Erlaubt deinem HTML-File den Zugriff auf diesen Server

# API Keys aus .env
O_KEY = os.getenv("OPENAI_API_KEY")
S_URL = os.getenv("SUPABASE_URL")
S_KEY = os.getenv("SUPABASE_KEY")
T_KEY = os.getenv("TAVILY_API_KEY")

# Clients initialisieren
openai_client = OpenAI(api_key=O_KEY)
supabase = create_client(S_URL, S_KEY)
tavily = TavilyClient(api_key=T_KEY)

# Header für Supabase (aus deinem Scraper)
HEADERS_SB = {
    "apikey": S_KEY,
    "Authorization": f"Bearer {S_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# --- 2. ENGINE LOGIK (REMIX) ---

def get_embedding(text):
    """Erstellt Vektoren für die Suche und Speicherung."""
    try:
        res = openai_client.embeddings.create(input=text, model="text-embedding-3-small")
        return res.data[0].embedding
    except Exception as e:
        print(f"⚠️ Vektor-Fehler: {e}")
        return None

def analyze_input_book(book_title):
    """Analysiert den Vibe des gesuchten Buches (aus deiner Engine)."""
    search = tavily.search(query=f"Buch {book_title} Genre Inhalt Autor", max_results=3)
    blob = "\n".join([r['content'] for r in search['results']])
    prompt = (
        f"Kontext:\n{blob}\n\nAnalysiere '{book_title}'. Gib NUR Fakten zurück:\n"
        f"Autor | Gattung | Genre-Anker | Tempo | Vibe"
    )
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
    content = res.choices[0].message.content.strip()
    p = content.split('|')
    return {
        "author": p[0].strip(), 
        "is_poetry": any(k in p[1].strip().lower() for k in ["lyrik", "gedicht", "vers"]),
        "anchor": p[2].strip(), "vibe": p[4].strip()
    }

# --- 3. SCRAPER LOGIK (REMIX) ---

def fetch_book_metadata(title, author):
    """Sucht Cover und Beschreibung via Google Books (aus deinem Scraper)."""
    try:
        query = f"{title} {author}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1"
        res = requests.get(url, timeout=5).json()
        if "items" in res:
            vol = res["items"][0].get("volumeInfo", {})
            img = vol.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            desc = vol.get("description", "Keine Beschreibung verfügbar.")[:2000]
            return img, desc
    except: pass
    return "https://via.placeholder.com/110x180?text=Kein+Cover", ""

# --- 4. API ENDPUNKTE (DIE SCHNITTSTELLEN) ---

@app.route('/get_inspiration', methods=['POST'])
def inspiration():
    """Der Endpunkt für deine Suchleiste."""
    data = request.json
    user_input = data.get('query')
    print(f"🔍 Suche Inspiration für: {user_input}")
    
    info = analyze_input_book(user_input)
    final_results = []

    # 1. Datenbank-Suche (Bestand)
    try:
        vector = get_embedding(f"{info['anchor']} {info['vibe']}")
        # Nutzt deine rpc Funktion in Supabase
        db_res = supabase.rpc('match_books', {'query_embedding': vector, 'match_threshold': 0.42, 'match_count': 5}).execute()
        
        for b in db_res.data:
            if info['author'].lower() not in b['author'].lower():
                final_results.append({
                    "id": b['id'], "title": b['title'], "author": b['author'], 
                    "reason": "Aus deinem Bestand – passt perfekt zum Vibe.", "source": "database"
                })
                if len(final_results) >= 1: break
    except Exception as e: print(f"DB Error: {e}")

    # 2. Web-Auffüllung (Tavily/GPT)
    needed = 3 - len(final_results)
    if needed > 0:
        prompt = f"Nenne {needed} moderne, anspruchsvolle Bücher wie {user_input} ({info['vibe']}). Format: Titel | Autor | Begründung"
        res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        for line in res.choices[0].message.content.strip().split('\n'):
            if '|' in line:
                p = line.split('|')
                final_results.append({
                    "title": p[0].strip(), "author": p[1].strip(), 
                    "reason": p[2].strip(), "source": "live_web"
                })

    return jsonify(final_results[:3])

@app.route('/add_book', methods=['POST'])
def add_book():
    """Der Endpunkt, wenn der User auf 'Aufnehmen' klickt."""
    data = request.json
    title = data.get('title')
    author = data.get('author')
    
    print(f"📥 Scrape & Add: {title} von {author}")
    
    img, desc = fetch_book_metadata(title, author)
    vector = get_embedding(desc)
    
    new_book = {
        "title": title, "author": author, "cover_url": img,
        "tags": desc, "embedding": vector, "year": 2026
    }
    
    res = requests.post(f"{S_URL}/rest/v1/buecher", headers=HEADERS_SB, json=new_book)
    if res.status_code in [200, 201]:
        return jsonify({"status": "success", "book": new_book})
    else:
        return jsonify({"status": "error", "message": res.text}), 400

if __name__ == '__main__':
    app.run(port=5000, debug=True)