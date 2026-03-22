import os
import datetime
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import plaid
import json
import jwt as pyjwt
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
import stripe
import requests

app = Flask(__name__)
CORS(app)



# ── App config ────────────────────────────────────────────────────────────────
database_url = os.environ.get("DATABASE_URL", "sqlite:///users.db")
# Render uses postgres:// but SQLAlchemy needs postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY environment variable is required. Set it in Render dashboard.")

# Fix stale/dropped connections (SSL error: decryption failed or bad record mac)
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,        # Test connections before using them
    "pool_recycle": 300,           # Recycle connections every 5 minutes
    "pool_size": 5,
    "max_overflow": 10,
}

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ── JWT Authentication ────────────────────────────────────────────────────────
JWT_SECRET = app.config["SECRET_KEY"]
JWT_EXPIRY_HOURS = 72  # Tokens last 3 days


def generate_token(user_id):
    """Create a JWT token for a user."""
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.datetime.utcnow(),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_token(token):
    """Decode and verify a JWT token. Returns user_id or None."""
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("user_id")
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator that requires a valid JWT token in the Authorization header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.split("Bearer ", 1)[1]
        user_id = verify_token(token)
        if user_id is None:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.user_id = str(user_id)
        return f(*args, **kwargs)
    return decorated

# ── Database models ───────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    plaid_access_token = db.Column(db.String(255), nullable=True)
    plaid_item_id      = db.Column(db.String(255), nullable=True)
    plaid_reauth_required     = db.Column(db.Boolean, default=False)
    plaid_new_accounts        = db.Column(db.Boolean, default=False)
    is_subscribed          = db.Column(db.Boolean, default=False)
    subscription_end       = db.Column(db.DateTime, nullable=True)
    stripe_customer_id     = db.Column(db.String(100), nullable=True)
    paypal_subscription_id = db.Column(db.String(100), nullable=True)
    profile_picture        = db.Column(db.Text, nullable=True)  # base64 JPEG, max ~200KB


def find_user_by_id(user_id):
    """Safely look up a user by integer ID or username string.
    Handles PostgreSQL type strictness (can't compare int column to string)."""
    if user_id is None:
        return None
    user_id = str(user_id).strip()
    if not user_id:
        return None
    # Try integer ID first
    try:
        uid_int = int(user_id)
        user = User.query.filter(User.id == uid_int).first()
        if user:
            return user
    except (ValueError, TypeError):
        pass
    # Fall back to username match
    return User.query.filter(User.username == user_id).first()


#===============================================================================
# Stripe and Paypal
# ==============================================================================
# ── Stripe setup ──────────────────────────────────────────────────────────────
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID       = os.getenv("STRIPE_PRICE_ID")
 
# ── PayPal setup ──────────────────────────────────────────────────────────────
PAYPAL_CLIENT_ID     = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_PLAN_ID       = os.getenv("PAYPAL_PLAN_ID")
PAYPAL_API_BASE      = os.getenv("PAYPAL_API_BASE", "https://api-m.paypal.com")  # Live by default
 
def get_paypal_access_token():
    import base64
    credentials = base64.b64encode(
        f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data="grant_type=client_credentials",
        timeout=10
    )
    return resp.json().get("access_token")
 

 
# ── Check subscription status ─────────────────────────────────────────────────
@app.route("/subscription/status", methods=["GET"])
@require_auth
def subscription_status():
    user_id = request.args.get("user_id")
    user = find_user_by_id(user_id)
    if not user:
        return jsonify({"subscribed": False}), 404

    cancel_at_period_end = False

    # Check if subscription_end has passed
    if user.is_subscribed and user.subscription_end:
        if datetime.datetime.utcnow() > user.subscription_end:
            user.is_subscribed = False
            db.session.commit()

    # Check Stripe for current state
    if user.stripe_customer_id:
        try:
            subs = stripe.Subscription.list(
                customer=user.stripe_customer_id, limit=5
            )
            valid_statuses = {"active", "trialing", "past_due"}
            valid_sub = next(
                (s for s in subs.data if s.status in valid_statuses), None
            )
            if valid_sub:
                user.is_subscribed = True
                user.subscription_end = datetime.datetime.utcfromtimestamp(
                    valid_sub.current_period_end
                )
                cancel_at_period_end = bool(valid_sub.cancel_at_period_end)
                db.session.commit()
            elif not user.is_subscribed:
                pass  # Already not subscribed
        except Exception as e:
            print(f"[SUB STATUS] Stripe check failed: {e}")

    return jsonify({
        "subscribed":           user.is_subscribed or False,
        "subscription_end":     user.subscription_end.isoformat() if user.subscription_end else None,
        "cancel_at_period_end": cancel_at_period_end,
    })


# ── Debug: check what Stripe knows about a user ─────────────────────────────
@app.route("/subscription/debug", methods=["GET"])
@require_auth
def subscription_debug():
    user_id = request.args.get("user_id")
    user = find_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    debug = {
        "user_id":            user.id,
        "username":           user.username,
        "is_subscribed":      user.is_subscribed or False,
        "stripe_customer_id": user.stripe_customer_id,
        "subscription_end":   user.subscription_end.isoformat() if user.subscription_end else None,
        "stripe_subscriptions": [],
        "stripe_error": None,
    }

    if user.stripe_customer_id:
        try:
            subs = stripe.Subscription.list(
                customer=user.stripe_customer_id, limit=10
            )
            for s in subs.data:
                debug["stripe_subscriptions"].append({
                    "id": s.id,
                    "status": s.status,
                    "current_period_end": datetime.datetime.utcfromtimestamp(
                        s.current_period_end
                    ).isoformat(),
                })
        except Exception as e:
            debug["stripe_error"] = str(e)
    else:
        debug["stripe_error"] = "No stripe_customer_id on user record"

    return jsonify(debug)
 
 
# ── Stripe: create checkout session ──────────────────────────────────────────
@app.route("/subscription/stripe/create-session", methods=["POST"])
@require_auth
def stripe_create_session():
    try:
        data    = request.get_json() or {}
        user_id = str(data.get("user_id", ""))
        user    = find_user_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
 
        # Create or reuse Stripe customer
        if user.stripe_customer_id:
            customer_id = user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=user.email if "@" in (user.email or "") else None,
                metadata={"user_id": str(user.id)}
            )
            user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id
 
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url="https://evernest-swz9.onrender.com/subscription/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://evernest-swz9.onrender.com/subscription/cancel",
            metadata={"user_id": str(user.id)},
        )
        return jsonify({"url": session.url})
 
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400
 
 
# ── Stripe: webhook ───────────────────────────────────────────────────────────
@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload   = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
 
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400
 
    if event["type"] == "invoice.paid":
        invoice     = event["data"]["object"]
        customer_id = invoice.get("customer")
        period_end  = invoice.get("lines", {}).get("data", [{}])[0].get(
            "period", {}).get("end")
 
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.is_subscribed    = True
            user.subscription_end = (
                datetime.datetime.utcfromtimestamp(period_end)
                if period_end else
                datetime.datetime.utcnow() + datetime.timedelta(days=31)
            )
            db.session.commit()
 
    elif event["type"] in ("invoice.payment_failed", "customer.subscription.deleted"):
        customer_id = event["data"]["object"].get("customer")
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.is_subscribed = False
            db.session.commit()
 
    return "", 200
   
   
 
# ── Stripe: success / cancel redirect pages ───────────────────────────────────
@app.route("/subscription/success")
def subscription_success():
    session_id = request.args.get("session_id")
    activated = False

    if session_id:
        try:
            # Retrieve the checkout session from Stripe to confirm payment
            session = stripe.checkout.Session.retrieve(session_id)

            if session.payment_status == "paid":
                # Find the user via the metadata we attached when creating the session
                user_id = session.metadata.get("user_id")
                user = User.query.get(int(user_id)) if user_id else None

                # Fallback: find by stripe customer id
                if not user and session.customer:
                    user = User.query.filter_by(
                        stripe_customer_id=str(session.customer)
                    ).first()

                if user and not user.is_subscribed:
                    user.is_subscribed = True
                    # Try to get the subscription period end from Stripe
                    try:
                        sub = stripe.Subscription.retrieve(session.subscription)
                        user.subscription_end = datetime.datetime.utcfromtimestamp(
                            sub.current_period_end
                        )
                    except Exception:
                        user.subscription_end = (
                            datetime.datetime.utcnow() + datetime.timedelta(days=31)
                        )

                    # Store stripe customer id if not already saved
                    if session.customer and not user.stripe_customer_id:
                        user.stripe_customer_id = str(session.customer)

                    db.session.commit()
                    activated = True
                elif user and user.is_subscribed:
                    activated = True  # Already active
        except Exception as e:
            print(f"Subscription activation error: {e}")

    if activated:
        return """
        <html><body style="background:#23272D;color:#96abff;font-family:Arial;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;">
        <div>
          <div style="font-size:48px;margin-bottom:16px;">✓</div>
          <h2 style="color:#4CFF7A;font-size:24px;margin-bottom:8px;">Subscription Active!</h2>
          <p style="color:#9A9A9A;">You now have full access to EverNest Pro.<br>
          Close this tab and click <b>Refresh</b> in EverNest.</p>
        </div></body></html>
        """, 200, {"Content-Type": "text/html"}
    else:
        return """
        <html><body style="background:#23272D;color:#96abff;font-family:Arial;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;">
        <div>
          <div style="font-size:48px;margin-bottom:16px;">⏳</div>
          <h2 style="color:#FFD700;font-size:24px;margin-bottom:8px;">Processing...</h2>
          <p style="color:#9A9A9A;">Your payment is being confirmed.<br>
          Close this tab, wait a moment, then click <b>Refresh</b> in EverNest.</p>
        </div></body></html>
        """, 200, {"Content-Type": "text/html"}
 
 
@app.route("/subscription/cancel")
def subscription_cancel():
    return """
    <html><body style="background:#23272D;color:#96abff;font-family:Arial;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;">
    <div>
      <div style="font-size:48px;margin-bottom:16px;">✕</div>
      <h2 style="color:#FF6B6B;font-size:24px;margin-bottom:8px;">Checkout Cancelled</h2>
      <p style="color:#9A9A9A;">No charges were made.<br>
      Close this tab and try again from EverNest.</p>
    </div></body></html>
    """, 200, {"Content-Type": "text/html"}
 
 
# ── PayPal: create subscription ───────────────────────────────────────────────
@app.route("/subscription/paypal/create", methods=["POST"])
@require_auth
def paypal_create_subscription():
    try:
        data    = request.get_json() or {}
        user_id = str(data.get("user_id", ""))
        user    = find_user_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
 
        token = get_paypal_access_token()
        resp  = requests.post(
            f"{PAYPAL_API_BASE}/v1/billing/subscriptions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json={
                "plan_id": PAYPAL_PLAN_ID,
                "subscriber": {"email_address": user.email},
                "application_context": {
                    "return_url": "https://evernest-swz9.onrender.com/subscription/paypal/success",
                    "cancel_url": "https://evernest-swz9.onrender.com/subscription/cancel",
                    "user_action": "SUBSCRIBE_NOW",
                },
            },
            timeout=15
        )
        pp_data = resp.json()
        approve_url = next(
            (l["href"] for l in pp_data.get("links", []) if l["rel"] == "approve"),
            None
        )
        sub_id = pp_data.get("id")
 
        # Store PayPal sub ID on user
        if sub_id:
            user.paypal_subscription_id = sub_id
            db.session.commit()
 
        if approve_url:
            return jsonify({"url": approve_url})
        return jsonify({"error": "Could not get PayPal approval URL"}), 400
 
    except Exception as e:
        return jsonify({"error": str(e)}), 400
 
 
# ── PayPal: webhook ───────────────────────────────────────────────────────────
@app.route("/paypal/webhook", methods=["POST"])
def paypal_webhook():
    try:
        event     = request.get_json() or {}
        event_type = event.get("event_type", "")
        resource   = event.get("resource", {})
        sub_id     = resource.get("id")
 
        user = User.query.filter_by(paypal_subscription_id=sub_id).first()
        if not user:
            return "", 200
 
        if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
            user.is_subscribed    = True
            user.subscription_end = datetime.datetime.utcnow() + datetime.timedelta(days=31)
            db.session.commit()
 
        elif event_type in (
            "BILLING.SUBSCRIPTION.CANCELLED",
            "BILLING.SUBSCRIPTION.SUSPENDED",
            "BILLING.SUBSCRIPTION.EXPIRED",
        ):
            user.is_subscribed = False
            db.session.commit()
 
        elif event_type == "PAYMENT.SALE.COMPLETED":
            # Renew for another month
            user.is_subscribed    = True
            user.subscription_end = datetime.datetime.utcnow() + datetime.timedelta(days=31)
            db.session.commit()
 
        return "", 200
    except Exception:
        return "", 200
 
 
@app.route("/subscription/paypal/success")
def paypal_success():
    return """
    <html><body style="background:#23272D;color:#96abff;font-family:Arial;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;">
    <div>
      <div style="font-size:48px;margin-bottom:16px;">✓</div>
      <h2 style="color:#4CFF7A;font-size:24px;margin-bottom:8px;">PayPal Subscription Active!</h2>
      <p style="color:#9A9A9A;">You now have full access to EverNest Pro.<br>
      Close this tab and reopen the app to get started.</p>
    </div></body></html>
    """, 200, {"Content-Type": "text/html"}
 
 
# ── Daily job: expire lapsed subscriptions ────────────────────────────────────
#def expire_lapsed_subscriptions():
#    with app.app_context():
#        now      = datetime.datetime.utcnow()
#        expired  = User.query.filter(
#            User.is_subscribed == True,
#            User.subscription_end != None,
#            User.subscription_end < now
#        ).all()
#        for user in expired:
#            user.is_subscribed = False
#        if expired:
#            db.session.commit()
 
# Start scheduler


# ==============================================================================
# Budget Model
# ==============================================================================
class Budget(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.String(80), unique=True, nullable=False)
    income      = db.Column(db.Float, default=0)
    payday_freq = db.Column(db.String(30), default="Bi-Weekly")
    next_payday = db.Column(db.String(10), nullable=True)
    categories  = db.Column(db.Text, default="{}")   # JSON string
    bills       = db.Column(db.Text, default="[]")   # JSON string


# ==============================================================================
# Balance Snapshot Model (for net worth over time chart)
# ==============================================================================
class BalanceSnapshot(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.String(80), nullable=False)
    date       = db.Column(db.String(10), nullable=False)   # YYYY-MM-DD
    net_worth  = db.Column(db.Float, default=0)
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='uq_user_date'),)

# =============================================================================
# Family Models
# =============================================================================
class Family(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), default="My Family")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
 
 
class FamilyMember(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("family.id"), nullable=False)
    user_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    color     = db.Column(db.String(10), default="#96abff")   # calendar color
 
 
class FamilyInvite(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    family_id      = db.Column(db.Integer, db.ForeignKey("family.id"), nullable=False)
    invited_email  = db.Column(db.String(120), nullable=False)
    invited_by     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status         = db.Column(db.String(20), default="pending")  # pending/accepted/declined
    created_at     = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# ==============================================================================
# Family Routes
# ==============================================================================
# ── Helper: get user's family ─────────────────────────────────────────────────
 
def get_user_family(user_id):
    """Returns (family, member) or (None, None)"""
    member = FamilyMember.query.filter_by(user_id=user_id).first()
    if not member:
        return None, None
    family = Family.query.get(member.family_id)
    return family, member
 
 
def get_family_member_ids(family_id):
    """Returns list of user_ids in a family"""
    members = FamilyMember.query.filter_by(family_id=family_id).all()
    return [m.user_id for m in members]
 
 
# ── Family routes ─────────────────────────────────────────────────────────────
 
@app.route("/family/create", methods=["POST"])
@require_auth
def create_family():
    data    = request.get_json() or {}
    user_id = int(data.get("user_id", 0))
    name    = data.get("name", "My Family")
 
    # Check if user already in a family
    existing = FamilyMember.query.filter_by(user_id=user_id).first()
    if existing:
        return jsonify({"success": False, "message": "You are already in a family."}), 400
 
    family = Family(name=name, created_by=user_id)
    db.session.add(family)
    db.session.flush()
 
    member = FamilyMember(family_id=family.id, user_id=user_id, color="#96abff")
    db.session.add(member)
    db.session.commit()
 
    return jsonify({"success": True, "family_id": family.id, "name": family.name}), 201
 
 
@app.route("/family/info", methods=["GET"])
@require_auth
def get_family_info():
    user_id = int(request.args.get("user_id", 0))
    family, member = get_user_family(user_id)
 
    if not family:
        # Check for pending invites
        user = User.query.get(user_id)
        pending = []
        if user:
            invites = FamilyInvite.query.filter_by(
                invited_email=user.email, status="pending"
            ).all()
            for inv in invites:
                fam = Family.query.get(inv.family_id)
                inviter = User.query.get(inv.invited_by)
                pending.append({
                    "invite_id":    inv.id,
                    "family_id":    inv.family_id,
                    "family_name":  fam.name if fam else "Unknown",
                    "invited_by":   inviter.username if inviter else "Unknown",
                })
        return jsonify({"family": None, "pending_invites": pending})
 
    # Get all members
    members_data = []
    member_ids   = get_family_member_ids(family.id)
    colors       = ["#96abff", "#4CFF7A", "#FF6B6B", "#FFD700", "#FF9F40", "#C084FC"]
    for i, uid in enumerate(member_ids):
        u = User.query.get(uid)
        if u:
            fm = FamilyMember.query.filter_by(family_id=family.id, user_id=uid).first()
            members_data.append({
                "user_id":         uid,
                "username":        u.username,
                "email":           u.email,
                "color":           fm.color if fm else colors[i % len(colors)],
                "is_me":           uid == user_id,
                "profile_picture": u.profile_picture or None,
            })
 
    # Pending outgoing invites
    pending_out = []
    outgoing = FamilyInvite.query.filter_by(family_id=family.id, status="pending").all()
    for inv in outgoing:
        pending_out.append({"invite_id": inv.id, "email": inv.invited_email})
 
    return jsonify({
        "family": {
            "id":      family.id,
            "name":    family.name,
            "members": members_data,
            "my_color": member.color,
        },
        "pending_invites":  [],
        "pending_outgoing": pending_out,
    })
 
 
@app.route("/family/invite", methods=["POST"])
@require_auth
def invite_to_family():
    data          = request.get_json() or {}
    user_id       = int(data.get("user_id", 0))
    invited_email = data.get("email", "").strip().lower()
 
    family, member = get_user_family(user_id)
    if not family:
        return jsonify({"success": False, "message": "You are not in a family yet."}), 400
 
    # Check if email already in family
    invited_user = User.query.filter_by(email=invited_email).first()
    if invited_user:
        already = FamilyMember.query.filter_by(
            family_id=family.id, user_id=invited_user.id
        ).first()
        if already:
            return jsonify({"success": False, "message": "That user is already in your family."}), 400
 
    # Check for existing pending invite
    existing_inv = FamilyInvite.query.filter_by(
        family_id=family.id, invited_email=invited_email, status="pending"
    ).first()
    if existing_inv:
        return jsonify({"success": False, "message": "Invite already sent."}), 400
 
    invite = FamilyInvite(
        family_id=family.id,
        invited_email=invited_email,
        invited_by=user_id,
    )
    db.session.add(invite)
    db.session.commit()
    return jsonify({"success": True, "message": f"Invite sent to {invited_email}."})
 
 
@app.route("/family/invite/respond", methods=["POST"])
@require_auth
def respond_to_invite():
    data      = request.get_json() or {}
    user_id   = int(data.get("user_id", 0))
    invite_id = int(data.get("invite_id", 0))
    accept    = data.get("accept", False)
 
    invite = FamilyInvite.query.get(invite_id)
    if not invite or invite.status != "pending":
        return jsonify({"success": False, "message": "Invite not found."}), 404
 
    if accept:
        invite.status = "accepted"
        # Check not already in a family
        existing = FamilyMember.query.filter_by(user_id=user_id).first()
        if not existing:
            colors = ["#96abff", "#4CFF7A", "#FF6B6B", "#FFD700", "#FF9F40", "#C084FC"]
            count  = FamilyMember.query.filter_by(family_id=invite.family_id).count()
            member = FamilyMember(
                family_id=invite.family_id,
                user_id=user_id,
                color=colors[count % len(colors)]
            )
            db.session.add(member)
    else:
        invite.status = "declined"
 
    db.session.commit()
    return jsonify({"success": True})
 
 
@app.route("/family/leave", methods=["POST"])
@require_auth
def leave_family():
    data    = request.get_json() or {}
    user_id = int(data.get("user_id", 0))
    member  = FamilyMember.query.filter_by(user_id=user_id).first()
    if member:
        db.session.delete(member)
        db.session.commit()
    return jsonify({"success": True})

# ==============================================================================
# Note Model
# ==============================================================================
class Note(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.String(80), nullable=False)
    title            = db.Column(db.String(200), default="Untitled")
    body             = db.Column(db.Text, default="")
    note_type        = db.Column(db.String(20), default="note")   # "note" or "checklist"
    checklist_items  = db.Column(db.Text, default="[]")            # JSON string
    updated_at       = db.Column(db.DateTime, default=datetime.datetime.utcnow,
                                  onupdate=datetime.datetime.utcnow)
    
# ==============================================================================
# Note Routes
# ==============================================================================
@app.route("/notes", methods=["GET"])
@require_auth
def get_notes():
    user_id = request.args.get("user_id", "")
    notes   = Note.query.filter_by(user_id=user_id).order_by(Note.updated_at.desc()).all()
    return jsonify({"notes": [{
        "id":               n.id,
        "title":            n.title,
        "body":             n.body,
        "note_type":        n.note_type,
        "checklist_items":  json.loads(n.checklist_items or "[]"),
        "updated_at":       n.updated_at.isoformat() if n.updated_at else "",
    } for n in notes]})
 
 
@app.route("/notes", methods=["POST"])
@require_auth
def create_note():
    data = request.get_json() or {}
    note = Note(
        user_id         = str(data.get("user_id", "")),
        title           = data.get("title", "Untitled"),
        body            = data.get("body", ""),
        note_type       = data.get("note_type", "note"),
        checklist_items = json.dumps(data.get("checklist_items", [])),
        updated_at      = datetime.datetime.utcnow(),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify({"success": True, "note": {
        "id":              note.id,
        "title":           note.title,
        "body":            note.body,
        "note_type":       note.note_type,
        "checklist_items": json.loads(note.checklist_items),
        "updated_at":      note.updated_at.isoformat(),
    }}), 201
 
 
@app.route("/notes/<int:note_id>", methods=["PUT"])
@require_auth
def update_note(note_id):
    note = Note.query.get(note_id)
    if not note:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    note.title           = data.get("title", note.title)
    note.body            = data.get("body", note.body)
    note.note_type       = data.get("note_type", note.note_type)
    note.checklist_items = json.dumps(data.get("checklist_items", []))
    note.updated_at      = datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"success": True})
 
 
@app.route("/notes/<int:note_id>", methods=["DELETE"])
@require_auth
def delete_note_route(note_id):
    note = Note.query.get(note_id)
    if note:
        db.session.delete(note)
        db.session.commit()
    return jsonify({"success": True})

# ── Budget routes ─────────────────────────────────────────────────────────────
# Paste alongside /login, /signup, /calendar routes

@app.route("/budget", methods=["GET"])
@require_auth
def get_budget():
    user_id = request.args.get("user_id", "")
    budget  = Budget.query.filter_by(user_id=user_id).first()
    if not budget:
        return jsonify({"budget": None})
    return jsonify({"budget": {
        "income":      budget.income,
        "payday_freq": budget.payday_freq,
        "next_payday": budget.next_payday,
        "categories":  json.loads(budget.categories or "{}"),
        "bills":       json.loads(budget.bills or "[]"),
    }})


@app.route("/budget", methods=["POST"])
@require_auth
def save_budget_route():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))

    budget = Budget.query.filter_by(user_id=user_id).first()
    if not budget:
        budget = Budget(user_id=user_id)
        db.session.add(budget)

    budget.income      = float(data.get("income", 0))
    budget.payday_freq = data.get("payday_freq", "Bi-Weekly")
    budget.next_payday = data.get("next_payday", "")
    budget.categories  = json.dumps(data.get("categories", {}))
    budget.bills       = json.dumps(data.get("bills", []))

    db.session.commit()
    return jsonify({"success": True}), 201


# ==============================================================================
# Balance Snapshot Routes
# ==============================================================================

@app.route("/balance/snapshot", methods=["POST"])
@require_auth
def record_balance_snapshot():
    """Record today's net worth for a user. Called from the dashboard on load."""
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    net_worth = float(data.get("net_worth", 0))
    today_str = datetime.date.today().isoformat()

    # Upsert — update if already exists for today, else insert
    existing = BalanceSnapshot.query.filter_by(user_id=user_id, date=today_str).first()
    if existing:
        existing.net_worth = net_worth
    else:
        snap = BalanceSnapshot(user_id=user_id, date=today_str, net_worth=net_worth)
        db.session.add(snap)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/balance/history", methods=["GET"])
@require_auth
def get_balance_history():
    """Return the last 30 days of net worth snapshots."""
    user_id = request.args.get("user_id", "")
    days    = request.args.get("days", 30, type=int)

    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    snapshots = BalanceSnapshot.query.filter(
        BalanceSnapshot.user_id == user_id,
        BalanceSnapshot.date >= cutoff
    ).order_by(BalanceSnapshot.date).all()

    return jsonify({"snapshots": [
        {"date": s.date, "net_worth": s.net_worth} for s in snapshots
    ]})


# ── Calendar Integration ──────────────────────────────────────────────────────
# ── Calendar model ────────────────────────────────────────────────────────────
# Add this class alongside your User model

class CalendarEvent(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.String(80), nullable=False)
    title         = db.Column(db.String(200), nullable=False)
    event_date    = db.Column(db.String(10), nullable=False)   # YYYY-MM-DD
    event_type    = db.Column(db.String(50), default="Other")
    event_time    = db.Column(db.String(20), nullable=True)
    notify_before = db.Column(db.String(20), default="None")
    note          = db.Column(db.String(500), nullable=True)
    color         = db.Column(db.String(10), nullable=True)    # custom hex color
    recurrence    = db.Column(db.String(20), default="None")   # None/Daily/Weekly/Bi-Weekly/Monthly/Yearly
    family_shared = db.Column(db.Boolean, default=True)        # visible to family members


# ── Calendar routes ───────────────────────────────────────────────────────────
# Paste these alongside your /login and /signup routes

@app.route("/calendar/events", methods=["GET"])
@require_auth
def get_calendar_events():
    import calendar as cal_mod

    user_id = request.args.get("user_id", "")
    year    = request.args.get("year",  type=int)
    month   = request.args.get("month", type=int)

    # Get all user_ids to fetch events for (self + family members)
    family = None
    try:
        uid_int = int(user_id)
        family, member = get_user_family(uid_int)
        if family:
            member_ids = get_family_member_ids(family.id)
        else:
            member_ids = [uid_int]
    except (ValueError, TypeError):
        member_ids = []

    # Build color map
    color_map = {}
    if family:
        for fm in FamilyMember.query.filter_by(family_id=family.id).all():
            color_map[fm.user_id] = fm.color

    # Determine month date range
    if year and month:
        month_start = datetime.date(year, month, 1)
        month_days  = cal_mod.monthrange(year, month)[1]
        month_end   = datetime.date(year, month, month_days)
    else:
        month_start = None
        month_end   = None

    # Fetch ALL events for relevant users, then sort in Python
    raw_events = []

    query_uids = member_ids if member_ids else []
    if not query_uids:
        query_uids = [user_id]

    for uid in query_uids:
        is_self = (str(uid) == str(user_id))
        all_user_events = CalendarEvent.query.filter_by(user_id=str(uid)).all()

        for ev in all_user_events:
            # Respect family_shared — skip private events from other users
            if not is_self:
                try:
                    shared = ev.family_shared
                except AttributeError:
                    shared = True
                # Explicitly treat False as private, anything else as shared
                if shared is False or shared == 0:
                    continue

            recurrence = getattr(ev, 'recurrence', None) or "None"
            is_recurring = recurrence not in ("None", "")

            if not is_recurring:
                # One-time event — only include if it falls in the requested month
                if month_start:
                    if ev.event_date and ev.event_date.startswith(f"{year:04d}-{month:02d}"):
                        raw_events.append((ev, ev.event_date, is_self))
                else:
                    raw_events.append((ev, ev.event_date, is_self))
            else:
                # Recurring event — generate instances for this month
                try:
                    base_date = datetime.date.fromisoformat(ev.event_date)
                except (ValueError, TypeError):
                    print(f"[RECUR] Bad date for event {ev.id}: {ev.event_date}")
                    continue

                if not month_start:
                    raw_events.append((ev, ev.event_date, is_self))
                    continue

                try:
                    dates = _generate_recurrence_dates(
                        base_date, recurrence, month_start, month_end
                    )
                    print(f"[RECUR] Event '{ev.title}' ({recurrence}) base={ev.event_date} → {len(dates)} dates in {year}-{month:02d}")
                    for d in dates:
                        raw_events.append((ev, d.isoformat(), is_self))
                except Exception as e:
                    print(f"[RECUR] Error generating dates for event {ev.id}: {e}")
                    # Fall back to showing on original date only
                    if ev.event_date and ev.event_date.startswith(f"{year:04d}-{month:02d}"):
                        raw_events.append((ev, ev.event_date, is_self))

    # Build response
    output = []
    for ev, date_str, is_self in raw_events:
        ev_color = getattr(ev, 'color', '') or ''
        if not ev_color and ev.user_id.isdigit():
            ev_color = color_map.get(int(ev.user_id), "#96abff")
        elif not ev_color:
            ev_color = "#96abff"

        output.append({
            "id":            ev.id,
            "title":         ev.title,
            "event_date":    date_str,
            "event_type":    ev.event_type,
            "event_time":    ev.event_time,
            "notify_before": ev.notify_before,
            "note":          ev.note,
            "created_by":    ev.user_id,
            "color":         ev_color,
            "recurrence":    getattr(ev, 'recurrence', 'None') or "None",
            "family_shared": getattr(ev, 'family_shared', True) if getattr(ev, 'family_shared', None) is not None else True,
            "is_mine":       is_self,
        })

    output.sort(key=lambda x: (x["event_date"], x.get("event_time") or ""))
    return jsonify({"events": output})


def _generate_recurrence_dates(base_date, recurrence, month_start, month_end):
    """Generate all occurrences of a recurring event within [month_start, month_end].
    Uses only stdlib — no dateutil needed."""
    dates = []
    if not recurrence or recurrence == "None":
        return dates

    def add_days(d, n):
        return d + datetime.timedelta(days=n)

    def add_months(d, n):
        """Add n months to date d, clamping day to valid range."""
        m = d.month - 1 + n
        y = d.year + m // 12
        m = m % 12 + 1
        import calendar as _cal
        max_day = _cal.monthrange(y, m)[1]
        return d.replace(year=y, month=m, day=min(d.day, max_day))

    def add_years(d, n):
        import calendar as _cal
        try:
            return d.replace(year=d.year + n)
        except ValueError:
            # Feb 29 on non-leap year
            max_day = _cal.monthrange(d.year + n, d.month)[1]
            return d.replace(year=d.year + n, day=min(d.day, max_day))

    # Step function based on recurrence type
    if recurrence == "Daily":
        step = lambda d: add_days(d, 1)
    elif recurrence == "Weekly":
        step = lambda d: add_days(d, 7)
    elif recurrence == "Bi-Weekly":
        step = lambda d: add_days(d, 14)
    elif recurrence == "Monthly":
        step = lambda d: add_months(d, 1)
    elif recurrence == "Yearly":
        step = lambda d: add_years(d, 1)
    else:
        return dates

    # Walk forward from base_date to reach the month window
    current = base_date

    # Fast-forward for daily events to avoid thousands of iterations
    if recurrence == "Daily" and current < month_start:
        diff = (month_start - current).days
        current = add_days(current, diff)
    else:
        # For weekly/monthly/yearly, step forward until we reach or pass month_start
        safety = 0
        while current < month_start and safety < 5000:
            current = step(current)
            safety += 1

    # Generate all dates within the month range
    safety = 0
    while current <= month_end and safety < 500:
        if current >= month_start:
            dates.append(current)
        current = step(current)
        safety += 1

    return dates


@app.route("/calendar/events", methods=["POST"])
@require_auth
def add_calendar_event():
    data = request.get_json() or {}
    ev = CalendarEvent(
        user_id       = str(data.get("user_id", "")),
        title         = data.get("title", ""),
        event_date    = data.get("event_date", ""),
        event_type    = data.get("event_type", "Other"),
        event_time    = data.get("event_time", ""),
        notify_before = data.get("notify_before", "None"),
        note          = data.get("note", ""),
        color         = data.get("color", ""),
        recurrence    = data.get("recurrence", "None"),
        family_shared = data.get("family_shared", True),
    )
    db.session.add(ev)
    db.session.commit()
    return jsonify({"success": True, "id": ev.id}), 201


@app.route("/calendar/events/<int:event_id>", methods=["PUT"])
@require_auth
def update_calendar_event(event_id):
    ev = CalendarEvent.query.get(event_id)
    if not ev:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    ev.title         = data.get("title", ev.title)
    ev.event_date    = data.get("event_date", ev.event_date)
    ev.event_type    = data.get("event_type", ev.event_type)
    ev.event_time    = data.get("event_time", ev.event_time)
    ev.notify_before = data.get("notify_before", ev.notify_before)
    ev.note          = data.get("note", ev.note)
    ev.color         = data.get("color", ev.color)
    ev.recurrence    = data.get("recurrence", ev.recurrence)
    ev.family_shared = data.get("family_shared", ev.family_shared)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/calendar/events/<int:event_id>", methods=["DELETE"])
@require_auth
def delete_calendar_event(event_id):
    ev = CalendarEvent.query.get(event_id)
    if ev:
        db.session.delete(ev)
        db.session.commit()
    return jsonify({"success": True})


@app.route("/calendar/debug", methods=["GET"])
@require_auth
def debug_calendar_events():
    """Debug route — shows raw DB values for all events of a user."""
    user_id = request.args.get("user_id", "")
    events = CalendarEvent.query.filter_by(user_id=str(user_id)).all()
    return jsonify({"events": [{
        "id":            e.id,
        "title":         e.title,
        "event_date":    e.event_date,
        "event_type":    e.event_type,
        "recurrence":    getattr(e, 'recurrence', 'N/A'),
        "color":         getattr(e, 'color', 'N/A'),
        "family_shared": getattr(e, 'family_shared', 'N/A'),
    } for e in events]})

with app.app_context():
    db.create_all()


# ── Plaid client setup ────────────────────────────────────────────────────────
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET    = os.getenv("PLAID_SECRET")
PLAID_ENV       = os.getenv("PLAID_ENV", "production")

_env_map = {
    "sandbox":    plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

plaid_client = None
try:
    configuration = plaid.Configuration(
        host=_env_map.get(PLAID_ENV, plaid.Environment.Production),
        api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
    )
    api_client   = plaid.ApiClient(configuration)
    plaid_client = plaid_api.PlaidApi(api_client)
except Exception as e:
    print(f"Warning: Plaid client init failed ({e}). Plaid routes will be unavailable.")


# ── Core routes ───────────────────────────────────────────────────────────────

# Current app version — bump this when you push a new release
APP_VERSION = "1.0.0"

@app.route("/")
def home():
    return "API running", 200


# ==============================================================================
# Admin Panel — protected by SECRET_KEY
# ==============================================================================

@app.route("/admin")
def admin_panel():
    """Serve the admin panel HTML. Auth happens client-side via admin_key."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EverNest Admin</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                   background: #0c0e14; color: #e5e7eb; min-height: 100vh; }
            .login-wrap { display: flex; justify-content: center; align-items: center;
                          height: 100vh; }
            .login-card { background: #161a1f; border: 1px solid #2a2f38; border-radius: 12px;
                          padding: 40px; width: 360px; text-align: center; }
            .login-card h2 { color: #8b9cf7; margin-bottom: 8px; }
            .login-card p { color: #4b5563; font-size: 13px; margin-bottom: 24px; }
            input, select { width: 100%; padding: 10px 14px; border-radius: 8px;
                     border: 1px solid #2a2f38; background: #0c0e14; color: #e5e7eb;
                     font-size: 14px; margin-bottom: 12px; outline: none; }
            input:focus { border-color: #5b6ef7; }
            button { padding: 10px 20px; border-radius: 8px; border: none; cursor: pointer;
                     font-size: 13px; font-weight: 600; transition: all 0.15s; }
            .btn-primary { background: #5b6ef7; color: #fff; width: 100%; }
            .btn-primary:hover { background: #4a5ce0; }
            .btn-danger { background: transparent; color: #f87171; border: 1px solid #f87171; }
            .btn-danger:hover { background: #2a1520; }
            .btn-success { background: #4ade80; color: #0c0e14; }
            .btn-success:hover { background: #3bca70; }
            .btn-warn { background: #fbbf24; color: #0c0e14; }
            .btn-warn:hover { background: #e5ac1e; }
            .btn-sm { padding: 6px 14px; font-size: 12px; }

            .panel { display: none; max-width: 900px; margin: 0 auto; padding: 30px; }
            .panel.active { display: block; }
            .header { display: flex; justify-content: space-between; align-items: center;
                       margin-bottom: 24px; border-bottom: 2px solid #5b6ef7; padding-bottom: 16px; }
            .header h1 { font-size: 22px; color: #e5e7eb; }
            .header span { color: #4b5563; font-size: 12px; }
            .section { background: #161a1f; border: 1px solid #2a2f38; border-radius: 10px;
                        padding: 20px; margin-bottom: 20px; }
            .section h3 { color: #8b9cf7; margin-bottom: 12px; font-size: 15px; }
            .row { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
            .row input, .row select { margin-bottom: 0; }
            .row input { flex: 1; }
            .msg { padding: 10px 14px; border-radius: 8px; margin-top: 10px; font-size: 13px; }
            .msg-ok { background: #0f2918; color: #4ade80; border: 1px solid #166534; }
            .msg-err { background: #2a1520; color: #f87171; border: 1px solid #7f1d1d; }

            table { width: 100%; border-collapse: collapse; margin-top: 12px; }
            th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #2a2f38;
                      font-size: 13px; }
            th { color: #6b7280; font-weight: 600; font-size: 11px; text-transform: uppercase; }
            td { color: #d1d5db; }
            .badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
                      font-size: 11px; font-weight: 600; }
            .badge-active { background: #166534; color: #4ade80; }
            .badge-cancelled { background: #78350f; color: #fbbf24; }
            .badge-inactive { background: #1f2328; color: #6b7280; }
        </style>
    </head>
    <body>
        <!-- Login -->
        <div class="login-wrap" id="loginView">
            <div class="login-card">
                <h2>EverNest Admin</h2>
                <p>Enter your admin key to continue</p>
                <input type="password" id="adminKeyInput" placeholder="Admin Key (SECRET_KEY)">
                <button class="btn-primary" onclick="doLogin()">Sign In</button>
                <div id="loginMsg"></div>
            </div>
        </div>

        <!-- Admin Panel -->
        <div class="panel" id="adminPanel">
            <div class="header">
                <div>
                    <h1>EverNest Admin Panel</h1>
                    <span>N0Ctrl Studios — Developer Tools</span>
                </div>
                <button class="btn-danger btn-sm" onclick="logout()">Logout</button>
            </div>

            <!-- Search User -->
            <div class="section">
                <h3>🔍  Find User</h3>
                <div class="row">
                    <input type="text" id="searchInput" placeholder="Search by email or username">
                    <button class="btn-primary btn-sm" onclick="searchUser()">Search</button>
                    <button class="btn-primary btn-sm" onclick="listAllUsers()">List All</button>
                </div>
                <div id="searchResult"></div>
            </div>

            <!-- Grant Subscription -->
            <div class="section">
                <h3>⭐  Grant Free Subscription</h3>
                <div class="row">
                    <input type="text" id="grantEmail" placeholder="Email or username">
                    <select id="grantDays" style="width:140px;flex:none;">
                        <option value="30">30 days</option>
                        <option value="90">90 days</option>
                        <option value="180">6 months</option>
                        <option value="365" selected>1 year</option>
                        <option value="36500">Lifetime</option>
                    </select>
                    <button class="btn-success btn-sm" onclick="grantSub()">Grant</button>
                </div>
                <div id="grantMsg"></div>
            </div>

            <!-- Remove Subscription -->
            <div class="section">
                <h3>🚫  Remove Subscription</h3>
                <div class="row">
                    <input type="text" id="revokeEmail" placeholder="Email or username">
                    <button class="btn-danger btn-sm" onclick="revokeSub()">Revoke</button>
                </div>
                <div id="revokeMsg"></div>
            </div>

            <!-- Users Table -->
            <div class="section">
                <h3>👥  Users</h3>
                <div id="usersTable"></div>
            </div>
        </div>

        <script>
            let ADMIN_KEY = '';
            const API = '';

            function doLogin() {
                ADMIN_KEY = document.getElementById('adminKeyInput').value;
                // Test the key with a simple request
                fetch(API + '/admin/users', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({admin_key: ADMIN_KEY})
                })
                .then(r => { if (!r.ok) throw new Error('Invalid key'); return r.json(); })
                .then(() => {
                    document.getElementById('loginView').style.display = 'none';
                    document.getElementById('adminPanel').classList.add('active');
                    listAllUsers();
                })
                .catch(() => {
                    document.getElementById('loginMsg').innerHTML =
                        '<div class="msg msg-err">Invalid admin key</div>';
                });
            }

            // Enter key on password field
            document.getElementById('adminKeyInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') doLogin();
            });

            function logout() {
                ADMIN_KEY = '';
                document.getElementById('adminPanel').classList.remove('active');
                document.getElementById('loginView').style.display = 'flex';
                document.getElementById('adminKeyInput').value = '';
            }

            function adminFetch(path, body) {
                return fetch(API + path, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({admin_key: ADMIN_KEY, ...body})
                }).then(r => r.json());
            }

            function searchUser() {
                const q = document.getElementById('searchInput').value.trim();
                if (!q) return;
                adminFetch('/admin/search', {query: q}).then(data => {
                    if (data.error) {
                        document.getElementById('searchResult').innerHTML =
                            '<div class="msg msg-err">' + data.error + '</div>';
                        return;
                    }
                    document.getElementById('searchResult').innerHTML = renderUserCard(data.user);
                });
            }

            function listAllUsers() {
                adminFetch('/admin/users', {}).then(data => {
                    if (!data.users) return;
                    let html = '<table><tr><th>ID</th><th>Username</th><th>Email</th>' +
                               '<th>Status</th><th>Ends</th><th>Actions</th></tr>';
                    data.users.forEach(u => {
                        let badge = '';
                        if (u.is_subscribed && u.cancel_at_period_end)
                            badge = '<span class="badge badge-cancelled">Cancelled</span>';
                        else if (u.is_subscribed)
                            badge = '<span class="badge badge-active">Active</span>';
                        else
                            badge = '<span class="badge badge-inactive">Free</span>';

                        let end = u.subscription_end ? u.subscription_end.substring(0, 10) : '—';
                        html += '<tr><td>' + u.id + '</td><td>' + u.username +
                                '</td><td>' + u.email + '</td><td>' + badge +
                                '</td><td>' + end + '</td><td>' +
                                '<button class="btn-success btn-sm" onclick="quickGrant(\\''+u.email+'\\')">Grant</button> ' +
                                '<button class="btn-danger btn-sm" onclick="quickRevoke(\\''+u.email+'\\')">Revoke</button>' +
                                '</td></tr>';
                    });
                    html += '</table>';
                    document.getElementById('usersTable').innerHTML = html;
                });
            }

            function renderUserCard(u) {
                let badge = u.is_subscribed ?
                    '<span class="badge badge-active">Active</span>' :
                    '<span class="badge badge-inactive">Free</span>';
                return '<div style="margin-top:10px;padding:12px;background:#0c0e14;' +
                    'border-radius:8px;border:1px solid #2a2f38;">' +
                    '<strong>' + u.username + '</strong> &lt;' + u.email + '&gt;<br>' +
                    'ID: ' + u.id + ' | ' + badge +
                    (u.subscription_end ? ' | Ends: ' + u.subscription_end.substring(0,10) : '') +
                    (u.stripe_customer_id ? ' | Stripe: ' + u.stripe_customer_id : '') +
                    (u.plaid_access_token ? ' | 🏦 Bank linked' : '') +
                    '</div>';
            }

            function grantSub() {
                const input = document.getElementById('grantEmail').value.trim();
                const days = parseInt(document.getElementById('grantDays').value);
                const isEmail = input.includes('@');
                adminFetch('/admin/grant_subscription', {
                    email: isEmail ? input : '', username: isEmail ? '' : input, days: days
                }).then(data => {
                    document.getElementById('grantMsg').innerHTML = data.success ?
                        '<div class="msg msg-ok">Granted ' + days + ' days to ' + (data.user || input) + '</div>' :
                        '<div class="msg msg-err">' + (data.error || 'Failed') + '</div>';
                    listAllUsers();
                });
            }

            function revokeSub() {
                const input = document.getElementById('revokeEmail').value.trim();
                const isEmail = input.includes('@');
                adminFetch('/admin/revoke_subscription', {
                    email: isEmail ? input : '', username: isEmail ? '' : input
                }).then(data => {
                    document.getElementById('revokeMsg').innerHTML = data.success ?
                        '<div class="msg msg-ok">Revoked subscription for ' + (data.user || input) + '</div>' :
                        '<div class="msg msg-err">' + (data.error || 'Failed') + '</div>';
                    listAllUsers();
                });
            }

            function quickGrant(email) {
                adminFetch('/admin/grant_subscription', {email: email, days: 365}).then(() => listAllUsers());
            }
            function quickRevoke(email) {
                adminFetch('/admin/revoke_subscription', {email: email}).then(() => listAllUsers());
            }
        </script>
    </body>
    </html>
    """
    return html, 200, {"Content-Type": "text/html"}


def _admin_auth(data):
    """Verify admin key from request body. Returns True if valid."""
    return data.get("admin_key", "") == app.config["SECRET_KEY"]


@app.route("/admin/users", methods=["POST"])
def admin_list_users():
    data = request.get_json() or {}
    if not _admin_auth(data):
        return jsonify({"error": "Unauthorized"}), 403
    users = User.query.order_by(User.id).all()

    # Check Stripe cancel status for subscribed users
    result = []
    for u in users:
        cancel = False
        if u.is_subscribed and u.stripe_customer_id:
            try:
                subs = stripe.Subscription.list(customer=u.stripe_customer_id, limit=1)
                for s in subs.data:
                    if s.status in ("active", "trialing", "past_due"):
                        cancel = bool(s.cancel_at_period_end)
            except Exception:
                pass
        result.append({
            "id": u.id, "username": u.username, "email": u.email,
            "is_subscribed": u.is_subscribed or False,
            "subscription_end": u.subscription_end.isoformat() if u.subscription_end else None,
            "stripe_customer_id": u.stripe_customer_id,
            "plaid_access_token": bool(u.plaid_access_token),
            "cancel_at_period_end": cancel,
        })
    return jsonify({"users": result})


@app.route("/admin/search", methods=["POST"])
def admin_search_user():
    data = request.get_json() or {}
    if not _admin_auth(data):
        return jsonify({"error": "Unauthorized"}), 403
    q = data.get("query", "").strip().lower()
    if not q:
        return jsonify({"error": "No search query"}), 400
    user = User.query.filter(
        (db.func.lower(User.email) == q) | (db.func.lower(User.username) == q)
    ).first()
    if not user:
        return jsonify({"error": f"No user found matching '{q}'"}), 404
    return jsonify({"user": {
        "id": user.id, "username": user.username, "email": user.email,
        "is_subscribed": user.is_subscribed or False,
        "subscription_end": user.subscription_end.isoformat() if user.subscription_end else None,
        "stripe_customer_id": user.stripe_customer_id,
        "plaid_access_token": bool(user.plaid_access_token),
    }})


@app.route("/admin/grant_subscription", methods=["POST"])
def admin_grant_subscription():
    data = request.get_json() or {}
    if not _admin_auth(data):
        return jsonify({"error": "Unauthorized"}), 403

    email    = data.get("email", "").strip().lower()
    username = data.get("username", "").strip()
    days     = data.get("days", 365)

    user = None
    if email:
        user = User.query.filter(db.func.lower(User.email) == email).first()
    elif username:
        user = User.query.filter(db.func.lower(User.username) == db.func.lower(username)).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    user.is_subscribed    = True
    user.subscription_end = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    db.session.commit()

    print(f"[ADMIN] Granted {days}-day subscription to {user.username} ({user.email})")
    return jsonify({"success": True, "user": user.username, "email": user.email,
                     "subscription_end": user.subscription_end.isoformat()})


@app.route("/admin/revoke_subscription", methods=["POST"])
def admin_revoke_subscription():
    data = request.get_json() or {}
    if not _admin_auth(data):
        return jsonify({"error": "Unauthorized"}), 403

    email    = data.get("email", "").strip().lower()
    username = data.get("username", "").strip()

    user = None
    if email:
        user = User.query.filter(db.func.lower(User.email) == email).first()
    elif username:
        user = User.query.filter(db.func.lower(User.username) == db.func.lower(username)).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    # Cancel on Stripe too
    if user.stripe_customer_id:
        try:
            subs = stripe.Subscription.list(customer=user.stripe_customer_id)
            for sub in subs.auto_paging_iter():
                if sub.status in ("active", "trialing", "past_due"):
                    stripe.Subscription.delete(sub.id)
                    print(f"[ADMIN] Cancelled Stripe sub {sub.id} for {user.username}")
        except Exception as e:
            print(f"[ADMIN] Stripe cancel failed: {e}")

    user.is_subscribed    = False
    user.subscription_end = None
    db.session.commit()

    print(f"[ADMIN] Revoked subscription for {user.username} ({user.email})")
    return jsonify({"success": True, "user": user.username})

@app.route("/version")
def get_version():
    """Returns current app version and download URLs for auto-updater."""
    return jsonify({
        "version": APP_VERSION,
        "downloads": {
            "windows": f"https://github.com/Sh3ll-pixel/EverNest/releases/download/v{APP_VERSION}/EverNest_Setup_v{APP_VERSION}.exe",
            "mac":     f"https://github.com/Sh3ll-pixel/EverNest/releases/download/v{APP_VERSION}/EverNest_v{APP_VERSION}_mac.dmg",
            "linux":   f"https://github.com/Sh3ll-pixel/EverNest/releases/download/v{APP_VERSION}/evernest_{APP_VERSION}_amd64.deb",
        },
        "required": False,  # Set True to force update (block app until updated)
        "changelog": "",     # Optional: "Bug fixes and performance improvements"
    })

@app.route("/lookup")
def lookup_user():
    email = request.args.get("email", "").strip().lower()
    username = request.args.get("username", "").strip()
    if not email and not username:
        return jsonify({"error": "Provide ?email=... or ?username=..."}), 400
    user = User.query.filter(
        (User.email == email) | (User.username == username)
    ).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "stripe_customer_id": user.stripe_customer_id,
        "is_subscribed": user.is_subscribed or False,
    })

@app.route("/signup", methods=["POST"])
def signup():
    data     = request.get_json()
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not username or not email or not password:
        return jsonify({"success": False, "message": "All fields are required"}), 400

    existing_user = User.query.filter(
        (User.username == username) | (User.email == email)
    ).first()

    if existing_user:
        return jsonify({"success": False, "message": "User already exists"}), 409

    password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    new_user = User(username=username, email=email, password_hash=password_hash)
    db.session.add(new_user)
    db.session.commit()

    token = generate_token(new_user.id)
    return jsonify({"success": True, "message": "Signup successful", "token": token}), 201

@app.route("/login", methods=["POST"])
def login():
    data        = request.get_json()
    login_value = data.get("login", "").strip()
    password    = data.get("password", "")

    if not login_value or not password:
        return jsonify({"success": False, "message": "Login and password required"}), 400

    user = User.query.filter(
        (User.username == login_value) | (User.email == login_value.lower())
    ).first()

    if not user or not bcrypt.check_password_hash(user.password_hash, password):
        return jsonify({"success": False, "message": "Invalid credentials"}), 401

    family, member = get_user_family(user.id)

    token = generate_token(user.id)
    return jsonify({
    "success": True,
    "message": "Login successful",
    "token": token,
    "user": {
        "id":        user.id,
        "username":  user.username,
        "email":     user.email,
        "family_id": family.id if family else None,
    }
}), 200

# ==============================================================================
# Temp Routes
# ==============================================================================


# =============================================================================
#  SETTINGS ROUTES
# =============================================================================
 
@app.route("/settings/update_profile", methods=["POST"])
@require_auth
def update_profile():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
 
    if "username" in data:
        existing = User.query.filter_by(username=data["username"]).first()
        if existing and existing.id != user.id:
            return jsonify({"success": False, "message": "Username already taken"}), 409
        user.username = data["username"]
 
    if "email" in data:
        existing = User.query.filter_by(email=data["email"]).first()
        if existing and existing.id != user.id:
            return jsonify({"success": False, "message": "Email already in use"}), 409
        user.email = data["email"]
 
    db.session.commit()
    return jsonify({"success": True})
 
 
@app.route("/settings/change_password", methods=["POST"])
@require_auth
def change_password():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
 
    if not bcrypt.check_password_hash(user.password_hash, data.get("current_password", "")):
        return jsonify({"success": False, "message": "Current password is incorrect"}), 401
 
    user.password_hash = bcrypt.generate_password_hash(
        data.get("new_password", "")
    ).decode("utf-8")
    db.session.commit()
    return jsonify({"success": True})
 
 
@app.route("/settings/remove_bank", methods=["POST"])
@require_auth
def remove_bank():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False}), 404

    # Call Plaid /item/remove to deactivate the Item and stop billing
    if user.plaid_access_token and plaid_client:
        try:
            req = ItemRemoveRequest(access_token=user.plaid_access_token)
            plaid_client.item_remove(req)
            print(f"[PLAID] Removed Item for user {user_id}")
        except Exception as e:
            print(f"[PLAID] Failed to remove Item (may already be removed): {e}")

    user.plaid_access_token    = None
    user.plaid_item_id         = None
    user.plaid_reauth_required = False
    user.plaid_new_accounts    = False

    # Delete balance snapshots derived from Plaid data
    BalanceSnapshot.query.filter_by(user_id=str(user.id)).delete()

    db.session.commit()
    print(f"[PLAID] Cleaned up all Plaid data for user {user_id}")
    return jsonify({"success": True})
 
 
@app.route("/settings/report_bug", methods=["POST"])
@require_auth
def report_bug():
    data = request.get_json() or {}
    # Log it to Render logs for now — can wire to email later
    print(f"BUG REPORT from user {data.get('user_id')}: {data.get('description')}")
    return jsonify({"success": True})
 
 
@app.route("/settings/delete_account", methods=["DELETE"])
@require_auth
def delete_account():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False}), 404

    # Call Plaid /item/remove to deactivate the Item on user offboarding
    if user.plaid_access_token and plaid_client:
        try:
            req = ItemRemoveRequest(access_token=user.plaid_access_token)
            plaid_client.item_remove(req)
            print(f"[PLAID] Removed Item for deleted user {user_id}")
        except Exception as e:
            print(f"[PLAID] Failed to remove Item on account deletion: {e}")

    # Delete all related data
    CalendarEvent.query.filter_by(user_id=str(user.id)).delete()
    CalendarEvent.query.filter_by(user_id=user.username).delete()
    Note.query.filter_by(user_id=str(user.id)).delete()
    Budget.query.filter_by(user_id=str(user.id)).delete()
    BalanceSnapshot.query.filter_by(user_id=str(user.id)).delete()
    FamilyMember.query.filter_by(user_id=user.id).delete()

    db.session.delete(user)
    db.session.commit()
    return jsonify({"success": True})


# =============================================================================
#  PROFILE PICTURE
# =============================================================================

@app.route("/profile/upload_picture", methods=["POST"])
@require_auth
def upload_profile_picture():
    """Accept a base64-encoded JPEG image (max ~200 KB encoded)."""
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    image   = data.get("image", "")

    user = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    # Sanity-check size (~200 KB base64 ≈ 270 000 chars)
    if len(image) > 300_000:
        return jsonify({"success": False, "message": "Image too large. Max ~200 KB."}), 400

    user.profile_picture = image
    db.session.commit()
    return jsonify({"success": True})


@app.route("/profile/picture", methods=["GET"])
@require_auth
def get_profile_picture():
    user_id = request.args.get("user_id", "")
    user = find_user_by_id(user_id)
    if not user or not user.profile_picture:
        return jsonify({"image": None})
    return jsonify({"image": user.profile_picture})


@app.route("/profile/pictures", methods=["GET"])
@require_auth
def get_profile_pictures_bulk():
    """Return profile pictures for multiple user_ids at once (for family view)."""
    ids_raw = request.args.get("user_ids", "")
    if not ids_raw:
        return jsonify({"pictures": {}})

    user_ids = [uid.strip() for uid in ids_raw.split(",") if uid.strip()]
    result = {}
    for uid in user_ids:
        user = find_user_by_id(uid)
        if user and user.profile_picture:
            result[str(user.id)] = user.profile_picture
    return jsonify({"pictures": result})
 
 
@app.route("/subscription/cancel", methods=["POST"])
@require_auth
def cancel_subscription():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False}), 404

    # Cancel on Stripe if applicable
    if user.stripe_customer_id:
        try:
            subscriptions = stripe.Subscription.list(customer=user.stripe_customer_id)
            cancelled_count = 0
            for sub in subscriptions.auto_paging_iter():
                if sub.status in ("active", "trialing", "past_due"):
                    stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
                    cancelled_count += 1
                    print(f"[SUB CANCEL] Set cancel_at_period_end=True on sub {sub.id} for user {user_id}")
            if cancelled_count == 0:
                print(f"[SUB CANCEL] No active subscriptions found for user {user_id}")
                return jsonify({"success": False, "message": "No active subscription found"}), 400
        except Exception as e:
            print(f"[SUB CANCEL] Stripe error for user {user_id}: {e}")
            return jsonify({"success": False, "message": "Failed to cancel on Stripe"}), 500
    else:
        print(f"[SUB CANCEL] No stripe_customer_id for user {user_id}")
        return jsonify({"success": False, "message": "No payment method on file"}), 400

    return jsonify({"success": True})

# ── Plaid routes ──────────────────────────────────────────────────────────────
PLAID_REDIRECT_URI = "https://evernest-swz9.onrender.com/plaid/oauth-return"

@app.route("/plaid/create_link_token", methods=["POST"])
@require_auth
def create_link_token():
    try:
        data    = request.get_json() or {}
        user_id = str(data.get("user_id", "default_user"))

        # Duplicate Item detection — if user already has a linked bank, warn them
        user = find_user_by_id(user_id)
        if user and user.plaid_access_token:
            return jsonify({
                "error": "You already have a bank account connected. "
                         "Disconnect it in Settings before linking a new one.",
                "duplicate": True,
            }), 409

        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            client_name="EverNest",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
            webhook="https://evernest-swz9.onrender.com/plaid/webhook",
            redirect_uri=PLAID_REDIRECT_URI,
        )
        response   = plaid_client.link_token_create(req)
        link_token = response["link_token"]
        request_id = response.get("request_id", "")
        print(f"[PLAID] link_token_create | user={user_id} | request_id={request_id}")
        return jsonify({"link_token": link_token})

    except plaid.ApiException as e:
        print(f"[PLAID] link_token_create FAILED | user={user_id} | error={e}")
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/exchange_token", methods=["POST"])
@require_auth
def exchange_token():
    try:
        data         = request.get_json() or {}
        public_token = data.get("public_token")
        user_id      = data.get("user_id")

        if not public_token:
            return jsonify({"error": "public_token is required"}), 400

        req          = ItemPublicTokenExchangeRequest(public_token=public_token)
        response     = plaid_client.item_public_token_exchange(req)
        access_token = response["access_token"]
        item_id      = response["item_id"]
        request_id   = response.get("request_id", "")
        print(f"[PLAID] Token exchange | item_id={item_id} | request_id={request_id} | user={user_id}")

        user = find_user_by_id(user_id)
        if user:
            user.plaid_access_token    = access_token
            user.plaid_item_id         = item_id
            user.plaid_reauth_required = False
            db.session.commit()

        return jsonify({"success": True})
    except plaid.ApiException as e:
        print(f"[PLAID] Token exchange FAILED | user={user_id} | error={e}")
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/accounts", methods=["GET"])
@require_auth
def get_accounts():
    try:
        user_id = request.args.get("user_id")
 
        # Collect all user_ids in family
        try:
            uid_int = int(user_id)
            family, _ = get_user_family(uid_int)
            if family:
                member_ids = get_family_member_ids(family.id)
            else:
                member_ids = [uid_int]
        except (ValueError, TypeError):
            user = find_user_by_id(user_id)
            member_ids = [user.id] if user else []
 
        all_accounts = []
        for uid in member_ids:
            u = User.query.get(uid)
            if u and u.plaid_access_token:
                try:
                    resp     = plaid_client.accounts_get(
                        AccountsGetRequest(access_token=u.plaid_access_token)
                    )
                    request_id = resp.get("request_id", "")
                    accounts = [a.to_dict() for a in resp["accounts"]]
                    print(f"[PLAID] accounts_get | user={uid} | accounts={len(accounts)} | request_id={request_id}")
                    # Tag each account with owner username
                    for acct in accounts:
                        acct["owner"] = u.username
                    all_accounts.extend(accounts)
                except Exception as e:
                    print(f"[PLAID] accounts_get FAILED | user={uid} | error={e}")
 
        return jsonify({"accounts": all_accounts})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/balance", methods=["GET"])
@require_auth
def get_realtime_balance():
    """Fetch real-time balance data using /accounts/balance/get.
    Unlike /plaid/accounts which returns cached data, this makes a live
    request to the financial institution for up-to-date balances.
    """
    try:
        user_id = request.args.get("user_id")
        user = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"accounts": [], "error": "No bank account connected"}), 400

        req = AccountsBalanceGetRequest(access_token=user.plaid_access_token)
        response = plaid_client.accounts_balance_get(req)
        request_id = response.get("request_id", "")
        accounts = [a.to_dict() for a in response["accounts"]]
        account_ids = [a.get("account_id", "") for a in accounts]

        # Tag with owner
        for acct in accounts:
            acct["owner"] = user.username

        print(f"[PLAID] balance_get | user={user_id} | accounts={account_ids} | request_id={request_id}")
        return jsonify({"accounts": accounts})

    except plaid.ApiException as e:
        error_body = e.body if hasattr(e, 'body') else str(e)
        print(f"[PLAID BALANCE] Error for user {user_id}: {error_body}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/transactions", methods=["GET"])
@require_auth
def get_transactions():
    """Fetch transactions with proper pagination as required by Plaid."""
    try:
        user_id = request.args.get("user_id")
        days    = request.args.get("days", 30, type=int)
        user    = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"transactions": []})

        end_date   = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days)

        all_transactions = []
        total_transactions = None
        offset = 0
        page_size = 100

        # Paginate through all transactions
        while True:
            req = TransactionsGetRequest(
                access_token=user.plaid_access_token,
                start_date=start_date,
                end_date=end_date,
                options=TransactionsGetRequestOptions(
                    count=page_size,
                    offset=offset
                ),
            )
            response = plaid_client.transactions_get(req)

            transactions = [t.to_dict() for t in response["transactions"]]
            all_transactions.extend(transactions)

            if total_transactions is None:
                total_transactions = response["total_transactions"]
                request_id = response.get("request_id", "")
                item = response.get("item", {})
                item_id = item.get("item_id", "") if isinstance(item, dict) else ""
                print(f"[PLAID] transactions_get | user={user_id} | item_id={item_id} | total={total_transactions} | request_id={request_id}")

            offset += len(transactions)

            # Stop when we've fetched all transactions or got an empty page
            if offset >= total_transactions or len(transactions) == 0:
                break

            # Safety limit to prevent infinite loops
            if offset > 5000:
                break

        print(f"[PLAID TXN] Fetched {len(all_transactions)}/{total_transactions} transactions for user {user_id}")
        return jsonify({
            "transactions": all_transactions,
            "total": total_transactions,
        })

    except plaid.ApiException as e:
        # Check for ITEM_LOGIN_REQUIRED
        try:
            error_body = json.loads(e.body) if hasattr(e, 'body') else {}
            error_code = error_body.get("error_code", "")
        except Exception:
            error_code = ""

        if error_code == "ITEM_LOGIN_REQUIRED":
            print(f"[PLAID TXN] User {user_id} needs to re-authenticate bank connection")
            return jsonify({
                "transactions": [],
                "error": "ITEM_LOGIN_REQUIRED",
                "message": "Your bank connection has expired. Please reconnect your bank account.",
            }), 400

        return jsonify({"error": str(e)}), 400


@app.route("/plaid/transactions/refresh", methods=["POST"])
@require_auth
def refresh_transactions():
    """Force a refresh of transaction data from the bank.
    Plaid will send a TRANSACTIONS webhook when new data is available."""
    try:
        from plaid.model.transactions_refresh_request import TransactionsRefreshRequest

        data    = request.get_json() or {}
        user_id = str(data.get("user_id", ""))
        user    = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"error": "No bank account connected"}), 400

        req = TransactionsRefreshRequest(access_token=user.plaid_access_token)
        plaid_client.transactions_refresh(req)
        print(f"[PLAID TXN] Triggered refresh for user {user_id}")
        return jsonify({"success": True, "message": "Refresh triggered. New data will arrive via webhook."})

    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/update_link_token", methods=["POST"])
@require_auth
def create_update_link_token():
    """Create a Link token in update mode for re-authentication.
    Used when ITEM_LOGIN_REQUIRED, PENDING_EXPIRATION, or PENDING_DISCONNECT occurs."""
    try:
        data    = request.get_json() or {}
        user_id = str(data.get("user_id", ""))
        user    = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"error": "No bank account connected"}), 400

        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            client_name="EverNest",
            country_codes=[CountryCode("US")],
            language="en",
            webhook="https://evernest-swz9.onrender.com/plaid/webhook",
            redirect_uri=PLAID_REDIRECT_URI,
            access_token=user.plaid_access_token,
        )
        response   = plaid_client.link_token_create(req)
        link_token = response["link_token"]
        print(f"[PLAID] Created update mode link token for user {user_id}")
        return jsonify({"link_token": link_token})

    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/reauth_complete", methods=["POST"])
@require_auth
def plaid_reauth_complete():
    """Called after user completes update mode Link flow. Clears the reauth flag."""
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if user:
        user.plaid_reauth_required = False
        db.session.commit()
        print(f"[PLAID] Cleared reauth flag for user {user_id}")
    return jsonify({"success": True})


@app.route("/plaid/new_accounts_link_token", methods=["POST"])
@require_auth
def create_new_accounts_link_token():
    """Create a Link token in update mode with account_selection_enabled.
    Used when NEW_ACCOUNTS_AVAILABLE webhook fires — lets user add new accounts."""
    try:
        from plaid.model.link_token_account_filters import LinkTokenAccountFilters

        data    = request.get_json() or {}
        user_id = str(data.get("user_id", ""))
        user    = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"error": "No bank account connected"}), 400

        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            client_name="EverNest",
            country_codes=[CountryCode("US")],
            language="en",
            webhook="https://evernest-swz9.onrender.com/plaid/webhook",
            redirect_uri=PLAID_REDIRECT_URI,
            access_token=user.plaid_access_token,
            update={"account_selection_enabled": True},
        )
        response   = plaid_client.link_token_create(req)
        link_token = response["link_token"]
        print(f"[PLAID] Created account selection link token for user {user_id}")
        return jsonify({"link_token": link_token})

    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/new_accounts_complete", methods=["POST"])
@require_auth
def plaid_new_accounts_complete():
    """Called after user completes the account selection flow. Clears the flag."""
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if user:
        user.plaid_new_accounts = False
        db.session.commit()
        print(f"[PLAID] Cleared new_accounts flag for user {user_id}")
    return jsonify({"success": True})


@app.route("/plaid/status", methods=["GET"])
@require_auth
def plaid_connection_status():
    """Check if the user's bank connection needs re-authentication or has new accounts."""
    user_id = request.args.get("user_id", "")
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"connected": False, "reauth_required": False, "new_accounts": False})
    return jsonify({
        "connected":       bool(user.plaid_access_token),
        "reauth_required": bool(getattr(user, 'plaid_reauth_required', False)),
        "new_accounts":    bool(getattr(user, 'plaid_new_accounts', False)),
    })
    
@app.route("/plaid/link")
def plaid_link_page():
    user_id = request.args.get("user_id", "default_user")
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connect Bank - EverNest</title>
        <style>
            body { font-family: Arial, sans-serif; background: #23272D; color: #96abff;
                   display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            #status { font-size: 20px; text-align: center; }
        </style>
    </head>
    <body>
        <div id="status"><p>Loading Plaid...</p></div>
        <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
        <script>
            const userId = \"""" + user_id + """\";
            fetch("/plaid/create_link_token", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({user_id: userId})
            })
            .then(r => r.json())
            .then(data => {
                // Store for OAuth return page
                localStorage.setItem('evernest_link_token', data.link_token);
                localStorage.setItem('evernest_link_user_id', userId);
                const handler = Plaid.create({
                    token: data.link_token,
                    onSuccess: function(public_token, metadata) {
                        console.log('[PLAID LINK] SUCCESS', metadata);
                        document.getElementById("status").innerHTML = "<p>Linking account...</p>";
                        fetch("/plaid/exchange_token", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({public_token: public_token, user_id: userId})
                        })
                        .then(r => r.json())
                        .then(() => {
                            localStorage.removeItem('evernest_link_token');
                            localStorage.removeItem('evernest_link_user_id');
                            document.getElementById("status").innerHTML =
                                "<p style='color:#4CFF7A'>✓ Bank connected! Close this tab and click Refresh in EverNest.</p>";
                        });
                    },
                    onExit: function(err, metadata) {
                        console.log('[PLAID LINK] EXIT', err, metadata);
                        fetch("/plaid/log_link_event", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                event: "EXIT",
                                error: err,
                                institution: metadata && metadata.institution,
                                link_session_id: metadata && metadata.link_session_id,
                                status: metadata && metadata.status,
                                user_id: userId
                            })
                        }).catch(() => {});
                        document.getElementById("status").innerHTML =
                            "<p>Cancelled. Close this tab and try again.</p>";
                    },
                    onEvent: function(eventName, metadata) {
                        console.log('[PLAID LINK]', eventName, metadata);
                        fetch("/plaid/log_link_event", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                event: eventName,
                                institution: metadata && metadata.institution_name,
                                link_session_id: metadata && metadata.link_session_id,
                                error_code: metadata && metadata.error_code,
                                error_type: metadata && metadata.error_type,
                                view_name: metadata && metadata.view_name,
                                user_id: userId
                            })
                        }).catch(() => {});
                    }
                });
                handler.open();
            });
        </script>
    </body>
    </html>
    """
    return html, 200, {"Content-Type": "text/html"}


@app.route("/plaid/oauth-return")
def plaid_oauth_return():
    """OAuth return page. After a user authenticates with their bank via OAuth,
    the bank redirects back here. This page re-initializes Plaid Link to
    complete the connection."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connecting Bank - EverNest</title>
        <style>
            body { font-family: Arial, sans-serif; background: #0c0e14; color: #8b9cf7;
                   display: flex; justify-content: center; align-items: center;
                   height: 100vh; margin: 0; }
            #status { font-size: 18px; text-align: center; }
            .spinner { font-size: 36px; animation: spin 1s linear infinite; display: inline-block; }
            @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        </style>
    </head>
    <body>
        <div id="status">
            <div class="spinner">⟳</div>
            <p>Completing bank connection...</p>
        </div>
        <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
        <script>
            // Re-initialize Link with the same link_token to complete the OAuth flow.
            // The link_token is stored in localStorage by the /plaid/link page.
            const linkToken = localStorage.getItem('evernest_link_token');

            if (linkToken) {
                const handler = Plaid.create({
                    token: linkToken,
                    receivedRedirectUri: window.location.href,
                    onSuccess: function(public_token, metadata) {
                        console.log('[PLAID LINK] SUCCESS (OAuth)', metadata);
                        document.getElementById("status").innerHTML =
                            "<p>Linking account...</p>";
                        // Get user_id from localStorage
                        const userId = localStorage.getItem('evernest_link_user_id') || 'default_user';
                        fetch("/plaid/exchange_token", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({public_token: public_token, user_id: userId})
                        })
                        .then(r => r.json())
                        .then(() => {
                            document.getElementById("status").innerHTML =
                                "<p style='color:#4ade80'>✓ Bank connected!</p>" +
                                "<p style='color:#6b7280; font-size:14px;'>Close this tab and click Refresh in EverNest.</p>";
                            localStorage.removeItem('evernest_link_token');
                            localStorage.removeItem('evernest_link_user_id');
                        });
                    },
                    onExit: function(err, metadata) {
                        console.log('[PLAID LINK] EXIT (OAuth)', err, metadata);
                        if (err) {
                            document.getElementById("status").innerHTML =
                                "<p style='color:#f87171'>Connection failed.</p>" +
                                "<p style='color:#6b7280; font-size:14px;'>Close this tab and try again.</p>";
                        } else {
                            document.getElementById("status").innerHTML =
                                "<p>Cancelled. Close this tab and try again.</p>";
                        }
                    },
                    onEvent: function(eventName, metadata) {
                        console.log('[PLAID LINK]', eventName, metadata);
                        fetch("/plaid/log_link_event", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                event: eventName,
                                institution: metadata && metadata.institution_name,
                                link_session_id: metadata && metadata.link_session_id,
                                error_code: metadata && metadata.error_code,
                                error_type: metadata && metadata.error_type,
                                view_name: metadata && metadata.view_name,
                                source: "oauth_return"
                            })
                        }).catch(() => {});
                    }
                });
                handler.open();
            } else {
                document.getElementById("status").innerHTML =
                    "<p style='color:#f87171'>Session expired.</p>" +
                    "<p style='color:#6b7280; font-size:14px;'>Close this tab and reconnect your bank from EverNest.</p>";
            }
        </script>
    </body>
    </html>
    """
    return html, 200, {"Content-Type": "text/html"}


# ── Plaid Link event logging ──────────────────────────────────────────────────
@app.route("/plaid/log_link_event", methods=["POST"])
def log_link_event():
    """Log frontend Link events for conversion monitoring."""
    data = request.get_json() or {}
    event      = data.get("event", "")
    user_id    = data.get("user_id", "")
    institution = data.get("institution", "")
    session_id = data.get("link_session_id", "")
    error_code = data.get("error_code", "")
    view_name  = data.get("view_name", "")
    source     = data.get("source", "link")

    parts = [f"[PLAID LINK EVENT] {event}"]
    if institution:  parts.append(f"institution={institution}")
    if session_id:   parts.append(f"session={session_id}")
    if error_code:   parts.append(f"error={error_code}")
    if view_name:    parts.append(f"view={view_name}")
    if user_id:      parts.append(f"user={user_id}")
    if source != "link": parts.append(f"source={source}")
    print(" | ".join(parts))

    return "", 200


# ── Plaid webhook ────────────────────────────────────────────────────────────
@app.route("/plaid/webhook", methods=["POST"])
def plaid_webhook():
    """Handle Plaid webhook events.
    
    Key events:
    - TRANSACTIONS.INITIAL_UPDATE: First batch of transactions ready
    - TRANSACTIONS.DEFAULT_UPDATE: New transactions available
    - TRANSACTIONS.HISTORICAL_UPDATE: Historical transactions ready
    - TRANSACTIONS.TRANSACTIONS_REMOVED: Transactions were deleted
    - ITEM.ERROR: Bank connection has an error (e.g. credentials expired)
    - ITEM.PENDING_EXPIRATION: Access token expiring soon
    - ITEM.PENDING_DISCONNECT: Bank about to revoke access
    - ITEM.NEW_ACCOUNTS_AVAILABLE: New accounts detected on the linked Item
    - ITEM.LOGIN_REPAIRED: User successfully re-authenticated
    """
    try:
        data = request.get_json() or {}
        webhook_type = data.get("webhook_type", "")
        webhook_code = data.get("webhook_code", "")
        item_id      = data.get("item_id", "")

        print(f"[PLAID WEBHOOK] {webhook_type}.{webhook_code} for item {item_id}")

        if webhook_type == "TRANSACTIONS":
            if webhook_code in ("INITIAL_UPDATE", "DEFAULT_UPDATE", "HISTORICAL_UPDATE"):
                new_count = data.get("new_transactions", 0)
                print(f"[PLAID WEBHOOK] {new_count} new transactions for item {item_id}")

            elif webhook_code == "TRANSACTIONS_REMOVED":
                removed = data.get("removed_transactions", [])
                print(f"[PLAID WEBHOOK] {len(removed)} transactions removed for item {item_id}")

        elif webhook_type == "ITEM":
            if webhook_code == "ERROR":
                error = data.get("error", {})
                error_code = error.get("error_code", "UNKNOWN")
                print(f"[PLAID WEBHOOK] Item error: {error_code} for item {item_id}")
                if error_code == "ITEM_LOGIN_REQUIRED":
                    print(f"[PLAID WEBHOOK] User needs to re-authenticate bank connection")
                    # Flag user for re-authentication
                    user = User.query.filter_by(plaid_item_id=item_id).first()
                    if user:
                        user.plaid_reauth_required = True
                        db.session.commit()
                        print(f"[PLAID WEBHOOK] Flagged user {user.id} for reauth")

            elif webhook_code == "PENDING_EXPIRATION":
                print(f"[PLAID WEBHOOK] Access token expiring soon for item {item_id}")
                # Flag user — they'll need to re-auth before it fully expires
                user = User.query.filter_by(plaid_item_id=item_id).first()
                if user:
                    user.plaid_reauth_required = True
                    db.session.commit()
                    print(f"[PLAID WEBHOOK] Flagged user {user.id} for pending expiration")

            elif webhook_code == "PENDING_DISCONNECT":
                print(f"[PLAID WEBHOOK] Bank pending disconnect for item {item_id}")
                # Flag user — bank is about to revoke access
                user = User.query.filter_by(plaid_item_id=item_id).first()
                if user:
                    user.plaid_reauth_required = True
                    db.session.commit()
                    print(f"[PLAID WEBHOOK] Flagged user {user.id} for pending disconnect")

            elif webhook_code == "NEW_ACCOUNTS_AVAILABLE":
                print(f"[PLAID WEBHOOK] New accounts available for item {item_id}")
                user = User.query.filter_by(plaid_item_id=item_id).first()
                if user:
                    user.plaid_new_accounts = True
                    db.session.commit()
                    print(f"[PLAID WEBHOOK] Flagged user {user.id} for new accounts")

            elif webhook_code == "LOGIN_REPAIRED":
                print(f"[PLAID WEBHOOK] Login repaired for item {item_id}")
                # User successfully re-authenticated — clear the reauth flag
                user = User.query.filter_by(plaid_item_id=item_id).first()
                if user:
                    user.plaid_reauth_required = False
                    db.session.commit()
                    print(f"[PLAID WEBHOOK] Cleared reauth flag for user {user.id}")

        return "", 200

    except Exception as e:
        print(f"[PLAID WEBHOOK] Error processing webhook: {e}")
        return "", 200  # Always return 200 so Plaid doesn't retry


@app.route("/migrate_subscription")
def migrate_subscription():
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS is_subscribed BOOLEAN DEFAULT FALSE"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_end TIMESTAMP"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(100)"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS paypal_subscription_id VARCHAR(100)"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS profile_picture TEXT"
            ))
            conn.commit()
        return "Migration successful", 200
    except Exception as e:
        return str(e), 400


@app.route("/migrate_profile_picture")
def migrate_profile_picture():
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS profile_picture TEXT"
            ))
            conn.commit()
        return "Profile picture migration successful", 200
    except Exception as e:
        return str(e), 400


@app.route("/migrate_calendar")
def migrate_calendar():
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "ALTER TABLE calendar_event ADD COLUMN IF NOT EXISTS color VARCHAR(10)"
            ))
            conn.execute(db.text(
                "ALTER TABLE calendar_event ADD COLUMN IF NOT EXISTS recurrence VARCHAR(20) DEFAULT 'None'"
            ))
            conn.execute(db.text(
                "ALTER TABLE calendar_event ADD COLUMN IF NOT EXISTS family_shared BOOLEAN DEFAULT TRUE"
            ))
            conn.commit()
        return "Calendar migration successful", 200
    except Exception as e:
        return str(e), 400


@app.route("/migrate_balance")
def migrate_balance():
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS balance_snapshot (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(80) NOT NULL,
                    date VARCHAR(10) NOT NULL,
                    net_worth FLOAT DEFAULT 0,
                    UNIQUE(user_id, date)
                )
            """))
            conn.commit()
        return "Balance snapshot migration successful", 200
    except Exception as e:
        return str(e), 400


@app.route("/migrate_plaid")
def migrate_plaid():
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS plaid_item_id VARCHAR(255)"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS plaid_reauth_required BOOLEAN DEFAULT FALSE"
            ))
            conn.execute(db.text(
                "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS plaid_new_accounts BOOLEAN DEFAULT FALSE"
            ))
            conn.commit()
        return "Plaid migration successful", 200
    except Exception as e:
        return str(e), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)