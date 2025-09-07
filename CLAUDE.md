# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (FastAPI)
```bash
# Start backend server
uvicorn backend.main:app --reload

# Install backend dependencies
pip install -r backend/requirements.txt
```

### Frontend (Streamlit)
```bash
# Start frontend
cd frontend
streamlit run app.py

# Install all dependencies
pip install -r requirements.txt
```

### Environment Setup
The project uses virtual environments. Install dependencies using:
```bash
# Install all dependencies (includes both backend and frontend)
pip install -r requirements.txt
```

## Project Architecture

### Core Structure
- **backend/**: FastAPI server handling OAuth, OpenAI integration, and database operations
- **frontend/**: Streamlit web app providing the game interface
- **Database**: SQLite (`trivia.db`) with SQLAlchemy ORM

### Authentication Flow
- Uses Google OAuth for authentication via `backend/main.py:47-67`
- Token-based session management with `itsdangerous` (no cookies)
- All API calls use `Authorization: Bearer <token>` headers
- Tokens are managed by Streamlit frontend in session state

### Key Components

#### Backend (`backend/main.py`)
- FastAPI app with Google OAuth integration
- OpenAI client for question generation 
- SQLAlchemy database models and operations
- CORS middleware configured for frontend integration

#### Frontend (`frontend/app.py`)
- Streamlit UI for game interface
- Handles OAuth callback and token management
- Game logic for multiplayer trivia sessions
- API communication with backend

#### Database Models (`backend/models.py`)
- `User`: Stores Google OAuth user data (email, name, picture)
- `TriviaLog`: Tracks trivia sessions (user_id, topic, rounds, timestamp)

### Configuration
Requires `.env` file with:
- `OPENAI_API_KEY`: For question generation
- `GOOGLE_CLIENT_ID` & `GOOGLE_CLIENT_SECRET`: For OAuth
- `SECRET_KEY`: For token signing
- `FRONTEND_URL`: Default "http://localhost:8501"
- `BACKEND_URL`: Default "http://localhost:8000"

### Testing
No test framework currently configured. No existing test files in the main project structure.

### Important Notes
- The codebase has both `trivia-app-pip/` and `trivia/` virtual environment directories
- Session management is entirely token-based - avoid using SessionMiddleware
- All protected backend endpoints expect valid Bearer tokens
- Frontend runs on port 8501, backend on port 8000