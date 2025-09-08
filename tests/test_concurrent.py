import pytest
import json
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.models import Base, User, Question, UserQuestion
from backend.database import SessionLocal


@pytest.fixture
def test_db():
    """Create a test database in memory with thread-safe settings."""
    # Use check_same_thread=False for concurrent testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[SessionLocal] = override_get_db
    
    db = TestingSessionLocal()
    yield db
    db.close()
    
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def test_user(test_db):
    """Create a test user for concurrent testing."""
    user = User(
        email="concurrent@example.com",
        name="Concurrent User",
        picture="https://example.com/avatar.jpg"
    )
    test_db.add(user)
    test_db.commit()
    return user


@pytest.fixture
def many_questions(test_db):
    """Create many test questions for concurrent testing."""
    questions = []
    for i in range(20):  # Create 20 questions
        content_hash = hashlib.sha256(f"Question {i}Answer {i}".encode()).hexdigest()[:16]
        question = Question(
            prompt=f"Concurrent test question {i}?",
            options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
            answer=f"A{i}",
            topic="Concurrent",
            min_age=8,
            max_age=15,
            hash=content_hash
        )
        questions.append(question)
    
    test_db.add_all(questions)
    test_db.commit()
    return questions


class TestConcurrentSameUser:
    """Test concurrent requests from the same user."""
    
    @patch('backend.main.verify_token')
    def test_concurrent_requests_no_duplicates(self, mock_verify_token, client, test_db, test_user, many_questions):
        """Test that concurrent requests from same user don't get duplicate questions."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        def make_request(request_id):
            """Make a single request for questions."""
            headers = {"Authorization": f"Bearer token_{request_id}"}
            response = client.get("/questions?limit=3", headers=headers)
            return response.json() if response.status_code == 200 else []
        
        # Make 5 concurrent requests
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(5)]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
        
        # Collect all question IDs received
        all_question_ids = []
        for questions in results:
            question_ids = [q["id"] for q in questions]
            all_question_ids.extend(question_ids)
        
        # Verify no duplicates across all concurrent requests
        assert len(all_question_ids) == len(set(all_question_ids)), \
            f"Duplicate questions found: {all_question_ids}"
        
        # Verify all questions were actually assigned in database
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == test_user.id
        ).all()
        
        assigned_ids = [a.question_id for a in assignments]
        assert set(all_question_ids) == set(assigned_ids)
    
    @patch('backend.main.verify_token')
    def test_rapid_sequential_requests(self, mock_verify_token, client, test_db, test_user, many_questions):
        """Test rapid sequential requests from same user."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        headers = {"Authorization": "Bearer token"}
        
        all_received_ids = []
        
        # Make 10 rapid sequential requests
        for i in range(10):
            response = client.get("/questions?limit=2", headers=headers)
            assert response.status_code == 200
            
            questions = response.json()
            question_ids = [q["id"] for q in questions]
            
            # Check for duplicates with previous requests
            for q_id in question_ids:
                assert q_id not in all_received_ids, f"Duplicate question {q_id} in request {i}"
                all_received_ids.append(q_id)
            
            if not questions:
                break  # No more questions available
        
        # Verify assignments in database match received questions
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == test_user.id
        ).all()
        
        assigned_ids = [a.question_id for a in assignments]
        assert set(all_received_ids) == set(assigned_ids)
    
    @patch('backend.main.verify_token')
    def test_concurrent_with_different_limits(self, mock_verify_token, client, test_db, test_user, many_questions):
        """Test concurrent requests with different limit parameters."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        def make_request_with_limit(limit):
            """Make request with specific limit."""
            headers = {"Authorization": f"Bearer token_limit_{limit}"}
            response = client.get(f"/questions?limit={limit}", headers=headers)
            return response.json() if response.status_code == 200 else []
        
        # Make concurrent requests with different limits
        limits = [1, 2, 3, 4, 5]
        results = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request_with_limit, limit) for limit in limits]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
        
        # Collect all question IDs
        all_question_ids = []
        for questions in results:
            question_ids = [q["id"] for q in questions]
            all_question_ids.extend(question_ids)
        
        # Should have no duplicates
        assert len(all_question_ids) == len(set(all_question_ids))
        
        # Total should not exceed sum of limits (15 in this case)
        assert len(all_question_ids) <= sum(limits)


class TestConcurrentDifferentUsers:
    """Test concurrent requests from different users."""
    
    @patch('backend.main.verify_token')
    def test_different_users_can_get_same_questions_concurrently(self, mock_verify_token, client, test_db, many_questions):
        """Test that different users can receive the same questions concurrently."""
        # Create multiple users
        users = []
        for i in range(3):
            user = User(
                email=f"user{i}@example.com",
                name=f"User {i}",
                picture=f"pic{i}.jpg"
            )
            users.append(user)
        
        test_db.add_all(users)
        test_db.commit()
        
        def make_request_as_user(user_index):
            """Make request as specific user."""
            user = users[user_index]
            mock_verify_token.return_value = {"email": user.email, "name": user.name}
            headers = {"Authorization": f"Bearer token_user_{user_index}"}
            response = client.get("/questions?limit=5", headers=headers)
            return user_index, response.json() if response.status_code == 200 else []
        
        # Make concurrent requests as different users
        results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(make_request_as_user, i) for i in range(3)]
            for future in as_completed(futures):
                user_index, questions = future.result()
                results.append((user_index, questions))
        
        # Verify each user got questions
        user_questions = {}
        for user_index, questions in results:
            assert len(questions) > 0, f"User {user_index} got no questions"
            user_questions[user_index] = [q["id"] for q in questions]
        
        # Different users should be able to get the same questions
        # (no cross-user deduplication)
        all_user_questions = list(user_questions.values())
        
        # Each user should have gotten questions
        assert all(len(questions) > 0 for questions in all_user_questions)
        
        # Verify assignments created for each user
        for user_index, user in enumerate(users):
            assignments = test_db.query(UserQuestion).filter(
                UserQuestion.user_id == user.id
            ).all()
            
            assigned_ids = [a.question_id for a in assignments]
            expected_ids = user_questions[user_index]
            assert set(assigned_ids) == set(expected_ids)


class TestConcurrentEdgeCases:
    """Test edge cases in concurrent scenarios."""
    
    @patch('backend.main.verify_token')
    def test_concurrent_requests_exhaust_questions(self, mock_verify_token, client, test_db, test_user):
        """Test concurrent requests when questions are nearly exhausted."""
        # Create only 5 questions
        questions = []
        for i in range(5):
            content_hash = hashlib.sha256(f"Limited {i}Answer {i}".encode()).hexdigest()[:16]
            question = Question(
                prompt=f"Limited question {i}?",
                options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
                answer=f"A{i}",
                topic="Limited",
                min_age=8,
                max_age=15,
                hash=content_hash
            )
            questions.append(question)
        
        test_db.add_all(questions)
        test_db.commit()
        
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        def make_request(request_id):
            """Make request for questions."""
            headers = {"Authorization": f"Bearer token_{request_id}"}
            response = client.get("/questions?limit=3", headers=headers)  # Request 3, but only 5 total
            return response.json() if response.status_code == 200 else []
        
        # Make 3 concurrent requests (each wants 3 questions, but only 5 total exist)
        results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(make_request, i) for i in range(3)]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
        
        # Collect all received questions
        all_question_ids = []
        for questions in results:
            question_ids = [q["id"] for q in questions]
            all_question_ids.extend(question_ids)
        
        # Should have no duplicates
        assert len(all_question_ids) == len(set(all_question_ids))
        
        # Should not exceed total available questions
        assert len(all_question_ids) <= 5
        
        # All received questions should be assigned in database
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == test_user.id
        ).all()
        
        assigned_ids = [a.question_id for a in assignments]
        assert set(all_question_ids) == set(assigned_ids)
    
    @patch('backend.main.verify_token')
    def test_concurrent_with_filters(self, mock_verify_token, client, test_db, test_user):
        """Test concurrent requests with different filters."""
        # Create questions with different attributes
        questions_data = [
            {"topic": "Math", "min_age": 5, "max_age": 10},
            {"topic": "Math", "min_age": 8, "max_age": 12},
            {"topic": "Science", "min_age": 10, "max_age": 15},
            {"topic": "Science", "min_age": 12, "max_age": 18},
            {"topic": "History", "min_age": 14, "max_age": 20},
        ]
        
        questions = []
        for i, q_data in enumerate(questions_data):
            content_hash = hashlib.sha256(f"Filter {i}Answer {i}".encode()).hexdigest()[:16]
            question = Question(
                prompt=f"Filter question {i}?",
                options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
                answer=f"A{i}",
                topic=q_data["topic"],
                min_age=q_data["min_age"],
                max_age=q_data["max_age"],
                hash=content_hash
            )
            questions.append(question)
        
        test_db.add_all(questions)
        test_db.commit()
        
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        def make_filtered_request(filter_params):
            """Make request with specific filters."""
            headers = {"Authorization": f"Bearer token_{hash(str(filter_params))}"}
            params = "&".join([f"{k}={v}" for k, v in filter_params.items()])
            response = client.get(f"/questions?limit=10&{params}", headers=headers)
            return filter_params, response.json() if response.status_code == 200 else []
        
        # Make concurrent requests with different filters
        filter_combinations = [
            {"age": 10},
            {"topic": "Math"},
            {"age": 15, "topic": "Science"},
        ]
        
        results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(make_filtered_request, filters) for filters in filter_combinations]
            for future in as_completed(futures):
                filters, questions = future.result()
                results.append((filters, questions))
        
        # Verify filtering worked and no duplicates
        all_question_ids = []
        for filters, questions in results:
            question_ids = [q["id"] for q in questions]
            all_question_ids.extend(question_ids)
            
            # Verify filters were applied
            for q in questions:
                if "age" in filters:
                    age = filters["age"]
                    assert q["min_age"] <= age <= q["max_age"]
                if "topic" in filters:
                    assert filters["topic"] in q["topic"]
        
        # Should have no duplicates across different filtered requests
        assert len(all_question_ids) == len(set(all_question_ids))


class TestTransactionIntegrity:
    """Test transaction integrity under concurrent load."""
    
    @patch('backend.main.verify_token')
    def test_assignment_atomicity_under_load(self, mock_verify_token, client, test_db, test_user, many_questions):
        """Test that assignments are atomic even under high concurrent load."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        # Track all requests and responses
        request_results = []
        result_lock = threading.Lock()
        
        def make_request(request_id):
            """Make a single request and record results."""
            headers = {"Authorization": f"Bearer token_{request_id}"}
            start_time = time.time()
            response = client.get("/questions?limit=2", headers=headers)
            end_time = time.time()
            
            result = {
                "request_id": request_id,
                "status_code": response.status_code,
                "questions": response.json() if response.status_code == 200 else [],
                "duration": end_time - start_time
            }
            
            with result_lock:
                request_results.append(result)
            
            return result
        
        # Make many concurrent requests
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(15)]
            for future in as_completed(futures):
                future.result()  # Wait for completion
        
        # Analyze results
        successful_requests = [r for r in request_results if r["status_code"] == 200]
        all_question_ids = []
        
        for result in successful_requests:
            question_ids = [q["id"] for q in result["questions"]]
            all_question_ids.extend(question_ids)
        
        # Verify no duplicates
        assert len(all_question_ids) == len(set(all_question_ids)), \
            "Found duplicate questions in concurrent requests"
        
        # Verify database consistency
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == test_user.id
        ).all()
        
        assigned_ids = [a.question_id for a in assignments]
        
        # All returned questions should have assignments
        assert set(all_question_ids) == set(assigned_ids)
        
        # Number of assignments should match total questions returned
        assert len(assignments) == len(all_question_ids)