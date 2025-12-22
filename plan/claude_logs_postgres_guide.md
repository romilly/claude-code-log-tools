# PostgreSQL Full-Text Search Setup for Claude Code Logs

A practical guide to storing and searching Claude Code conversation logs using PostgreSQL with native full-text search (`tsvector`). This setup prioritizes speed and simplicity—no fuzzy matching, just fast exact-match full-text search with proper indexing.

## Prerequisites

- PostgreSQL 13+ installed locally
- Python 3.8+ with `psycopg2` library
- Access to your Claude Code logs at `~/.claude/projects/`

## Part 1: Database Setup

### 1.1 Create Database and Extensions

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE claude_logs;

# Connect to the new database
\c claude_logs

```

### 1.2 Create Schema

```sql
-- Run this in psql connected to claude_logs database

-- Sessions table: one row per Claude Code session
CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    session_uuid UUID UNIQUE NOT NULL,
    project_path TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    total_input_tokens INT DEFAULT 0,
    total_output_tokens INT DEFAULT 0
);

-- Messages table: one row per message in a conversation
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    uuid TEXT,
    type TEXT NOT NULL,  -- 'user', 'assistant', 'system', 'summary'
    role TEXT,            -- 'user', 'assistant'
    content TEXT,
    timestamp TIMESTAMPTZ,
    cwd TEXT,
    input_tokens INT,
    output_tokens INT,
    version TEXT,
    -- Full-text search column
    content_tsvector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', COALESCE(content, ''))
    ) STORED
);

-- Indexes for performance
CREATE INDEX idx_messages_session_id 
    ON messages(session_id);

CREATE INDEX idx_messages_type 
    ON messages(type);

CREATE INDEX idx_messages_timestamp 
    ON messages(timestamp DESC);

-- Critical: GIN index for full-text search speed
CREATE INDEX idx_messages_fts 
    ON messages USING GIN(content_tsvector);

-- Optional: prefix indexes for common filters
CREATE INDEX idx_messages_session_timestamp 
    ON messages(session_id, timestamp DESC);

-- Verify schema
\d sessions
\d messages
```

**Why this structure:**
- `tsvector GENERATED ALWAYS AS ... STORED`: PostgreSQL automatically updates the search index whenever `content` changes. "STORED" means the index is materialized in the table (faster searches, slightly larger table).
- `GIN` index: Specialized index for `tsvector` columns. Fast full-text queries, handles millions of documents efficiently.
- Sessions table: Lets you track projects and aggregate token usage per session.

### 1.3 Verify Setup

```sql
-- Check that indexes exist
SELECT indexname FROM pg_indexes 
WHERE tablename IN ('messages', 'sessions');

-- Expected output:
-- pk_messages
-- idx_messages_session_id
-- idx_messages_type
-- idx_messages_timestamp
-- idx_messages_fts
-- idx_messages_session_timestamp
```

## Part 2: Data Import Script

### 2.1 Python Import Script

Create a file called `import_claude_logs.py`:

```python
#!/usr/bin/env python3
"""
Import Claude Code conversation logs from JSONL files into PostgreSQL.

Usage:
    python import_claude_logs.py  # Imports from ~/.claude/projects/
    python import_claude_logs.py /path/to/session.jsonl  # Import specific file
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from uuid import UUID
import psycopg2
from psycopg2.extras import execute_values

# Configuration
DB_CONFIG = {
    'host': 'localhost',
    'database': 'claude_logs',
    'user': 'postgres',
    'password': '',  # Set if needed, or use ~/.pgpass
}

CLAUDE_LOGS_DIR = Path.home() / '.claude' / 'projects'


def find_jsonl_files():
    """Discover all JSONL files in Claude Code log directory."""
    if not CLAUDE_LOGS_DIR.exists():
        print(f"Error: Claude logs directory not found at {CLAUDE_LOGS_DIR}")
        sys.exit(1)
    
    jsonl_files = list(CLAUDE_LOGS_DIR.glob('**/*.jsonl'))
    print(f"Found {len(jsonl_files)} JSONL files:")
    for f in jsonl_files:
        print(f"  - {f.relative_to(CLAUDE_LOGS_DIR)}")
    
    return jsonl_files


def get_or_create_session(cur, session_uuid, project_path):
    """
    Get session ID, creating it if needed.
    Returns: (session_id, is_new)
    """
    try:
        session_uuid = UUID(session_uuid) if isinstance(session_uuid, str) else session_uuid
    except (ValueError, AttributeError):
        # Invalid UUID, use string
        pass
    
    cur.execute(
        """
        INSERT INTO sessions (session_uuid, project_path, created_at, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (session_uuid) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (str(session_uuid), project_path)
    )
    return cur.fetchone()[0]


def import_jsonl_file(filepath, conn):
    """
    Import a single JSONL file into the database.
    Returns: (message_count, error_count)
    """
    cur = conn.cursor()
    message_count = 0
    error_count = 0
    session_id = None
    project_path = str(filepath.parent.relative_to(CLAUDE_LOGS_DIR))
    
    print(f"\nImporting: {filepath.name}")
    print(f"  Project: {project_path}")
    
    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    
                    # Create or get session
                    if data.get('sessionId') and session_id is None:
                        session_id = get_or_create_session(
                            cur, 
                            data['sessionId'],
                            project_path
                        )
                    
                    if not session_id:
                        continue
                    
                    # Extract nested message data
                    message = data.get('message', {})
                    content = message.get('content', '') if isinstance(message, dict) else ''
                    usage = message.get('usage', {}) if isinstance(message, dict) else {}
                    
                    # Insert message
                    cur.execute(
                        """
                        INSERT INTO messages (
                            session_id, uuid, type, role, content, 
                            timestamp, cwd, input_tokens, output_tokens, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            session_id,
                            data.get('uuid'),
                            data.get('type'),
                            message.get('role') if isinstance(message, dict) else None,
                            content,
                            data.get('timestamp'),
                            data.get('cwd'),
                            usage.get('input_tokens') if isinstance(usage, dict) else None,
                            usage.get('output_tokens') if isinstance(usage, dict) else None,
                            data.get('version')
                        )
                    )
                    
                    message_count += 1
                    
                    # Commit every 100 messages
                    if message_count % 100 == 0:
                        conn.commit()
                        print(f"  Imported {message_count} messages...", end='\r')
                
                except (json.JSONDecodeError, ValueError) as e:
                    error_count += 1
                    if error_count <= 3:  # Show first 3 errors only
                        print(f"  Error at line {line_num}: {e}")
                    continue
        
        # Final commit
        conn.commit()
        
        # Update session token totals
        if session_id:
            cur.execute(
                """
                UPDATE sessions 
                SET total_input_tokens = (
                    SELECT COALESCE(SUM(input_tokens), 0) FROM messages 
                    WHERE session_id = %s
                ),
                total_output_tokens = (
                    SELECT COALESCE(SUM(output_tokens), 0) FROM messages 
                    WHERE session_id = %s
                )
                WHERE id = %s
                """,
                (session_id, session_id, session_id)
            )
            conn.commit()
        
        print(f"  ✓ Imported {message_count} messages ({error_count} errors)")
        return message_count, error_count
    
    except Exception as e:
        print(f"  ✗ Failed to import: {e}")
        conn.rollback()
        return 0, -1
    
    finally:
        cur.close()


def main():
    """Main import flow."""
    print("=== Claude Code Log Importer ===\n")
    
    # Determine which files to import
    if len(sys.argv) > 1:
        # Import specific file
        filepath = Path(sys.argv[1])
        if not filepath.exists():
            print(f"Error: File not found: {filepath}")
            sys.exit(1)
        files_to_import = [filepath]
    else:
        # Auto-discover from CLAUDE_LOGS_DIR
        files_to_import = find_jsonl_files()
    
    if not files_to_import:
        print("No JSONL files found to import.")
        sys.exit(0)
    
    # Connect to database
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"Error connecting to PostgreSQL: {e}")
        print("\nMake sure:")
        print("  1. PostgreSQL is running")
        print("  2. Database 'claude_logs' exists")
        print("  3. Update DB_CONFIG in this script if using non-standard settings")
        sys.exit(1)
    
    # Import each file
    total_messages = 0
    total_errors = 0
    
    for filepath in files_to_import:
        messages, errors = import_jsonl_file(filepath, conn)
        total_messages += messages
        if errors > 0:
            total_errors += errors
    
    conn.close()
    
    # Summary
    print("\n=== Import Complete ===")
    print(f"Total messages imported: {total_messages}")
    if total_errors > 0:
        print(f"Total errors: {total_errors}")
    
    # Show database stats
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM sessions")
        session_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM messages")
        message_count = cur.fetchone()[0]
        
        cur.execute("""
            SELECT 
                COUNT(*) as total_messages,
                COALESCE(SUM(input_tokens), 0) as total_input,
                COALESCE(SUM(output_tokens), 0) as total_output
            FROM messages
        """)
        stats = cur.fetchone()
        
        print(f"\nDatabase stats:")
        print(f"  Sessions: {session_count}")
        print(f"  Messages: {message_count}")
        print(f"  Total tokens: {stats[1] + stats[2]:,}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Could not fetch stats: {e}")


if __name__ == '__main__':
    main()
```

### 2.2 Installation and Usage

```bash
# Install required Python package
pip install psycopg2-binary

# Make script executable
chmod +x import_claude_logs.py

# Run import (discovers logs automatically)
python import_claude_logs.py

# Or import a specific file
python import_claude_logs.py ~/.claude/projects/my-project/session-uuid.jsonl
```

**What the script does:**
1. Discovers all `.jsonl` files in `~/.claude/projects/`
2. Creates a session record for each unique `sessionId`
3. Inserts messages with automatic `tsvector` indexing
4. Commits in batches (every 100 messages) for performance
5. Updates session token aggregates
6. Shows summary statistics

## Part 3: Querying with Full-Text Search

### 3.1 Simple Full-Text Search

```sql
-- Find messages about authentication errors
SELECT 
    id, type, content, timestamp,
    ts_rank(content_tsvector, query) AS relevance
FROM messages, 
     plainto_tsquery('english', 'authentication error') query
WHERE content_tsvector @@ query
ORDER BY relevance DESC
LIMIT 10;
```

### 3.2 Phrase Search

```sql
-- Find exact phrases
SELECT id, type, content, timestamp
FROM messages
WHERE content_tsvector @@ phraseto_tsquery('english', 'permission denied')
ORDER BY timestamp DESC;
```

### 3.3 Boolean Operators

```sql
-- Combine multiple terms with AND/OR/NOT
SELECT id, type, content, timestamp
FROM messages
WHERE content_tsvector @@ to_tsquery('english', 
    '(database | postgres) & (error | fail)'
)
ORDER BY timestamp DESC;
```

### 3.4 Search Within a Session

```sql
-- Search only within a specific session
SELECT 
    m.id, m.type, m.content, m.timestamp,
    ts_rank(m.content_tsvector, query) AS relevance
FROM messages m
JOIN sessions s ON m.session_id = s.id,
     plainto_tsquery('english', 'authentication') query
WHERE s.session_uuid = 'YOUR-SESSION-UUID'
  AND m.content_tsvector @@ query
ORDER BY m.timestamp DESC;
```

### 3.5 Aggregate Statistics

```sql
-- Token usage summary
SELECT 
    s.session_uuid,
    s.project_path,
    COUNT(m.id) as message_count,
    COALESCE(SUM(m.input_tokens), 0) as total_input,
    COALESCE(SUM(m.output_tokens), 0) as total_output,
    s.created_at
FROM sessions s
LEFT JOIN messages m ON s.id = m.session_id
GROUP BY s.id
ORDER BY s.created_at DESC;
```

### 3.6 Search with Type Filtering

```sql
-- Search only assistant responses about errors
SELECT id, content, timestamp
FROM messages
WHERE type = 'assistant'
  AND content_tsvector @@ plainto_tsquery('english', 'error')
ORDER BY timestamp DESC
LIMIT 20;
```

## Part 4: Python Query Helpers

Create `query_logs.py` for convenient searching:

```python
#!/usr/bin/env python3
"""
Query helper for Claude Code logs PostgreSQL database.
"""

import psycopg2
from datetime import datetime
from tabulate import tabulate  # Optional: pip install tabulate

DB_CONFIG = {
    'host': 'localhost',
    'database': 'claude_logs',
    'user': 'postgres',
}


def search_fulltext(query_text, limit=10):
    """Search logs using full-text search."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            m.id, m.type, m.content, m.timestamp,
            ts_rank(m.content_tsvector, q.query) AS relevance
        FROM messages m,
             plainto_tsquery('english', %s) q(query)
        WHERE m.content_tsvector @@ q.query
        ORDER BY relevance DESC, m.timestamp DESC
        LIMIT %s
    """, (query_text, limit))
    
    results = cur.fetchall()
    cur.close()
    conn.close()
    
    return results


def search_in_session(session_uuid, query_text, limit=10):
    """Search within a specific session."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            m.id, m.type, m.content, m.timestamp,
            ts_rank(m.content_tsvector, q.query) AS relevance
        FROM messages m
        JOIN sessions s ON m.session_id = s.id,
             plainto_tsquery('english', %s) q(query)
        WHERE s.session_uuid = %s
          AND m.content_tsvector @@ q.query
        ORDER BY m.timestamp DESC
        LIMIT %s
    """, (query_text, session_uuid, limit))
    
    results = cur.fetchall()
    cur.close()
    conn.close()
    
    return results


def get_session_stats():
    """Get statistics for all sessions."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            s.session_uuid,
            s.project_path,
            COUNT(m.id) as messages,
            COALESCE(SUM(m.input_tokens), 0) as input_tokens,
            COALESCE(SUM(m.output_tokens), 0) as output_tokens,
            s.created_at
        FROM sessions s
        LEFT JOIN messages m ON s.id = m.session_id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """)
    
    results = cur.fetchall()
    cur.close()
    conn.close()
    
    return results


def print_results(results):
    """Pretty-print search results."""
    for msg_id, msg_type, content, timestamp, relevance in results:
        print(f"\n[{relevance:.3f}] {timestamp} ({msg_type})")
        print(f"  ID: {msg_id}")
        print(f"  Content: {content[:200]}...")


if __name__ == '__main__':
    # Example usage
    print("=== Full-Text Search Examples ===\n")
    
    # Search 1: General search
    print("Search: 'authentication error'")
    results = search_fulltext('authentication error', limit=5)
    print_results(results)
    
    # Search 2: Session stats
    print("\n\n=== Session Statistics ===")
    stats = get_session_stats()
    if stats:
        print(tabulate(stats, 
            headers=['Session UUID', 'Project', 'Messages', 'Input Tokens', 'Output Tokens', 'Created'],
            tablefmt='grid'))
```

## Part 5: Performance Tuning

### Check Index Usage

```sql
-- See if FTS index is being used
EXPLAIN ANALYZE
SELECT * FROM messages
WHERE content_tsvector @@ plainto_tsquery('english', 'authentication error')
LIMIT 10;

-- Should show: "Index Scan using idx_messages_fts"
```

### View Query Statistics

```sql
-- See which queries are slow
SELECT query, calls, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- Note: Requires shared_preload_libraries = 'pg_stat_statements' in postgresql.conf
```

### VACUUM and ANALYZE

```sql
-- Optimize table after large imports
VACUUM ANALYZE messages;
VACUUM ANALYZE sessions;
```

## Part 6: Backup and Restore

### Backup Database

```bash
# Full database backup
pg_dump claude_logs > claude_logs_backup.sql

# Compressed backup (recommended)
pg_dump claude_logs | gzip > claude_logs_backup.sql.gz
```

### Restore Database

```bash
# From SQL file
psql claude_logs < claude_logs_backup.sql

# From compressed file
gunzip -c claude_logs_backup.sql.gz | psql claude_logs
```

## Troubleshooting

### "Database does not exist"
```bash
psql -U postgres -l  # List databases
createdb claude_logs  # Create if missing
```

### "psycopg2: permission denied"
Create `~/.pgpass` file with credentials:
```
localhost:5432:claude_logs:postgres:your_password
chmod 600 ~/.pgpass
```

### Import seems stuck
```sql
-- Check progress
SELECT COUNT(*) FROM messages;

-- Kill slow queries if needed
SELECT pid, query FROM pg_stat_activity WHERE state = 'active';
SELECT pg_terminate_backend(pid);
```

### Full-text search returning no results
Make sure you're using `plainto_tsquery()` (simple) or `to_tsquery()` (with operators):
```sql
-- This works
SELECT * FROM messages 
WHERE content_tsvector @@ plainto_tsquery('english', 'authentication');

-- This doesn't work (missing query constructor)
SELECT * FROM messages 
WHERE content_tsvector @@ 'authentication';
```

## Next Steps

Once you have data in PostgreSQL:

1. **Semantic search**: Add embeddings and `pgvector` for similar-context search
2. **Glossary expansion**: Implement LLM-powered query expansion (separate guide)
3. **Web interface**: Build a simple Flask/FastAPI app to search logs
4. **Monitoring**: Set up `pg_stat_statements` to track performance
5. **Scheduling**: Use `cron` to auto-import logs daily

---

## Quick Reference

```bash
# Connect to database
psql claude_logs

# List tables and indexes
\d

# Check table sizes
\d+ messages

# Run import script
python import_claude_logs.py

# Basic search (in psql)
SELECT * FROM messages 
WHERE content_tsvector @@ plainto_tsquery('english', 'your search terms')
LIMIT 10;
```
