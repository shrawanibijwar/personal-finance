from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from decimal import Decimal
import os, math, io, csv
import joblib
import numpy as np
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from apscheduler.schedulers.background import BackgroundScheduler
from flask_mail import Mail, Message



app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


from flask.json.provider import DefaultJSONProvider
from decimal import Decimal

class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

app.json = CustomJSONProvider(app)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

mail = Mail(app)

# ---- Load ML Model ----
model = None
try:
    model_path = os.path.join(os.path.dirname(__file__), 'models', 'expense_predictor.pkl')
    if os.path.exists(model_path):
        model = joblib.load(model_path)
except Exception as e:
    print(f"Warning: Could not load expense prediction model: {e}")

# ---- Models ----
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100))
    occupation = db.Column(db.String(100))
    monthly_income = db.Column(db.Numeric(12,2), default=0)
    current_savings = db.Column(db.Numeric(12,2), default=0)
    is_admin = db.Column(db.Boolean, default=False)

    expenses = db.relationship('Expense', backref='user', lazy=True, cascade="all, delete-orphan")
    goals = db.relationship('Goal', backref='user', lazy=True, cascade="all, delete-orphan")
    loans = db.relationship('Loan', back_populates='user', lazy=True, cascade="all, delete-orphan")



    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)




class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255))
    category = db.Column(db.String(100))
    amount = db.Column(db.Numeric(12,2), nullable=False)
    frequency = db.Column(db.Enum('daily','monthly','yearly'), nullable=False)
    description = db.Column(db.String(500))
    date_recorded = db.Column(db.Date, default=date.today)
    is_auto = db.Column(db.Boolean, default=False)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
        

    

class Goal(db.Model):
    __tablename__ = 'goals'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    date_created = db.Column(db.Date, default=date.today)
    # numeric priority (1 = highest). Default 5 if not provided.
    priority = db.Column(db.Integer, default=5, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

from datetime import datetime
class Loan(db.Model):
    __tablename__ = 'loans'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    principal = db.Column(db.Numeric(12,2), nullable=False)
    annual_rate = db.Column(db.Numeric(5,4), nullable=False)
    years = db.Column(db.Integer, nullable=False)
    monthly_emi = db.Column(db.Numeric(12,2), nullable=False)
    active = db.Column(db.Boolean, default=True)
    date_added = db.Column(db.Date, default=date.today)
    emi_day = db.Column(db.Integer, default=1)
    last_added = db.Column(db.Date, nullable=True)
    total_months = db.Column(db.Integer)
    paid_months = db.Column(db.Integer, default=0)
    user = db.relationship('User', back_populates='loans')
 
class Review(db.Model):
    __tablename__ = 'review'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    rating = db.Column(db.Integer, default=0)
    text = db.Column(db.String(200))

    date_posted = db.Column(db.DateTime, default=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user = db.relationship('User', backref='reviews')


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    message = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='notifications')


# ---- Helpers ----
def monthly_expense_total(user):
    expenses = Expense.query.filter_by(
        user_id=user.id,
        month=date.today().month,
        year=date.today().year
    ).all()

    total = Decimal('0.0')
    categories = {}

    for e in expenses:
        amt = e.amount

        if e.frequency == 'daily':
            m = amt * Decimal('30.44')
        elif e.frequency == 'yearly':
            m = amt / Decimal('12')
        else:
            m = amt

        total += m

        key = e.category or 'Other'
        categories[key] = categories.get(key, Decimal('0')) + m

    return float(total), {k: float(v) for k, v in categories.items()}


def predict_goals_sequential(user, monthly_saving, current_savings):
    """
    Sequential prediction algorithm using numeric priorities:
    - Sort goals by numeric priority ascending (1 = highest), then by creation date.
    - Use current_savings as immediate pot applied to the first goals in order.
    - For remaining amount, compute fractional months needed = remaining / monthly_saving.
      Convert months to days using average month length (30.44 days).
    - Next goal's start_date = previous goal end_date.
    Returns list of dicts with start_date, end_date, status, progress_percent.
    """
    results = []
    # Fetch goals and sort by numeric priority then date_created then id
    goals = sorted(
        Goal.query.filter_by(user_id=user.id).all(),
        key=lambda g: (g.priority if g.priority is not None else 9999, g.date_created, g.id)
    )

    start_dt = datetime.today()
    remaining_pot = float(current_savings or 0.0)
    avg_days_per_month = 30.44  # realistic month average

    # Snapshot to show how much of each goal is already covered by current savings (progress)
    original_savings = remaining_pot

    for g in goals:
        target = float(g.target_amount)
        # progress percent based on original_savings towards the specific goal (bounded to 100)
        if original_savings <= 0:
            progress_percent = 0.0
        else:
            progress_percent = min(100.0, (original_savings / target) * 100.0)

        # Compute achieved_amount safely with Decimal
        target_decimal = Decimal(target).quantize(Decimal('0.01'))
        progress_decimal = Decimal(str(progress_percent)).quantize(Decimal('0.01'))
        achieved_amount = (target_decimal * progress_decimal / Decimal('100')).quantize(Decimal('0.01'))

        if target <= 0:
            end_dt = start_dt
            status = "Invalid target"
        else:
            # If current pot covers the goal immediately:
            if remaining_pot >= target:
                end_dt = start_dt  # achieved now
                status = f"✅ You can afford '{g.title}' now!"
                remaining_pot -= target
            else:
                # use whatever remains from the pot
                still_needed = target - remaining_pot
                if monthly_saving <= 0:
                    end_dt = None
                    status = f"❌ Can't predict '{g.title}' (no monthly savings)."
                    remaining_pot = 0.0
                else:
                    months_needed = still_needed / monthly_saving  # fractional months allowed
                    days_needed = months_needed * avg_days_per_month
                    # ensure at least 1 day if something is needed
                    days_needed = max(1.0, days_needed)
                    end_dt = start_dt + timedelta(days=days_needed)
                    remaining_pot = 0.0
                    status = f"⏳ Predicted by {end_dt.strftime('%d-%m-%Y')}"
        results.append({
            'goal': g,
            'start_date': start_dt,
            'end_date': end_dt,
            'status': status,
            'progress_percent': round(progress_percent, 2),
            'achieved_amount': float(achieved_amount),
            'priority': int(g.priority or 0)
        })
        # Next goal starts at end_dt (if end_dt is None, future goals cannot be predicted - keep same start)
        last_end = results[-1]['end_date']
        if last_end:
            # next goal starts right after this end
            start_dt = last_end
        else:
            # cannot predict further; keep start_dt unchanged (or break if you prefer)
            start_dt = last_end or start_dt

    return results


def generate_ai_insights(user):
    """Generate structured financial insights"""

    monthly_total, categories = monthly_expense_total(user)
    income = float(user.monthly_income or 0)
    expenses_list = Expense.query.filter_by(user_id=user.id).all()

    # Check if data exists
    if income == 0 or len(expenses_list) == 0:
        return "Please add income and expenses to get insights."

    expense_total = float(monthly_total)

    # Highest category
    if categories:
        highest_name, highest_amt = max(categories.items(), key=lambda x: x[1])
    else:
        highest_name, highest_amt = "Other", 0

    # Calculations
    net = income - expense_total
    savings_rate = (net / income * 100) if income > 0 else 0

    # Summary
    summary = f"Your income is ₹{income:.0f} and expenses are ₹{expense_total:.0f}. You are saving ₹{net:.0f} this month."

    # Insights
    insights = [
        f"Highest spending category is {highest_name} (₹{highest_amt:.0f})",
    ]

    if expense_total > income * 0.7:
        insights.append("You are spending a high portion of your income")
    else:
        insights.append("Your spending is under control")

    insights.append(f"Your savings rate is {savings_rate:.1f}%")

    # Suggestions
    suggestions = [
        "Set a monthly budget for each category",
        "Reduce spending in high-expense areas",
        "Track daily expenses regularly",
        "Try saving at least 20% of your income"
    ]

    # Final formatted output
    result = "Summary:\n"
    result += summary + "\n\n"

    result += "Insights:\n"
    for i in insights:
        result += f"- {i}\n"

    result += "\nSuggestions:\n"
    for s in suggestions:
        result += f"- {s}\n"

    return result
def get_active_loans(user):
    return Loan.query.filter_by(user_id=user.id, active=True).all()

def detect_recurring_expenses(user):
    from collections import defaultdict
    from datetime import date

    today = date.today()

    expenses = Expense.query.filter_by(user_id=user.id).all()

    grouped = defaultdict(list)

    for e in expenses:
        if (e.title and e.title.lower() == "loan emi") or e.is_auto:
            continue

        key = (e.title.lower().strip(), float(e.amount))
        grouped[key].append(e)

    recurring = []

    for (title_key, amount), exps in grouped.items():

        if len(exps) >= 3:

            original_title = exps[0].title  # ✅ FIX: use original title

            existing = Expense.query.filter(
                Expense.user_id == user.id,
                Expense.title == original_title,
                Expense.month == today.month,
                Expense.year == today.year,
                Expense.is_auto == True
            ).first()

            if existing:
                continue

            recurring.append({
                "title": original_title,
                "amount": amount,
                "category": exps[0].category
            })

    return recurring

def decimal_emi(P, R, Y):
    monthly_rate = R / Decimal('12')
    n = Y * 12

    if monthly_rate == 0:
        return P / n

    emi = P * monthly_rate * (1 + monthly_rate) ** n / ((1 + monthly_rate) ** n - 1)
    return emi.quantize(Decimal('0.01'))


def send_emi_email(user, loan):
    try:
        msg = Message(
            subject="EMI Reminder",
            sender=app.config['MAIL_USERNAME'],
            recipients=[user.email]
        )

        msg.body = f"""
Hello {user.name},

Reminder: Your EMI of ₹{loan.monthly_emi} is due in 2 days (Day {loan.emi_day}).

Please ensure sufficient balance.

- Finance Tracker
"""

        mail.send(msg)
        print(f"✅ Email sent to {user.email}")

    except Exception as e:
        print(" Email Error:", e)



def emi_due_reminder():
    with app.app_context():
        print("🔔 Running EMI reminder job...")

        today = date.today()
        users = User.query.all()

        for user in users:
            loans = Loan.query.filter_by(user_id=user.id, active=True).all()

            for loan in loans:
                emi_day = loan.emi_day if loan.emi_day else 1

                # 👉 Calculate next EMI date (this month)
                try:
                    emi_date = date(today.year, today.month, emi_day)
                except ValueError:
                    # Handle invalid date (like Feb 30)
                    continue

                # 👉 Days left
                days_left = (emi_date - today).days

                # ✅ CONDITION: 2 days before EMI
                if days_left == 2:

                    message_text = f"📢 Reminder: Your EMI of ₹{loan.monthly_emi} is due in 2 days!"

                    # 🚫 Prevent duplicate reminder
                    existing_notif = Notification.query.filter_by(
                        user_id=user.id,
                        message=message_text
                    ).first()

                    if existing_notif:
                        continue

                    # 📩 Send email
                    send_emi_email(user, loan)

                    # 🔔 Add notification
                    notif = Notification(
                        user_id=user.id,
                        message=message_text
                    )
                    db.session.add(notif)

        db.session.commit()
        print("✅ EMI reminder job completed")

        



# ---- Routes ----

#===== AUTO EMI ====
def auto_add_all_emis():
    with app.app_context():

        today = date.today()
        users = User.query.all()

        for user in users:
            loans = Loan.query.filter_by(
                user_id=user.id,
                active=True
            ).all()

            for loan in loans:

                emi_day = loan.emi_day or 1

                # EMI date not reached yet
                if today.day < emi_day:
                    continue

                # Prevent duplicate EMI for same loan same month
                if loan.last_added:
                    if (
                        loan.last_added.month == today.month and
                        loan.last_added.year == today.year
                    ):
                        continue

                # Unique EMI title per loan
                emi_title = f"Loan EMI #{loan.id}"

                # Double safety check
                existing = Expense.query.filter_by(
                    user_id=user.id,
                    title=emi_title,
                    month=today.month,
                    year=today.year,
                    is_auto=True
                ).first()

                if existing:
                    continue

                # Add EMI expense
                emi_expense = Expense(
                    user_id=user.id,
                    title=emi_title,
                    category="Bills",
                    amount=loan.monthly_emi,
                    frequency="monthly",
                    description=f"Auto EMI for Loan #{loan.id}",
                    is_auto=True,
                    month=today.month,
                    year=today.year
                )

                db.session.add(emi_expense)

                # Update progress
                loan.paid_months = (loan.paid_months or 0) + 1
                loan.last_added = today

                # Loan completed
                if loan.paid_months >= loan.total_months:
                    loan.active = False

                    notif = Notification(
                        user_id=user.id,
                        message=f"🎉 Loan #{loan.id} fully paid!"
                    )
                    db.session.add(notif)

                else:
                    notif = Notification(
                        user_id=user.id,
                        message=f"⚠️ EMI ₹{loan.monthly_emi} deducted for Loan #{loan.id}"
                    )
                    db.session.add(notif)

        db.session.commit()

def auto_add_recurring_expenses():
    with app.app_context():
        print("🔄 Running recurring expense job...")

        today = date.today()
        users = User.query.all()

        for user in users:

            recurring_expenses = detect_recurring_expenses(user)

            for exp in recurring_expenses:

                # Check already added this month
                existing = Expense.query.filter(
                    Expense.user_id == user.id,
                    Expense.title == exp["title"],
                    db.extract('month', Expense.date_recorded) == today.month,
                    db.extract('year', Expense.date_recorded) == today.year
                ).first()

                if existing:
                    continue

                new_exp = Expense(
                user_id=user.id,
                title=exp["title"],
                category=exp["category"],
                amount=exp["amount"],
                frequency="monthly",
                description="Auto-detected recurring",
                is_auto=True,
                month=today.month,
                year=today.year
                )

                db.session.add(new_exp)

                # Notification
                notif = Notification(
                    user_id=user.id,
                    message=f"🔁 Auto-added: {exp['title']} ₹{exp['amount']}"
                )
                db.session.add(notif)

        db.session.commit()
        print("✅ Recurring expense job completed")




@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        pw = request.form['password']
        name = request.form.get('name','').strip()
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
        u = User(email=email, name=name)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        flash('Registered! Login now.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        email = request.form['email'].lower().strip()
        pw = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(pw):
            login_user(user)
            session['user_id'] = user.id
            flash('Logged in', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out', 'info')
    return redirect(url_for('home'))



@app.route('/notification/delete/<int:notif_id>', methods=['POST'])
@login_required
def delete_notification(notif_id):
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if notif:
        db.session.delete(notif)
        db.session.commit()
    return redirect(url_for('dashboard'))



@app.route('/chatbot')
@login_required
def chatbot():
    return "<h2>AI Assistant Coming Soon 🤖</h2>"






# ---- Admin routes ----
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username'].strip().lower()
        pw = request.form['password']

        # Find user from database
        user = User.query.filter_by(email=username).first()

        # Check password + admin access
        if user and user.check_password(pw) and user.is_admin:
            login_user(user)
            session['is_admin'] = True
            flash('Admin logged in successfully!', 'success')
            return redirect(url_for('admin_panel'))

        flash('Invalid admin credentials', 'danger')

    return render_template('admin_login.html')

@app.route('/admin/panel')
def admin_panel():
    if not session.get('is_admin'):
        flash('Unauthorized', 'danger')
        return redirect(url_for('admin_login'))
    users = User.query.all()
    return render_template('admin_panel.html', users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('is_admin'):
        flash('Unauthorized', 'danger')
        return redirect(url_for('admin_login'))
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.email} deleted successfully.', 'success')
    except:
        db.session.rollback()
        flash('Error deleting user.', 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/test-recurring')
def test_recurring():
    auto_add_recurring_expenses()
    return "Recurring job executed!"


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    user = current_user

    # ---- BASIC DATA ----
    monthly_total, categories = monthly_expense_total(user)
    monthly_income = float(user.monthly_income or 0)
    current_savings = float(user.current_savings or 0)

    notifications = Notification.query.filter_by(
        user_id=user.id,
        is_read=False
    ).all()

    loan_result = session.pop('loan_result', None)

    # ---- AI INSIGHTS ----
    ai_predicted_expense = None
    ai_insights = generate_ai_insights(user)

    if model is not None:
        try:
            X_input = np.array([[monthly_income, current_savings, monthly_total]])
            pred = model.predict(X_input)
            ai_predicted_expense = max(0, round(float(pred[0]), 2))
        except Exception as e:
            print("AI Prediction Error:", e)

    # ---- LOAN FORM SUBMIT ----
    if request.method == 'POST' and 'loan_submit' in request.form:
        try:
            principal_str = request.form.get('principal', '').strip()
            annual_rate_str = request.form.get('annual_rate', '').strip()
            years_str = request.form.get('years', '').strip()

            if principal_str == '' or annual_rate_str == '' or years_str == '':
                flash("Fill all fields", "warning")
                return redirect(url_for('dashboard'))

            P = Decimal(principal_str)
            R = Decimal(annual_rate_str) / Decimal('100')
            Y = int(years_str)

            monthly_emi = decimal_emi(P, R, Y)

            new_loan = Loan(
                user_id=user.id,
                principal=P,
                annual_rate=R,
                years=Y,
                monthly_emi=monthly_emi,
                active=True,
                total_months=Y * 12,
                paid_months=0,
                emi_day=1,
                last_added=None
            )

            db.session.add(new_loan)
            db.session.commit()

            today = date.today()

            # First EMI instantly added
            emi_expense = Expense(
                user_id=user.id,
                title=f"Loan EMI #{new_loan.id}",
                category="Bills",
                amount=monthly_emi,
                frequency="monthly",
                description="First EMI instantly added",
                is_auto=True,
                month=today.month,
                year=today.year
            )
             
            db.session.add(emi_expense)

            new_loan.last_added = today
            new_loan.paid_months = 1

            db.session.commit()

            flash("Loan added successfully!", "success")
            return redirect(url_for('dashboard'))

        except Exception as ex:
            db.session.rollback()
            flash(str(ex), "danger")
            return redirect(url_for('dashboard'))
    

    # ---- LOAN DATA ----
    active_loans = get_active_loans(user)
    total_emi = sum(float(l.monthly_emi) for l in active_loans)

    # ---- EXPENSE & GOALS ----
    today = date.today()

    expenses = Expense.query.filter_by(
        user_id=user.id,
        month=today.month,
        year=today.year
    ).all()
    goals = Goal.query.filter_by(user_id=user.id).all()

    monthly_savings = monthly_income - monthly_total
    predicted_goals = predict_goals_sequential(user, monthly_savings, current_savings)

    expense_labels = list(categories.keys())
    expense_values = list(categories.values())

    # ---- WARNING ----
    warning = None
    if monthly_total > monthly_income:
        warning = "⚠️ You are overspending!"

    # ---- FINAL RETURN ----
    return render_template('dashboard.html',
        monthly_income=monthly_income,
        monthly_expenses=monthly_total,
        categories=categories,
        total_emi=total_emi,
        active_loans=active_loans,
        current_savings=current_savings,
        user=user,
        expenses=expenses,
        expense_labels=expense_labels,
        expense_values=expense_values,
        warning=warning,
        goals=goals,
        predicted_goals=predicted_goals,
        loan_result=loan_result,
        ai_predicted_expense=ai_predicted_expense,
        notifications=notifications,
        ai_insights=ai_insights
    )


@app.route('/test-emi')
def test_emi():
    auto_add_all_emis()
    return "EMI Added Successfully"



# ---- Add / Delete Goal & Expense ----
@app.route('/goal/add', methods=['POST'])
@login_required
def add_goal():
    title = request.form.get('goal_title', '').strip()
    try:
        target = float(request.form.get('goal_amount', 0))
    except:
        target = 0.0
    # parse priority (integer). Default to 5 if missing or invalid.
    try:
        priority = int(request.form.get('priority', 5))
    except:
        priority = 5

    if not title or target <= 0:
        flash('Please enter a valid goal and amount.', 'warning')
        return redirect(url_for('dashboard'))
    try:
        goal = Goal(user_id=current_user.id, title=title, target_amount=target, priority=priority)
        db.session.add(goal)
        db.session.commit()
        flash('Goal added successfully!', 'success')
    except Exception as ex:
        db.session.rollback()
        flash('Error adding goal: ' + str(ex), 'danger')
    return redirect(url_for('dashboard'))

@app.route('/goal/delete/<int:goal_id>', methods=['POST'])
@login_required
def delete_goal(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=current_user.id).first()
    if not goal:
        flash('Goal not found.', 'danger')
        return redirect(url_for('dashboard'))
    try:
        db.session.delete(goal)
        db.session.commit()
        flash('Goal deleted!', 'warning')
    except:
        db.session.rollback()
        flash('Error deleting goal.', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/expenses/add', methods=['POST'])
@login_required
def add_expense():
    try:
        title = request.form.get('title','').strip()
        category = request.form.get('category', '').strip() or 'Other'
        amount = Decimal(request.form.get('amount', '0'))  # ✅ FIX
        frequency = request.form.get('frequency','monthly')
        desc = request.form.get('description','')
        today = date.today()

        e = Expense(
            user_id=current_user.id,
            title=title,
            category=category,
            amount=amount,
            frequency=frequency,
            description=desc,
            month=today.month,
            year=today.year
        )

        db.session.add(e)
        db.session.commit()
        flash('Expense added', 'success')

    except Exception as ex:
        db.session.rollback()
        flash('Error adding expense: ' + str(ex), 'danger')

    return redirect(url_for('dashboard'))

@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
    if not expense:
        flash('Expense not found', 'danger')
        return redirect(url_for('dashboard'))
    try:
        db.session.delete(expense)
        db.session.commit()
        flash('Expense deleted!', 'warning')
    except:
        db.session.rollback()
        flash('Error deleting expense', 'danger')
    return redirect(url_for('dashboard'))



@app.route('/loans/delete/<int:loan_id>', methods=['POST'])
@login_required
def delete_loan(loan_id):
    loan = Loan.query.filter_by(
        id=loan_id,
        user_id=current_user.id
    ).first()

    if not loan:
        flash('Loan not found.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        # delete auto EMI expenses
        Expense.query.filter_by(
            user_id=current_user.id,
            title="Loan EMI",
            is_auto=True
        ).delete()

        db.session.delete(loan)
        db.session.commit()

        flash('Loan deleted and EMI records removed.', 'success')

    except Exception:
        db.session.rollback()
        flash('Error deleting loan.', 'danger')

    return redirect(url_for('dashboard'))

@app.route('/notification/read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if notif:
        notif.is_read = True
        db.session.commit()
    return redirect(url_for('dashboard'))   


@app.route('/expenses/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):

    expense = Expense.query.filter_by(
        id=expense_id,
        user_id=current_user.id
    ).first()

    if not expense:
        flash('Expense not found', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            expense.title = request.form.get('title', '').strip()
            expense.category = request.form.get('category', '').strip() or "Other"
            expense.amount = Decimal(request.form.get('amount', '0'))
            expense.frequency = request.form.get('frequency', 'monthly')
            expense.description = request.form.get('description', '')

            db.session.commit()
            flash('Expense updated successfully!', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating expense: {str(e)}', 'danger')

        return redirect(url_for('dashboard'))

    return render_template('edit_expense.html', expense=expense)     


# ---- Export CSV / PDF ----
@app.route('/export_csv')
@login_required
def export_csv():
    si = io.StringIO()
    cw = csv.writer(si)

    # Expenses
    cw.writerow(['Expenses'])
    cw.writerow(['Title', 'Amount', 'Frequency', 'Description', 'Date'])
    expenses = Expense.query.filter_by(user_id=current_user.id).all()
    for e in expenses:
        cw.writerow([e.title, float(e.amount), e.frequency, e.description or '', e.date_recorded.strftime('%d-%m-%Y')])

    # Goals with predictions (use same helper)
    cw.writerow([])
    cw.writerow(['Goals'])
    cw.writerow(['Title', 'Target Amount', 'Date Created', 'Priority', 'Predicted Completion', 'Status'])

    monthly_income = float(current_user.monthly_income or 0)
    current_savings = float(current_user.current_savings or 0)
    monthly_total, _ = monthly_expense_total(current_user)
    monthly_savings = monthly_income - monthly_total

    predicted = predict_goals_sequential(current_user, monthly_savings, current_savings)
    for p in predicted:
        g = p['goal']
        pred_end = p['end_date'].strftime('%d-%m-%Y') if p['end_date'] else 'N/A'
        cw.writerow([g.title, float(g.target_amount), g.date_created.strftime('%d-%m-%Y'), int(g.priority or 0), pred_end, p['status']])

    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8'))
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name='dashboard.csv')


@app.route('/export_pdf')
@login_required
def export_pdf():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph(f"{current_user.name}'s Dashboard", styles['Title']))

    # Expenses Table
    expenses = Expense.query.filter_by(user_id=current_user.id).all()
    data = [['Title','Amount','Frequency','Description','Date']]
    for e in expenses:
        data.append([e.title, float(e.amount), e.frequency, e.description or '', e.date_recorded.strftime('%d-%m-%Y')])
    t=Table(data, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#0d6efd")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(1,1),(-1,-1),[colors.whitesmoke, colors.lightgrey])
    ]))
    elements.append(Paragraph("Expenses", styles['Heading2']))
    elements.append(t)

    # Goals Table with predicted completion from helper
    monthly_income = float(current_user.monthly_income or 0)
    current_savings = float(current_user.current_savings or 0)
    monthly_total, _ = monthly_expense_total(current_user)
    monthly_savings = monthly_income - monthly_total

    predicted = predict_goals_sequential(current_user, monthly_savings, current_savings)
    data = [['Title','Target Amount','Date Created','Priority','Predicted Completion','Status']]
    for p in predicted:
        g = p['goal']
        pred_end = p['end_date'].strftime('%d-%m-%Y') if p['end_date'] else 'N/A'
        data.append([g.title, float(g.target_amount), g.date_created.strftime('%d-%m-%Y'), int(g.priority or 0), pred_end, p['status']])

    t=Table(data, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#198754")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(1,1),(-1,-1),[colors.whitesmoke, colors.lightgrey])
    ]))
    elements.append(Paragraph("Goals", styles['Heading2']))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='dashboard.pdf', mimetype='application/pdf')


# ---- Clear Dashboard ----
@app.route('/dashboard/clear', methods=['POST'])
@login_required
def clear_dashboard():
    try:
        Expense.query.filter_by(user_id=current_user.id).delete()
        Goal.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        flash('All your dashboard data cleared.', 'info')
    except:
        db.session.rollback()
        flash('Error clearing dashboard data', 'danger')
    return redirect(url_for('dashboard'))

# ---- Profile Update ----
@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    occ = request.form.get('occupation','').strip()
    income = request.form.get('monthly_income') or '0'
    savings = request.form.get('current_savings') or '0'

    try:
        current_user.occupation = occ
        current_user.monthly_income = Decimal(income)  # ✅ FIX
        current_user.current_savings = Decimal(savings)  # ✅ FIX

        db.session.commit()
        flash('Profile updated', 'success')

    except Exception:
        db.session.rollback()
        flash('Error updating profile', 'danger')

    return redirect(url_for('dashboard'))

# ---- About & Review ----
@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/review')
def review():
    reviews = Review.query.order_by(Review.date_posted.desc()).all()
    return render_template('review.html', reviews=reviews)

@app.route('/add_review', methods=['GET', 'POST'])
@login_required
def add_review():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:100] or 'Anonymous'
        try:
            rating = max(1, min(5, int(request.form.get('rating', 3))))
        except ValueError:
            flash("Invalid rating!", "danger")
            return redirect(url_for('add_review'))
        text = request.form['text'].strip()

        if not text:
            flash("Please enter review text!", "danger")
            return redirect(url_for('add_review'))

        new_review = Review(
            name=name,
            rating=rating,
            text=text,
            user_id=current_user.id
        )
        db.session.add(new_review)
        db.session.commit()
        flash("Review submitted successfully!", "success")
        return redirect(url_for('review'))

    return render_template('add_review.html')

@app.route('/delete_review/<int:review_id>', methods=['POST'])
@login_required
def delete_review(review_id):
    review = Review.query.get_or_404(review_id)
    if review.user_id != current_user.id:
        flash('Unauthorized to delete this review', 'danger')
        return redirect(url_for('review'))
    
    try:
        db.session.delete(review)
        db.session.commit()
        flash('Review deleted successfully!', 'success')
    except Exception:
        db.session.rollback()
        flash('Error deleting review', 'danger')
    return redirect(url_for('review'))



scheduler = None

def start_scheduler():
    global scheduler

    # Prevent duplicate scheduler
    if scheduler and scheduler.running:
        print("⚠️ Scheduler already running")
        return

    scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={
            'max_instances': 1,
            'coalesce': True
        }
    )

    # EMI Auto Add
    scheduler.add_job(
        func=auto_add_all_emis,
        trigger="cron",
        hour=0,
        minute=1,
        id="emi_job",
        replace_existing=True
    )

    # Recurring Expenses
    scheduler.add_job(
        func=auto_add_recurring_expenses,
        trigger="cron",
        day=1,
        hour=0,
        minute=5,
        id="recurring_job",
        replace_existing=True
    )

    # EMI Reminder
    scheduler.add_job(
        func=emi_due_reminder,
        trigger="cron",
        hour=0,
        minute=2,
        id="reminder_job",
        replace_existing=True
    )

    scheduler.start()
    print(" Scheduler started safely")



# ---- DB / App setup ----
with app.app_context():
    db.create_all()


# ---- Scheduler (ONLY for local) ----
if os.environ.get("RENDER") != "true":
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()


# ---- Run locally only ----
if __name__ == "__main__":
    app.run(debug=True)




