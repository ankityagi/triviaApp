
# TriviaApp

Multiplayer trivia game with Google OAuth authentication, OpenAI-powered question generation, and secure token-based session management.

## Project Structure

- `backend/` - FastAPI backend (OAuth, question generation, database)
- `frontend/` - Streamlit frontend (UI, game logic)
- `trivia.db` - SQLite database
- `.env` - Environment variables (see below)

## Setup Instructions

### 1. Clone and Install

```bash
git clone <repo-url>
cd triviaApp
conda env create -f environment.yml
conda activate trivia-env
```

### 2. Environment Variables

Create a `.env` file in the root directory with:

```
OPENAI_API_KEY=your-openai-key
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
SECRET_KEY=your-secret-key
```

### 3. Start Backend

```bash
uvicorn backend.main:app --reload
```

### 4. Start Frontend

```bash
cd frontend
streamlit run app.py
```

## Authentication & Security

- Google OAuth for login
- All session management is token-based using `itsdangerous` (no cookies)
- Tokens are passed in the `Authorization: Bearer <token>` header
- Backend expects valid tokens for all protected endpoints

## Usage

1. Open [http://localhost:8501](http://localhost:8501) in your browser
2. Login with Google
3. Set up players, rounds, and topic
4. Play the trivia game!

## Troubleshooting

- If you see `SessionMiddleware must be installed` errors, ensure you are not using session-based auth (remove SessionMiddleware from backend)
- For 500/401 errors, check your `.env` values and token handling in frontend/backend

## License

MIT