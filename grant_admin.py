import sqlite3

DB_PATH = "tickets.db"
ADMIN_CHAT_ID = 7769189255

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. Ensure there’s a row for you (won’t duplicate if it already exists)
cur.execute("""
    INSERT OR IGNORE INTO technicians (chat_id, name, phone, skills, status)
    VALUES (?, ?, ?, ?, 'admin')
""", (ADMIN_CHAT_ID, 'AdminUser', '0000000000', 'n/a'))

# 2. Force your status to 'admin'
cur.execute("""
    UPDATE technicians
       SET status = 'admin'
     WHERE chat_id = ?
""", (ADMIN_CHAT_ID,))

conn.commit()
conn.close()

print("✅ Your user (chat_id=7769189255) is now an admin.")
