# service_fix_bot.py
"""
Enhanced HVAC Service Booking Telegram Bot (v4 - AI Removed)
-------------------------------------------------------------
* Updates in this revision *
- The Google Gemini AI feature has been completely removed to ensure
  reliability and simplify the booking process.
- The bot now takes the user's full problem description as the
  issue summary directly.
- Location and preferred time are no longer parsed from the message.
- All bug fixes from previous versions are retained.
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
import time
import difflib
import csv

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    Message,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Import static lists for districts and complaints
from static_data import districts, complaints

# ---------- Load env & set globals ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if BOT_TOKEN is None:
    raise RuntimeError("BOT_TOKEN is not set in the environment. Please add it to your .env file.")
assert BOT_TOKEN is not None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------- DB helpers ----------
DB_PATH = "tickets.db"

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


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # allow readers & writers to coexist
    conn.execute("PRAGMA journal_mode=WAL;")
    # wait up to 30s for any lock to clear
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.executescript(CREATE_TABLES_SQL)
    conn.commit()
    conn.close()


# --- Async DB Wrappers ---
async def db_write(sql, params=()):
    def _write():
        for attempt in range(5):
            try:
                conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout = 30000;")
                conn.execute(sql, params)
                conn.commit()
                conn.close()
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    time.sleep(0.1)
                    continue
                raise
        raise sqlite3.OperationalError("Failed to write after retries")
    await asyncio.to_thread(_write)


async def db_read_one(sql, params=()):
    def _read_one():
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, params)
        result = cursor.fetchone()
        conn.close()
        return result
    return await asyncio.to_thread(_read_one)


async def db_read_all(sql, params=()):
    def _read_all():
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, params)
        result = cursor.fetchall()
        conn.close()
        return result
    return await asyncio.to_thread(_read_all)

# ---------- AI HELPER REMOVED ----------

# ---------- Conversation States ----------
AWAITING_APPLIANCE, AWAITING_CITY, AWAITING_COMPLAINT, AWAITING_PROBLEM = range(4)
TECH_AWAITING_NAME, TECH_AWAITING_PHONE, TECH_AWAITING_SKILLS = range(4, 7)
AWAITING_TICKET_ID = range(7, 8)

# ---------- Fuzzy Matching Helpers ----------
def get_city_suggestions(user_input, n=5):
    # Return top n district suggestions (with state) for the user input
    names = [f"{d['district']} ({d['state']})" for d in districts]
    matches = difflib.get_close_matches(user_input, names, n=n, cutoff=0.6)
    return matches

def get_complaint_suggestions(appliance, user_input, n=5):
    # Return top n complaint suggestions for the appliance and user input
    filtered = [c['complaint'] for c in complaints if c['appliance'].lower() == appliance.lower()]
    matches = difflib.get_close_matches(user_input, filtered, n=n, cutoff=0.6)
    return matches

def find_district_and_state(name):
    # Find the district and state from the suggestion string
    for d in districts:
        if name.lower() == f"{d['district']} ({d['state']})".lower():
            return d['district'], d['state']
    return None, None

# ---------- General User Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "Hi! I'm the service bot for *HVAC/R & WM Repairs*.\n\n"
        "👤 *Customers*: Type /book to create a new service ticket.\n\n"
        "🛠️ *Technicians*: Type /register to sign up or /myjobs to see your assigned work.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- Customer Booking Conversation ----------
async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    keyboard = [
        [InlineKeyboardButton("AC", callback_data="AC"), InlineKeyboardButton("Fridge", callback_data="Fridge")],
        [InlineKeyboardButton("Washing Machine", callback_data="Washing Machine"), InlineKeyboardButton("Other", callback_data="Other")],
    ]
    await update.message.reply_text("Great! Which appliance needs service?", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAITING_APPLIANCE


async def appliance_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("appliance_chosen called")
    query = update.callback_query
    if not query:
        logger.warning("appliance_chosen: query missing")
        return
    logger.info(f"appliance_chosen: callback data = {query.data}")
    if context.user_data is not None:
        context.user_data["appliance"] = query.data
    await query.answer()
    await query.edit_message_text("Please enter your city (district) name:")
    return AWAITING_CITY


async def city_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return AWAITING_CITY
    if context.user_data is None:
        context.user_data = {}
    user_input = update.message.text.strip()
    suggestions = get_city_suggestions(user_input)
    if suggestions:
        keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in suggestions]
        keyboard.append([InlineKeyboardButton("My city is not listed", callback_data="free_text_city")])
        await update.message.reply_text(
            "Did you mean one of these? Please select or choose 'My city is not listed' to enter manually:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        # Store the original input in case user wants to use it as free text
        context.user_data["city_free_text"] = user_input
        return AWAITING_CITY
    else:
        # No suggestions, treat as free text
        context.user_data["district"] = user_input
        context.user_data["state"] = None
        await update.message.reply_text("Now, please describe your problem or select a complaint category:")
        return AWAITING_COMPLAINT


async def city_suggestion_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return AWAITING_CITY
    if context.user_data is None:
        context.user_data = {}
    if query.data == "free_text_city":
        # Use the free text city
        user_input = context.user_data.get("city_free_text", "")
        context.user_data["district"] = user_input
        context.user_data["state"] = None
        await query.answer()
        await query.edit_message_text(f"City set as: {user_input}\nNow, please describe your problem or select a complaint category:")
        return AWAITING_COMPLAINT
    else:
        # Use the selected suggestion
        district, state = find_district_and_state(query.data)
        context.user_data["district"] = district
        context.user_data["state"] = state
        await query.answer()
        await query.edit_message_text(f"City set as: {district} ({state})\nNow, please describe your problem or select a complaint category:")
        return AWAITING_COMPLAINT


async def complaint_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return AWAITING_COMPLAINT
    if context.user_data is None:
        context.user_data = {}
    appliance = context.user_data.get("appliance")
    user_input = update.message.text.strip()
    suggestions = get_complaint_suggestions(appliance, user_input)
    if suggestions:
        keyboard = [[InlineKeyboardButton(s, callback_data=s)] for s in suggestions]
        keyboard.append([InlineKeyboardButton("My complaint is not listed", callback_data="free_text_complaint")])
        await update.message.reply_text(
            "Did you mean one of these? Please select or choose 'My complaint is not listed' to enter manually:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["complaint_free_text"] = user_input
        return AWAITING_COMPLAINT
    else:
        # No suggestions, treat as free text
        context.user_data["complaint"] = user_input
        await update.message.reply_text("Please describe your problem in detail (optional, or type /skip):")
        return AWAITING_PROBLEM


async def complaint_suggestion_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return AWAITING_COMPLAINT
    if context.user_data is None:
        context.user_data = {}
    if query.data == "free_text_complaint":
        user_input = context.user_data.get("complaint_free_text", "")
        context.user_data["complaint"] = user_input
        await query.answer()
        await query.edit_message_text(f"Complaint set as: {user_input}\nPlease describe your problem in detail (optional, or type /skip):")
        return AWAITING_PROBLEM
    else:
        context.user_data["complaint"] = query.data
        await query.answer()
        await query.edit_message_text(f"Complaint set as: {query.data}\nPlease describe your problem in detail (optional, or type /skip):")
        return AWAITING_PROBLEM


async def problem_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or context.user_data is None:
        return
    problem_text = update.message.text if update.message.text else ""
    appliance = context.user_data.get("appliance")
    district = context.user_data.get("district")
    state = context.user_data.get("state")
    complaint = context.user_data.get("complaint")
    # Insert the ticket and get the ticket id
    def _insert_ticket():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        sql = (
            "INSERT INTO tickets (chat_id, appliance, issue_summary, location, preferred_time, raw_problem_text) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (
            update.effective_chat.id if update.effective_chat is not None else None,
            appliance,
            complaint,  # Use the selected complaint as the summary
            f"{district}, {state}" if district and state else district or "",
            None,  # Preferred time is not used
            problem_text,
        )
        cur = conn.execute(sql, params)
        ticket_id = cur.lastrowid
        conn.commit()
        conn.close()
        return ticket_id
    ticket_id = await asyncio.to_thread(_insert_ticket)
    await update.message.reply_text(
        f"Thanks! Your request has been logged. Your ticket ID is #{ticket_id}.\n"
        "A technician will contact you shortly. You can use /status to check your ticket status."
    )
    if context.user_data is not None:
        context.user_data.clear()
    return ConversationHandler.END


async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data:
        context.user_data.clear()
    if update.message:
        await update.message.reply_text("Booking cancelled.")
    return ConversationHandler.END


# ---------- Customer Status Check ----------
async def status_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text("Please enter your Ticket ID to check its status.")
    return AWAITING_TICKET_ID


async def status_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return ConversationHandler.END
    ticket_id = update.message.text
    if ticket_id is None:
        await update.message.reply_text("That doesn't look like a valid Ticket ID. Please enter a number.")
        return AWAITING_TICKET_ID
    try:
        ticket_id_int = int(ticket_id)
        ticket = await db_read_one(
            "SELECT status, technician_id FROM tickets WHERE id = ? AND chat_id = ?",
            (ticket_id_int, update.effective_chat.id),
        )
        if not ticket:
            await update.message.reply_text("Sorry, I couldn't find a ticket with that ID for your account.")
            return ConversationHandler.END
        status_message = f"Status for Ticket #{ticket_id}: *{ticket['status'].upper()}*"
        if ticket["status"] == "assigned" and ticket["technician_id"]:
            tech = await db_read_one("SELECT name, phone FROM technicians WHERE id = ?", (ticket["technician_id"],))
            if tech:
                status_message += f"\n\nAssigned to: {tech['name']}\nContact: {tech['phone']}"
        await update.message.reply_text(status_message, parse_mode=ParseMode.MARKDOWN)
    except (ValueError, TypeError):
        await update.message.reply_text("That doesn't look like a valid Ticket ID. Please enter a number.")
        return AWAITING_TICKET_ID
    return ConversationHandler.END


# ---------- Technician Registration Conversation ----------
async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "Welcome! Let's get you registered as a technician.\nFirst, what is your full name?"
    )
    return TECH_AWAITING_NAME


async def tech_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("tech_name_received called")
    if not update.message or update.message.text is None:
        logger.warning("tech_name_received: missing update.message or text")
        if update.message:
            await update.message.reply_text("Sorry, I didn't catch your name—please type your full name.")
        return TECH_AWAITING_NAME
    if context.user_data is None:
        context.user_data = {}
    context.user_data["tech_name"] = update.message.text.strip()
    if update.message:
        await update.message.reply_text("Got it. What is your 10‑digit contact number?")
    logger.info("tech_name_received: returning TECH_AWAITING_PHONE")
    return TECH_AWAITING_PHONE


async def tech_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("tech_phone_received called")
    if not update.message or update.message.text is None:
        logger.warning("tech_phone_received: missing update.message or text")
        if update.message:
            await update.message.reply_text("Sorry, I didn't catch your phone number—please type your 10-digit contact number.")
        return TECH_AWAITING_PHONE
    if context.user_data is None:
        context.user_data = {}
    context.user_data["tech_phone"] = update.message.text.strip()
    if update.message:
        await update.message.reply_text(
            "Great. What are your main skills? (e.g., AC, Fridge, Washing Machine)"
        )
    logger.info("tech_phone_received: returning TECH_AWAITING_SKILLS")
    return TECH_AWAITING_SKILLS


async def tech_skills_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("tech_skills_received called")
    if not update.message or not update.effective_chat or update.message.text is None:
        logger.warning("tech_skills_received: missing update.message, effective_chat, or text")
        if update.message:
            await update.message.reply_text("Sorry, I didn't catch your skills—please type your main skills.")
        return TECH_AWAITING_SKILLS
    skills = update.message.text.strip()
    name = context.user_data["tech_name"] if context.user_data is not None else None
    phone = context.user_data["tech_phone"] if context.user_data is not None else None
    chat_id = update.effective_chat.id
    try:
        await db_write(
            "INSERT INTO technicians (chat_id, name, phone, skills) VALUES (?, ?, ?, ?)",
            (chat_id, name, phone, skills),
        )
        if update.message:
            await update.message.reply_text(
                "Thank you! Your registration is complete and has been sent for approval."
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"New technician for approval:\nName: {name}\nPhone: {phone}\nSkills: {skills}",
            )
    except sqlite3.IntegrityError:
        if update.message:
            await update.message.reply_text("You have already registered. Please wait for approval.")
    except Exception as e:
        logger.error(f"Error during technician registration: {e}")
        if update.message:
            await update.message.reply_text("Sorry, something went wrong.")
    if context.user_data is not None:
        context.user_data.clear()
    logger.info("tech_skills_received: returning ConversationHandler.END")
    return ConversationHandler.END


async def tech_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data:
        context.user_data.clear()
    if update.message:
        await update.message.reply_text("Registration cancelled.")
    return ConversationHandler.END


# ---------- Technician Command Handlers ----------
async def my_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    tech = await db_read_one("SELECT id, status FROM technicians WHERE chat_id = ?", (chat_id,))
    if not tech or tech["status"] != "approved":
        await update.message.reply_text("This command is only for approved technicians.")
        return

    jobs = await db_read_all(
        "SELECT * FROM tickets WHERE technician_id = ? AND status = 'assigned'",
        (tech["id"],),
    )
    if not jobs:
        await update.message.reply_text("You have no new jobs assigned.")
        return

    await update.message.reply_text("Here are your assigned jobs:")
    for job in jobs:
        text = (
            f"<b>Ticket #{job['id']}</b> - {job['location'] or 'Vizag'}\n"
            f"<b>Appliance:</b> {job['appliance']}\n"
            f"<b>Issue:</b> {job['issue_summary']}\n"
            f"<b>Customer Time:</b> {job['preferred_time'] or 'Not specified'}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------- Admin Handlers ----------
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("admin handler called")
    if not update.effective_user or not update.message:
        logger.warning("admin: missing effective_user or message")
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to access the admin panel.")
        return
    keyboard = [
        [InlineKeyboardButton("List New Tickets", callback_data="admin_list_tickets")],
        [InlineKeyboardButton("Approve Technicians", callback_data="admin_list_techs")],
    ]
    await update.message.reply_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(keyboard))


# ---- LIST & APPROVE TECHNICIANS ----
async def admin_list_techs_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    techs = await db_read_all("SELECT * FROM technicians WHERE status='pending'")
    if not techs:
        await query.edit_message_text("No pending technicians for approval.")
        return
    await query.edit_message_text("Pending Technicians:")
    for tech in techs:
        text = f"Name: {tech['name']}\nPhone: {tech['phone']}\nSkills: {tech['skills']}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Approve ✅", callback_data=f"approve_tech_{tech['id']}")]]
        )
        if query.message:
            msg: Message = query.message  # type: ignore
            await msg.reply_text(text, reply_markup=keyboard)


async def admin_approve_tech_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if query.data is None:
        return
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    tech_id = int(parts[2])
    tech_info = await db_read_one("SELECT chat_id, name FROM technicians WHERE id=?", (tech_id,))
    await db_write("UPDATE technicians SET status='approved' WHERE id=?", (tech_id,))
    if tech_info:
        await context.bot.send_message(
            chat_id=tech_info["chat_id"],
            text="Congratulations! Your registration has been approved. You can now use /myjobs.",
        )
        await query.edit_message_text(f"Technician {tech_info['name']} approved. ✅")
        await query.answer("Technician Approved!")


# ---- LIST & ASSIGN TICKETS ----
async def admin_list_tickets_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='new' ORDER BY created_at ASC")
    if not tickets:
        await query.edit_message_text("No new tickets.")
        return
    await query.edit_message_text("New Tickets:")
    for ticket in tickets:
        # Parse city and state from location
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Assign Technician", callback_data=f"assign_ticket_{ticket['id']}")]]
        )
        if query.message:
            msg: Message = query.message  # type: ignore
            await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ---- TECHNICIAN SELECTION & ASSIGNMENT ----
async def admin_assign_ticket_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if query.data is None:
        return
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    _, _, ticket_id_str = parts
    ticket_id = int(ticket_id_str)
    await query.answer()

    technicians = await db_read_all(
        "SELECT * FROM technicians WHERE status='approved' ORDER BY created_at ASC"
    )
    if not technicians:
        await query.edit_message_text("No approved technicians available right now.")
        return

    keyboard_rows = []
    for tech in technicians:
        keyboard_rows.append(
            [InlineKeyboardButton(tech["name"], callback_data=f"assign_{ticket_id}_{tech['id']}")]
        )
    keyboard = InlineKeyboardMarkup(keyboard_rows)
    await query.edit_message_text("Choose a technician to assign:", reply_markup=keyboard)


async def admin_assign_ticket_finalize_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if query.data is None:
        return
    parts = query.data.split("_")
    if len(parts) < 3:
        return
    _, ticket_id_str, tech_id_str = parts
    ticket_id, tech_id = int(ticket_id_str), int(tech_id_str)
    await db_write(
        "UPDATE tickets SET technician_id=?, status='assigned' WHERE id=?", (tech_id, ticket_id)
    )

    # Notify technician
    tech = await db_read_one("SELECT chat_id FROM technicians WHERE id=?", (tech_id,))
    ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (ticket_id,))

    if tech:
        await context.bot.send_message(
            chat_id=tech["chat_id"],
            text=(
                f"🛠️  You have been assigned Ticket #{ticket_id}.\n"
                f"Appliance: {ticket['appliance']}\nIssue: {ticket['issue_summary']}\n"
                f"Location: {ticket['location'] or 'Vizag'}\nPreferred Time: {ticket['preferred_time'] or 'Not specified'}\n"
                "Please contact the customer from the app as soon as possible."
            ),
        )

    await query.edit_message_text("Ticket assigned successfully! ✅")
    await query.answer()


# ---------- Admin Shortcuts ----------
async def listall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets ORDER BY created_at DESC")
    if not tickets:
        await update.message.reply_text("No tickets found.")
        return
    for ticket in tickets:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        status = ticket['status']
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {status}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def listnew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='new' ORDER BY created_at DESC")
    if not tickets:
        await update.message.reply_text("No new/unassigned tickets found.")
        return
    for ticket in tickets:
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def listassigned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='assigned' ORDER BY created_at DESC")
    if not tickets:
        await update.message.reply_text("No assigned tickets found.")
        return
    for ticket in tickets:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def listtechs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    techs = await db_read_all("SELECT * FROM technicians ORDER BY created_at DESC")
    if not techs:
        await update.message.reply_text("No technicians found.")
        return
    for tech in techs:
        text = (
            f"<b>Technician ID:</b> {tech['id']}\n"
            f"<b>Name:</b> {tech['name']}\n"
            f"<b>Phone:</b> {tech['phone']}\n"
            f"<b>Skills:</b> {tech['skills']}\n"
            f"<b>Status:</b> {tech['status']}\n"
            f"<b>Created At:</b> {tech['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Ticket Search/Filter Commands ----------
async def searchtickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /searchtickets <keyword>")
        return
    keyword = ' '.join(context.args).lower()
    tickets = await db_read_all("SELECT * FROM tickets ORDER BY created_at DESC")
    found = []
    for ticket in tickets:
        fields = [str(ticket.get('appliance', '')), str(ticket.get('issue_summary', '')), str(ticket.get('location', '')), str(ticket.get('raw_problem_text', ''))]
        if any(keyword in f.lower() for f in fields):
            found.append(ticket)
    if not found:
        await update.message.reply_text("No tickets found matching that keyword.")
        return
    for ticket in found:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def ticketsbycity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ticketsbycity <city>")
        return
    city = ' '.join(context.args).strip().lower()
    tickets = await db_read_all("SELECT * FROM tickets ORDER BY created_at DESC")
    found = []
    for ticket in tickets:
        location = ticket['location'] or ''
        city_part = location.split(',')[0].strip().lower() if ',' in location else location.strip().lower()
        if city in city_part:
            found.append(ticket)
    if not found:
        await update.message.reply_text(f"No tickets found for city: {city}")
        return
    for ticket in found:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city_disp, state = [x.strip() for x in location.split(',', 1)]
        else:
            city_disp, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city_disp}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def ticketsbystate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ticketsbystate <state>")
        return
    state = ' '.join(context.args).strip().lower()
    tickets = await db_read_all("SELECT * FROM tickets ORDER BY created_at DESC")
    found = []
    for ticket in tickets:
        location = ticket['location'] or ''
        state_part = location.split(',')[1].strip().lower() if ',' in location else ''
        if state and state in state_part:
            found.append(ticket)
    if not found:
        await update.message.reply_text(f"No tickets found for state: {state}")
        return
    for ticket in found:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state_disp = [x.strip() for x in location.split(',', 1)]
        else:
            city, state_disp = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state_disp}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def ticketsbydate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ticketsbydate <YYYY-MM-DD>")
        return
    date_str = context.args[0]
    tickets = await db_read_all("SELECT * FROM tickets WHERE date(created_at) = ? ORDER BY created_at DESC", (date_str,))
    if not tickets:
        await update.message.reply_text(f"No tickets found for date: {date_str}")
        return
    for ticket in tickets:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Ticket Management Commands ----------
async def closeticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /closeticket <ticket_id>")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ticket ID.")
        return
    ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if not ticket:
        await update.message.reply_text("Ticket not found.")
        return
    await db_write("UPDATE tickets SET status='closed' WHERE id=?", (ticket_id,))
    await update.message.reply_text(f"Ticket #{ticket_id} marked as closed.")

async def reassign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reassign <ticket_id> <tech_id>")
        return
    try:
        ticket_id = int(context.args[0])
        tech_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid ticket or technician ID.")
        return
    ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    tech = await db_read_one("SELECT * FROM technicians WHERE id=?", (tech_id,))
    if not ticket:
        await update.message.reply_text("Ticket not found.")
        return
    if not tech:
        await update.message.reply_text("Technician not found.")
        return
    await db_write("UPDATE tickets SET technician_id=?, status='assigned' WHERE id=?", (tech_id, ticket_id))
    await update.message.reply_text(f"Ticket #{ticket_id} reassigned to technician {tech['name']} (ID: {tech_id}).")

async def ticketdetails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ticketdetails <ticket_id>")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ticket ID.")
        return
    ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if not ticket:
        await update.message.reply_text("Ticket not found.")
        return
    tech = None
    if ticket['technician_id']:
        tech = await db_read_one("SELECT name, phone FROM technicians WHERE id=?", (ticket['technician_id'],))
    location = ticket['location'] or 'Not Specified'
    if location and ',' in location:
        city, state = [x.strip() for x in location.split(',', 1)]
    else:
        city, state = location, ''
    text = (
        f"<b>Ticket #{ticket['id']}</b>\n"
        f"<b>Appliance:</b> {ticket['appliance']}\n"
        f"<b>Complaint:</b> {ticket['issue_summary']}\n"
        f"<b>City:</b> {city}\n"
        f"<b>State:</b> {state}\n"
        f"<b>Status:</b> {ticket['status']}\n"
        f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
        f"<b>Created At:</b> {ticket['created_at']}\n"
    )
    if tech:
        text += f"<b>Assigned Technician:</b> {tech['name']} ({tech['phone']})\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Customer Management Command ----------
async def userhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /userhistory <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    tickets = await db_read_all("SELECT * FROM tickets WHERE chat_id=? ORDER BY created_at DESC", (user_id,))
    if not tickets:
        await update.message.reply_text("No tickets found for this user.")
        return
    for ticket in tickets:
        tech = None
        if ticket['technician_id']:
            tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (ticket['technician_id'],))
        assigned = f"Assigned to: {tech['name']}" if tech else "Not assigned"
        location = ticket['location'] or 'Not Specified'
        if location and ',' in location:
            city, state = [x.strip() for x in location.split(',', 1)]
        else:
            city, state = location, ''
        text = (
            f"<b>Ticket #{ticket['id']}</b>\n"
            f"<b>Appliance:</b> {ticket['appliance']}\n"
            f"<b>Complaint:</b> {ticket['issue_summary']}\n"
            f"<b>City:</b> {city}\n"
            f"<b>State:</b> {state}\n"
            f"<b>Status:</b> {ticket['status']}\n"
            f"<b>{assigned}</b>\n"
            f"<b>Description:</b> {ticket['raw_problem_text'] or '-'}\n"
            f"<b>Created At:</b> {ticket['created_at']}"
        )
        # Show feedback if any
        feedback = await db_read_one("SELECT rating, comment FROM feedback WHERE ticket_id=?", (ticket['id'],))
        if feedback:
            text += (
                f"\n<b>Feedback:</b> {feedback['rating'] or '-'} / 5\n"
                f"<b>Comment:</b> {feedback['comment'] or '-'}\""
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Feedback and Ratings Commands ----------
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    feedbacks = await db_read_all("SELECT * FROM feedback ORDER BY created_at DESC")
    if not feedbacks:
        await update.message.reply_text("No feedback found.")
        return
    for fb in feedbacks:
        ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (fb['ticket_id'],))
        if ticket:
            user_id = ticket['chat_id']
            summary = ticket['issue_summary']
        else:
            user_id = "-"
            summary = "-"
        text = (
            f"<b>Ticket #{fb['ticket_id']}</b>\n"
            f"<b>User ID:</b> {user_id}\n"
            f"<b>Complaint:</b> {summary}\n"
            f"<b>Rating:</b> {fb['rating'] or '-'} / 5\n"
            f"<b>Comment:</b> {fb['comment'] or '-'}\n"
            f"<b>Created At:</b> {fb['created_at']}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def feedbackbyticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /feedbackbyticket <ticket_id>")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ticket ID.")
        return
    fb = await db_read_one("SELECT * FROM feedback WHERE ticket_id=?", (ticket_id,))
    if not fb:
        await update.message.reply_text("No feedback found for this ticket.")
        return
    ticket = await db_read_one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    if ticket:
        user_id = ticket['chat_id']
        summary = ticket['issue_summary']
    else:
        user_id = "-"
        summary = "-"
    text = (
        f"<b>Ticket #{fb['ticket_id']}</b>\n"
        f"<b>User ID:</b> {user_id}\n"
        f"<b>Complaint:</b> {summary}\n"
        f"<b>Rating:</b> {fb['rating'] or '-'} / 5\n"
        f"<b>Comment:</b> {fb['comment'] or '-'}\n"
        f"<b>Created At:</b> {fb['created_at']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Statistics and Reports Commands ----------
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets")
    techs = await db_read_all("SELECT * FROM technicians")
    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets if t['status'] not in ('closed',))
    closed_tickets = sum(1 for t in tickets if t['status'] == 'closed')
    assigned_tickets = sum(1 for t in tickets if t['technician_id'])
    pending_techs = sum(1 for t in techs if t['status'] == 'pending')
    approved_techs = sum(1 for t in techs if t['status'] == 'approved')
    text = (
        f"<b>ServiceFix Stats</b>\n"
        f"Total Tickets: {total_tickets}\n"
        f"Open Tickets: {open_tickets}\n"
        f"Closed Tickets: {closed_tickets}\n"
        f"Assigned Tickets: {assigned_tickets}\n"
        f"Approved Technicians: {approved_techs}\n"
        f"Pending Technicians: {pending_techs}\n"
    )
    # Top cities/states
    from collections import Counter
    cities = [t['location'].split(',')[0].strip() if t['location'] and ',' in t['location'] else t['location'] for t in tickets if t['location']]
    states = [t['location'].split(',')[1].strip() if t['location'] and ',' in t['location'] else '' for t in tickets if t['location'] and ',' in t['location']]
    if cities:
        top_cities = Counter(cities).most_common(3)
        text += "\nTop Cities:\n" + "\n".join(f"{c}: {n}" for c, n in top_cities)
    if states:
        top_states = Counter(states).most_common(3)
        text += "\nTop States:\n" + "\n".join(f"{s}: {n}" for s, n in top_states)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def toptechs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='closed'")
    tech_counts = {}
    for t in tickets:
        if t['technician_id']:
            tech_counts[t['technician_id']] = tech_counts.get(t['technician_id'], 0) + 1
    if not tech_counts:
        await update.message.reply_text("No closed tickets or assigned technicians found.")
        return
    sorted_techs = sorted(tech_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    text = "<b>Top Technicians (by closed tickets):</b>\n"
    for tech_id, count in sorted_techs:
        tech = await db_read_one("SELECT name FROM technicians WHERE id=?", (tech_id,))
        name = tech['name'] if tech else f"ID {tech_id}"
        text += f"{name}: {count} closed tickets\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def pendingapproval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    techs = await db_read_all("SELECT * FROM technicians WHERE status='pending'")
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='new'")
    text = "<b>Pending Approvals</b>\n"
    if techs:
        text += "\nPending Technicians:\n"
        for t in techs:
            text += f"ID: {t['id']} | Name: {t['name']} | Phone: {t['phone']}\n"
    else:
        text += "\nNo pending technicians.\n"
    if tickets:
        text += "\nNew Tickets:\n"
        for t in tickets:
            text += f"Ticket #{t['id']} | {t['appliance']} | {t['issue_summary']} | {t['location']}\n"
    else:
        text += "\nNo new tickets."
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------- Admin Bulk Actions Commands ----------
async def bulkassign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /bulkassign <city> <tech_id>")
        return
    city = context.args[0].strip().lower()
    try:
        tech_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid technician ID.")
        return
    tech = await db_read_one("SELECT * FROM technicians WHERE id=?", (tech_id,))
    if not tech:
        await update.message.reply_text("Technician not found.")
        return
    tickets = await db_read_all("SELECT * FROM tickets WHERE status='new'")
    count = 0
    for ticket in tickets:
        location = ticket['location'] or ''
        city_part = location.split(',')[0].strip().lower() if ',' in location else location.strip().lower()
        if city in city_part:
            await db_write("UPDATE tickets SET technician_id=?, status='assigned' WHERE id=?", (tech_id, ticket['id']))
            count += 1
    await update.message.reply_text(f"Assigned {count} tickets in city '{city}' to technician {tech['name']} (ID: {tech_id}).")

async def bulkclose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /bulkclose <city>")
        return
    city = context.args[0].strip().lower()
    tickets = await db_read_all("SELECT * FROM tickets WHERE status!='closed'")
    count = 0
    for ticket in tickets:
        location = ticket['location'] or ''
        city_part = location.split(',')[0].strip().lower() if ',' in location else location.strip().lower()
        if city in city_part:
            await db_write("UPDATE tickets SET status='closed' WHERE id=?", (ticket['id'],))
            count += 1
    await update.message.reply_text(f"Closed {count} tickets in city '{city}'.")

# ---------- Admin Export Data Commands ----------
async def exporttickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    tickets = await db_read_all("SELECT * FROM tickets ORDER BY created_at DESC")
    if not tickets:
        await update.message.reply_text("No tickets found.")
        return
    filename = "tickets_export.csv"
    with open(filename, "w", newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=tickets[0].keys())
        writer.writeheader()
        for t in tickets:
            writer.writerow(dict(t))
    await update.message.reply_text(f"Tickets exported to {filename}.")
    try:
        with open(filename, "rb") as f:
            await update.message.reply_document(f, filename=filename)
    except Exception:
        pass

async def exporttechs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    techs = await db_read_all("SELECT * FROM technicians ORDER BY created_at DESC")
    if not techs:
        await update.message.reply_text("No technicians found.")
        return
    filename = "technicians_export.csv"
    with open(filename, "w", newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=techs[0].keys())
        writer.writeheader()
        for t in techs:
            writer.writerow(dict(t))
    await update.message.reply_text(f"Technicians exported to {filename}.")
    try:
        with open(filename, "rb") as f:
            await update.message.reply_document(f, filename=filename)
    except Exception:
        pass

# ---------- Main Application Setup ----------

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "*Visakhapatnam Repairs Bot Help*\n\n"
        "👤 *Customers*:\n"
        "- Use /book to create a new service ticket.\n"
        "- Use /status to check your ticket status.\n"
        "- Use /cancel to cancel an ongoing booking.\n\n"
        "🛠️ *Technicians*:\n"
        "- Use /register to sign up as a technician.\n"
        "- Use /myjobs to see your assigned jobs.\n\n"
        "*Admin Panel*:\n"
        "- Only authorized technicians can access the admin panel via /admin.\n",
        parse_mode=ParseMode.MARKDOWN,
    )

def build_app() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # --- Conversations ---
    booking_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("book", book_start)],
        states={
            AWAITING_APPLIANCE: [CallbackQueryHandler(appliance_chosen, pattern=r"^(AC|Fridge|Washing Machine|Other)$")],
            AWAITING_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_received), CallbackQueryHandler(city_suggestion_chosen)],
            AWAITING_COMPLAINT: [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_received), CallbackQueryHandler(complaint_suggestion_chosen)],
            AWAITING_PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, problem_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_booking)],
    )

    tech_reg_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("register", register_start)],
        states={
            TECH_AWAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, tech_name_received)],
            TECH_AWAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tech_phone_received)],
            TECH_AWAITING_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tech_skills_received)],
        },
        fallbacks=[CommandHandler("cancel", tech_cancel)],
    )

    status_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("status", status_start)],
        states={AWAITING_TICKET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, status_received)]},
        fallbacks=[CommandHandler("cancel", cancel_booking)],
    )

    # --- Add Handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(booking_conv_handler)
    app.add_handler(tech_reg_conv_handler)
    app.add_handler(status_conv_handler)
    app.add_handler(CommandHandler("myjobs", my_jobs))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("help", help_command))
    # Admin shortcuts
    app.add_handler(CommandHandler("listall", listall))
    app.add_handler(CommandHandler("listnew", listnew))
    app.add_handler(CommandHandler("listassigned", listassigned))
    app.add_handler(CommandHandler("listtechs", listtechs))
    # Admin callbacks
    app.add_handler(CallbackQueryHandler(admin_list_tickets_cb, pattern="^admin_list_tickets$") )
    app.add_handler(CallbackQueryHandler(admin_list_techs_cb, pattern="^admin_list_techs$") )
    app.add_handler(CallbackQueryHandler(admin_approve_tech_cb, pattern=r"^approve_tech_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_assign_ticket_start_cb, pattern=r"^assign_ticket_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_assign_ticket_finalize_cb, pattern=r"^assign_\d+_\d+$"))
    app.add_handler(CommandHandler("searchtickets", searchtickets))
    app.add_handler(CommandHandler("ticketsbycity", ticketsbycity))
    app.add_handler(CommandHandler("ticketsbystate", ticketsbystate))
    app.add_handler(CommandHandler("ticketsbydate", ticketsbydate))
    app.add_handler(CommandHandler("closeticket", closeticket))
    app.add_handler(CommandHandler("reassign", reassign))
    app.add_handler(CommandHandler("ticketdetails", ticketdetails))
    app.add_handler(CommandHandler("userhistory", userhistory))
    app.add_handler(CommandHandler("feedback", feedback))
    app.add_handler(CommandHandler("feedbackbyticket", feedbackbyticket))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("toptechs", toptechs))
    app.add_handler(CommandHandler("pendingapproval", pendingapproval))
    app.add_handler(CommandHandler("bulkassign", bulkassign))
    app.add_handler(CommandHandler("bulkclose", bulkclose))
    app.add_handler(CommandHandler("exporttickets", exporttickets))
    app.add_handler(CommandHandler("exporttechs", exporttechs))

    return app


if __name__ == "__main__":
    app = build_app()
    logger.info("Starting bot in polling mode…")
    app.run_polling()