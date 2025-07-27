from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List
import random
from openai import OpenAI
import os
import json


load_dotenv()

# Set the key globally
api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=api_key)



app = FastAPI()

# Define topics
topics = ["Animals", "Space", "History", "Science", "Sports"]

class Player(BaseModel):
    name: str
    age: int

class GameSetup(BaseModel):
    players: List[Player]
    rounds: int
    topic: str = "Random"

@app.get("/")
def read_root():
    return {"message": "Trivia backend is running!"}


@app.post("/generate_questions/")
def generate_questions(setup: GameSetup):
    chosen_topic = random.choice(topics) if setup.topic == "random" else setup.topic
    questions = []

    for round_num in range(setup.rounds):
        for player in setup.players:
            # Force prompt uniqueness by using player name and randomness
            seed = random.randint(1000, 9999)
            prompt = (
                f"You are a trivia question generator. "
                f"Create a fun, multiple-choice question for a {player.age}-year-old named {player.name}. "
                f"Topic: {chosen_topic}. Each question must be unique across players and not a repeat of previous examples. "
                f"Inject creativity and age-appropriate fun. Format your output as a JSON object with these keys: "
                f"'question' (string), 'options' (list of 4 strings), and 'answer' (string). "
                f"Use this random context ID to vary the question: {seed}."
            )
            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You generate trivia questions in JSON format."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.8
                )

                # Expecting a dict in the format: {"question": ..., "options": [...], "answer": ...}
                q_json = json.loads(response.choices[0].message.content)

                questions.append({
                    "player": player.name,
                    "age": player.age,
                    "round": round_num + 1,
                    "topic": chosen_topic,
                    **q_json
                })

            except Exception as e:
                questions.append({
                    "player": player.name,
                    "age": player.age,
                    "round": round_num + 1,
                    "topic": chosen_topic,
                    "question": f"Error generating question: {str(e)}",
                    "options": ["N/A", "N/A", "N/A", "N/A"],
                    "answer": "N/A"
                })
    print(questions)
    return {"questions": questions}



