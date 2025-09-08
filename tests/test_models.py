import pytest
import json
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Base, User, TriviaLog, Question, UserQuestion
from backend.database import SessionLocal


@pytest.fixture
def test_db():
    """Create a test database in memory."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()


class TestUserModel:
    def test_create_user(self, test_db):
        """Test creating a user."""
        user = User(
            email="test@example.com",
            name="Test User",
            picture="https://example.com/avatar.jpg"
        )
        test_db.add(user)
        test_db.commit()
        
        # Verify user was created
        saved_user = test_db.query(User).filter(User.email == "test@example.com").first()
        assert saved_user is not None
        assert saved_user.name == "Test User"
        assert saved_user.picture == "https://example.com/avatar.jpg"
    
    def test_user_email_index(self, test_db):
        """Test that email indexing works for fast lookups."""
        user = User(email="indexed@example.com", name="Indexed User", picture="pic.jpg")
        test_db.add(user)
        test_db.commit()
        
        # This should use the email index for fast lookup
        result = test_db.query(User).filter(User.email == "indexed@example.com").first()
        assert result.name == "Indexed User"


class TestTriviaLogModel:
    def test_create_trivia_log(self, test_db):
        """Test creating a trivia log entry."""
        # First create a user
        user = User(email="player@example.com", name="Player", picture="pic.jpg")
        test_db.add(user)
        test_db.commit()
        
        # Create trivia log
        log = TriviaLog(
            user_id=user.id,
            topic="Science",
            rounds=3
        )
        test_db.add(log)
        test_db.commit()
        
        # Verify log was created
        saved_log = test_db.query(TriviaLog).filter(TriviaLog.user_id == user.id).first()
        assert saved_log is not None
        assert saved_log.topic == "Science"
        assert saved_log.rounds == 3
        assert saved_log.created_at is not None
    
    def test_trivia_log_foreign_key(self, test_db):
        """Test foreign key relationship with User."""
        user = User(email="fk@example.com", name="FK User", picture="pic.jpg")
        test_db.add(user)
        test_db.commit()
        
        log = TriviaLog(user_id=user.id, topic="History", rounds=2)
        test_db.add(log)
        test_db.commit()
        
        # Verify relationship
        assert log.user_id == user.id


class TestQuestionModel:
    def test_create_question(self, test_db):
        """Test creating a question."""
        options = ["Option A", "Option B", "Option C", "Option D"]
        question = Question(
            prompt="What is 2+2?",
            options=json.dumps(options),
            answer="Option A",
            topic="Math",
            min_age=6,
            max_age=12,
            hash="testhash123"
        )
        test_db.add(question)
        test_db.commit()
        
        # Verify question was created
        saved_q = test_db.query(Question).filter(Question.hash == "testhash123").first()
        assert saved_q is not None
        assert saved_q.prompt == "What is 2+2?"
        assert json.loads(saved_q.options) == options
        assert saved_q.min_age == 6
        assert saved_q.max_age == 12
    
    def test_question_hash_unique(self, test_db):
        """Test that question hash must be unique."""
        q1 = Question(
            prompt="Question 1", options=json.dumps(["A", "B", "C", "D"]),
            answer="A", topic="Test", min_age=8, max_age=15, hash="unique123"
        )
        q2 = Question(
            prompt="Question 2", options=json.dumps(["E", "F", "G", "H"]),
            answer="E", topic="Test", min_age=8, max_age=15, hash="unique123"  # Same hash
        )
        
        test_db.add(q1)
        test_db.commit()
        
        test_db.add(q2)
        with pytest.raises(Exception):  # Should fail due to unique constraint
            test_db.commit()
    
    def test_age_range_filtering(self, test_db):
        """Test age range filtering functionality."""
        # Create questions with different age ranges
        q1 = Question(
            prompt="Easy question", options=json.dumps(["A", "B", "C", "D"]),
            answer="A", topic="Test", min_age=5, max_age=10, hash="easy123"
        )
        q2 = Question(
            prompt="Hard question", options=json.dumps(["E", "F", "G", "H"]),
            answer="E", topic="Test", min_age=15, max_age=20, hash="hard123"
        )
        
        test_db.add_all([q1, q2])
        test_db.commit()
        
        # Test filtering for age 8 (should match q1 only)
        age_8_questions = test_db.query(Question).filter(
            (Question.min_age <= 8) & (Question.max_age >= 8)
        ).all()
        
        assert len(age_8_questions) == 1
        assert age_8_questions[0].prompt == "Easy question"
        
        # Test filtering for age 18 (should match q2 only)
        age_18_questions = test_db.query(Question).filter(
            (Question.min_age <= 18) & (Question.max_age >= 18)
        ).all()
        
        assert len(age_18_questions) == 1
        assert age_18_questions[0].prompt == "Hard question"


class TestUserQuestionModel:
    def test_create_user_question_assignment(self, test_db):
        """Test creating a user-question assignment."""
        # Create test data
        user = User(email="assign@example.com", name="Assign User", picture="pic.jpg")
        question = Question(
            prompt="Test?", options=json.dumps(["A", "B", "C", "D"]),
            answer="A", topic="Test", min_age=8, max_age=15, hash="assign123"
        )
        test_db.add_all([user, question])
        test_db.commit()
        
        # Create assignment
        assignment = UserQuestion(
            user_id=user.id,
            question_id=question.id,
            seen=False
        )
        test_db.add(assignment)
        test_db.commit()
        
        # Verify assignment
        saved_assignment = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == user.id,
            UserQuestion.question_id == question.id
        ).first()
        
        assert saved_assignment is not None
        assert saved_assignment.seen == False
        assert saved_assignment.assigned_at is not None
    
    def test_user_question_deduplication(self, test_db):
        """Test that user-question deduplication works."""
        # Create test data
        user = User(email="dedup@example.com", name="Dedup User", picture="pic.jpg")
        q1 = Question(
            prompt="Q1", options=json.dumps(["A", "B", "C", "D"]),
            answer="A", topic="Test", min_age=8, max_age=15, hash="dedup1"
        )
        q2 = Question(
            prompt="Q2", options=json.dumps(["E", "F", "G", "H"]),
            answer="E", topic="Test", min_age=8, max_age=15, hash="dedup2"
        )
        q3 = Question(
            prompt="Q3", options=json.dumps(["I", "J", "K", "L"]),
            answer="I", topic="Test", min_age=8, max_age=15, hash="dedup3"
        )
        
        test_db.add_all([user, q1, q2, q3])
        test_db.commit()
        
        # Assign q1 to user
        assignment1 = UserQuestion(user_id=user.id, question_id=q1.id)
        test_db.add(assignment1)
        test_db.commit()
        
        # Query for questions NOT already assigned to this user
        already_assigned = test_db.query(UserQuestion.question_id).filter(
            UserQuestion.user_id == user.id
        ).subquery()
        
        available_questions = test_db.query(Question).filter(
            ~Question.id.in_(test_db.query(already_assigned.c.question_id))
        ).all()
        
        # Should return q2 and q3, but not q1
        assert len(available_questions) == 2
        available_prompts = [q.prompt for q in available_questions]
        assert "Q1" not in available_prompts
        assert "Q2" in available_prompts
        assert "Q3" in available_prompts
    
    def test_composite_index_performance(self, test_db):
        """Test that the composite index exists and works."""
        # Create test data
        user = User(email="perf@example.com", name="Perf User", picture="pic.jpg")
        question = Question(
            prompt="Performance test", options=json.dumps(["A", "B", "C", "D"]),
            answer="A", topic="Test", min_age=8, max_age=15, hash="perf123"
        )
        test_db.add_all([user, question])
        test_db.commit()
        
        assignment = UserQuestion(user_id=user.id, question_id=question.id)
        test_db.add(assignment)
        test_db.commit()
        
        # This query should use the composite index
        result = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == user.id,
            UserQuestion.question_id == question.id
        ).first()
        
        assert result is not None
        assert result.user_id == user.id
        assert result.question_id == question.id


class TestModelIntegration:
    def test_full_workflow(self, test_db):
        """Test complete workflow: user -> questions -> assignments -> deduplication."""
        # Create user and questions
        user = User(email="workflow@example.com", name="Workflow User", picture="pic.jpg")
        questions = []
        for i in range(3):
            q = Question(
                prompt=f"Question {i+1}?",
                options=json.dumps([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]),
                answer=f"A{i}",
                topic="Workflow",
                min_age=8,
                max_age=15,
                hash=f"workflow{i}"
            )
            questions.append(q)
        
        test_db.add_all([user] + questions)
        test_db.commit()
        
        # Phase 1: Get all questions (no deduplication)
        all_questions = test_db.query(Question).all()
        assert len(all_questions) == 3
        
        # Phase 2: Assign first 2 questions to user
        for q in questions[:2]:
            assignment = UserQuestion(user_id=user.id, question_id=q.id)
            test_db.add(assignment)
        test_db.commit()
        
        # Phase 2: Get remaining questions (with deduplication)
        already_assigned = test_db.query(UserQuestion.question_id).filter(
            UserQuestion.user_id == user.id
        ).subquery()
        
        remaining_questions = test_db.query(Question).filter(
            ~Question.id.in_(test_db.query(already_assigned.c.question_id))
        ).all()
        
        assert len(remaining_questions) == 1
        assert remaining_questions[0].prompt == "Question 3?"
        
        # Verify user assignments
        user_assignments = test_db.query(UserQuestion).filter(
            UserQuestion.user_id == user.id
        ).count()
        assert user_assignments == 2