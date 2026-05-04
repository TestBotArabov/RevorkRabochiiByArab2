import telebot
import os
import sqlite3
import time
from dotenv import load_dotenv

load_dotenv()

TOKEN = "8614082185:AAEsAEIQgFuJo7z2eXxe2g4Jetxyu4g-8aM"
if not TOKEN:
    raise ValueError("TOKEN не задан!")

bot = telebot.TeleBot(TOKEN)

OWNER_ID = 7925843350

# ================= DB =================
conn = sqlite3.connect("bank.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS credits (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    total INTEGER,
    payment INTEGER,
    last_pay REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS requests (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    amount INTEGER,
    periods INTEGER,
    status TEXT,
    created_at REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    rating INTEGER DEFAULT 5
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT,
    target TEXT,
    timestamp REAL
)
""")

conn.commit()

cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
conn.commit()

# ================= SETTINGS =================
PENALTY_RATE = 0.02
RATING_DROP = 1
DAY_SEC = 86400

# ================= UTILS =================
def is_admin(uid):
    cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return cursor.fetchone() is not None

def log_admin(admin, action, target=""):
    cursor.execute(
        "INSERT INTO admin_logs (admin_id, action, target, timestamp) VALUES (?, ?, ?, ?)",
        (admin, action, target, time.time())
    )
    conn.commit()

def get_rating(user_id, username="no_username"):
    cursor.execute("SELECT rating FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        "INSERT INTO users (user_id, username, rating) VALUES (?, ?, ?)",
        (user_id, username, 5)
    )
    conn.commit()
    return 5

def percent(r):
    if r >= 9: return 0.05
    if r >= 7: return 0.08
    if r >= 5: return 0.10
    if r >= 3: return 0.15
    return 0.20

# ================= OVERDUE SYSTEM =================
def check_overdue():
    now = time.time()

    cursor.execute("SELECT user_id, total, last_pay FROM credits")
    rows = cursor.fetchall()

    for uid, total, last_pay in rows:
        if not last_pay:
            continue

        overdue = int((now - last_pay) // DAY_SEC)

        if overdue > 0:
            penalty = int(total * PENALTY_RATE * overdue)
            new_total = total + penalty

            cursor.execute("""
            UPDATE credits SET total=?, last_pay=? WHERE user_id=?
            """, (new_total, now, uid))

            cursor.execute("SELECT rating FROM users WHERE user_id=?", (uid,))
            r = cursor.fetchone()

            if r:
                new_rating = max(1, r[0] - RATING_DROP * overdue)
                cursor.execute("UPDATE users SET rating=? WHERE user_id=?", (new_rating, uid))

    conn.commit()

# ================= CREDIT =================
@bot.message_handler(commands=['credit'])
def credit(m):
    uid = str(m.from_user.id)
    username = m.from_user.username or "no_username"

    args = m.text.split()
    if len(args) < 3:
        return bot.reply_to(m, "Пример: /credit 10000 7")

    try:
        amount = int(args[1])
        periods = int(args[2])
    except:
        return bot.reply_to(m, "Ошибка ввода")

    if amount <= 0 or periods <= 0:
        return bot.reply_to(m, "Неверные данные")

    cursor.execute("SELECT status FROM requests WHERE user_id=?", (uid,))
    r = cursor.fetchone()
    if r and r[0] == "pending":
        return bot.reply_to(m, "Заявка уже есть")

    cursor.execute("""
    INSERT OR REPLACE INTO requests VALUES (?, ?, ?, ?, ?, ?)
    """, (uid, username, amount, periods, "pending", time.time()))
    conn.commit()

    bot.reply_to(m, "📄 Заявка отправлена")

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    if not is_admin(c.from_user.id):
        return

    if c.data.startswith("approve_"):
        approve(c.data.split("_")[1], c)

    if c.data.startswith("deny_"):
        deny(c.data.split("_")[1], c)

# ================= APPROVE =================
def approve(uid, call):
    cursor.execute("SELECT username, amount, periods, status FROM requests WHERE user_id=?", (uid,))
    r = cursor.fetchone()
    if not r or r[3] != "pending":
        return

    username, amount, periods, _ = r

    rating = get_rating(uid, username)
    p = percent(rating)

    total = int(amount * (1 + p))
    payment = total // periods

    cursor.execute("""
    INSERT OR REPLACE INTO credits VALUES (?, ?, ?, ?, ?)
    """, (uid, username, total, payment, time.time()))

    cursor.execute("UPDATE requests SET status='approved' WHERE user_id=?", (uid,))
    conn.commit()

    bot.send_message(uid,
        f"🏦 ОДОБРЕНО\n⭐ {rating}/10\n💰 {total}\n💳 {payment} за 1 РП"
    )

    log_admin(call.from_user.id, "approve", uid)

    bot.answer_callback_query(call.id, "OK")

# ================= DENY =================
def deny(uid, call):
    cursor.execute("UPDATE requests SET status='rejected' WHERE user_id=?", (uid,))
    conn.commit()

    bot.send_message(uid, "❌ Отклонено")

    log_admin(call.from_user.id, "deny", uid)

    bot.answer_callback_query(call.id, "OK")

# ================= PAY =================
@bot.message_handler(commands=['pay'])
def pay(m):
    uid = str(m.from_user.id)
    args = m.text.split()

    if len(args) < 2:
        return bot.reply_to(m, "Пример: /pay 1000")

    try:
        amount = int(args[1])
    except:
        return bot.reply_to(m, "Ошибка")

    cursor.execute("SELECT total FROM credits WHERE user_id=?", (uid,))
    r = cursor.fetchone()

    if not r:
        return bot.reply_to(m, "Нет кредита")

    total = r[0] - amount

    cursor.execute("""
    UPDATE credits SET total=?, last_pay=? WHERE user_id=?
    """, (max(total, 0), time.time(), uid))

    conn.commit()

    bot.reply_to(m, f"💳 Осталось: {max(total,0)}")

# ================= TOP =================
@bot.message_handler(commands=['top'])
def top(m):
    cursor.execute("SELECT user_id, username, rating FROM users ORDER BY rating DESC LIMIT 10")
    rows = cursor.fetchall()

    text = "🏆 ТОП:\n\n"

    i = 1
    for uid, name, r in rows:
        text += f"{i}. @{name} — ⭐ {r}\n"
        i += 1

    bot.reply_to(m, text)

# ================= LOGS =================
@bot.message_handler(commands=['logs'])
def logs(m):
    if not is_admin(m.from_user.id):
        return

    args = m.text.split()

    query = "SELECT admin_id, action, target, timestamp FROM admin_logs"
    params = []

    if len(args) > 1:
        query += " WHERE action LIKE ? OR target LIKE ?"
        params = [f"%{args[1]}%", f"%{args[1]}%"]

    query += " ORDER BY id DESC LIMIT 10"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    text = "📜 ЛОГИ:\n\n"

    for a, ac, t, ts in rows:
        tm = time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
        text += f"{a} | {ac} | {t} | {tm}\n\n"

    bot.reply_to(m, text)

# ================= OVERDUE LOOP =================
def loop():
    while True:
        try:
            check_overdue()
            bot.polling(none_stop=True)
        except Exception as e:
            print(e)
            time.sleep(5)

# ================= START =================
if __name__ == "__main__":
    print("BOT STARTED")
    loop()
