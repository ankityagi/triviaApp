
from fastapi import FastAPI, Request, Depends, HTTPException, Header
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from authlib.integrations.starlette_client import OAuth
from typing import List, Optional
from openai import OpenAI
import random, os, json
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


from .models import User, QuestionLog
from .database import SessionLocal, engine, Base
from pydantic import BaseModel



load_dotenv()
# Create all tables defined in models.py
print("Tables to create:", Base.metadata.tables.keys())
Base.metadata.create_all(bind=engine)

# Set the key globally
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)
testing = False
app = FastAPI()


app.add_middleware(SessionMiddleware, os.getenv("SECRET_KEY"))


# CORS only (no session middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_origins=["https://triviaapp.streamlit.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Google OAuth Setup
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# itsdangerous setup
SECRET_KEY = os.getenv("SECRET_KEY")
TOKEN_EXPIRY_SECONDS = 3600  # 1 hour
serializer = URLSafeTimedSerializer(SECRET_KEY)

def generate_token(user_info):
    return serializer.dumps(user_info)

def verify_token(token):
    try:
        user_info = serializer.loads(token, max_age=TOKEN_EXPIRY_SECONDS)
        return user_info
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Token expired")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid token")



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

##################################
## Autheticate using google starts
##################################

## Start with login


@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")

    return await oauth.google.authorize_redirect(request, redirect_uri)



# Function to check if user is authenticated (token-based)
@app.get("/me")
async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    return JSONResponse(content=user_info)



@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        if "id_token" not in token:
            raise HTTPException(status_code=400, detail="Missing id_token in response")
        user_info = await oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        user_info = user_info.json()

        db = SessionLocal()
        if not db.query(User).filter(User.email == user_info["email"]).first():
            db.add(User(
                email=user_info["email"],
                name=user_info["name"],
                picture=user_info["picture"]
            ))
            db.commit()
        db.close()

        # Generate signed token
        signed_token = generate_token(user_info)
        # Redirect with token in query param
        return RedirectResponse(url=f"http://localhost:8501/?token={signed_token}")
    except Exception as e:
        print(f"Error in auth callback: {str(e)}")
        raise HTTPException(status_code=500, detail="Authentication failed")



@app.get("/protected")
def protected(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    return {"user": user_info}


@app.post("/logout")
async def logout():
    # Token-based: client just deletes token
    return {"message": "Logged out successfully"}


## Auth classes ends



@app.post("/generate_questions/")
def generate_questions(setup: GameSetup, authorization: Optional[str] = Header(None)):
    # Token-based authentication
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    questions = []
    db = SessionLocal()

    if testing == False:
        chosen_topic = random.choice(topics) if setup.topic == "random" else setup.topic
        for round_num in range(setup.rounds):
            for player in setup.players:
                # Force prompt uniqueness by using player name and randomness
                seed = random.randint(1000, 9999)
                prompt = (
                    f"You are a trivia question generator. "
                    f"Create a fun, multiple-choice question for a {player.age}-year-old named {player.name}. "
                    f"Topic: {chosen_topic}. Each question must be unique across players and not a repeat of previous question. "
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
    elif testing == True:
        # Mock questions here for testing
        questions = [
            {
                "player": "Player1",
                "age": 8,
                "round": 1,
                "topic": "Space",
                "question": "Hey Player1, do you know what we call a group of stars that forms an imaginary picture in the sky?",
                "options": ["A Star Party", "A Star Picnic", "A Star Cluster", "A Constellation"],
                "answer": "A Constellation"
            },
            {
                "player": "Player2",
                "age": 8,
                "round": 1,
                "topic": "Space",
                "question": "Hey Player2, did you know Outer Space is full of surprises? Can you guess what color the Sun is, from outer space?",
                "options": ["Red", "Yellow", "Blue", "It's not there!"],
                "answer": "Blue"
            },
            {
                "player": "Player1",
                "age": 8,
                "round": 2,
                "topic": "Space",
                "question": "Hey, Player1! Which planet in our solar system is known as the 'Red Planet'?",
                "options": ["A. Jupiter", "B. Pluto", "C. Mars", "D. Venus"],
                "answer": "C. Mars"
            },
            {
                "player": "Player2",
                "age": 8,
                "round": 2,
                "topic": "Space",
                "question": "Hey Player2, if you were on the moon, which of these things would be true?",
                "options": [
                    "You could eat as much ice cream as you want without feeling full",
                    "Your favorite teddy bear would start to talk",
                    "You would weigh less than you do on Earth",
                    "Your sneakers could turn into rocket boots"
                ],
                "answer": "You would weigh less than you do on Earth"
            }
        ]

    db.commit()
    return {"questions": questions}



