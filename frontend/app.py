from dotenv import load_dotenv
import os
import streamlit as st
import requests
import random
import asyncio
import websockets
import json
import threading
import time
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
        if not st.session_state.token:
            show_login()
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

# Question management functions

def fetch_questions_from_db(age=None, topic=None, limit=10):
    """Fetch questions from database without triggering generation"""
    params = {"limit": limit}
    if age:
        params["age"] = age
    if topic and topic != "random":
        params["topic"] = topic
    
    try:
        res = backend_get("/questions", params=params)
        if res.status_code == 200:
            return res.json()
        else:
            st.error(f"Failed to fetch questions: {res.status_code}")
            return []
    except Exception as e:
        st.error(f"Error fetching questions: {e}")
        return []

def trigger_async_generation(target_count, age_range, topic):
    """Start async question generation"""
    try:
        generation_request = {
            "target_count": target_count,
            "age_range": age_range,
            "topic": topic if topic != "random" else "General"
        }
        res = backend_post("/generate_questions_async", json=generation_request)
        if res.status_code == 200:
            job_data = res.json()
            return job_data["job_id"]
        else:
            st.error(f"Failed to start generation: {res.status_code}")
            return None
    except Exception as e:
        st.error(f"Error starting generation: {e}")
        return None

def check_generation_status(job_id):
    """Check status of async generation job"""
    try:
        res = backend_get(f"/generation_status/{job_id}")
        if res.status_code == 200:
            return res.json()
        else:
            return None
    except Exception as e:
        return None

def build_game_questions(players, rounds, topic, db_questions):
    """Build game questions from available database questions"""
    game_questions = []
    question_pool = db_questions.copy()
    
    for round_num in range(1, rounds + 1):
        for player in players:
            if question_pool:
                # Pick a question appropriate for the player's age
                suitable_questions = [
                    q for q in question_pool 
                    if q.get("min_age", 0) <= player["age"] <= q.get("max_age", 99)
                ]
                
                if suitable_questions:
                    chosen_question = random.choice(suitable_questions)
                    question_pool.remove(chosen_question)
                else:
                    # Fallback: use any available question
                    if question_pool:
                        chosen_question = random.choice(question_pool)
                        question_pool.remove(chosen_question)
                    else:
                        # No questions available
                        return game_questions
                
                game_questions.append({
                    "round": round_num,
                    "player": player["name"],
                    "question": chosen_question["prompt"],
                    "options": chosen_question["options"],
                    "answer": chosen_question["answer"],
                    "topic": chosen_question.get("topic", "General")
                })
    
    return game_questions

# WebSocket support for real-time updates

def init_websocket():
    """Initialize WebSocket connection in session state"""
    if "websocket_status" not in st.session_state:
        st.session_state.websocket_status = "disconnected"
        st.session_state.websocket_messages = []
        
async def connect_websocket(user_email):
    """Connect to WebSocket for real-time updates"""
    try:
        websocket_url = BACKEND_URL.replace("http://", "ws://").replace("https://", "wss://")
        uri = f"{websocket_url}/ws/{user_email}"
        
        async with websockets.connect(uri) as websocket:
            st.session_state.websocket_status = "connected"
            
            # Listen for messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    st.session_state.websocket_messages.append(data)
                    
                    # Handle job status updates
                    if data.get("type") == "job_status" and data.get("job_id") == st.session_state.generation_job_id:
                        st.rerun()  # Refresh the UI when job status changes
                        
                except json.JSONDecodeError:
                    continue
                    
    except Exception as e:
        st.session_state.websocket_status = f"error: {str(e)}"

def start_websocket_connection():
    """Start WebSocket connection in background thread"""
    if (st.session_state.user and 
        st.session_state.websocket_status == "disconnected" and 
        st.session_state.generation_job_id):
        
        def run_websocket():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(connect_websocket(st.session_state.user["email"]))
            
        websocket_thread = threading.Thread(target=run_websocket, daemon=True)
        websocket_thread.start()
        st.session_state.websocket_status = "connecting"

############## Main App ###########################

# Check auth before rendering anything
check_auth()

if not st.session_state.user:
    show_login()

# Initialize WebSocket support
init_websocket()

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
    st.session_state.question_queue = []
    st.session_state.total_needed = 0
    st.session_state.generation_job_id = None

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
    
    # Calculate questions needed
    total_questions_needed = num_players * rounds
    
    # Add a spinner state to prevent multiple clicks
    if "quiz_loading" not in st.session_state:
        st.session_state.quiz_loading = False
        
    # Show available questions info with enhanced feedback
    if not st.session_state.quiz_loading:
        # Preview available questions
        avg_age = sum(p["age"] for p in players) / len(players) if players else 8
        preview_questions = fetch_questions_from_db(age=int(avg_age), topic=topic, limit=total_questions_needed + 10)  # Check for buffer
        
        col1, col2 = st.columns([3, 1])
        with col1:
            available_count = len(preview_questions)
            buffer_threshold = total_questions_needed + 5  # We want at least 5 extra questions
            
            if available_count >= buffer_threshold:
                st.success(f"✅ {available_count} questions available (need {total_questions_needed}) - Excellent supply!")
            elif available_count >= total_questions_needed:
                st.info(f"📚 {available_count} questions available (need {total_questions_needed}) - Sufficient for game")
            else:
                shortage = total_questions_needed - available_count
                st.warning(f"⚠️ Only {available_count} questions available (need {total_questions_needed}). Will auto-generate {shortage + 5} more questions.")
                
                # Show auto-trigger info
                st.caption("🤖 Auto-generation will start when you click 'Start Game'")
        
        with col2:
            st.metric("Need", total_questions_needed, delta=None)
            st.metric("Available", available_count, delta=available_count - total_questions_needed)
    
    # Button click: set loading and rerun
    if not st.session_state.quiz_loading:
        if st.button("Start Game", key="start_quiz_btn"):
            st.session_state.quiz_loading = True
            st.session_state.setup_data = {
                "players": players,
                "rounds": rounds,
                "topic": topic,
                "total_needed": total_questions_needed
            }
            st.rerun()
            
    # After rerun, show spinner and fetch/generate questions
    elif st.session_state.quiz_loading:
        setup_data = st.session_state.setup_data
        players = setup_data["players"]
        rounds = setup_data["rounds"] 
        topic = setup_data["topic"]
        total_questions_needed = setup_data["total_needed"]
        
        # Initialize scores
        for player in players:
            st.session_state.scores[player["name"]] = 0
        
        with st.spinner("Loading questions from database..."):
            # First try to get questions from database
            avg_age = sum(p["age"] for p in players) / len(players) if players else 8
            db_questions = fetch_questions_from_db(age=int(avg_age), topic=topic, limit=total_questions_needed * 2)
            
            if len(db_questions) >= total_questions_needed:
                # We have enough questions, build the game immediately
                game_questions = build_game_questions(players, rounds, topic, db_questions)
                st.session_state.questions = game_questions
                st.session_state.quiz_loading = False
                st.success(f"✅ Loaded {len(game_questions)} questions from database!")
                st.rerun()
            else:
                # Not enough questions, show status and trigger generation
                st.session_state.quiz_loading = False
                shortage = total_questions_needed - len(db_questions)
                
                # Show what we have so far
                if db_questions:
                    partial_questions = build_game_questions(players, rounds, topic, db_questions)
                    st.session_state.question_queue = partial_questions
                    st.info(f"📚 Found {len(partial_questions)} questions in database")
                
                st.warning(f"🔄 Need {shortage} more questions. Starting generation...")
                
                # Trigger async generation for the shortage
                age_range = [min(p["age"] for p in players), max(p["age"] for p in players)]
                job_id = trigger_async_generation(shortage + 5, age_range, topic)  # Generate a few extra
                
                if job_id:
                    st.session_state.generation_job_id = job_id
                    st.session_state.total_needed = total_questions_needed
                    st.info(f"⏳ Generation job started (ID: {job_id[:8]}...)")
                    
                    # Start WebSocket connection for real-time updates
                    start_websocket_connection()
                    st.rerun()
                else:
                    st.error("Failed to start question generation. Please try again.")
                    st.session_state.quiz_loading = False

    # Show generation progress if we're waiting for questions
    if st.session_state.generation_job_id and not st.session_state.questions:
        # Show WebSocket connection status
        if st.session_state.websocket_status not in ["disconnected", "error"]:
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.session_state.websocket_status == "connected":
                    st.success("🔗 Real-time updates")
                elif st.session_state.websocket_status == "connecting":
                    st.info("🔄 Connecting...")
        
        job_status = check_generation_status(st.session_state.generation_job_id)
        if job_status:
            if job_status["status"] == "completed":
                st.success("✅ Question generation completed!")
                # Fetch the newly generated questions and start the game
                setup_data = st.session_state.setup_data
                players = setup_data["players"]
                rounds = setup_data["rounds"]
                topic = setup_data["topic"]
                
                avg_age = sum(p["age"] for p in players) / len(players) if players else 8
                all_questions = fetch_questions_from_db(age=int(avg_age), topic=topic, limit=st.session_state.total_needed * 2)
                game_questions = build_game_questions(players, rounds, topic, all_questions)
                
                st.session_state.questions = game_questions
                st.session_state.generation_job_id = None
                st.session_state.websocket_status = "disconnected"  # Reset WebSocket
                st.balloons()  # Celebration effect
                st.rerun()
                
            elif job_status["status"] == "failed":
                st.error(f"❌ Question generation failed: {job_status.get('message', 'Unknown error')}")
                st.session_state.generation_job_id = None
                st.session_state.websocket_status = "disconnected"  # Reset WebSocket
                
            elif job_status["status"] in ["pending", "running"]:
                # Enhanced progress display
                generated = job_status.get("generated_count", 0)
                target = job_status.get("target_count", 1)
                progress = generated / target if target > 0 else 0
                
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.progress(progress)
                with col2:
                    st.metric("Progress", f"{generated}/{target}")
                
                # Status message with more detail
                if job_status["status"] == "pending":
                    st.info("⏳ Waiting for question generation to start...")
                else:
                    st.info(f"🤖 AI is generating questions... ({generated}/{target} complete)")
                
                # Show any recent WebSocket messages
                if st.session_state.websocket_messages:
                    latest_message = st.session_state.websocket_messages[-1]
                    if latest_message.get("type") == "job_status":
                        st.caption(f"💬 Latest update: {latest_message.get('message', '')}")
                
                # Auto-refresh every 3 seconds (less aggressive than before)
                time.sleep(3)
                st.rerun()
        else:
            st.warning("⚠️ Unable to check generation status. Please refresh the page.")
            if st.button("🔄 Refresh Status"):
                st.rerun()
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
            st.session_state.current_index += 1
            st.rerun()
    with col2:
        if st.button("❌ Exit Quiz"):
            st.session_state.exit_quiz = True
            # Clear all game-related session state
            keys_to_clear = [
                "questions", "current_index", "scores", "answers", 
                "question_queue", "total_needed", "generation_job_id",
                "quiz_loading", "setup_data", "websocket_status",
                "websocket_messages"
            ]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

# Game Over
else:
    st.success("🎉 Game Over!")
    st.header("Final Scores")
    sorted_scores = sorted(st.session_state.scores.items(), key=lambda x: x[1], reverse=True)
    for player, score in sorted_scores:
        st.write(f"**{player}:** {score} points")
    if st.button("Play Again"):
        # Clear all game-related session state for fresh start
        keys_to_clear = [
            "questions", "current_index", "scores", "answers", 
            "question_queue", "total_needed", "generation_job_id",
            "quiz_loading", "setup_data", "websocket_status",
            "websocket_messages"
        ]
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()