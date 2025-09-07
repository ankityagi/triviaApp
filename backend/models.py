from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base
from datetime import datetime
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String, index=True)
    name = Column(String)
    picture = Column(String)

class TriviaLog(Base):
    __tablename__ = "trivia_logs"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    topic = Column(String)
    rounds = Column(Integer)  # total rounds for the session
    created_at = Column(DateTime, default=datetime.utcnow)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    prompt = Column(Text)
    options = Column(Text)  # JSON stored as text
    answer = Column(Text)
    topic = Column(String)
    min_age = Column(Integer)
    max_age = Column(Integer)
    hash = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserQuestion(Base):
    __tablename__ = "user_questions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    seen = Column(Boolean, default=False)
