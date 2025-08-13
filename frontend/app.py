from dotenv import load_dotenv
import os
import streamlit as st
import requests
import random
from urllib.parse import urlencode

# Constants
load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
#BACKEND_URL = "http://localhost:8000"  # or your deployed backend

#############################
## Auth Items
#############################


# Initialize session state
if "token" not in st.session_state:
    st.session_state.token = None
if "user" not in st.session_state:
    st.session_state.user = None
if "auth_checked" not in st.session_state:
    st.session_state.auth_checked = False

# Show login UI if not authenticated

def show_login():
    st.warning("Please login to continue.")
    login_url = f"{BACKEND_URL}/login"
    st.title("🧠 Multiplayer Trivia Game")
    st.write(f"[LOG] show_login called. Login URL: {login_url}")
    if st.button("Login with Google"):
        st.write("Redirecting to Google...")
        st.write(f"[LOG] Login button clicked. Redirecting to: {login_url}")
        st.markdown(f'<meta http-equiv="refresh" content="0;url={login_url}">', unsafe_allow_html=True)
    st.stop()

# Check user authentication via backend

def check_auth():
    st.write(f"[LOG] check_auth called. auth_checked: {st.session_state.auth_checked}")
    if st.session_state.auth_checked:
        st.write("[LOG] Already authenticated.")
        return
    try:
        # Check for token in URL (after OAuth)
        query_params = st.query_params
        st.write(f"[LOG] Query params: {query_params}")
        if "token" in query_params:
            st.write(f"[LOG] Token found in query params: {query_params['token']}")
            st.session_state.token = query_params["token"]
            query_params.clear()
            st.rerun()
        if not st.session_state.token:
            st.write("[LOG] No token in session state. Showing login.")
            show_login()
        headers = {"Authorization": f"Bearer {st.session_state.token}"}
        st.write(f"[LOG] Sending /me request with headers: {headers}")
        res = requests.get(f"{BACKEND_URL}/me", headers=headers)
        st.write(f"[LOG] /me response status: {res.status_code}")
        if res.status_code == 200:
            st.session_state.user = res.json()
            st.session_state.auth_checked = True
            st.write(f"[LOG] Authenticated user: {st.session_state.user}")
            return
        else:
            st.write(f"[LOG] /me failed. Status: {res.status_code}")
            st.session_state.token = None
            show_login()
    except Exception as e:
        st.error(f"Auth check failed: {e}")
        st.write(f"[LOG] Auth check exception: {e}")
        st.stop()

# Backend wrappers with proper session handling

def backend_get(path, **kwargs):
    headers = kwargs.pop("headers", {})
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    st.write(f"[LOG] backend_get: GET {BACKEND_URL}{path} headers={headers}")
    response = requests.get(f"{BACKEND_URL}{path}", headers=headers, **kwargs)
    st.write(f"[LOG] backend_get: response status={response.status_code}")
    if response.status_code == 401:
        show_login()
    return response

def backend_post(path, json=None):
    headers = {}
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    st.write(f"[LOG] backend_post: POST {BACKEND_URL}{path} headers={headers} json={json}")
    response = requests.post(f"{BACKEND_URL}{path}", json=json, headers=headers)
    st.write(f"[LOG] backend_post: response status={response.status_code}")
    if response.status_code == 401:
        show_login()
    return response

# Logout function

def logout():
    st.write("[LOG] logout called.")
    try:
        res = backend_post("/logout")
        st.write(f"[LOG] logout response status: {res.status_code}")
        if res.status_code == 200:
            st.session_state.user = None
            st.session_state.auth_checked = False
            st.session_state.token = None
            st.write("[LOG] User logged out. Rerunning app.")
            st.rerun()
    except Exception as e:
        st.error(f"Logout failed: {e}")
        st.write(f"[LOG] Logout exception: {e}")

############## Main App ###########################

# Check auth before rendering anything
check_auth()

if not st.session_state.user:
    show_login()

st.title("🧠 Multiplayer Trivia Game")

# User info and logout button
col1, col2 = st.columns([4, 1])
with col1:
    st.success(f"Welcome, {st.session_state.user['name']}! 👋")
with col2:
    if st.button("Logout"):
        logout()

# Session state setup
if "questions" not in st.session_state:
    st.session_state.questions = []
    st.session_state.current_index = 0
    st.session_state.scores = {}
    st.session_state.answers = {}
    st.session_state.exit_quiz = False

# Setup form
if not st.session_state.questions:
    st.header("Game Setup")
    st.write("[LOG] Game setup form displayed.")
    num_players = st.number_input("Number of players", 1, 5, 2)
    players = []
    for i in range(num_players):
        name = st.text_input(f"Player {i+1} Name", f"Player{i+1}")
        age = st.number_input(f"{name}'s Age", 3, 99, 8, key=f"age_{i}")
        players.append({"name": name, "age": age})
    st.write(f"[LOG] Players: {players}")
    rounds = st.slider("Rounds", 1, 5, 2)
    topic = st.selectbox("Topic", ["random", "Animals", "Space", "Science", "History", "Sports"])
    st.write(f"[LOG] Setup: rounds={rounds}, topic={topic}")
    if st.button("Start Game"):
        setup = {
            "players": players,
            "rounds": rounds,
            "topic": topic
        }
        for i in range(num_players):
            st.session_state.scores[players[i]["name"]] = 0
        st.write(f"[LOG] Starting game with setup: {setup}")
        with st.spinner("Generating questions..."):
            res = backend_post("/generate_questions/", json=setup)
            st.write(f"[LOG] /generate_questions/ response status: {res.status_code}")
            if res.status_code == 200:
                st.session_state.questions = res.json()["questions"]
                st.write(f"[LOG] Questions loaded: {st.session_state.questions}")
                st.rerun()
            else:
                st.error(f"Failed to load questions. Status: {res.status_code}")
                st.write(f"[LOG] Failed to load questions. Status: {res.status_code}")

# Game Loop
elif st.session_state.current_index < len(st.session_state.questions):
    q = st.session_state.questions[st.session_state.current_index]
    current_player = q['player']
    st.write(f"[LOG] Displaying question {st.session_state.current_index} for player {current_player}")
    st.subheader(f"Round {q['round']} - {q['player']}")
    st.markdown(f"**Question:** {q['question']}")
    selected = st.radio("Choose your answer:", q["options"], key=f"q{st.session_state.current_index}")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Submit Answer"):
            st.write(f"[LOG] Answer submitted: {selected}")
            if selected == q["answer"]:
                st.success("✅ Correct!")
                st.session_state.scores[current_player] += 1
                st.write(f"[LOG] Correct answer! Score updated: {st.session_state.scores}")
            else:
                st.error(f"❌ Wrong! Correct answer: {q['answer']}")
                st.write(f"[LOG] Wrong answer. Correct: {q['answer']}")
            st.session_state.current_index += 1
            st.write(f"[LOG] Moving to next question. Current index: {st.session_state.current_index}")
            st.rerun()
    with col2:
        if st.button("❌ Exit Quiz"):
            st.session_state.exit_quiz = True
            for key in ["questions", "current_index", "scores", "answers"]:
                del st.session_state[key]
            st.write("[LOG] Quiz exited. Session state cleared.")
            st.rerun()

# Game Over
else:
    st.success("🎉 Game Over!")
    st.header("Final Scores")
    sorted_scores = sorted(st.session_state.scores.items(), key=lambda x: x[1], reverse=True)
    st.write(f"[LOG] Final scores: {sorted_scores}")
    for player, score in sorted_scores:
        st.write(f"**{player}:** {score} points")
    if st.button("Play Again"):
        for key in ["questions", "current_index", "scores", "answers"]:
            del st.session_state[key]
            st.write(f"[LOG] Cleared {key} from session state")
        st.rerun()