from flask import Flask, render_template, request, jsonify
from flask_cors import CORS  # NEU: Erlaubt den Zugriff von GitHub Pages
from logic_engine import get_recommendations

app = Flask(__name__)
CORS(app) # NEU: Aktiviert die Erlaubnis für Cross-Origin Anfragen

@app.route('/')
def index():
    # Render dein bisheriges HTML
    return render_template('index.html')

# NEU: Diese Route wird laut deinem Log vom Frontend aufgerufen
@app.route('/get_inspiration/', methods=['POST', 'OPTIONS'])
@app.route('/get_inspiration', methods=['POST', 'OPTIONS'])
def get_inspiration():
    data = request.json
    # Falls dein Frontend "vibe" oder "title" sendet
    query = data.get("vibe") or data.get("title") or ""
    try:
        results = get_recommendations(query)
        return jsonify({"books": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/search', methods=['POST'])
def search_books():
    data = request.json
    book_title = data.get("title", "")
    try:
        # Ruft direkt deine "schlaue" Engine auf
        results = get_recommendations(book_title)
        return jsonify({"books": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)