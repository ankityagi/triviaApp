from fastapi import FastAPI, Request, Depends, HTTPException, Header, BackgroundTasks, WebSocket, WebSocketDisconnect
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from authlib.integrations.starlette_client import OAuth
from typing import List, Optional
from openai import OpenAI
import random, os, json, hashlib, uuid, asyncio
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Dict, Set
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


from .models import User, TriviaLog, Question, UserQuestion
from .database import SessionLocal, engine, Base
from pydantic import BaseModel
import os, pprint


# Load environment variables
load_dotenv()

print("=== All Environment Variables ===")
for k, v in os.environ.items():
    print(f"{k}={v}")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
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
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("CORS middleware added with frontend URL:", FRONTEND_URL)
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

# In-memory job tracking (in production, use Redis or database)
job_storage = {}
thread_pool = ThreadPoolExecutor(max_workers=3)

# Metrics tracking
class Metrics:
    def __init__(self):
        self.jobs_enqueued = 0
        self.jobs_completed = 0
        self.jobs_failed = 0
        self.questions_generated = 0
        self.duplicates_skipped = 0
        self.auto_triggers = 0
        self.manual_triggers = 0
        self.start_time = datetime.now()
    
    def to_dict(self):
        uptime = (datetime.now() - self.start_time).total_seconds()
        return {
            "jobs_enqueued": self.jobs_enqueued,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "questions_generated": self.questions_generated,
            "duplicates_skipped": self.duplicates_skipped,
            "auto_triggers": self.auto_triggers,
            "manual_triggers": self.manual_triggers,
            "success_rate": self.jobs_completed / max(self.jobs_enqueued, 1) * 100,
            "uptime_seconds": uptime,
            "questions_per_minute": self.questions_generated / max(uptime / 60, 1)
        }

metrics = Metrics()

# Performance monitoring and alerting thresholds
class AlertThresholds:
    def __init__(self):
        self.max_active_jobs = 15
        self.min_success_rate = 80.0
        self.max_avg_generation_time = 300  # 5 minutes per job
        self.max_failed_jobs_per_hour = 10
        self.max_duplicates_ratio = 50  # 50% duplicates is concerning
        self.max_websocket_connections = 100
        
    def check_alerts(self, current_metrics):
        alerts = []
        
        # Check active jobs
        active_jobs = len([job for job in job_storage.values() 
                          if job["status"] in [JobStatus.PENDING, JobStatus.RUNNING]])
        if active_jobs > self.max_active_jobs:
            alerts.append({
                "level": "warning",
                "type": "high_active_jobs",
                "message": f"High number of active jobs: {active_jobs} (threshold: {self.max_active_jobs})",
                "value": active_jobs,
                "threshold": self.max_active_jobs
            })
        
        # Check success rate
        if (current_metrics["success_rate"] < self.min_success_rate and 
            current_metrics["jobs_completed"] > 5):
            alerts.append({
                "level": "critical",
                "type": "low_success_rate",
                "message": f"Low success rate: {current_metrics['success_rate']:.1f}% (threshold: {self.min_success_rate}%)",
                "value": current_metrics["success_rate"],
                "threshold": self.min_success_rate
            })
        
        # Check duplicate ratio
        if current_metrics["questions_generated"] > 0:
            duplicate_ratio = (current_metrics["duplicates_skipped"] / 
                             (current_metrics["questions_generated"] + current_metrics["duplicates_skipped"]) * 100)
            if duplicate_ratio > self.max_duplicates_ratio:
                alerts.append({
                    "level": "warning",
                    "type": "high_duplicate_ratio",
                    "message": f"High duplicate ratio: {duplicate_ratio:.1f}% (threshold: {self.max_duplicates_ratio}%)",
                    "value": duplicate_ratio,
                    "threshold": self.max_duplicates_ratio
                })
        
        # Check WebSocket connections
        total_connections = sum(len(connections) for connections in manager.active_connections.values())
        if total_connections > self.max_websocket_connections:
            alerts.append({
                "level": "warning",
                "type": "high_websocket_connections",
                "message": f"High WebSocket connections: {total_connections} (threshold: {self.max_websocket_connections})",
                "value": total_connections,
                "threshold": self.max_websocket_connections
            })
        
        return alerts

alert_thresholds = AlertThresholds()

# WebSocket connection manager for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)
        print(f"[WEBSOCKET] User {user_id} connected. Total connections: {len(self.active_connections.get(user_id, []))}")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        print(f"[WEBSOCKET] User {user_id} disconnected. Remaining connections: {len(self.active_connections.get(user_id, []))}")
    
    async def send_personal_message(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[user_id].copy():
                try:
                    await connection.send_text(json.dumps(message))
                except:
                    disconnected.append(connection)
            
            # Clean up disconnected connections
            for connection in disconnected:
                self.active_connections[user_id].discard(connection)
    
    async def broadcast_message(self, message: dict):
        for user_connections in self.active_connections.values():
            for connection in user_connections.copy():
                try:
                    await connection.send_text(json.dumps(message))
                except:
                    # Remove disconnected connections
                    user_connections.discard(connection)

manager = ConnectionManager()

def generate_token(user_info):
    return serializer.dumps(user_info)

def verify_token(token):
    print(f"[VERIFY TOKEN] Verifying token: {token}")
    try:
        user_info = serializer.loads(token, max_age=TOKEN_EXPIRY_SECONDS)
        print(f"[VERIFY TOKEN] Token valid. User info: {user_info}")
        return user_info
    except SignatureExpired:
        print("[VERIFY TOKEN] Token expired.")
        raise HTTPException(status_code=401, detail="Token expired")
    except BadSignature:
        print("[VERIFY TOKEN] Invalid token signature.")
        raise HTTPException(status_code=401, detail="Invalid token")

def normalize_text(text: str) -> str:
    """
    Normalize text for consistent hashing.
    Removes extra whitespace, standardizes punctuation, and converts to lowercase.
    """
    import re
    if not text:
        return ""
    
    # Convert to lowercase and strip
    normalized = text.lower().strip()
    
    # Remove extra whitespace (multiple spaces, tabs, newlines)
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Standardize punctuation spacing
    normalized = re.sub(r'\s*([.!?,:;])\s*', r'\1 ', normalized)
    
    # Remove trailing punctuation space
    normalized = normalized.rstrip(' ')
    
    return normalized

def generate_content_hash(question: str, answer: str, options: list = None) -> str:
    """
    Generate deterministic hash for question content.
    Uses normalized text to improve deduplication accuracy.
    """
    # Normalize the core content
    norm_question = normalize_text(question)
    norm_answer = normalize_text(answer)
    
    # Include normalized options for better uniqueness
    content_parts = [norm_question, norm_answer]
    
    if options:
        # Sort options to handle order variations
        norm_options = sorted([normalize_text(opt) for opt in options])
        content_parts.extend(norm_options)
    
    # Create deterministic content string
    content = "|".join(content_parts)
    
    # Generate SHA-256 hash (truncated to 16 chars for storage efficiency)
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
    
    return content_hash

def cleanup_old_jobs(max_age_hours: int = 24):
    """
    Clean up completed/failed jobs older than max_age_hours.
    Prevents memory leaks in production environments.
    """
    current_time = datetime.now()
    jobs_to_remove = []
    
    for job_id, job_data in job_storage.items():
        if job_data["status"] in [JobStatus.COMPLETED, JobStatus.FAILED]:
            if job_data.get("completed_at"):
                completed_time = datetime.fromisoformat(job_data["completed_at"])
                age_hours = (current_time - completed_time).total_seconds() / 3600
                if age_hours > max_age_hours:
                    jobs_to_remove.append(job_id)
    
    for job_id in jobs_to_remove:
        del job_storage[job_id]
    
    return len(jobs_to_remove)

async def generate_questions_background(job_id: str, target_count: int, age_range: Optional[tuple[int, int]] = None, topic: Optional[str] = None):
    """
    Background task to generate questions using OpenAI API.
    Updates job status in job_storage as it progresses.
    """
    print(f"[BACKGROUND] Starting question generation job {job_id}")
    job_storage[job_id]["status"] = JobStatus.RUNNING
    job_storage[job_id]["message"] = "Generating questions..."
    
    # Send WebSocket update
    user_email = job_storage[job_id].get("user_email")
    if user_email:
        await manager.send_personal_message({
            "type": "job_update",
            "job_id": job_id,
            "status": "running",
            "message": "Starting question generation..."
        }, user_email)
    
    try:
        db = SessionLocal()
        generated_count = 0
        
        # Determine age range and topic
        min_age, max_age = age_range if age_range else (8, 15)
        chosen_topic = topic if topic and topic.lower() != "random" else random.choice(topics)
        
        print(f"[BACKGROUND] Job {job_id}: Generating {target_count} questions for topic '{chosen_topic}', ages {min_age}-{max_age}")
        
        for i in range(target_count):
            try:
                # Create enhanced prompt with better randomization and variety
                seed = random.randint(1000, 9999)
                
                # Add variety to prompt styles
                prompt_styles = [
                    "fun and engaging",
                    "educational and interesting", 
                    "challenging but fair",
                    "creative and thought-provoking"
                ]
                
                question_types = [
                    "multiple-choice question",
                    "trivia question with interesting facts",
                    "knowledge-based question",
                    "quiz question with educational value"
                ]
                
                style = random.choice(prompt_styles)
                q_type = random.choice(question_types)
                
                # Enhanced prompt with more specific instructions
                prompt = (
                    f"You are an expert trivia question generator. "
                    f"Create a {style} {q_type} appropriate for ages {min_age} to {max_age}. "
                    f"Topic: {chosen_topic}. "
                    f"Requirements: "
                    f"- Make it age-appropriate and engaging "
                    f"- Include interesting facts or context when possible "
                    f"- Ensure one clearly correct answer and three plausible distractors "
                    f"- Use clear, simple language suitable for the age group "
                    f"Format your output as a JSON object with these exact keys: "
                    f"'question' (string), 'options' (list of exactly 4 strings), and 'answer' (string that exactly matches one of the options). "
                    f"Random seed for uniqueness: {seed}. "
                    f"Generate question #{i+1} of {target_count}."
                )
                
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You generate trivia questions in valid JSON format."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.8,
                    timeout=30
                )
                
                content = response.choices[0].message.content.strip()
                print(f"[BACKGROUND] Job {job_id}: Raw response {i+1}: {content[:100]}...")
                
                # Parse JSON response
                try:
                    q_json = json.loads(content)
                    if not all(key in q_json for key in ["question", "options", "answer"]):
                        raise ValueError("Missing required keys")
                    if not isinstance(q_json["options"], list) or len(q_json["options"]) != 4:
                        raise ValueError("Options must be a list of 4 items")
                    if q_json["answer"] not in q_json["options"]:
                        raise ValueError("Answer must be one of the options")
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"[BACKGROUND] Job {job_id}: Invalid JSON response {i+1}: {e}")
                    continue
                
                # Create improved content hash for deduplication
                content_hash = generate_content_hash(
                    q_json['question'], 
                    q_json['answer'], 
                    q_json['options']
                )
                
                # Check if question already exists
                existing = db.query(Question).filter(Question.hash == content_hash).first()
                if existing:
                    print(f"[BACKGROUND] Job {job_id}: Skipping duplicate question {i+1}")
                    metrics.duplicates_skipped += 1
                    continue
                
                # Create new question with conflict handling
                question = Question(
                    prompt=q_json["question"],
                    options=json.dumps(q_json["options"]),
                    answer=q_json["answer"],
                    topic=chosen_topic,
                    min_age=min_age,
                    max_age=max_age,
                    hash=content_hash,
                    created_at=datetime.now()
                )
                
                try:
                    db.add(question)
                    db.commit()
                    generated_count += 1
                    metrics.questions_generated += 1
                except Exception as db_error:
                    # Handle database conflicts gracefully
                    db.rollback()
                    print(f"[BACKGROUND] Job {job_id}: Database conflict for question {i+1}: {str(db_error)}")
                    metrics.duplicates_skipped += 1
                    continue
                
                # Update job progress
                job_storage[job_id]["generated_count"] = generated_count
                job_storage[job_id]["message"] = f"Generated {generated_count}/{target_count} questions"
                
                # Send WebSocket progress update
                if user_email:
                    await manager.send_personal_message({
                        "type": "job_progress",
                        "job_id": job_id,
                        "status": "running",
                        "progress": generated_count / target_count * 100,
                        "generated_count": generated_count,
                        "target_count": target_count,
                        "message": f"Generated {generated_count}/{target_count} questions"
                    }, user_email)
                
                print(f"[BACKGROUND] Job {job_id}: Successfully generated question {generated_count}/{target_count}")
                
            except Exception as e:
                print(f"[BACKGROUND] Job {job_id}: Error generating question {i+1}: {str(e)}")
                continue
        
        # Job completed
        job_storage[job_id]["status"] = JobStatus.COMPLETED
        job_storage[job_id]["generated_count"] = generated_count
        job_storage[job_id]["completed_at"] = datetime.now().isoformat()
        job_storage[job_id]["message"] = f"Successfully generated {generated_count} questions"
        
        # Update metrics
        metrics.jobs_completed += 1
        
        # Send WebSocket completion update
        if user_email:
            await manager.send_personal_message({
                "type": "job_completed",
                "job_id": job_id,
                "status": "completed",
                "generated_count": generated_count,
                "target_count": target_count,
                "message": f"Successfully generated {generated_count} questions"
            }, user_email)
        
        print(f"[BACKGROUND] Job {job_id}: Completed with {generated_count} questions generated")
        db.close()
        
    except Exception as e:
        # Job failed
        job_storage[job_id]["status"] = JobStatus.FAILED
        job_storage[job_id]["message"] = f"Job failed: {str(e)}"
        job_storage[job_id]["completed_at"] = datetime.now().isoformat()
        
        # Update metrics
        metrics.jobs_failed += 1
        
        # Send WebSocket failure update
        if user_email:
            await manager.send_personal_message({
                "type": "job_failed",
                "job_id": job_id,
                "status": "failed",
                "message": f"Job failed: {str(e)}"
            }, user_email)
        
        print(f"[BACKGROUND] Job {job_id}: Failed with error: {str(e)}")
        try:
            db.close()
        except:
            pass



# Define topics
topics = ["Animals", "Space", "History", "Science", "Sports"]

class Player(BaseModel):
    name: str
    age: int

class GameSetup(BaseModel):
    players: List[Player]
    rounds: int
    topic: str = "Random"

class QuestionResponse(BaseModel):
    id: int
    prompt: str
    options: str  # JSON as string
    answer: str
    topic: str
    min_age: int
    max_age: int
    created_at: str

class QuestionImport(BaseModel):
    prompt: str
    options: List[str]  # List of option strings
    answer: str
    topic: str
    min_age: int
    max_age: int

class ImportRequest(BaseModel):
    questions: List[QuestionImport]

class ImportResponse(BaseModel):
    imported_count: int
    skipped_count: int
    total_questions: int
    message: str

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class GenerationRequest(BaseModel):
    target_count: int = 5
    age_range: Optional[tuple[int, int]] = None
    topic: Optional[str] = None

class GenerationResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str

class GenerationStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    target_count: int
    generated_count: int
    message: str
    created_at: str
    completed_at: Optional[str] = None

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
    print(f"[LOGIN] Redirecting to Google OAuth. Redirect URI: {redirect_uri}")
    return await oauth.google.authorize_redirect(request, redirect_uri)



# Function to check if user is authenticated (token-based)
@app.get("/me")
async def get_current_user(authorization: Optional[str] = Header(None)):
    print(f"[/me] Authorization header: {authorization}")
    if not authorization or not authorization.startswith("Bearer "):
        print("[/me] Missing or invalid token format.")
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    try:
        user_info = verify_token(token)
        print(f"[/me] Token verified. User info: {user_info}")
        return JSONResponse(content=user_info)
    except Exception as e:
        print(f"[/me] Token verification failed: {str(e)}")
        raise

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        print("[AUTH CALLBACK] Starting Google OAuth callback.")
        token = await oauth.google.authorize_access_token(request)
        print(f"[AUTH CALLBACK] Received token: {token}")
        if "id_token" not in token:
            print("[AUTH CALLBACK] Missing id_token in response.")
            raise HTTPException(status_code=400, detail="Missing id_token in response")
        user_info = await oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        user_info = user_info.json()
        print(f"[AUTH CALLBACK] User info: {user_info}")

        db = SessionLocal()
        if not db.query(User).filter(User.email == user_info["email"]).first():
            db.add(User(
                email=user_info["email"],
                name=user_info["name"],
                picture=user_info["picture"]
            ))
            db.commit()
            print(f"[AUTH CALLBACK] New user added: {user_info['email']}")
        else:
            print(f"[AUTH CALLBACK] User already exists: {user_info['email']}")
        db.close()

        # Generate signed token
        signed_token = generate_token(user_info)
        print(f"[AUTH CALLBACK] Generated signed token: {signed_token}")
        # Redirect with token in query param
        redirect_url = f"{FRONTEND_URL}/?token={signed_token}"
        print(f"[AUTH CALLBACK] Redirecting to: {redirect_url}")
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        print(f"[AUTH CALLBACK] Error: {str(e)}")
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
                    q_json = json.loads(response.choices[0].message.content)

                    questions.append({
                        "player": player.name,
                        "age": player.age,
                        "round": round_num + 1,
                        "topic": chosen_topic,
                        **q_json
                    })
                    # Save question to DB for user
                    user_obj = db.query(User).filter(User.email == user["email"]).first()
                    if user_obj:
                        db.add(TriviaLog(
                            user_id=user_obj.id,
                            topic=chosen_topic,
                            rounds=round_num + 1
                        ))
                        db.commit()
                except Exception as e:
                    questions.append({
                        "email": user["email"],
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

@app.get("/user_quiz_stats")
def user_quiz_stats():
    db = SessionLocal()
    # Alternatively, use a group by for efficiency
    from sqlalchemy import func
    stats = db.query(User.name, User.email, func.count(TriviaLog.id)).join(TriviaLog, User.id == TriviaLog.user_id).group_by(User.id).all()
    db.close()
    # Format output
    output = [
        {
            "name": name,
            "email": email,
            "quizzes_played": quizzes
        }
        for name, email, quizzes in stats
    ]
    return {"user_quiz_stats": output}

@app.get("/questions", response_model=List[QuestionResponse])
def get_questions(limit: int = 10, age: Optional[int] = None, topic: Optional[str] = None, authorization: Optional[str] = Header(None)):
    """
    Get questions from database filtered by age, topic, and user assignment history.
    Phase 2: Includes per-user deduplication with atomic assignment.
    Users must authenticate to receive questions.
    """
    # Phase 2: Require authentication
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    db = SessionLocal()
    try:
        # Get user from database
        user = db.query(User).filter(User.email == user_info["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Phase 2: Atomic transaction for question assignment
        # Find candidate questions filtered by age/topic and NOT already assigned to user
        query = db.query(Question)
        
        # Apply age filtering if provided
        if age is not None:
            query = query.filter(
                (Question.min_age <= age) & (Question.max_age >= age)
            )
        
        # Apply topic filtering if provided
        if topic is not None and topic.lower() != "random":
            query = query.filter(Question.topic.ilike(f"%{topic}%"))
        
        # Phase 2: Exclude questions already assigned to this user
        already_assigned_subquery = db.query(UserQuestion.question_id).filter(
            UserQuestion.user_id == user.id
        ).subquery()
        
        query = query.filter(
            ~Question.id.in_(db.query(already_assigned_subquery.c.question_id))
        )
        
        # Get available questions
        available_questions = query.limit(limit).all()
        
        if not available_questions:
            return []
        
        # Phase 2: Atomically assign questions to user
        assigned_questions = []
        for question in available_questions:
            # Create user_question assignment record
            user_question = UserQuestion(
                user_id=user.id,
                question_id=question.id,
                assigned_at=datetime.now(),
                seen=False
            )
            db.add(user_question)
            
            # Add to response
            assigned_questions.append(QuestionResponse(
                id=question.id,
                prompt=question.prompt,
                options=question.options,
                answer=question.answer,
                topic=question.topic,
                min_age=question.min_age,
                max_age=question.max_age,
                created_at=question.created_at.isoformat() if question.created_at else ""
            ))
        
        # Commit the assignments
        db.commit()
        
        # Phase 3: Auto-trigger background generation if supply is low
        # Check if we returned fewer questions than requested (supply is low)
        if len(assigned_questions) < limit:
            # Calculate deficit
            deficit = limit - len(assigned_questions)
            
            # Check if user doesn't already have a pending/running generation job
            user_has_active_job = any(
                job["user_email"] == user_info["email"] and job["status"] in [JobStatus.PENDING, JobStatus.RUNNING]
                for job in job_storage.values()
            )
            
            if not user_has_active_job:
                # Auto-trigger background generation
                job_id = str(uuid.uuid4())
                target_count = max(deficit, 5)  # Generate at least 5 questions
                
                # Initialize job status
                job_storage[job_id] = {
                    "status": JobStatus.PENDING,
                    "target_count": target_count,
                    "generated_count": 0,
                    "message": "Auto-triggered job queued for processing",
                    "created_at": datetime.now().isoformat(),
                    "completed_at": None,
                    "user_email": user_info["email"],
                    "auto_triggered": True
                }
                
                # Submit to thread pool with asyncio wrapper
                def run_async_background():
                    asyncio.run(generate_questions_background(
                        job_id,
                        target_count,
                        (age, age) if age else None,
                        topic
                    ))
                
                thread_pool.submit(run_async_background)
                
                # Update metrics
                metrics.jobs_enqueued += 1
                metrics.auto_triggers += 1
                
                print(f"[AUTO-TRIGGER] Started background generation job {job_id} for user {user_info['email']}: {target_count} questions")
        
        return assigned_questions
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to get questions: {str(e)}")
    finally:
        db.close()

@app.post("/questions/import", response_model=ImportResponse)
def import_questions(request: ImportRequest):
    """
    Admin/dev endpoint to import questions into the database.
    Supports bulk import with content hashing to prevent duplicates.
    """
    db = SessionLocal()
    imported_count = 0
    skipped_count = 0
    
    try:
        for q_import in request.questions:
            # Create improved content hash for deduplication
            content_hash = generate_content_hash(
                q_import.prompt,
                q_import.answer,
                q_import.options
            )
            
            # Check if question already exists
            existing = db.query(Question).filter(Question.hash == content_hash).first()
            if existing:
                skipped_count += 1
                continue
            
            # Convert options list to JSON string
            options_json = json.dumps(q_import.options)
            
            # Create new question
            question = Question(
                prompt=q_import.prompt,
                options=options_json,
                answer=q_import.answer,
                topic=q_import.topic,
                min_age=q_import.min_age,
                max_age=q_import.max_age,
                hash=content_hash
            )
            
            db.add(question)
            imported_count += 1
        
        db.commit()
        
        total_in_db = db.query(Question).count()
        
        return ImportResponse(
            imported_count=imported_count,
            skipped_count=skipped_count,
            total_questions=total_in_db,
            message=f"Successfully imported {imported_count} questions, skipped {skipped_count} duplicates. Total questions in database: {total_in_db}"
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        db.close()

@app.post("/generate_questions_async", response_model=GenerationResponse)
async def generate_questions_async(
    request: GenerationRequest, 
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None)
):
    """
    Phase 3: Async question generation endpoint.
    Enqueues a background job to generate questions and returns immediately.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    job_storage[job_id] = {
        "status": JobStatus.PENDING,
        "target_count": request.target_count,
        "generated_count": 0,
        "message": "Job queued for processing",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "user_email": user_info["email"]
    }
    
    # Add background task with async support
    def run_async_background():
        asyncio.run(generate_questions_background(
            job_id,
            request.target_count,
            request.age_range,
            request.topic
        ))
    
    background_tasks.add_task(run_async_background)
    
    # Update metrics
    metrics.jobs_enqueued += 1
    metrics.manual_triggers += 1
    
    print(f"[ASYNC] Created job {job_id} for user {user_info['email']}: {request.target_count} questions")
    
    return GenerationResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message=f"Question generation job started. Job ID: {job_id}"
    )

@app.get("/generation_status/{job_id}", response_model=GenerationStatusResponse)
async def get_generation_status(job_id: str, authorization: Optional[str] = Header(None)):
    """
    Get the status of a question generation job.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Check if job exists
    if job_id not in job_storage:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job_data = job_storage[job_id]
    
    # Check if user can access this job
    if job_data.get("user_email") != user_info["email"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return GenerationStatusResponse(
        job_id=job_id,
        status=job_data["status"],
        target_count=job_data["target_count"],
        generated_count=job_data["generated_count"],
        message=job_data["message"],
        created_at=job_data["created_at"],
        completed_at=job_data.get("completed_at")
    )

@app.get("/metrics")
async def get_metrics(authorization: Optional[str] = Header(None)):
    """
    Get system metrics for monitoring and debugging.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Get current metrics
    current_metrics = metrics.to_dict()
    
    # Add additional system info
    db = SessionLocal()
    try:
        total_questions = db.query(Question).count()
        total_users = db.query(User).count()
        current_metrics.update({
            "total_questions_in_db": total_questions,
            "total_users": total_users,
            "active_jobs": len([job for job in job_storage.values() if job["status"] in [JobStatus.PENDING, JobStatus.RUNNING]]),
            "total_job_history": len(job_storage)
        })
    finally:
        db.close()
    
    return current_metrics

@app.websocket("/ws/{user_email}")
async def websocket_endpoint(websocket: WebSocket, user_email: str):
    """
    WebSocket endpoint for real-time job status updates.
    Clients can connect and receive live updates about their question generation jobs.
    """
    await manager.connect(websocket, user_email)
    try:
        # Send initial connection confirmation
        await manager.send_personal_message({
            "type": "connection_established",
            "message": f"Connected to real-time updates for {user_email}"
        }, user_email)
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                # Handle different message types
                if message.get("type") == "ping":
                    await manager.send_personal_message({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    }, user_email)
                elif message.get("type") == "get_jobs":
                    # Send current job status for this user
                    user_jobs = [
                        {
                            "job_id": job_id,
                            "status": job_data["status"],
                            "target_count": job_data.get("target_count", 0),
                            "generated_count": job_data.get("generated_count", 0),
                            "message": job_data.get("message", ""),
                            "created_at": job_data.get("created_at", ""),
                            "completed_at": job_data.get("completed_at")
                        }
                        for job_id, job_data in job_storage.items()
                        if job_data.get("user_email") == user_email
                    ]
                    await manager.send_personal_message({
                        "type": "jobs_status",
                        "jobs": user_jobs
                    }, user_email)
                    
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"[WEBSOCKET] Error handling message from {user_email}: {e}")
                break
                
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, user_email)

@app.post("/admin/cleanup_jobs")
async def cleanup_jobs(authorization: Optional[str] = Header(None)):
    """
    Admin endpoint to clean up old completed/failed jobs.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Run cleanup
    removed_count = cleanup_old_jobs(max_age_hours=1)  # Clean jobs older than 1 hour for development
    
    return {
        "message": f"Cleaned up {removed_count} old jobs",
        "removed_count": removed_count,
        "remaining_jobs": len(job_storage)
    }

@app.get("/health")
async def health_check():
    """
    Basic health check endpoint for load balancers and monitoring systems.
    Returns simple OK status with minimal processing.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "trivia-backend"
    }

@app.get("/health/detailed")
async def detailed_health_check():
    """
    Detailed health check with system component validation.
    Checks database connectivity, job system status, and metrics.
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "trivia-backend",
        "checks": {}
    }
    
    # Database health check
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        health_status["checks"]["database"] = {
            "status": "healthy",
            "message": "Database connection successful"
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["database"] = {
            "status": "unhealthy",
            "message": f"Database connection failed: {str(e)}"
        }
    
    # OpenAI API health check (optional - only if API key is available)
    try:
        if api_key:
            # Quick test - just validate client initialization
            test_client = OpenAI(api_key=api_key)
            health_status["checks"]["openai"] = {
                "status": "healthy",
                "message": "OpenAI client initialized successfully"
            }
        else:
            health_status["checks"]["openai"] = {
                "status": "warning", 
                "message": "OpenAI API key not configured"
            }
    except Exception as e:
        health_status["checks"]["openai"] = {
            "status": "warning",
            "message": f"OpenAI client issue: {str(e)}"
        }
    
    # Job system health check
    active_jobs = len([job for job in job_storage.values() if job["status"] in [JobStatus.PENDING, JobStatus.RUNNING]])
    total_jobs = len(job_storage)
    
    if active_jobs > 10:  # Threshold for too many active jobs
        health_status["status"] = "warning"
        health_status["checks"]["job_system"] = {
            "status": "warning",
            "message": f"High number of active jobs: {active_jobs}",
            "active_jobs": active_jobs,
            "total_jobs": total_jobs
        }
    else:
        health_status["checks"]["job_system"] = {
            "status": "healthy",
            "message": "Job system operating normally",
            "active_jobs": active_jobs,
            "total_jobs": total_jobs
        }
    
    # WebSocket connections health
    total_connections = sum(len(connections) for connections in manager.active_connections.values())
    health_status["checks"]["websocket"] = {
        "status": "healthy",
        "message": f"WebSocket manager operational",
        "active_connections": total_connections,
        "connected_users": len(manager.active_connections)
    }
    
    # Memory/performance indicators
    current_metrics = metrics.to_dict()
    if current_metrics["success_rate"] < 80 and current_metrics["jobs_completed"] > 10:
        health_status["status"] = "warning" 
        health_status["checks"]["performance"] = {
            "status": "warning",
            "message": f"Low success rate: {current_metrics['success_rate']:.1f}%"
        }
    else:
        health_status["checks"]["performance"] = {
            "status": "healthy",
            "message": "Performance metrics within normal range",
            "success_rate": current_metrics["success_rate"],
            "questions_per_minute": current_metrics["questions_per_minute"]
        }
    
    return health_status

@app.get("/health/ready")
async def readiness_check():
    """
    Readiness check for Kubernetes/container orchestration.
    Verifies the service is ready to handle requests.
    """
    try:
        # Check database connection
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        
        # Check if critical components are initialized
        if not metrics or not manager:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "message": "Critical components not initialized"
                }
            )
        
        return {
            "status": "ready",
            "timestamp": datetime.now().isoformat(),
            "message": "Service ready to handle requests"
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "message": f"Service not ready: {str(e)}"
            }
        )

@app.get("/alerts")
async def get_alerts(authorization: Optional[str] = Header(None)):
    """
    Get current system alerts based on performance thresholds.
    Returns warnings and critical alerts for monitoring systems.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Get current metrics and check for alerts
    current_metrics = metrics.to_dict()
    alerts = alert_thresholds.check_alerts(current_metrics)
    
    return {
        "timestamp": datetime.now().isoformat(),
        "alert_count": len(alerts),
        "critical_alerts": len([a for a in alerts if a["level"] == "critical"]),
        "warning_alerts": len([a for a in alerts if a["level"] == "warning"]),
        "alerts": alerts,
        "system_status": "critical" if any(a["level"] == "critical" for a in alerts) else 
                        "warning" if alerts else "healthy"
    }

@app.get("/performance/summary")
async def get_performance_summary(authorization: Optional[str] = Header(None)):
    """
    Get performance summary with key metrics and trends.
    """
    # Authentication required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split(" ", 1)[1]
    user_info = verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    current_metrics = metrics.to_dict()
    
    # Calculate additional performance indicators
    active_jobs = len([job for job in job_storage.values() 
                      if job["status"] in [JobStatus.PENDING, JobStatus.RUNNING]])
    
    total_connections = sum(len(connections) for connections in manager.active_connections.values())
    
    # Calculate duplicate ratio
    duplicate_ratio = 0
    if current_metrics["questions_generated"] > 0:
        duplicate_ratio = (current_metrics["duplicates_skipped"] / 
                         (current_metrics["questions_generated"] + current_metrics["duplicates_skipped"]) * 100)
    
    return {
        "timestamp": datetime.now().isoformat(),
        "performance_score": min(100, max(0, current_metrics["success_rate"])),
        "key_metrics": {
            "questions_per_minute": current_metrics["questions_per_minute"],
            "success_rate": current_metrics["success_rate"],
            "active_jobs": active_jobs,
            "duplicate_ratio": round(duplicate_ratio, 2),
            "websocket_connections": total_connections,
            "uptime_hours": round(current_metrics["uptime_seconds"] / 3600, 2)
        },
        "thresholds": {
            "max_active_jobs": alert_thresholds.max_active_jobs,
            "min_success_rate": alert_thresholds.min_success_rate,
            "max_duplicates_ratio": alert_thresholds.max_duplicates_ratio,
            "max_websocket_connections": alert_thresholds.max_websocket_connections
        },
        "alerts": alert_thresholds.check_alerts(current_metrics)
    }

