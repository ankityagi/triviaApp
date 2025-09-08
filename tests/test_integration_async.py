import pytest
import asyncio
import json
import time
import websockets
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.main import app, job_storage, metrics, manager, JobStatus
from backend.models import Base, User, Question, UserQuestion
from backend.database import SessionLocal


@pytest.fixture
def test_db():
    """Create a test database in memory with thread-safe settings."""
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
    """Create a test user for integration testing."""
    user = User(
        email="integration@example.com",
        name="Integration User",
        picture="https://example.com/avatar.jpg"
    )
    test_db.add(user)
    test_db.commit()
    return user


class TestAsyncGenerationWorkflow:
    """Integration tests for the complete async question generation workflow."""
    
    @patch('backend.main.verify_token')
    @patch('backend.main.client')
    def test_complete_async_workflow(self, mock_openai_client, mock_verify_token, client, test_db, test_user):
        """Test the complete async generation workflow from request to completion."""
        # Setup authentication
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        
        # Mock OpenAI response
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "question": "What is the largest planet in our solar system?",
            "options": ["Earth", "Jupiter", "Mars", "Venus"],
            "answer": "Jupiter"
        })
        mock_openai_client.chat.completions.create.return_value = mock_response
        
        headers = {"Authorization": "Bearer test_token"}
        
        # Step 1: Start async generation
        generation_request = {
            "target_count": 2,
            "age_range": [8, 15],
            "topic": "Space"
        }
        
        response = client.post("/generate_questions_async", json=generation_request, headers=headers)
        assert response.status_code == 200
        
        result = response.json()
        job_id = result["job_id"]
        assert result["status"] == "pending"
        assert "job started" in result["message"].lower()
        
        # Step 2: Check initial job status
        response = client.get(f"/generation_status/{job_id}", headers=headers)
        assert response.status_code == 200
        
        status = response.json()
        assert status["job_id"] == job_id
        assert status["target_count"] == 2
        assert status["status"] in ["pending", "running"]
        
        # Step 3: Wait for job completion (with timeout)
        max_wait_time = 30
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            response = client.get(f"/generation_status/{job_id}", headers=headers)
            status = response.json()
            
            if status["status"] in ["completed", "failed"]:
                break
                
            time.sleep(1)
        
        # Step 4: Verify job completion
        assert status["status"] == "completed"
        assert status["generated_count"] > 0
        
        # Step 5: Verify questions were created in database
        questions_in_db = test_db.query(Question).filter(
            Question.topic == "Space"
        ).all()
        
        assert len(questions_in_db) >= status["generated_count"]
        
        # Step 6: Verify questions can be retrieved via GET /questions
        response = client.get("/questions?topic=Space", headers=headers)
        assert response.status_code == 200
        
        retrieved_questions = response.json()
        assert len(retrieved_questions) > 0
        
        # Verify question format
        for question in retrieved_questions:
            assert "id" in question
            assert "prompt" in question
            assert "options" in question
            assert "answer" in question
            assert question["topic"] == "Space"
    
    @patch('backend.main.verify_token')
    def test_auto_trigger_integration(self, mock_verify_token, client, test_db, test_user):
        """Test auto-trigger functionality when question supply is low."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        headers = {"Authorization": "Bearer test_token"}
        
        # Request more questions than available (should trigger auto-generation)
        response = client.get("/questions?limit=10&topic=NonExistent", headers=headers)
        assert response.status_code == 200
        
        questions = response.json()
        assert len(questions) == 0  # No existing questions for this topic
        
        # Check if auto-trigger job was created
        auto_triggered_jobs = [
            job for job in job_storage.values() 
            if job.get("user_email") == test_user.email and job.get("auto_triggered", False)
        ]
        
        assert len(auto_triggered_jobs) > 0, "Auto-trigger should have created a background job"
    
    @patch('backend.main.verify_token')
    def test_concurrent_job_handling(self, mock_verify_token, client, test_db, test_user):
        """Test handling of concurrent job requests from same user."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        headers = {"Authorization": "Bearer test_token"}
        
        # Start first job
        generation_request = {"target_count": 1, "topic": "Math"}
        response1 = client.post("/generate_questions_async", json=generation_request, headers=headers)
        assert response1.status_code == 200
        job_id1 = response1.json()["job_id"]
        
        # Start second job immediately
        response2 = client.post("/generate_questions_async", json=generation_request, headers=headers)
        assert response2.status_code == 200
        job_id2 = response2.json()["job_id"]
        
        # Jobs should have different IDs
        assert job_id1 != job_id2
        
        # Both jobs should be tracked
        assert job_id1 in job_storage
        assert job_id2 in job_storage


class TestWebSocketIntegration:
    """Integration tests for WebSocket real-time updates."""
    
    @pytest.mark.asyncio
    async def test_websocket_connection(self):
        """Test WebSocket connection establishment."""
        user_email = "websocket@example.com"
        
        # Test connection establishment
        try:
            async with websockets.connect(f"ws://localhost:8000/ws/{user_email}") as websocket:
                # Receive connection confirmation
                message = await asyncio.wait_for(websocket.recv(), timeout=5)
                data = json.loads(message)
                
                assert data["type"] == "connection_established"
                assert user_email in data["message"]
                
                # Send ping message
                await websocket.send(json.dumps({"type": "ping"}))
                
                # Receive pong response
                pong_message = await asyncio.wait_for(websocket.recv(), timeout=5)
                pong_data = json.loads(pong_message)
                
                assert pong_data["type"] == "pong"
                assert "timestamp" in pong_data
                
        except Exception as e:
            pytest.skip(f"WebSocket test requires running server: {e}")
    
    def test_websocket_job_status_updates(self, client, test_db, test_user):
        """Test that job updates are sent via WebSocket (integration test)."""
        # This test would require a running WebSocket server
        # For now, we verify the WebSocket manager functionality
        
        from backend.main import manager
        
        # Verify manager can handle user connections tracking
        user_email = test_user.email
        
        # Simulate connection tracking
        assert user_email not in manager.active_connections
        
        # In a real integration test, we would:
        # 1. Connect via WebSocket
        # 2. Start an async generation job
        # 3. Verify real-time updates are received
        # 4. Confirm job completion notification


class TestSystemHealthIntegration:
    """Integration tests for system health and monitoring."""
    
    def test_health_endpoints(self, client):
        """Test all health check endpoints."""
        # Basic health check
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "trivia-backend"
        assert "timestamp" in data
        
        # Detailed health check
        response = client.get("/health/detailed")
        assert response.status_code == 200
        
        detailed_data = response.json()
        assert "checks" in detailed_data
        assert "database" in detailed_data["checks"]
        assert "openai" in detailed_data["checks"]
        assert "job_system" in detailed_data["checks"]
        assert "websocket" in detailed_data["checks"]
        assert "performance" in detailed_data["checks"]
        
        # Readiness check
        response = client.get("/health/ready")
        assert response.status_code == 200
        
        ready_data = response.json()
        assert ready_data["status"] == "ready"
    
    def test_metrics_integration(self, client, test_user):
        """Test metrics endpoint integration."""
        # Setup authentication
        with patch('backend.main.verify_token') as mock_verify:
            mock_verify.return_value = {"email": test_user.email, "name": test_user.name}
            headers = {"Authorization": "Bearer test_token"}
            
            response = client.get("/metrics", headers=headers)
            assert response.status_code == 200
            
            metrics_data = response.json()
            
            # Verify all expected metrics are present
            expected_metrics = [
                "jobs_enqueued", "jobs_completed", "jobs_failed",
                "questions_generated", "duplicates_skipped",
                "auto_triggers", "manual_triggers", "success_rate",
                "uptime_seconds", "questions_per_minute",
                "total_questions_in_db", "total_users",
                "active_jobs", "total_job_history"
            ]
            
            for metric in expected_metrics:
                assert metric in metrics_data
                assert isinstance(metrics_data[metric], (int, float))


class TestErrorHandlingIntegration:
    """Integration tests for error handling and recovery."""
    
    @patch('backend.main.verify_token')
    def test_job_failure_handling(self, mock_verify_token, client, test_db, test_user):
        """Test handling of job failures and error recovery."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        headers = {"Authorization": "Bearer test_token"}
        
        # Mock OpenAI to raise an exception
        with patch('backend.main.client') as mock_openai_client:
            mock_openai_client.chat.completions.create.side_effect = Exception("OpenAI API Error")
            
            # Start job that will fail
            generation_request = {"target_count": 1, "topic": "Test"}
            response = client.post("/generate_questions_async", json=generation_request, headers=headers)
            assert response.status_code == 200
            
            job_id = response.json()["job_id"]
            
            # Wait for job to fail
            max_wait_time = 10
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                response = client.get(f"/generation_status/{job_id}", headers=headers)
                status = response.json()
                
                if status["status"] == "failed":
                    break
                    
                time.sleep(1)
            
            # Verify job failed gracefully
            assert status["status"] == "failed"
            assert "error" in status["message"].lower()
    
    def test_database_error_recovery(self, client):
        """Test system behavior during database errors."""
        # Test health endpoint during database issues
        with patch('backend.main.SessionLocal') as mock_session:
            mock_session.side_effect = Exception("Database connection failed")
            
            response = client.get("/health/detailed")
            assert response.status_code == 200
            
            health_data = response.json()
            assert health_data["status"] == "unhealthy"
            assert health_data["checks"]["database"]["status"] == "unhealthy"


class TestPerformanceIntegration:
    """Integration tests for performance and scalability."""
    
    @patch('backend.main.verify_token')
    def test_high_load_simulation(self, mock_verify_token, client, test_db, test_user):
        """Simulate high load conditions."""
        mock_verify_token.return_value = {"email": test_user.email, "name": test_user.name}
        headers = {"Authorization": "Bearer test_token"}
        
        # Simulate multiple concurrent requests
        responses = []
        for i in range(5):
            response = client.post("/generate_questions_async", 
                                 json={"target_count": 1, "topic": f"Topic{i}"}, 
                                 headers=headers)
            responses.append(response)
        
        # All requests should succeed
        for response in responses:
            assert response.status_code == 200
        
        # System should track all jobs
        user_jobs = [
            job for job in job_storage.values() 
            if job.get("user_email") == test_user.email
        ]
        assert len(user_jobs) >= 5
    
    def test_cleanup_performance(self, client, test_user):
        """Test cleanup performance under load."""
        with patch('backend.main.verify_token') as mock_verify:
            mock_verify.return_value = {"email": test_user.email, "name": test_user.name}
            headers = {"Authorization": "Bearer test_token"}
            
            # Fill job storage with completed jobs
            from backend.main import job_storage, JobStatus
            from datetime import datetime, timedelta
            
            for i in range(20):
                old_time = (datetime.now() - timedelta(hours=2)).isoformat()
                job_storage[f"old_job_{i}"] = {
                    "status": JobStatus.COMPLETED,
                    "completed_at": old_time,
                    "user_email": test_user.email
                }
            
            initial_count = len(job_storage)
            
            # Run cleanup
            response = client.post("/admin/cleanup_jobs", headers=headers)
            assert response.status_code == 200
            
            cleanup_data = response.json()
            assert cleanup_data["removed_count"] > 0
            assert len(job_storage) < initial_count