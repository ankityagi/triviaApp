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
    if st.button("Login with Google"):
        st.write("Redirecting to Google...")
        st.markdown(f'<meta http-equiv="refresh" content="0;url={login_url}">', unsafe_allow_html=True)
    st.stop()

# Check user authentication via backend

def check_auth():
    if st.session_state.auth_checked:
        return
    try:
        # Check for token in URL (after OAuth)
        query_params = st.query_params
        if "token" in query_params:
            st.session_state.token = query_params["token"]
            query_params.clear()
            st.rerun()
        # If no token, show login
        if not st.session_state.token:
            show_login()
        # Validate token with backend
        headers = {"Authorization": f"Bearer {st.session_state.token}"}
        res = requests.get(f"{BACKEND_URL}/me", headers=headers)
        if res.status_code == 200:
            st.session_state.user = res.json()
            st.session_state.auth_checked = True
            return
        else:
            st.session_state.token = None
            show_login()
    except Exception as e:
        st.error(f"Auth check failed: {e}")
        st.stop()

# Backend wrappers with proper session handling

def backend_get(path, **kwargs):
    headers = kwargs.pop("headers", {})
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    response = requests.get(f"{BACKEND_URL}{path}", headers=headers, **kwargs)
    if response.status_code == 401:
        show_login()
    return response

def backend_post(path, json=None):
    headers = {}
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    response = requests.post(f"{BACKEND_URL}{path}", json=json, headers=headers)
    if response.status_code == 401:
        show_login()
    return response

# Logout function

def logout():
    try:
        res = backend_post("/logout")
        if res.status_code == 200:
            st.session_state.user = None
            st.session_state.auth_checked = False
            st.session_state.token = None
            st.rerun()
    except Exception as e:
        st.error(f"Logout failed: {e}")

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
    num_players = st.number_input("Number of players", 1, 5, 2)
    players = []
    for i in range(num_players):
        name = st.text_input(f"Player {i+1} Name", f"Player{i+1}")
        age = st.number_input(f"{name}'s Age", 3, 99, 8, key=f"age_{i}")
        players.append({"name": name, "age": age})
        

    rounds = st.slider("Rounds", 1, 5, 2)
    topic = st.selectbox("Topic", ["random", "Animals", "Space", "Science", "History", "Sports"])

    if st.button("Start Game"):
        setup = {
            "players": players,
            "rounds": rounds,
            "topic": topic
        }

        #initialize score once start button is clicked
        for i in range(num_players):
            st.session_state.scores[players[i]["name"]] = 0

        print(f"Total players = {len(players)}")
        print(f"len of scores = {len(st.session_state.scores)}")
        print(f"st.session_state.current_index {st.session_state.current_index}")
        with st.spinner("Generating questions..."):
            res = backend_post("/generate_questions/", json=setup)
            
            if res.status_code == 200:
                st.session_state.questions = res.json()["questions"]
                st.rerun()
            else:
                st.error(f"Failed to load questions. Status: {res.status_code}")

# Game Loop
elif st.session_state.current_index < len(st.session_state.questions):
    q = st.session_state.questions[st.session_state.current_index]
    current_player = q['player']
    st.subheader(f"Round {q['round']} - {q['player']}")
    st.markdown(f"**Question:** {q['question']}")
    selected = st.radio("Choose your answer:", q["options"], key=f"q{st.session_state.current_index}")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Submit Answer"):
            if selected == q["answer"]:
                st.success("✅ Correct!")
                st.session_state.scores[current_player] += 1
            else:
                st.error(f"❌ Wrong! Correct answer: {q['answer']}")

            # Move to next question
            st.session_state.current_index += 1
            st.rerun()

    with col2:
        if st.button("❌ Exit Quiz"):
            st.session_state.exit_quiz = True
            for key in ["questions", "current_index", "scores", "answers"]:
                del st.session_state[key]
            st.rerun()

# Game Over
else:
    st.success("🎉 Game Over!")
    st.header("Final Scores")
    sorted_scores = sorted(st.session_state.scores.items(), key=lambda x: x[1], reverse=True)
    print(sorted_scores)
    for player, score in sorted_scores:
        st.write(f"**{player}:** {score} points")

    if st.button("Play Again"):
        for key in ["questions", "current_index", "scores", "answers"]:
            del st.session_state[key]
            print(f"Cleared {key} from session state")
        st.rerun()