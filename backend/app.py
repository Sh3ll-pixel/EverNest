import os
import datetime
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import plaid
import json
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-this")

# Fix stale/dropped connections (SSL error: decryption failed or bad record mac)
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,        # Test connections before using them
    "pool_recycle": 300,           # Recycle connections every 5 minutes
    "pool_size": 5,
    "max_overflow": 10,
}

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ── Database models ───────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    plaid_access_token = db.Column(db.String(255), nullable=True)
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
PAYPAL_API_BASE      = "https://api-m.sandbox.paypal.com"  # change to api-m.paypal.com for live
 
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
def subscription_status():
    user_id = request.args.get("user_id")
    user = find_user_by_id(user_id)
    if not user:
        print(f"[SUB STATUS] User not found: {user_id}")
        return jsonify({"subscribed": False}), 404

    print(f"[SUB STATUS] User {user_id}: is_subscribed={user.is_subscribed}, "
          f"stripe_customer_id={user.stripe_customer_id}, "
          f"subscription_end={user.subscription_end}")

    # Check if subscription_end has passed
    if user.is_subscribed and user.subscription_end:
        if datetime.datetime.utcnow() > user.subscription_end:
            user.is_subscribed = False
            db.session.commit()

    # If DB says not subscribed but user has a Stripe customer,
    # check Stripe directly — covers cases where webhook/success page failed
    if not user.is_subscribed and user.stripe_customer_id:
        try:
            # Check ALL subscription statuses, not just "active"
            subs = stripe.Subscription.list(
                customer=user.stripe_customer_id,
                limit=5
            )
            print(f"[SUB STATUS] Stripe returned {len(subs.data)} subscriptions")
            for s in subs.data:
                print(f"[SUB STATUS]   -> id={s.id}, status={s.status}")

            # Accept active, trialing, or past_due (still has access)
            valid_statuses = {"active", "trialing", "past_due"}
            valid_sub = next(
                (s for s in subs.data if s.status in valid_statuses), None
            )
            if valid_sub:
                user.is_subscribed = True
                user.subscription_end = datetime.datetime.utcfromtimestamp(
                    valid_sub.current_period_end
                )
                db.session.commit()
                print(f"[SUB STATUS] Activated! status={valid_sub.status}, "
                      f"ends={user.subscription_end}")
            else:
                print(f"[SUB STATUS] No valid subscription found on Stripe")
        except Exception as e:
            print(f"[SUB STATUS] Stripe check failed: {e}")

    # If still not subscribed and NO stripe_customer_id, log that too
    if not user.is_subscribed and not user.stripe_customer_id:
        print(f"[SUB STATUS] No stripe_customer_id saved for user {user_id}")

    return jsonify({
        "subscribed":        user.is_subscribed or False,
        "subscription_end":  user.subscription_end.isoformat() if user.subscription_end else None,
    })


# ── Debug: check what Stripe knows about a user ─────────────────────────────
@app.route("/subscription/debug", methods=["GET"])
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
def delete_note_route(note_id):
    note = Note.query.get(note_id)
    if note:
        db.session.delete(note)
        db.session.commit()
    return jsonify({"success": True})

# ── Budget routes ─────────────────────────────────────────────────────────────
# Paste alongside /login, /signup, /calendar routes

@app.route("/budget", methods=["GET"])
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


# ── Calendar routes ───────────────────────────────────────────────────────────
# Paste these alongside your /login and /signup routes

@app.route("/calendar/events", methods=["GET"])
def get_calendar_events():
    user_id = request.args.get("user_id", "")
    year    = request.args.get("year",  type=int)
    month   = request.args.get("month", type=int)
 
    # Get all user_ids to fetch events for (self + family members)
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
 
    # Query events for all members
    all_events = []
    for uid in member_ids:
        query = CalendarEvent.query.filter_by(user_id=str(uid))
        if year and month:
            prefix = f"{year:04d}-{month:02d}"
            query  = query.filter(CalendarEvent.event_date.like(f"{prefix}%"))
        all_events.extend(query.order_by(
            CalendarEvent.event_date, CalendarEvent.event_time
        ).all())
 
    # Also fetch by string user_id for backwards compat
    if not member_ids:
        query = CalendarEvent.query.filter_by(user_id=str(user_id))
        if year and month:
            prefix = f"{year:04d}-{month:02d}"
            query  = query.filter(CalendarEvent.event_date.like(f"{prefix}%"))
        all_events = query.order_by(
            CalendarEvent.event_date, CalendarEvent.event_time
        ).all()
 
    return jsonify({"events": [{
        "id":            e.id,
        "title":         e.title,
        "event_date":    e.event_date,
        "event_type":    e.event_type,
        "event_time":    e.event_time,
        "notify_before": e.notify_before,
        "note":          e.note,
        "created_by":    e.user_id,
        "color":         color_map.get(int(e.user_id), "#96abff") if e.user_id.isdigit() else "#96abff",
    } for e in all_events]})


@app.route("/calendar/events", methods=["POST"])
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
    )
    db.session.add(ev)
    db.session.commit()
    return jsonify({"success": True, "id": ev.id}), 201


@app.route("/calendar/events/<int:event_id>", methods=["DELETE"])
def delete_calendar_event(event_id):
    ev = CalendarEvent.query.get(event_id)
    if ev:
        db.session.delete(ev)
        db.session.commit()
    return jsonify({"success": True})

with app.app_context():
    db.create_all()


# ── Plaid client setup ────────────────────────────────────────────────────────
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET    = os.getenv("PLAID_SECRET")
PLAID_ENV       = os.getenv("PLAID_ENV", "sandbox")

_env_map = {
    "sandbox":    plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

plaid_client = None
try:
    configuration = plaid.Configuration(
        host=_env_map.get(PLAID_ENV, plaid.Environment.Sandbox),
        api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
    )
    api_client   = plaid.ApiClient(configuration)
    plaid_client = plaid_api.PlaidApi(api_client)
except Exception as e:
    print(f"Warning: Plaid client init failed ({e}). Plaid routes will be unavailable.")


# ── Core routes ───────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return "API running", 200

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

    return jsonify({"success": True, "message": "Signup successful"}), 201

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

    return jsonify({
    "success": True,
    "message": "Login successful",
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
def remove_bank():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False}), 404
    user.plaid_access_token = None
    db.session.commit()
    return jsonify({"success": True})
 
 
@app.route("/settings/report_bug", methods=["POST"])
def report_bug():
    data = request.get_json() or {}
    # Log it to Render logs for now — can wire to email later
    print(f"BUG REPORT from user {data.get('user_id')}: {data.get('description')}")
    return jsonify({"success": True})
 
 
@app.route("/settings/delete_account", methods=["DELETE"])
def delete_account():
    data    = request.get_json() or {}
    user_id = str(data.get("user_id", ""))
    user    = find_user_by_id(user_id)
    if not user:
        return jsonify({"success": False}), 404
 
    # Delete all related data
    CalendarEvent.query.filter_by(user_id=str(user.id)).delete()
    CalendarEvent.query.filter_by(user_id=user.username).delete()
    Note.query.filter_by(user_id=str(user.id)).delete()
    Budget.query.filter_by(user_id=str(user.id)).delete()
    FamilyMember.query.filter_by(user_id=user.id).delete()
 
    db.session.delete(user)
    db.session.commit()
    return jsonify({"success": True})


# =============================================================================
#  PROFILE PICTURE
# =============================================================================

@app.route("/profile/upload_picture", methods=["POST"])
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
def get_profile_picture():
    user_id = request.args.get("user_id", "")
    user = find_user_by_id(user_id)
    if not user or not user.profile_picture:
        return jsonify({"image": None})
    return jsonify({"image": user.profile_picture})


@app.route("/profile/pictures", methods=["GET"])
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
            for sub in subscriptions.auto_paging_iter():
                stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
        except Exception:
            pass
 
    # Don't immediately revoke — let it expire at period end
    return jsonify({"success": True})

# ── Plaid routes ──────────────────────────────────────────────────────────────
@app.route("/plaid/create_link_token", methods=["POST"])
def create_link_token():
    try:
        data    = request.get_json() or {}
        user_id = str(data.get("user_id", "default_user"))

        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            client_name="EverNest",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        response   = plaid_client.link_token_create(req)
        link_token = response["link_token"]
        return jsonify({"link_token": link_token})

    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/exchange_token", methods=["POST"])
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

        user = find_user_by_id(user_id)
        if user:
            user.plaid_access_token = access_token
            db.session.commit()

        return jsonify({"success": True})
    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/accounts", methods=["GET"])
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
                    accounts = [a.to_dict() for a in resp["accounts"]]
                    # Tag each account with owner username
                    for acct in accounts:
                        acct["owner"] = u.username
                    all_accounts.extend(accounts)
                except Exception:
                    pass
 
        return jsonify({"accounts": all_accounts})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/transactions", methods=["GET"])
def get_transactions():
    try:
        user_id = request.args.get("user_id")
        user    = find_user_by_id(user_id)

        if not user or not user.plaid_access_token:
            return jsonify({"transactions": []})

        end_date   = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=30)

        req = TransactionsGetRequest(
            access_token=user.plaid_access_token,
            start_date=start_date,
            end_date=end_date,
            options=TransactionsGetRequestOptions(count=100),
        )
        response     = plaid_client.transactions_get(req)
        transactions = [t.to_dict() for t in response["transactions"]]
        return jsonify({"transactions": transactions})
    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400
    
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
                const handler = Plaid.create({
                    token: data.link_token,
                    onSuccess: function(public_token, metadata) {
                        document.getElementById("status").innerHTML = "<p>Linking account...</p>";
                        fetch("/plaid/exchange_token", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({public_token: public_token, user_id: userId})
                        })
                        .then(r => r.json())
                        .then(() => {
                            document.getElementById("status").innerHTML =
                                "<p style='color:#4CFF7A'>✓ Bank connected! Close this tab and click Refresh in EverNest.</p>";
                        });
                    },
                    onExit: function() {
                        document.getElementById("status").innerHTML =
                            "<p>Cancelled. Close this tab and try again.</p>";
                    }
                });
                handler.open();
            });
        </script>
    </body>
    </html>
    """
    return html, 200, {"Content-Type": "text/html"}

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)