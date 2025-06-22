#!/usr/bin/env python
"""
Database initialization script for ServiceFix Bot

This script creates a new clean database with the required schema.
Use this if you need to reset the database or start fresh.
"""

import os
import sqlite3

# Define the database schema
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    appliance TEXT,
    issue_summary TEXT,
    location TEXT,
    preferred_time TEXT,
    raw_problem_text TEXT,
    status TEXT DEFAULT 'new',
    technician_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (technician_id) REFERENCES technicians (id)
);
CREATE TABLE IF NOT EXISTS technicians (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER UNIQUE NOT NULL,
    name TEXT,
    phone TEXT,
    skills TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    rating INTEGER,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);
"""

# Database file path
DB_PATH = "tickets.db"

def init_db():
    """Initialize the database with required tables"""
    # Check if database exists and prompt for confirmation if it does
    if os.path.exists(DB_PATH):
        confirm = input(f"Database '{DB_PATH}' already exists. Overwrite? (y/n): ")
        if confirm.lower() != 'y':
            print("Database initialization cancelled.")
            return
        
    # Create or connect to the database
    conn = sqlite3.connect(DB_PATH)
    
    # Set pragmas for better performance
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    
    # Create tables
    conn.executescript(CREATE_TABLES_SQL)
    conn.commit()
    conn.close()
    
    print(f"✅ Database '{DB_PATH}' has been initialized successfully.")

if __name__ == "__main__":
    init_db()