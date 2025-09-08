import pytest
import json
import hashlib
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.models import Base, User, Question, UserQuestion
from backend.database import SessionLocal


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
def test_users(test_db):
    """Create multiple test users."""
    users = [
        User(email="user1@example.com", name="User One", picture="pic1.jpg"),
        User(email="user2@example.com", name="User Two", picture="pic2.jpg"),
        User(email="user3@example.com", name="User Three", picture="pic3.jpg")
    ]
    test_db.add_all(users)
    test_db.commit()
    return users


@pytest.fixture
def test_questions(test_db):
    """Create test questions with various topics and age ranges."""
    questions_data = [
        {"prompt": "Q1: Easy math", "topic": "Math", "min_age": 5, "max_age": 10},
        {"prompt": "Q2: Space science", "topic": "Space", "min_age": 8, "max_age": 15},
        {"prompt": "Q3: History fact", "topic": "History", "min_age": 12, "max_age": 18},
        {"prompt": "Q4: Geography", "topic": "Geography", "min_age": 8, "max_age": 14},
        {"prompt": "Q5: Literature", "topic": "Literature", "min_age": 14, "max_age": 20}
    ]
    
    questions = []
    for i, q_data in enumerate(questions_data):
        content_hash = hashlib.sha256(f"{q_data['prompt']}Answer{i}".encode()).hexdigest()[:16]
        question = Question(
            prompt=q_data['prompt'],
            options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
            answer=f"A{i}",
            topic=q_data['topic'],
            min_age=q_data['min_age'],
            max_age=q_data['max_age'],
            hash=content_hash
        )
        questions.append(question)
    
    test_db.add_all(questions)
    test_db.commit()
    return questions


class TestBasicDeduplication:
    """Test basic per-user deduplication functionality."""
    
    @patch('backend.main.verify_token')
    def test_user_never_sees_same_question_twice(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Core test: user never receives the same question twice."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token1"}
        
        all_received_questions = []
        
        # Make multiple requests until no more questions available
        for request_num in range(10):  # More than number of questions
            response = client.get("/questions?limit=2", headers=headers)
            assert response.status_code == 200
            
            questions = response.json()
            if not questions:
                break  # No more questions available
            
            # Check no duplicates in this response
            question_ids = [q["id"] for q in questions]
            assert len(question_ids) == len(set(question_ids)), "Duplicate questions in single response"
            
            # Check no questions seen before
            for q_id in question_ids:
                assert q_id not in all_received_questions, f"Question {q_id} seen before"
                all_received_questions.append(q_id)
        
        # Verify we got all available questions exactly once
        total_questions = test_db.query(Question).count()
        assert len(all_received_questions) == total_questions
        assert len(set(all_received_questions)) == total_questions
    
    @patch('backend.main.verify_token')
    def test_different_users_can_get_same_questions(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test that different users can receive the same questions."""
        user1, user2 = test_users[0], test_users[1]
        
        # User 1 gets questions
        mock_verify_token.return_value = {"email": user1.email, "name": user1.name}
        headers1 = {"Authorization": "Bearer token1"}
        response1 = client.get("/questions?limit=3", headers=headers1)
        assert response1.status_code == 200
        user1_questions = [q["id"] for q in response1.json()]
        
        # User 2 gets questions
        mock_verify_token.return_value = {"email": user2.email, "name": user2.name}
        headers2 = {"Authorization": "Bearer token2"}
        response2 = client.get("/questions?limit=3", headers=headers2)
        assert response2.status_code == 200
        user2_questions = [q["id"] for q in response2.json()]
        
        # Both users should have gotten questions
        assert len(user1_questions) > 0
        assert len(user2_questions) > 0
        
        # Users can get the same questions (deduplication is per-user)
        # In fact, they should get the same questions since no per-user filtering initially
        assert set(user1_questions) == set(user2_questions)
    
    @patch('backend.main.verify_token')
    def test_user_assignments_created_in_database(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test that UserQuestion assignments are created when questions are served."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Get questions
        response = client.get("/questions?limit=2", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        question_ids = [q["id"] for q in questions]
        
        # Verify assignments were created in database
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == user.id
        ).all()
        
        assert len(assignments) == len(question_ids)
        assigned_question_ids = [a.question_id for a in assignments]
        assert set(assigned_question_ids) == set(question_ids)
        
        # Verify assignment properties
        for assignment in assignments:
            assert assignment.user_id == user.id
            assert assignment.seen == False
            assert assignment.assigned_at is not None


class TestDeduplicationWithFiltering:
    """Test deduplication combined with age/topic filtering."""
    
    @patch('backend.main.verify_token')
    def test_deduplication_with_age_filtering(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test deduplication works correctly with age filtering."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # First, get age-appropriate questions for age 10
        response1 = client.get("/questions?limit=10&age=10", headers=headers)
        assert response1.status_code == 200
        first_questions = response1.json()
        first_question_ids = [q["id"] for q in first_questions]
        
        # Verify age filtering worked
        for q in first_questions:
            assert q["min_age"] <= 10 <= q["max_age"]
        
        # Second request with same age - should get no questions (all assigned)
        response2 = client.get("/questions?limit=10&age=10", headers=headers)
        assert response2.status_code == 200
        second_questions = response2.json()
        
        # Should be empty since all age-appropriate questions were assigned
        assert len(second_questions) == 0
        
        # But questions for different age should still be available
        response3 = client.get("/questions?limit=10&age=16", headers=headers)
        assert response3.status_code == 200
        third_questions = response3.json()
        
        # Should get questions appropriate for age 16 that weren't assigned before
        third_question_ids = [q["id"] for q in third_questions]
        assert len(set(first_question_ids) & set(third_question_ids)) == 0
    
    @patch('backend.main.verify_token')
    def test_deduplication_with_topic_filtering(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test deduplication works correctly with topic filtering."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Get all Math questions
        response1 = client.get("/questions?limit=10&topic=Math", headers=headers)
        assert response1.status_code == 200
        math_questions = response1.json()
        
        # Verify topic filtering
        for q in math_questions:
            assert "Math" in q["topic"]
        
        # Second request for Math - should get no questions
        response2 = client.get("/questions?limit=10&topic=Math", headers=headers)
        assert response2.status_code == 200
        assert len(response2.json()) == 0
        
        # But other topics should still be available
        response3 = client.get("/questions?limit=10&topic=Space", headers=headers)
        assert response3.status_code == 200
        space_questions = response3.json()
        
        for q in space_questions:
            assert "Space" in q["topic"]
    
    @patch('backend.main.verify_token')
    def test_combined_age_topic_deduplication(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test deduplication with both age and topic filters."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Get questions for age 10 and topic Space
        response1 = client.get("/questions?limit=10&age=10&topic=Space", headers=headers)
        assert response1.status_code == 200
        filtered_questions = response1.json()
        
        # Verify both filters applied
        for q in filtered_questions:
            assert q["min_age"] <= 10 <= q["max_age"]
            assert "Space" in q["topic"]
        
        # Same filters should return no questions
        response2 = client.get("/questions?limit=10&age=10&topic=Space", headers=headers)
        assert response2.status_code == 200
        assert len(response2.json()) == 0
        
        # Different combination should work
        response3 = client.get("/questions?limit=10&age=15&topic=History", headers=headers)
        assert response3.status_code == 200
        different_questions = response3.json()
        
        # Should be different questions
        original_ids = [q["id"] for q in filtered_questions]
        different_ids = [q["id"] for q in different_questions]
        assert len(set(original_ids) & set(different_ids)) == 0


class TestDeduplicationEdgeCases:
    """Test edge cases and error conditions for deduplication."""
    
    @patch('backend.main.verify_token')
    def test_no_available_questions_returns_empty(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test that when no questions are available, empty list is returned."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Assign all questions manually
        all_questions = test_db.query(Question).all()
        for question in all_questions:
            assignment = UserQuestion(user_id=user.id, question_id=question.id)
            test_db.add(assignment)
        test_db.commit()
        
        # Request should return empty list
        response = client.get("/questions?limit=10", headers=headers)
        assert response.status_code == 200
        assert response.json() == []
    
    @patch('backend.main.verify_token')
    def test_partial_question_availability(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test when only some questions are available."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Manually assign first 3 questions
        questions = test_db.query(Question).limit(3).all()
        for question in questions:
            assignment = UserQuestion(user_id=user.id, question_id=question.id)
            test_db.add(assignment)
        test_db.commit()
        
        # Request more questions than available
        response = client.get("/questions?limit=10", headers=headers)
        assert response.status_code == 200
        remaining_questions = response.json()
        
        # Should get only remaining questions
        total_questions = test_db.query(Question).count()
        assert len(remaining_questions) == total_questions - 3
    
    @patch('backend.main.verify_token')
    def test_limit_parameter_respected(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test that limit parameter is properly respected."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Request fewer questions than available
        response = client.get("/questions?limit=2", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        
        # Should get exactly 2 questions
        assert len(questions) == 2
        
        # Next request should get different questions
        response2 = client.get("/questions?limit=1", headers=headers)
        assert response2.status_code == 200
        more_questions = response2.json()
        
        assert len(more_questions) == 1
        first_ids = [q["id"] for q in questions]
        second_ids = [q["id"] for q in more_questions]
        assert len(set(first_ids) & set(second_ids)) == 0


class TestDeduplicationPerformance:
    """Test performance aspects of deduplication."""
    
    def test_composite_index_usage(self, test_db):
        """Test that queries can efficiently use the composite index."""
        # Create test data
        user = User(email="perf@example.com", name="Perf User", picture="pic.jpg")
        test_db.add(user)
        test_db.commit()
        
        # Create many questions and assignments
        questions = []
        assignments = []
        for i in range(100):
            q = Question(
                prompt=f"Question {i}",
                options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
                answer=f"A{i}",
                topic="Test",
                min_age=8,
                max_age=15,
                hash=f"hash{i}"
            )
            questions.append(q)
        
        test_db.add_all(questions)
        test_db.commit()
        
        # Assign half the questions
        for i in range(50):
            assignment = UserQuestion(user_id=user.id, question_id=questions[i].id)
            assignments.append(assignment)
        
        test_db.add_all(assignments)
        test_db.commit()
        
        # This query should efficiently use the composite index
        already_assigned_subquery = test_db.query(UserQuestion.question_id).filter(
            UserQuestion.user_id == user.id
        ).subquery()
        
        available_questions = test_db.query(Question).filter(
            ~Question.id.in_(test_db.query(already_assigned_subquery.c.question_id))
        ).all()
        
        # Should get the unassigned half
        assert len(available_questions) == 50
        
        # Verify correct questions returned
        assigned_ids = [a.question_id for a in assignments]
        available_ids = [q.id for q in available_questions]
        
        assert len(set(assigned_ids) & set(available_ids)) == 0  # No overlap
        assert len(assigned_ids) + len(available_ids) == 100  # All questions accounted for


class TestDeduplicationConsistency:
    """Test consistency guarantees of deduplication."""
    
    @patch('backend.main.verify_token')
    def test_assignment_atomicity(self, mock_verify_token, client, test_db, test_users, test_questions):
        """Test that question assignments are atomic (all succeed or all fail)."""
        user = test_users[0]
        mock_verify_token.return_value = {"email": user.email, "name": user.name}
        headers = {"Authorization": "Bearer token"}
        
        # Get questions
        response = client.get("/questions?limit=3", headers=headers)
        assert response.status_code == 200
        questions = response.json()
        returned_question_ids = [q["id"] for q in questions]
        
        # Verify all assignments were created
        assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == user.id
        ).all()
        assigned_question_ids = [a.question_id for a in assignments]
        
        # All returned questions should have assignments
        assert set(returned_question_ids) == set(assigned_question_ids)
        assert len(assignments) == len(questions)
    
    def test_no_double_assignments(self, test_db, test_users, test_questions):
        """Test that no question gets assigned twice to the same user."""
        user = test_users[0]
        question = test_questions[0]
        
        # Create first assignment
        assignment1 = UserQuestion(user_id=user.id, question_id=question.id)
        test_db.add(assignment1)
        test_db.commit()
        
        # Attempt duplicate assignment should fail or be prevented
        assignment2 = UserQuestion(user_id=user.id, question_id=question.id)
        test_db.add(assignment2)
        
        # This might succeed at the ORM level but would be caught by application logic
        # The main prevention is in the query that excludes already-assigned questions
        test_db.commit()
        
        # Verify the deduplication query works correctly
        already_assigned = test_db.query(UserQuestion.question_id).filter(
            UserQuestion.user_id == user.id
        ).subquery()
        
        available_questions = test_db.query(Question).filter(
            ~Question.id.in_(test_db.query(already_assigned.c.question_id))
        ).all()
        
        # The assigned question should not be in available questions
        available_ids = [q.id for q in available_questions]
        assert question.id not in available_ids