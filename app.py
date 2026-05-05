from flask import Flask, request, jsonify
from flask_cors import CORS
# WICHTIG: Hier muss add_book_to_database mit importiert werden
from logic_engine import get_recommendations, add_book_to_database 
import os

app = Flask(__name__)
CORS(app)

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "message": "Lit_select API is running"}), 200

# Diese Route fehlte bisher und ist für das Speichern zuständig
@app.route('/add_book', methods=['POST', 'OPTIONS'])
@app.route('/add_book/', methods=['POST', 'OPTIONS'])
def add_book():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    
    data = request.json
    title = data.get("title")
    author = data.get("author", "")
    
    try:
        new_book = add_book_to_database(title, author)
        return jsonify({"status": "success", "book": new_book})
    except Exception as e:
        print(f"Add Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_inspiration', methods=['POST', 'OPTIONS'])
@app.route('/get_inspiration/', methods=['POST', 'OPTIONS'])
def get_inspiration():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.json
    query = data.get("vibe") or data.get("title") or ""
    try:
        results = get_recommendations(query)
        return jsonify(results) 
    except Exception as e:
        print(f"Error: {str(e)}")
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)