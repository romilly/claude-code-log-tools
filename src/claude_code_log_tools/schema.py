"""Database schema for Claude Code log storage."""

SCHEMA = """
-- Sessions table: one row per Claude Code session
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    session_uuid UUID UNIQUE NOT NULL,
    project_path TEXT,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    total_input_tokens INT DEFAULT 0,
    total_output_tokens INT DEFAULT 0
);

-- Messages table: one row per log entry (envelope only, content in content_blocks)
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    uuid TEXT,
    type TEXT NOT NULL,           -- 'user', 'assistant', 'system', 'summary', etc.
    role TEXT,                     -- 'user', 'assistant' (from message.role)
    timestamp TIMESTAMPTZ,
    cwd TEXT,
    input_tokens INT,
    output_tokens INT,
    version TEXT
);

-- Content blocks table: one row per content block within a message
CREATE TABLE IF NOT EXISTS content_blocks (
    id SERIAL PRIMARY KEY,
    message_id INT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    block_index INT NOT NULL,      -- order within the message
    block_type TEXT NOT NULL,      -- 'text', 'tool_use', 'tool_result', 'thinking'

    -- Text content (for text, thinking, tool_result blocks)
    text_content TEXT,

    -- Tool use fields
    tool_name TEXT,                -- for tool_use blocks
    tool_input JSONB,              -- for tool_use blocks (the input parameters)
    tool_use_id TEXT,              -- links tool_use to its tool_result

    -- Full-text search on text content
    content_tsvector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', COALESCE(text_content, ''))
    ) STORED
);

-- Import metadata: tracks last import timestamp per project for idempotent imports
CREATE TABLE IF NOT EXISTS import_metadata (
    project_path TEXT PRIMARY KEY,
    last_import_timestamp TIMESTAMPTZ NOT NULL
);

-- Indexes for messages
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp DESC);

-- Unique index on messages.uuid as safety net for idempotent imports
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_uuid_unique
    ON messages(uuid) WHERE uuid IS NOT NULL;

-- Indexes for content_blocks
CREATE INDEX IF NOT EXISTS idx_content_blocks_message_id ON content_blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_content_blocks_type ON content_blocks(block_type);
CREATE INDEX IF NOT EXISTS idx_content_blocks_tool_use_id ON content_blocks(tool_use_id);
CREATE INDEX IF NOT EXISTS idx_content_blocks_tool_name ON content_blocks(tool_name);
CREATE INDEX IF NOT EXISTS idx_content_blocks_fts ON content_blocks USING GIN(content_tsvector);
"""
