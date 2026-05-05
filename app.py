from flask import Flask, request, jsonify
from flask_cors import CORS
from logic_engine import get_recommendations
import os

app = Flask(__name__)
# Erlaubt deiner GitHub-Seite den Zugriff
CORS(app)

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "message": "Lit_select API is running"}), 200

@app.route('/get_inspiration', methods=['POST', 'OPTIONS'])
@app.route('/get_inspiration/', methods=['POST', 'OPTIONS'])
def get_inspiration():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
        
    data = request.json
    # Akzeptiert "vibe" oder "title" vom Frontend
    query = data.get("vibe") or data.get("title") or ""
    
    try:
        results = get_recommendations(query)
        return jsonify({"books": results})
    except Exception as e:
        print(f"Error: {str(e)}") # Erscheint in den Render-Logs
        return jsonify({"error": str(e)}), 500

@app.route('/api/search', methods=['POST'])
def search_books():
    data = request.json
    book_title = data.get("title", "")
    try:
        results = get_recommendations(book_title)
        return jsonify({"books": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Render nutzt Gunicorn, aber für lokales Testen:
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)