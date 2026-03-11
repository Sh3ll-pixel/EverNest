import os
import datetime
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.country_code import CountryCode
from plaid.model.products import Products

app = Flask(__name__)
CORS(app)

# ── App config ────────────────────────────────────────────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///users.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-this")

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ── Database models ───────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    plaid_access_token = db.Column(db.String(255), nullable=True)

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

configuration = plaid.Configuration(
    host=_env_map.get(PLAID_ENV, plaid.Environment.Sandbox),
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
)
api_client   = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)


# ── Core routes ───────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return "API running", 200

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

    return jsonify({
        "success": True,
        "message": "Login successful",
        "user": {
            "id":       user.id,
            "username": user.username,
            "email":    user.email
        }
    }), 200

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

        user = User.query.filter(
            (User.id == user_id) | (User.username == user_id)
        ).first()
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
        user    = User.query.filter(
            (User.id == user_id) | (User.username == user_id)
        ).first()

        if not user or not user.plaid_access_token:
            return jsonify({"accounts": []})

        response = plaid_client.accounts_get(AccountsGetRequest(access_token=user.plaid_access_token))
        accounts = [a.to_dict() for a in response["accounts"]]
        return jsonify({"accounts": accounts})
    except plaid.ApiException as e:
        return jsonify({"error": str(e)}), 400


@app.route("/plaid/transactions", methods=["GET"])
def get_transactions():
    try:
        user_id = request.args.get("user_id")
        user    = User.query.filter(
            (User.id == user_id) | (User.username == user_id)
        ).first()

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
 
