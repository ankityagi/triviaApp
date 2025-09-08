import pytest
import json
import hashlib
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from itsdangerous import URLSafeTimedSerializer

from backend.main import app
from backend.models import Base, User, Question, UserQuestion
from backend.database import SessionLocal


# Test database fixture
@pytest.fixture
def test_db():
    """Create a test database in memory."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Override the database dependency
    app.dependency_overrides[SessionLocal] = override_get_db
    
    db = TestingSessionLocal()
    yield db
    db.close()
    
    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def sample_questions(test_db):
    """Create sample questions in test database."""
    questions_data = [
        {
            'prompt': 'What is the largest planet?',
            'options': json.dumps(['Jupiter', 'Saturn', 'Earth', 'Mars']),
            'answer': 'Jupiter',
            'topic': 'Space',
            'min_age': 8,
            'max_age': 15
        },
        {
            'prompt': 'What is 2+2?',
            'options': json.dumps(['3', '4', '5', '6']),
            'answer': '4',
            'topic': 'Math',
            'min_age': 5,
            'max_age': 10
        },
        {
            'prompt': 'Who wrote Romeo and Juliet?',
            'options': json.dumps(['Shakespeare', 'Dickens', 'Austen', 'Twain']),
            'answer': 'Shakespeare',
            'topic': 'Literature',
            'min_age': 12,
            'max_age': 18
        }
    ]
    
    questions = []
    for q_data in questions_data:
        content_hash = hashlib.sha256(f"{q_data['prompt']}{q_data['answer']}".encode()).hexdigest()[:16]
        question = Question(
            prompt=q_data['prompt'],
            options=q_data['options'],
            answer=q_data['answer'],
            topic=q_data['topic'],
            min_age=q_data['min_age'],
            max_age=q_data['max_age'],
            hash=content_hash
        )
        questions.append(question)
    
    test_db.add_all(questions)
    test_db.commit()
    
    return questions


@pytest.fixture
def test_user(test_db):
    """Create a test user."""
    user = User(
        email="test@example.com",
        name="Test User",
        picture="https://example.com/avatar.jpg"
    )
    test_db.add(user)
    test_db.commit()
    return user


@pytest.fixture
def auth_token():
    """Create a valid authentication token."""
    SECRET_KEY = "test_secret_key"
    serializer = URLSafeTimedSerializer(SECRET_KEY)
    user_info = {
        'email': 'test@example.com',
        'name': 'Test User',
        'picture': 'https://example.com/avatar.jpg'
    }
    return serializer.dumps(user_info)


class TestBasicEndpoints:
    def test_root_endpoint(self, client):
        """Test the root endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Trivia backend is running!"}


class TestQuestionsEndpointPhase1:
    """Test GET /questions endpoint without authentication (Phase 1 behavior)."""
    
    def test_get_questions_unauthenticated(self, client, sample_questions):
        """Test that unauthenticated requests are rejected."""
        response = client.get("/questions")
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]
    
    def test_get_questions_invalid_token(self, client, sample_questions):
        """Test that invalid tokens are rejected."""
        headers = {"Authorization": "Bearer invalid_token"}
        response = client.get("/questions", headers=headers)
        assert response.status_code == 401


class TestQuestionsEndpointPhase2:
    """Test GET /questions endpoint with authentication and deduplication (Phase 2)."""
    
    @patch('backend.main.verify_token')
    def test_get_questions_authenticated(self, mock_verify_token, client, test_db, sample_questions, test_user):
        """Test authenticated request returns questions."""
        # Mock authentication
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        response = client.get("/questions?limit=2", headers=headers)
        
        assert response.status_code == 200
        questions = response.json()
        assert len(questions) == 2
        assert all(q["id"] for q in questions)
        assert all(q["prompt"] for q in questions)
    
    @patch('backend.main.verify_token')
    def test_age_filtering(self, mock_verify_token, client, test_db, sample_questions, test_user):
        """Test age-based filtering."""
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        
        # Test age=8 (should get Space and Math questions)
        response = client.get("/questions?age=8", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        topics = [q["topic"] for q in questions]
        assert "Space" in topics
        assert "Math" in topics
        assert "Literature" not in topics  # min_age=12 > 8
    
    @patch('backend.main.verify_token')
    def test_topic_filtering(self, mock_verify_token, client, test_db, sample_questions, test_user):
        """Test topic-based filtering."""
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        
        # Test specific topic
        response = client.get("/questions?topic=Math", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        assert len(questions) == 1
        assert questions[0]["topic"] == "Math"
        assert "2+2" in questions[0]["prompt"]
    
    @patch('backend.main.verify_token')
    def test_per_user_deduplication(self, mock_verify_token, client, test_db, sample_questions, test_user):
        """Test that same user doesn't get duplicate questions."""
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        
        # First request - should get questions and create assignments
        response1 = client.get("/questions?limit=2", headers=headers)
        assert response1.status_code == 200
        first_questions = response1.json()
        assert len(first_questions) == 2
        first_ids = [q["id"] for q in first_questions]
        
        # Second request - should get different questions
        response2 = client.get("/questions?limit=2", headers=headers)
        assert response2.status_code == 200
        second_questions = response2.json()
        second_ids = [q["id"] for q in second_questions]
        
        # No overlap between first and second requests
        assert len(set(first_ids) & set(second_ids)) == 0
        
        # Third request - should return empty (all questions assigned)
        response3 = client.get("/questions?limit=2", headers=headers)
        assert response3.status_code == 200
        third_questions = response3.json()
        assert len(third_questions) == 0
    
    @patch('backend.main.verify_token')
    def test_user_not_found(self, mock_verify_token, client, test_db, sample_questions):
        """Test behavior when authenticated user doesn't exist in database."""
        mock_verify_token.return_value = {"email": "nonexistent@example.com", "name": "Ghost User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        response = client.get("/questions", headers=headers)
        
        assert response.status_code == 404
        assert "User not found" in response.json()["detail"]
    
    @patch('backend.main.verify_token')
    def test_assignment_atomicity(self, mock_verify_token, client, test_db, sample_questions, test_user):
        """Test that question assignments are atomic."""
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        headers = {"Authorization": "Bearer valid_token"}
        
        # Get questions
        response = client.get("/questions?limit=2", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        assert len(questions) == 2
        
        # Verify assignments were created in database
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == test_user.id
        ).all()
        assert len(assignments) == 2
        
        # Verify assigned questions match returned questions
        assigned_question_ids = {a.question_id for a in assignments}
        returned_question_ids = {q["id"] for q in questions}
        assert assigned_question_ids == returned_question_ids


class TestImportEndpoint:
    """Test POST /questions/import endpoint."""
    
    def test_import_questions_success(self, client, test_db):
        """Test successful question import."""
        import_data = {
            "questions": [
                {
                    "prompt": "What is the capital of France?",
                    "options": ["London", "Berlin", "Paris", "Madrid"],
                    "answer": "Paris",
                    "topic": "Geography",
                    "min_age": 8,
                    "max_age": 14
                },
                {
                    "prompt": "What is H2O?",
                    "options": ["Water", "Hydrogen", "Oxygen", "Salt"],
                    "answer": "Water",
                    "topic": "Science",
                    "min_age": 10,
                    "max_age": 16
                }
            ]
        }
        
        response = client.post("/questions/import", json=import_data)
        assert response.status_code == 200
        
        result = response.json()
        assert result["imported_count"] == 2
        assert result["skipped_count"] == 0
        assert result["total_questions"] == 2
        assert "Successfully imported 2 questions" in result["message"]
    
    def test_import_duplicate_questions(self, client, test_db):
        """Test importing duplicate questions (should skip)."""
        question_data = {
            "prompt": "Duplicate question",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "topic": "Test",
            "min_age": 8,
            "max_age": 15
        }
        
        import_data = {"questions": [question_data]}
        
        # First import
        response1 = client.post("/questions/import", json=import_data)
        assert response1.status_code == 200
        result1 = response1.json()
        assert result1["imported_count"] == 1
        assert result1["skipped_count"] == 0
        
        # Second import (same question)
        response2 = client.post("/questions/import", json=import_data)
        assert response2.status_code == 200
        result2 = response2.json()
        assert result2["imported_count"] == 0
        assert result2["skipped_count"] == 1
        assert result2["total_questions"] == 1
    
    def test_import_invalid_data(self, client, test_db):
        """Test importing invalid question data."""
        invalid_data = {
            "questions": [
                {
                    "prompt": "Missing fields question"
                    # Missing required fields
                }
            ]
        }
        
        response = client.post("/questions/import", json=invalid_data)
        assert response.status_code == 422  # Validation error
    
    def test_import_content_hashing(self, client, test_db):
        """Test that content hashing works for duplicate detection."""
        # Same content, different options order - should be treated as different
        q1 = {
            "prompt": "Test question",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "topic": "Test",
            "min_age": 8,
            "max_age": 15
        }
        
        q2 = {
            "prompt": "Test question",  # Same prompt
            "options": ["D", "C", "B", "A"],  # Different options order
            "answer": "A",  # Same answer
            "topic": "Test",
            "min_age": 8,
            "max_age": 15
        }
        
        # These should be treated as the same question (same prompt + answer)
        import_data = {"questions": [q1, q2]}
        response = client.post("/questions/import", json=import_data)
        
        assert response.status_code == 200
        result = response.json()
        assert result["imported_count"] == 1
        assert result["skipped_count"] == 1


class TestUserStatsEndpoint:
    """Test GET /user_quiz_stats endpoint."""
    
    def test_user_stats_empty(self, client, test_db):
        """Test user stats with no data."""
        response = client.get("/user_quiz_stats")
        assert response.status_code == 200
        result = response.json()
        assert result["user_quiz_stats"] == []
    
    def test_user_stats_with_data(self, client, test_db):
        """Test user stats with trivia log data."""
        # This test would require TriviaLog entries
        # Currently the endpoint queries TriviaLog which requires actual game completion
        # For now, just verify the endpoint is accessible
        response = client.get("/user_quiz_stats")
        assert response.status_code == 200
        assert "user_quiz_stats" in response.json()


class TestErrorHandling:
    """Test error handling in endpoints."""
    
    @patch('backend.main.verify_token')
    def test_database_error_handling(self, mock_verify_token, client, test_user):
        """Test that database errors are handled gracefully."""
        mock_verify_token.return_value = {"email": "test@example.com", "name": "Test User"}
        
        # Simulate database error by closing the connection
        headers = {"Authorization": "Bearer valid_token"}
        
        # This should handle the database error gracefully
        with patch('backend.main.SessionLocal') as mock_session:
            mock_db = MagicMock()
            mock_db.query.side_effect = Exception("Database connection error")
            mock_session.return_value = mock_db
            
            response = client.get("/questions", headers=headers)
            assert response.status_code == 500
            assert "Failed to get questions" in response.json()["detail"]
    
    def test_malformed_authorization_header(self, client):
        """Test malformed authorization headers."""
        headers = {"Authorization": "InvalidFormat"}
        response = client.get("/questions", headers=headers)
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]