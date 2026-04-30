import os
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from tavily import TavilyClient

# --- 1. SETUP & KONFIGURATION ---
load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

O_KEY = os.getenv("OPENAI_API_KEY")
S_URL = os.getenv("SUPABASE_URL")
S_KEY = os.getenv("SUPABASE_KEY")
T_KEY = os.getenv("TAVILY_API_KEY")

openai_client = OpenAI(api_key=O_KEY)
supabase = create_client(S_URL, S_KEY)
tavily = TavilyClient(api_key=T_KEY)

# Session-History (Global im Arbeitsspeicher des Backends)
# Hinweis: Leert sich bei jedem Neustart des Render-Dienstes
session_history = []

HEADERS_SB = {
    "apikey": S_KEY,
    "Authorization": f"Bearer {S_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# --- 2. ENGINE FUNKTIONEN (1:1 PORTIERT) ---

def verify_with_catalog(title, author_hint=""):
    """Prüft gegen Google Books (Exakt aus Engine)."""
    # hl=de und langRestrict=de korrigieren Punkt 9 (Sprachmängel)
    query = f"intitle:{title} inauthor:{author_hint}"
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de&hl=de"
    try:
        response = requests.get(url, timeout=5).json()
        if "items" in response:
            book_info = response["items"][0]["volumeInfo"]
            pub_date = book_info.get("publishedDate", "2024")
            year = int(pub_date[:4])
            
            # Filter: Vorschlag nicht älter als 10 Jahre (Engine Logik)
            # Kann für Punkt 7 auf 6 Jahre verschärft werden:
            if (datetime.now().year - year) > 6: 
                return None

            identifiers = book_info.get("industryIdentifiers", [])
            isbn = next((i["identifier"] for i in identifiers if i["type"] == "ISBN_13"), "ISBN prüfen")
            img = book_info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            
            return {
                "real_title": book_info.get("title"),
                "real_author": ", ".join(book_info.get("authors", ["Unbekannt"])),
                "year": str(year),
                "publisher": book_info.get("publisher", "Literaturverlag"),
                "isbn": isbn,
                "cover_url": img,
                "description": book_info.get("description", "")
            }
    except: return None 
    return None

def analyze_input_book(book_title):
    """Vor-Recherche (Exakt aus Engine)."""
    try:
        search = tavily.search(query=f"Buch {book_title} Genre Inhalt Autor", max_results=3)
        blob = "\n".join([r['content'] for r in search['results']])

        prompt = (
            f"Kontext:\n{blob}\n\nAnalysiere '{book_title}'. Gib NUR Fakten zurück, keine Sonderzeichen:\n"
            f"Autor | Gattung | Genre-Anker | Tempo | Vibe"
        )
        res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        content = res.choices[0].message.content.strip().replace('*', '')
        
        p = content.split('|')
        return {
            "author": p[0].strip(), 
            "is_poetry": any(k in p[1].strip().lower() for k in ["lyrik", "gedicht", "vers"]),
            "anchor": p[2].strip(), 
            "tempo": p[3].strip(), 
            "vibe": p[4].strip()
        }
    except:
        return {"author": "Unbekannt", "is_poetry": False, "anchor": "Gegenwartsliteratur", "tempo": "moderat", "vibe": "atmosphärisch"}

def search_external_books_live(original_book, info, needed):
    """Web-Recherche (Exakt aus Engine)."""
    if needed <= 0: return []
    results = []
    
    # Lyrik-Spezialsuche vs. Prosa
    if info['is_poetry']:
        query = f"Deutscher Lyrikpreis Peter-Huchel-Preis Neuerscheinungen 2024 2025"
    else:
        query = f"Anspruchsvolle moderne Bücher wie {original_book} {info['anchor']} {info['vibe']} 2024 2025"

    search_res = tavily.search(query=query, search_depth="advanced", max_results=10)
    context = "\n".join([r['content'] for r in search_res.get('results', [])])

    forbidden = ", ".join(session_history)
    prompt = (
        f"Kontext:\n{context}\n\nNenne exakt {needed} Buchempfehlungen (erschienen 2020-2026).\n"
        f"STRIKTE REGEL: NICHT von {info['author']} und NICHT diese Titel: {forbidden}.\n"
        f"Kein Markdown, keine Sternchen.\n"
        f"Format: Titel | Autor | Begründung (ausführlich, ca. 5 Zeilen)"
    )
    
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
    
    for line in res.choices[0].message.content.strip().split('\n'):
        if '|' in line and len(results) < needed:
            p = [x.strip().replace('*', '') for x in line.split('|')]
            title, author = p[0], p[1]
            
            if any(f.lower() in title.lower() for f in session_history): continue
            
            data = verify_with_catalog(title, author)
            if data:
                results.append({
                    "title": data['real_title'], "author": data['real_author'],
                    "year": data['year'], "publisher": data['publisher'],
                    "isbn": data['isbn'], "reason": p[2],
                    "cover_url": data['cover_url'], "source": "live_web"
                })
    return results

def get_embedding(text):
    try:
        res = openai_client.embeddings.create(input=text, model="text-embedding-3-small")
        return res.data[0].embedding
    except: return None

# --- 3. API ENDPUNKTE ---

@app.route('/get_inspiration', methods=['POST'], strict_slashes=False)
def inspiration():
    try:
        data = request.json
        user_input = data.get('query')
        if not user_input: return jsonify({"error": "Keine Suchanfrage"}), 400
            
        info = analyze_input_book(user_input)
        if user_input not in session_history:
            session_history.append(user_input)
        
        final_results = []
        
        # 1. Datenbank-Suche
        try:
            vector = get_embedding(f"{info['anchor']} {info['vibe']}")
            db_res = supabase.rpc('match_books', {
                'query_embedding': vector, 
                'match_threshold': 0.42, 
                'match_count': 10
            }).execute()
            
            for b in db_res.data:
                if any(f.lower() in b['title'].lower() for f in session_history): continue
                if info['author'].lower() in b['author'].lower(): continue
                
                # Lyrik-Check
                is_p = any(k in b['title'].lower() for k in ["gedichte", "lyrik", "balladen", "verse"])
                if info['is_poetry'] != is_p: continue
                
                final_results.append({
                    "id": b['id'], "title": b['title'], "author": b['author'], 
                    "reason": f"Dieses Buch aus deinem Bestand passt perfekt zum Vibe von '{user_input}'.", 
                    "source": "database"
                })
                session_history.append(b['title'])
                if len(final_results) >= 1: break
        except Exception as e: print(f"⚠️ DB-Fehler: {e}")

        # 2. Live-Auffüllung (Garantie für 3 Titel)
        needed = 3 - len(final_results)
        if needed > 0:
            web_tips = search_external_books_live(user_input, info, needed)
            for tip in web_tips:
                final_results.append(tip)
                session_history.append(tip['title'])
        
        return jsonify(final_results[:3])
    except Exception as e:
        print(f"💥 Fehler: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/add_book', methods=['POST'], strict_slashes=False)
def add_book():
    try:
        data = request.json
        title = data.get('title')
        author = data.get('author')
        
        # Nutzen der verify_with_catalog Funktion für konsistente DE-Daten
        meta = verify_with_catalog(title, author)
        if not meta:
            # Fallback falls Katalog-Check fehlschlägt
            return jsonify({"status": "error", "message": "Buch konnte nicht verifiziert werden"}), 400

        vector = get_embedding(meta['description'] or meta['real_title'])
        
        new_book = {
            "title": meta['real_title'], "author": meta['real_author'], 
            "cover_url": meta['cover_url'], "tags": meta['description'], 
            "embedding": vector, "year": int(meta['year'])
        }
        
        res = requests.post(f"{S_URL}/rest/v1/buecher", headers=HEADERS_SB, json=new_book)
        if res.status_code in [200, 201]:
            return jsonify({"status": "success", "book": new_book})
        return jsonify({"status": "error", "message": res.text}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)