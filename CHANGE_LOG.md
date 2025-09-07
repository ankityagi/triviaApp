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