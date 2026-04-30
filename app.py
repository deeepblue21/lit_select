import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from tavily import TavilyClient

# --- 1. SETUP & KONFIGURATION ---
load_dotenv()
app = Flask(__name__)

# CORS-Fix für GitHub Pages Kommunikation
CORS(app, resources={r"/*": {"origins": "*"}})

# API Keys aus Render Environment
O_KEY = os.getenv("OPENAI_API_KEY")
S_URL = os.getenv("SUPABASE_URL")
S_KEY = os.getenv("SUPABASE_KEY")
T_KEY = os.getenv("TAVILY_API_KEY")

# Clients initialisieren
openai_client = OpenAI(api_key=O_KEY)
supabase = create_client(S_URL, S_KEY)
tavily = TavilyClient(api_key=T_KEY)

# Header für Supabase
HEADERS_SB = {
    "apikey": S_KEY,
    "Authorization": f"Bearer {S_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# --- 2. HILFSFUNKTIONEN ---

def get_embedding(text):
    """Erzeugt Vektoren für die semantische Suche."""
    try:
        res = openai_client.embeddings.create(input=text, model="text-embedding-3-small")
        return res.data[0].embedding
    except Exception as e:
        print(f"⚠️ Vektor-Fehler: {e}")
        return None

def analyze_input_book(book_title):
    """Analysiert das vom Nutzer eingegebene Buch via Tavily & OpenAI."""
    try:
        print(f"📡 Starte Tavily Suche für: {book_title}")
        search = tavily.search(query=f"Buch {book_title} Genre Inhalt Autor", max_results=3)
        blob = "\n".join([r['content'] for r in search['results']])
        
        print(f"🧠 Starte OpenAI Analyse...")
        prompt = (
            f"Kontext:\n{blob}\n\nAnalysiere '{book_title}'. Gib NUR diese 5 Fakten zurück, "
            "getrennt durch das Symbol | ohne Fettdruck oder Sternchen:\n"
            "Autor | Gattung | Genre-Anker | Tempo | Vibe"
        )
        res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        content = res.choices[0].message.content.strip().replace('*', '')
        
        parts = [p.strip() for p in content.split('|')]
        while len(parts) < 5:
            parts.append("Unbekannt")
            
        print(f"✅ Analyse erfolgreich für: {parts[0]}")
        return {
            "author": parts[0], 
            "is_poetry": any(k in parts[1].lower() for k in ["lyrik", "gedicht", "vers"]),
            "anchor": parts[2], 
            "vibe": parts[4]
        }
    except Exception as e:
        print(f"❌ Fehler in analyze_input_book: {e}")
        return {"author": "Unbekannt", "is_poetry": False, "anchor": book_title, "vibe": "Spannend"}

def fetch_book_metadata(title, author):
    """Holt Cover und Beschreibung explizit auf DEUTSCH."""
    try:
        query = f"{title} {author}"
        # langRestrict=de und hl=de erzwingen deutsche Metadaten
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de&hl=de"
        res = requests.get(url, timeout=5).json()
        if "items" in res:
            vol = res["items"][0].get("volumeInfo", {})
            img = vol.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            desc = vol.get("description", "Keine Beschreibung verfügbar.")[:2000]
            return img, desc
    except Exception as e:
        print(f"⚠️ Google Books Fehler: {e}")
    return "https://via.placeholder.com/110x180?text=Kein+Cover", ""

# --- 3. API ENDPUNKTE ---

@app.route('/get_inspiration', methods=['POST'], strict_slashes=False)
def inspiration():
    try:
        data = request.json
        user_input = data.get('query')
        if not user_input:
            return jsonify({"error": "Keine Suchanfrage"}), 400
            
        info = analyze_input_book(user_input)
        final_results = []

        # 1. Datenbank-Suche (Bestand ab Threshold 0.42)
        try:
            vector = get_embedding(f"{info['anchor']} {info['vibe']}")
            if vector:
                db_res = supabase.rpc('match_books', {
                    'query_embedding': vector, 
                    'match_threshold': 0.42, 
                    'match_count': 5
                }).execute()
                
                for b in db_res.data:
                    # Ausschluss des gleichen Autors wie bei der Eingabe
                    if info['author'].lower() not in b['author'].lower():
                        final_results.append({
                            "id": b['id'], "title": b['title'], "author": b['author'], 
                            "reason": f"Dieses Buch aus deinem Bestand passt perfekt zum Vibe von '{user_input}'.", 
                            "source": "database"
                        })
                        # Wir nehmen max 1 aus der DB, damit wir noch Platz für KI-Vorschläge haben
                        if len(final_results) >= 1: break 
        except Exception as e: 
            print(f"⚠️ DB Suche übersprungen: {e}")

        # 2. KI-Vorschläge (Auffüllen auf insgesamt 3)
        needed = 3 - len(final_results)
        if needed > 0:
            prompt = (
                f"Nenne mir {needed} moderne, anspruchsvolle Buch-Vorschläge, die ähnlich sind wie '{user_input}' von {info['author']}.\n"
                f"Regeln:\n"
                f"- Erschienen zwischen 2020 und 2026.\n"
                f"- Auf KEINEN FALL vom Autor '{info['author']}'.\n"
                f"- Keine Sonderzeichen wie '*' oder '**'.\n"
                f"- Für jedes Buch eine Begründung von ca. 5 Zeilen (Inhalt & Vibe).\n"
                f"Format: Titel | Autor | Begründung"
            )
            res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
            for line in res.choices[0].message.content.strip().split('\n'):
                if '|' in line:
                    p = [item.strip().replace('*', '') for item in line.split('|')]
                    if len(p) >= 3:
                        final_results.append({
                            "title": p[0], "author": p[1], 
                            "reason": p[2], "source": "live_web"
                        })

        return jsonify(final_results[:3])
    except Exception as e:
        print(f"💥 Kritischer Fehler: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/add_book', methods=['POST'], strict_slashes=False)
def add_book():
    try:
        data = request.json
        title = data.get('title')
        author = data.get('author')
        
        img, desc = fetch_book_metadata(title, author)
        vector = get_embedding(desc)
        
        new_book = {
            "title": title, "author": author, "cover_url": img,
            "tags": desc, "embedding": vector, "year": 2026
        }
        
        res = requests.post(f"{S_URL}/rest/v1/buecher", headers=HEADERS_SB, json=new_book)
        if res.status_code in [200, 201]:
            return jsonify({"status": "success", "book": new_book})
        return jsonify({"status": "error", "message": res.text}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- START ---
if __name__ == '__main__':
    # Render nutzt oft Port 10000
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)