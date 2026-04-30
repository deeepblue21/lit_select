import os
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from tavily import TavilyClient

load_dotenv()
app = Flask(__name__)
CORS(app)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

session_history = []

def verify_with_catalog(title, author_hint=""):
    """Prüft Metadaten: Nur Deutsch + Zeitraum 2020-2026."""
    query = f"intitle:{title} inauthor:{author_hint}"
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de&hl=de"
    try:
        response = requests.get(url, timeout=5).json()
        if "items" in response:
            book_info = response["items"][0]["volumeInfo"]
            if book_info.get("language", "unknown") != "de": return None
            
            pub_date = book_info.get("publishedDate", "2020")
            year = int(pub_date[:4])
            if not (2020 <= year <= 2026): return None

            img = book_info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
            return {
                "real_title": book_info.get("title"),
                "real_author": ", ".join(book_info.get("authors", ["Unbekannt"])),
                "year": year, "cover_url": img, "description": book_info.get("description", "")
            }
    except: return None 
    return None

def analyze_input_book(book_title):
    try:
        search = tavily.search(query=f"Buch {book_title} Genre Inhalt Autor", max_results=3)
        blob = "\n".join([r['content'] for r in search['results']])
        prompt = f"Kontext:\n{blob}\n\nAnalysiere '{book_title}'. Gib NUR: Autor | Gattung | Genre-Anker | Tempo | Vibe"
        res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        p = res.choices[0].message.content.strip().split('|')
        return {"author": p[0].strip(), "anchor": p[2].strip(), "vibe": p[4].strip()}
    except: return {"author": "Unbekannt", "anchor": "Gegenwartsliteratur", "vibe": "atmosphärisch"}

@app.route('/get_inspiration/', methods=['POST'])
def inspiration():
    try:
        data = request.json
        user_input = data.get('query')
        info = analyze_input_book(user_input)
        if user_input not in session_history: session_history.append(user_input)
        
        final_results = []
        # 1. DB-Suche (Threshold STRENG bei 0.42)
        try:
            emb = openai_client.embeddings.create(input=f"{info['anchor']} {info['vibe']}", model="text-embedding-3-small").data[0].embedding
            db_res = supabase.rpc('match_books', {'query_embedding': emb, 'match_threshold': 0.42, 'match_count': 5}).execute()
            for b in db_res.data:
                if b['title'] in session_history: continue
                final_results.append({"id": b['id'], "title": b['title'], "author": b['author'], "reason": "Aus deinem Bestand.", "source": "database"})
                session_history.append(b['title'])
                break 
        except: pass

        # 2. Web-Suche mit Puffer, um 3 Titel zu garantieren
        needed = 3 - len(final_results)
        if needed > 0:
            search_res = tavily.search(query=f"Anspruchsvolle deutsche Literatur wie {user_input} {info['anchor']}", max_results=8)
            context = "\n".join([r['content'] for r in search_res.get('results', [])])
            prompt = f"Kontext:\n{context}\n\nNenne 8 Empfehlungen (DEUTSCH, 2020-2026). KEIN {info['author']}. Format: Titel | Autor | Begründung"
            res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
            
            for line in res.choices[0].message.content.strip().split('\n'):
                if '|' in line and len(final_results) < 3:
                    p = line.split('|')
                    data = verify_with_catalog(p[0].strip(), p[1].strip())
                    if data and data['real_title'] not in session_history:
                        final_results.append({"title": data['real_title'], "author": data['real_author'], "reason": p[2].strip(), "cover_url": data['cover_url'], "source": "live_web"})
                        session_history.append(data['real_title'])
        
        return jsonify(final_results)
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/add_book/', methods=['POST'])
def add_book():
    try:
        data = request.json
        meta = verify_with_catalog(data.get('title'), data.get('author'))
        if not meta: return jsonify({"status": "error"}), 400
        emb = openai_client.embeddings.create(input=meta['description'] or meta['real_title'], model="text-embedding-3-small").data[0].embedding
        new_book = {"title": meta['real_title'], "author": meta['real_author'], "cover_url": meta['cover_url'], "tags": meta['description'], "embedding": emb, "year": meta['year']}
        supabase.table("buecher").insert(new_book).execute()
        return jsonify({"status": "success", "book": new_book})
    except: return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))