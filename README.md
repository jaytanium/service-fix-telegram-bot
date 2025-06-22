# HVAC Service Fix Bot

A Telegram bot for booking HVAC/R & WM (Heating, Ventilation, Air Conditioning, Refrigeration & Washing Machine) repair services. This bot allows customers to create service tickets and technicians to manage their assigned jobs.

## Features

- **Customer Features**:
  - Book service tickets for AC, Fridge, Washing Machine, or other appliances
  - Specify location and issue details
  - Track ticket status
  - Provide feedback after service completion

- **Technician Features**:
  - Register as a technician
  - View assigned jobs
  - Update job status
  - Communicate with customers

- **Admin Features**:
  - Assign tickets to technicians
  - Manage technician registrations
  - View all tickets and their statuses
  - Export data to CSV

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with the following variables:
   ```
   BOT_TOKEN=your_telegram_bot_token
   ADMIN_ID=your_telegram_user_id
   ```
4. Run the bot:
   ```
   python service_fix_bot.py
   ```

## Database

The bot uses SQLite to store ticket, technician, and feedback data. The database is automatically created on first run.

## Project Structure

- `service_fix_bot.py`: Main bot code
- `static_data.py`: Contains static data for districts and complaint types
- `requirements.txt`: Project dependencies
- `grant_admin.py`: Utility script to grant admin privileges

## License

This project is licensed under the MIT License - see the LICENSE file for details.