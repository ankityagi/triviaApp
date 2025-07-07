import streamlit as st
import requests

st.title("🧠 ChatGPT Trivia Game")
st.markdown("Supports multiple players, age-based questions, and random topics.")

# Player setup
st.header("👥 Player Setup")
players = []
num_players = st.number_input("Number of Players", 1, 10, 2)

for i in range(num_players):
    name = st.text_input(f"Player {i+1} Name", f"Player{i+1}")
    age = st.number_input(f"Player {i+1} Age", min_value=3, max_value=99, value=8)
    players.append({"name": name, "age": age})

# Rounds and topic
st.header("🎲 Game Settings")
rounds = st.slider("Number of Rounds", 1, 5, 2)
topic = st.selectbox("Pick a Topic", ["random", "Animals", "Space", "History", "Science", "Sports"])

if st.button("Generate Questions"):
    setup = {
        "players": players,
        "rounds": rounds,
        "topic": topic
    }
    with st.spinner("Generating questions..."):
        response = requests.post("http://localhost:8000/generate_questions/", json=setup)
        if response.ok:
            data = response.json()
            st.success("Questions ready!")
            st.header("📝 Questions")
            for q in data["questions"]:
                st.subheader(f"Round {q['round']} - {q['player']} ({q['age']} yrs)")
                st.write(f"**Topic:** {q['topic']}")
                st.write(q["question"])
                for opt in q["options"]:
                    st.write(f"- {opt}")
                st.markdown(f"**Answer:** ||{q['answer']}||")
        else:
            st.error("Failed to generate questions.")
