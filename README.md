# Language Learning AI Backend

Minimal FastAPI backend for the language-learning assistant.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> to receive:

```json
{"message": "Hello, world!"}
```

Interactive API documentation is available at <http://127.0.0.1:8000/docs>.
