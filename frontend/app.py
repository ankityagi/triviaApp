import streamlit as st
import requests
import random

# Constants
BACKEND_URL = "http://localhost:8000"  # or your deployed backend

st.title("🧠 Multiplayer Trivia Game")

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
        st.session_state.scores[name] = 0

    rounds = st.slider("Rounds", 1, 5, 2)
    topic = st.selectbox("Topic", ["random", "Animals", "Space", "Science", "History", "Sports"])

    if st.button("Start Game"):
        setup = {
            "players": players,
            "rounds": rounds,
            "topic": topic
        }
        with st.spinner("Generating questions..."):
            res = requests.post(f"{BACKEND_URL}/generate_questions/", json=setup)
            print(res)


            st.session_state.questions = res.json()["questions"]
            st.rerun()

# Game Loop
elif st.session_state.current_index < len(st.session_state.questions):
    q = st.session_state.questions[st.session_state.current_index]
    st.subheader(f"Round {q['round']} - {q['player']}")
    st.markdown(f"**Question:** {q['question']}")
    selected = st.radio("Choose your answer:", q["options"], key=f"q{st.session_state.current_index}")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Submit Answer"):
            if selected == question["answer"]:
                st.success("✅ Correct!")
                st.session_state.scores[current_player] += 1
            else:
                st.error(f"❌ Wrong! Correct answer: {question['answer']}")

            # Move to next player or round
            if st.session_state.current_player_index + 1 < len(st.session_state.players):
                st.session_state.current_player_index += 1
            else:
                st.session_state.current_player_index = 0
                st.session_state.current_round += 1

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
    for player, score in sorted_scores:
        st.write(f"**{player}:** {score} points")

    if st.button("Play Again"):
        for key in ["questions", "current_index", "scores", "answers"]:
            del st.session_state[key]
        st.rerun()
