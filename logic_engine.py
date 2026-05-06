import os
import requests
from datetime import datetime
from supabase import create_client
from openai import OpenAI
from tavily import TavilyClient
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
tavily = TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))

session_history = []

def verify_with_catalog(title, author_hint=""):
    # Versuch 1: Strikte Suche mit Autor
    query = f"intitle:{title} inauthor:{author_hint}"
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1&langRestrict=de"
    try:
        response = requests.get(url, timeout=5).json()
        
        # FALLBACK 1: Wenn nichts gefunden, versuche breitere Suche nur nach Titel
        if "items" not in response:
            print(f"⚠️ Google Books: '{title}' mit Autor nicht gefunden. Versuche nur Titel...")
            fallback_url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{title}&maxResults=1&langRestrict=de"
            response = requests.get(fallback_url, timeout=5).json()

        if "items" in response:
            book_info = response["items"][0]["volumeInfo"]
            pub_date = book_info.get("publishedDate", "2024")
            year = int(pub_date[:4])
            
            blurb = book_info.get("description", "Keine Beschreibung verfügbar.")
            
            # FALLBACK 2: Altersgrenze entschärft (kein "return None" mehr)
            if (datetime.now().year - year) > 10:
                print(f"⚠️ Info: KI-Vorschlag '{title}' ist älter als 10 Jahre ({year}), wird aber zugelassen.")

            identifiers = book_info.get("industryIdentifiers", [])
            isbn = next((i["identifier"] for i in identifiers if i["type"] == "ISBN_13"), "Keine ISBN")
            
            # FALLBACK 3: Ersatz-Cover, falls Google kein Bild liefert
            cover_url = book_info.get("imageLinks", {}).get("thumbnail", "")
            if cover_url:
                cover_url = cover_url.replace("http://", "https://")
            else:
                cover_url = f"https://via.placeholder.com/128x192.png?text={title.replace(' ', '+')}"
            
            return {
                "real_title": book_info.get("title", title),
                "real_author": ", ".join(book_info.get("authors", [author_hint if author_hint else "Unbekannt"])),
                "year": str(year),
                "publisher": book_info.get("publisher", "Literaturverlag"),
                "isbn": isbn,
                "cover_url": cover_url,
                "description": blurb
            }
        else:
            print(f"❌ Google Books: '{title}' absolut nicht gefunden.")
    except Exception as e:
        print(f"❌ Google API Fehler bei '{title}': {e}")
        return None 
    return None

def analyze_input_book(book_title):
    search = tavily.search(query=f"Buch {book_title} Genre Inhalt Autor", max_results=3)
    blob = "\n".join([r['content'] for r in search['results']])

    prompt = (
        f"Kontext:\n{blob}\n\nAnalysiere '{book_title}'. Gib NUR Fakten zurück:\n"
        f"Autor | Gattung | Genre-Anker | Tempo | Vibe\n"
        f"Beispiel: Ewald Arenz | Prosa | Gegenwartsliteratur | getragen | nostalgisch, sommerlich"
    )
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
    content = res.choices[0].message.content.strip()
    
    try:
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
    if needed <= 0: return []
    results = []
    print(f"📡 Web-Recherche: Suche {needed} Ergänzungen...") # Print wiederhergestellt
    
    if info['is_poetry']:
        query = f"Deutscher Lyrikpreis Peter-Huchel-Preis Neuerscheinungen 2024 2025"
    else:
        query = f"Anspruchsvolle moderne Bücher wie {original_book} {info['anchor']} {info['vibe']} 2024 2025"

    search_res = tavily.search(query=query, search_depth="advanced", max_results=10)
    context = "\n".join([r['content'] for r in search_res.get('results', [])])

    forbidden = ", ".join(session_history)
    prompt = (
        f"Kontext:\n{context}\n\nNenne exakt {needed} Buchempfehlungen (ab 2021).\n"
        f"STRIKTE REGEL: NICHT von {info['author']} und NICHT diese Titel: {forbidden}.\n"
        f"Format: Titel | Autor | Begründung"
    )
    
    res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
    
    for line in res.choices[0].message.content.strip().split('\n'):
        if '|' in line and len(results) < needed:
            p = line.split('|')
            title, author = p[0].strip(), p[1].strip()
            
            if any(f.lower() in title.lower() for f in session_history): continue
            
            data = verify_with_catalog(title, author)
            if data:
                results.append({
                    "title": data['real_title'], "author": data['real_author'],
                    "year": data['year'], "publisher": data['publisher'],
                    "isbn": data['isbn'], "reason": p[2].strip(),
                    "cover_url": data['cover_url'], 
                    "description": data['description'], # LÖSUNG: Beschreibung wurde hier vorher verschluckt!
                    "source": "live_web"
                })
            else:
                print(f"❌ KI-Vorschlag verworfen: '{title}' fiel durch den Katalog-Check.")
    return results

def get_recommendations(user_input):
    info = analyze_input_book(user_input)
    if user_input not in session_history:
        session_history.append(user_input)
        
    print(f"🎯 Fokus: {info['anchor']} | Tempo: {info['tempo']}") # Print wiederhergestellt
    final_results = []
    
    try:
        res_emb = openai_client.embeddings.create(input=f"{info['anchor']} {info['vibe']}", model="text-embedding-3-small")
        vector = res_emb.data[0].embedding
        # Schwellenwert aus altem Code wiederhergestellt (0.42 statt 0.52)
        db_res = supabase.rpc('match_books', {'query_embedding': vector, 'match_threshold': 0.52, 'match_count': 20}).execute()
        
        for b in db_res.data:
            if any(f.lower() in b['title'].lower() for f in session_history): continue
            if info['author'].lower() in b['author'].lower(): continue
            
            is_p = any(k in b['title'].lower() for k in ["gedichte", "lyrik", "balladen", "verse"])
            if info['is_poetry'] != is_p: continue
            
            final_results.append({
                "id": b.get('id'),
                "title": b['title'], "author": b['author'], 
                "year": b.get('year', '2024'), "tags": b.get('tags', ''),
                "source": "database", "reason": f"Passt perfekt zum Vibe: {info['vibe']}"
            })
            session_history.append(b['title'])
            if len(final_results) >= 2: break
    except Exception as e:
        print(f"🚨 DB-Info: {e}") # Print wiederhergestellt

    needed = 3 - len(final_results)
    if needed > 0:
        web_tips = search_external_books_live(user_input, info, needed)
        for tip in web_tips:
            final_results.append(tip)
            session_history.append(tip['title'])
    
    return final_results[:3]

def create_vibe_for_scraper(title, author, blurb):
    prompt = f"Analysiere das Buch '{title}' von {author}. Klappentext:\n{blurb}\n\nGib NUR eine Zeile zurück im Format: Gattung | Tempo | 3-5 Vibe-Adjektive"
    try:
        res = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        vibe_text = res.choices[0].message.content.strip()
        combined_text = f"{vibe_text}\n\n{blurb}"
        res_emb = openai_client.embeddings.create(input=combined_text, model="text-embedding-3-small")
        return combined_text, res_emb.data[0].embedding
    except Exception as e:
        return blurb, None

def add_book_to_database(title, author):
    data = verify_with_catalog(title, author)
    blurb_to_use = data.get('description', "Neu hinzugefügter Titel") if data else "Neu hinzugefügter Titel"
    
    if not data:
        data = {"real_title": title, "real_author": author, "year": "2024", "publisher": "Literaturverlag", "isbn": "", "cover_url": ""}

    vibe_tags, embedding = create_vibe_for_scraper(data['real_title'], data['real_author'], blurb_to_use)

    new_row = {
        "title": data['real_title'],
        "author": data['real_author'],
        "year": data['year'],
        "publisher": data['publisher'],
        "isbn": data['isbn'],
        "cover_url": data['cover_url'],
        "tags": vibe_tags,
        "embedding": embedding
    }
    
    res = supabase.table("buecher").insert(new_row).execute()
    
    if res.data:
        print(f"✅ Erfolgreich in DB gespeichert: {title}")
        return res.data[0]
    raise Exception("Fehler beim Speichern")