# ❄️ HVAC Service Fix Bot

A Telegram bot for booking **HVAC/R & WM** (Heating, Ventilation, Air Conditioning, Refrigeration & Washing Machine) repair services. This bot allows customers to create service tickets and technicians to manage their assigned jobs.

---

## 🌟 Features

### 👤 Customer Features

* Book service tickets for AC, Fridge, Washing Machine, or other appliances
* Specify location and issue details
* Track ticket status
* Provide feedback after service completion

### 🧑‍🔧 Technician Features

* Register as a technician
* View assigned jobs
* Update job status
* Communicate with customers

### 🛠️ Admin Features

* Assign tickets to technicians
* Manage technician registrations
* View all tickets and their statuses
* Export data to CSV

---

## ⚙️ Setup

1. **Clone this repository**
2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```
3. **Create a `.env` file** with the following variables:

   ```ini
   BOT_TOKEN=your_telegram_bot_token
   ADMIN_ID=your_telegram_user_id
   ```
4. **Run the bot**:

   ```bash
   python service_fix_bot.py
   ```

---

## 🗃️ Database

The bot uses **SQLite** to store ticket, technician, and feedback data. The database is automatically created on first run.

---

## 📁 Project Structure

```
service-fix-bot/
├── service_fix_bot.py      # Main bot code
├── static_data.py          # Contains static data for districts and complaint types
├── requirements.txt        # Project dependencies
├── grant_admin.py          # Utility script to grant admin privileges
└── .env.sample             # Example environment config
```

---

## 👤 Author & Credits

**Created by:** Jayant Jain
**Project Purpose:** This bot was built to simplify HVAC and appliance repair service workflows using Telegram. It empowers customers to book repair jobs easily and helps technicians manage their assigned work in real time. The project is structured to support future enhancements such as AI-based technician assignment, CRM integration, and WhatsApp automation.

---

## 📄 License

This project is licensed under the **MIT License** – see the `LICENSE` file for details.
