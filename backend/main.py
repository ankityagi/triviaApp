from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List
import random
from openai import OpenAI
import os


load_dotenv()

# Set the key globally
api_key = os.getenv("REMOVED")

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
    topic: str = "random"

@app.get("/")
def read_root():
    return {"message": "Trivia backend is running!"}

@app.post("/generate_questions/")
async def generate_questions(setup: GameSetup):
    chosen_topic = random.choice(topics) if setup.topic == "random" else setup.topic
    questions = []

    for round_num in range(setup.rounds):
        for player in setup.players:
            prompt = (
                f"Create one multiple-choice trivia question (with 4 options and correct answer) "
                f"for a {player.age}-year-old about {chosen_topic}. Respond in JSON format like: "
                f"{{'question': '...', 'options': [...], 'answer': '...'}}"
            )
            response = client.chat.completions.create(model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a trivia game generator."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7)
            content = response.choices[0].message.content
            try:
                # Evaluate JSON response
                question_data = eval(content)
                questions.append({
                    "player": player.name,
                    "age": player.age,
                    "round": round_num + 1,
                    "topic": chosen_topic,
                    **question_data
                })
            except:
                questions.append({
                    "player": player.name,
                    "age": player.age,
                    "round": round_num + 1,
                    "topic": chosen_topic,
                    "question": "Error generating question",
                    "options": [],
                    "answer": ""
                })
    return {"questions": questions}


