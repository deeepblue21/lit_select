from flask import Flask, render_template, request, jsonify
from logic_engine import get_recommendations

app = Flask(__name__)

@app.route('/')
def index():
    # Render dein bisheriges HTML
    return render_template('index.html')

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