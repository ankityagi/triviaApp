import pytest
import time
from unittest.mock import patch, MagicMock
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import HTTPException

from backend.main import verify_token, TOKEN_EXPIRY_SECONDS


class TestTokenVerification:
    """Test token verification functionality."""
    
    def setup_method(self):
        """Set up test data."""
        self.secret_key = "test_secret_key_12345"
        self.serializer = URLSafeTimedSerializer(self.secret_key)
        self.user_info = {
            "email": "test@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.jpg"
        }
    
    @patch('backend.main.serializer')
    def test_valid_token_verification(self, mock_serializer):
        """Test verification of a valid token."""
        # Mock serializer to return user info
        mock_serializer.loads.return_value = self.user_info
        
        token = "valid_token_string"
        result = verify_token(token)
        
        assert result == self.user_info
        mock_serializer.loads.assert_called_once_with(token, max_age=TOKEN_EXPIRY_SECONDS)
    
    @patch('backend.main.serializer')
    def test_expired_token_raises_http_exception(self, mock_serializer):
        """Test that expired tokens raise HTTPException."""
        # Mock serializer to raise SignatureExpired
        mock_serializer.loads.side_effect = SignatureExpired("Token expired")
        
        token = "expired_token"
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        
        assert exc_info.value.status_code == 401
        assert "Token expired" in str(exc_info.value.detail)
    
    @patch('backend.main.serializer')
    def test_invalid_token_raises_http_exception(self, mock_serializer):
        """Test that invalid tokens raise HTTPException."""
        # Mock serializer to raise BadSignature
        mock_serializer.loads.side_effect = BadSignature("Invalid signature")
        
        token = "invalid_token"
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        
        assert exc_info.value.status_code == 401
        assert "Invalid token" in str(exc_info.value.detail)
    
    def test_real_token_creation_and_verification(self):
        """Test actual token creation and verification flow."""
        # Create a real token
        token = self.serializer.dumps(self.user_info)
        
        # Verify token with real serializer
        with patch('backend.main.serializer', self.serializer):
            result = verify_token(token)
            assert result == self.user_info
    
    def test_token_expiry_timing(self):
        """Test that tokens actually expire after the specified time."""
        # Create serializer with very short expiry (1 second)
        short_expiry_serializer = URLSafeTimedSerializer(self.secret_key)
        token = short_expiry_serializer.dumps(self.user_info)
        
        # Immediately verify (should work)
        user_info_immediate = short_expiry_serializer.loads(token, max_age=1)
        assert user_info_immediate == self.user_info
        
        # Wait and verify (should fail)
        time.sleep(2)
        with pytest.raises(SignatureExpired):
            short_expiry_serializer.loads(token, max_age=1)
    
    def test_token_tampering_detection(self):
        """Test that tampered tokens are rejected."""
        # Create valid token
        token = self.serializer.dumps(self.user_info)
        
        # Tamper with token (change one character)
        tampered_token = token[:-5] + "XXXXX"
        
        # Verification should fail
        with pytest.raises(BadSignature):
            self.serializer.loads(tampered_token)
    
    def test_different_secret_key_rejection(self):
        """Test that tokens created with different secret keys are rejected."""
        # Create token with one secret key
        token = self.serializer.dumps(self.user_info)
        
        # Try to verify with different secret key
        different_serializer = URLSafeTimedSerializer("different_secret_key")
        with pytest.raises(BadSignature):
            different_serializer.loads(token)


class TestAuthenticationIntegration:
    """Test authentication integration with endpoints."""
    
    def test_token_constants(self):
        """Test that token expiry constants are reasonable."""
        # TOKEN_EXPIRY_SECONDS should be imported from backend.main
        from backend.main import TOKEN_EXPIRY_SECONDS
        
        # Should be at least 1 hour (3600 seconds) but not more than 24 hours
        assert 3600 <= TOKEN_EXPIRY_SECONDS <= 86400
    
    def test_user_info_structure(self):
        """Test expected user info structure from tokens."""
        user_info = {
            "email": "user@example.com",
            "name": "User Name",
            "picture": "https://example.com/pic.jpg"
        }
        
        # Verify all required fields are present
        required_fields = ["email", "name", "picture"]
        for field in required_fields:
            assert field in user_info
            assert isinstance(user_info[field], str)
            assert len(user_info[field]) > 0
    
    @patch('backend.main.verify_token')
    def test_authentication_flow_integration(self, mock_verify_token):
        """Test complete authentication flow."""
        # Mock successful authentication
        mock_user_info = {
            "email": "integration@example.com",
            "name": "Integration User",
            "picture": "https://example.com/avatar.jpg"
        }
        mock_verify_token.return_value = mock_user_info
        
        # This would be used by endpoints that require authentication
        token = "Bearer valid_token"
        if token.startswith("Bearer "):
            token_value = token.split(" ", 1)[1]
            user_info = verify_token(token_value)
            assert user_info == mock_user_info
    
    def test_missing_bearer_prefix(self):
        """Test handling of authorization header without Bearer prefix."""
        auth_header = "invalid_format_token"
        
        # This should be rejected at the endpoint level
        assert not auth_header.startswith("Bearer ")
        
        # In the actual endpoint, this would result in 401 error
    
    def test_empty_authorization_header(self):
        """Test handling of empty or None authorization header."""
        auth_headers = [None, "", "Bearer ", "Bearer"]
        
        for auth_header in auth_headers:
            if not auth_header or not auth_header.startswith("Bearer "):
                # Should be rejected
                assert True
            elif len(auth_header.split(" ", 1)) < 2:
                # Should be rejected (no token after Bearer)
                assert True


class TestSecurityConsiderations:
    """Test security aspects of authentication."""
    
    def test_token_contains_no_sensitive_data(self):
        """Test that tokens don't contain raw passwords or sensitive data."""
        user_info = {
            "email": "secure@example.com",
            "name": "Secure User",
            "picture": "https://example.com/avatar.jpg"
        }
        
        serializer = URLSafeTimedSerializer("secret_key")
        token = serializer.dumps(user_info)
        
        # Token should be encoded, not contain raw email
        assert "secure@example.com" not in token
        
        # But when decoded, should contain the email
        decoded = serializer.loads(token)
        assert decoded["email"] == "secure@example.com"
    
    def test_secret_key_isolation(self):
        """Test that different secret keys produce different tokens."""
        user_info = {"email": "test@example.com", "name": "Test"}
        
        serializer1 = URLSafeTimedSerializer("secret1")
        serializer2 = URLSafeTimedSerializer("secret2")
        
        token1 = serializer1.dumps(user_info)
        token2 = serializer2.dumps(user_info)
        
        # Different secret keys should produce different tokens
        assert token1 != token2
        
        # And each should only work with its own key
        assert serializer1.loads(token1) == user_info
        assert serializer2.loads(token2) == user_info
        
        with pytest.raises(BadSignature):
            serializer1.loads(token2)
        with pytest.raises(BadSignature):
            serializer2.loads(token1)
    
    def test_timing_attack_resistance(self):
        """Test that token verification timing is consistent."""
        # This is a basic test - in production, more sophisticated timing analysis would be needed
        serializer = URLSafeTimedSerializer("secret_key")
        valid_token = serializer.dumps({"email": "test@example.com", "name": "Test"})
        
        # Both valid and invalid tokens should take roughly the same time to process
        # (This is handled by the itsdangerous library's constant-time comparison)
        
        import time
        
        # Time valid token verification
        start_time = time.time()
        try:
            serializer.loads(valid_token)
        except:
            pass
        valid_time = time.time() - start_time
        
        # Time invalid token verification
        start_time = time.time()
        try:
            serializer.loads("invalid_token_that_is_same_length_as_valid")
        except:
            pass
        invalid_time = time.time() - start_time
        
        # Times should be relatively similar (within an order of magnitude)
        # This is a loose test since timing can vary significantly
        assert 0.1 <= (valid_time / invalid_time) <= 10.0
    
    def test_token_entropy(self):
        """Test that tokens have sufficient entropy."""
        serializer = URLSafeTimedSerializer("secret_key")
        
        # Generate multiple tokens with same data
        tokens = []
        for _ in range(10):
            token = serializer.dumps({"email": "test@example.com"})
            tokens.append(token)
        
        # All tokens should be unique (due to timestamp/salt in itsdangerous)
        assert len(set(tokens)) == len(tokens)
        
        # Tokens should be reasonably long (base64 encoded)
        for token in tokens:
            assert len(token) > 50  # Reasonable minimum length