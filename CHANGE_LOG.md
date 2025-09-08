# CHANGE_LOG.md

This file documents all changes made to the trivia application during development.

## Phase 1: Database Design & Simple Query

### Phase 1 Step 1: Add Database Tables
**Date**: 2025-09-07  
**Status**: ✅ Completed  

#### Changes Made:
- Added two new database tables to support reusable, age-aware questions:
  1. `questions` table with columns:
     - `id` (Primary Key)
     - `prompt` (TEXT)
     - `options` (TEXT) - JSON stored as text
     - `answer` (TEXT)
     - `topic` (STRING)
     - `min_age` (INTEGER)
     - `max_age` (INTEGER)
     - `hash` (STRING, UNIQUE, indexed)
     - `created_at` (DATETIME)
  2. `user_questions` table with columns:
     - `id` (Primary Key)
     - `user_id` (Foreign Key to users.id, indexed)
     - `question_id` (Foreign Key to questions.id, indexed)
     - `assigned_at` (DATETIME)
     - `seen` (BOOLEAN, DEFAULT FALSE)
- Renamed existing `TriviaLog` table from "questions" to "trivia_logs" to avoid conflicts

#### Files Modified:
- `backend/models.py` - Added Question and UserQuestion models, renamed TriviaLog table

#### Purpose:
Enable question reuse across users while supporting age-appropriate filtering and preventing duplicate questions per user.

### Phase 1 Step 2: Database Migration/Recreation
**Date**: 2025-09-07  
**Status**: ✅ Completed  

#### Changes Made:
- Removed existing SQLite database file (`./frontend/trivia.db`)
- Recreated database schema with new table structure using `Base.metadata.create_all(bind=engine)`
- Verified all tables created correctly with proper indexes:
  - `users` table with email index
  - `questions` table with hash unique index  
  - `user_questions` table with user_id and question_id indexes
  - `trivia_logs` table (renamed from original questions table)

#### Database Location:
- New database created at `./trivia.db` (root directory)
- Contains all 4 tables: users, questions, trivia_logs, user_questions

#### Purpose:
Fresh database start to support new question management system without complex migrations.

### Phase 1 Step 3: API Implementation - GET /questions Endpoint
**Date**: 2025-09-07  
**Status**: ✅ Completed  

#### Changes Made:
- ✅ Implemented `GET /questions?limit=N&age=XX&topic=Y` endpoint
- ✅ Added age range filtering (questions with min_age <= age <= max_age)
- ✅ Added topic filtering with case-insensitive matching
- ✅ Added `QuestionResponse` Pydantic model for structured API responses
- ✅ Updated imports to include Question and UserQuestion models
- ✅ Added sample test data (3 questions across Space, Animals, History topics)

#### Files Modified:
- `backend/main.py` - Added new endpoint, imports, and response model

#### Testing Results:
- ✅ Basic endpoint returns all questions (3 questions returned)
- ✅ Age filtering works correctly (age=10 returned 2 age-appropriate questions)  
- ✅ Topic filtering works correctly (topic=Space returned 1 Space question)
- ✅ No OpenAI calls during question retrieval as intended

#### Acceptance Criteria Met:
- ✅ Users can receive questions from DB immediately without GPT calls
- ✅ Questions properly filtered by age range and topic
- ✅ Ready for Phase 2 per-user deduplication implementation