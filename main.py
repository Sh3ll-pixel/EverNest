import os
import random
import customtkinter as ctk
from PIL import Image, ImageTk
from pyuiWidgets.imageLabel import ImageLabel
import requests
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_BASE = "http://127.0.0.1:5000"

active_signup_window = None


main = ctk.CTk()
main.configure(fg_color="#23272D")
main.title("EverNest")
main.geometry("740x394")
main.update_idletasks()

geometryX = 0
geometryY = 0

main.geometry("+%d+%d" % (geometryX, geometryY))

main.resizable(False, False)


def center_toplevel(window, width, height):
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    x = int((screen_width / 2) - (width / 2))
    y = int((screen_height / 2) - (height / 2))

    window.geometry(f"{width}x{height}+{x}+{y}")


def close_signup_if_open():
    global active_signup_window
    try:
        if active_signup_window is not None and active_signup_window.winfo_exists():
            active_signup_window.destroy()
    except Exception:
        pass
    active_signup_window = None


def render_main_application(app_window, user_data=None):
    for widget in app_window.winfo_children():
        widget.destroy()

    app_window.configure(fg_color="#23272D")
    center_toplevel(app_window, 1100, 680)

    sidebar = ctk.CTkFrame(app_window, fg_color="#1C2026", width=220, height=680, corner_radius=0)
    sidebar.place(x=0, y=0)

    app_title = ctk.CTkLabel(sidebar, text="EverNest")
    app_title.configure(
        fg_color="transparent",
        width=120,
        height=40,
        text_color="#96abff",
        font=ctk.CTkFont(size=28, weight="bold")
    )
    app_title.place(x=35, y=28)

    nav_home = ctk.CTkButton(sidebar, text="Dashboard")
    nav_home.configure(
        fg_color="#029CFF",
        hover_color="#1e538d",
        width=150,
        height=36,
        text_color="#000000",
        corner_radius=6
    )
    nav_home.place(x=35, y=110)

    nav_accounts = ctk.CTkButton(sidebar, text="Accounts")
    nav_accounts.configure(
        fg_color="#343739",
        hover_color="#1e538d",
        width=150,
        height=36,
        text_color="#96abff",
        corner_radius=6
    )
    nav_accounts.place(x=35, y=156)

    nav_calendar = ctk.CTkButton(sidebar, text="Calendar")
    nav_calendar.configure(
        fg_color="#343739",
        hover_color="#1e538d",
        width=150,
        height=36,
        text_color="#96abff",
        corner_radius=6
    )
    nav_calendar.place(x=35, y=202)

    nav_settings = ctk.CTkButton(sidebar, text="Settings")
    nav_settings.configure(
        fg_color="#343739",
        hover_color="#1e538d",
        width=150,
        height=36,
        text_color="#96abff",
        corner_radius=6
    )
    nav_settings.place(x=35, y=248)

    def close_app():
        app_window.destroy()
        main.destroy()

    logout_button = ctk.CTkButton(sidebar, text="Log Out", command=close_app)
    logout_button.configure(
        fg_color="transparent",
        hover_color="#1e538d",
        width=150,
        height=30,
        text_color="#96abff",
        corner_radius=6,
        border_width=1,
        border_color="#5a5b5b"
    )
    logout_button.place(x=35, y=620)

    topbar = ctk.CTkFrame(app_window, fg_color="#23272D", width=880, height=80, corner_radius=0)
    topbar.place(x=220, y=0)

    username = "User"
    email = ""
    if user_data:
        username = user_data.get("username", "User")
        email = user_data.get("email", "")

    welcome_label = ctk.CTkLabel(app_window, text=f"Welcome back, {username}")
    welcome_label.configure(
        fg_color="transparent",
        width=260,
        height=40,
        text_color="#96abff",
        font=ctk.CTkFont(size=24, weight="bold")
    )
    welcome_label.place(x=255, y=28)

    sub_label = ctk.CTkLabel(app_window, text=email)
    sub_label.configure(
        fg_color="transparent",
        width=260,
        height=24,
        text_color="#5a82ff",
        font=ctk.CTkFont(size=13)
    )
    sub_label.place(x=255, y=58)

    card1 = ctk.CTkFrame(app_window, fg_color="#2C3138", width=250, height=120, corner_radius=8)
    card1.place(x=255, y=110)

    card1_title = ctk.CTkLabel(card1, text="Accounts")
    card1_title.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=18, weight="bold")
    )
    card1_title.place(x=20, y=18)

    card1_value = ctk.CTkLabel(card1, text="12")
    card1_value.configure(
        fg_color="transparent",
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=28, weight="bold")
    )
    card1_value.place(x=20, y=52)

    card1_sub = ctk.CTkLabel(card1, text="Tracked records")
    card1_sub.configure(
        fg_color="transparent",
        text_color="#9A9A9A",
        font=ctk.CTkFont(size=12)
    )
    card1_sub.place(x=20, y=86)

    card2 = ctk.CTkFrame(app_window, fg_color="#2C3138", width=250, height=120, corner_radius=8)
    card2.place(x=525, y=110)

    card2_title = ctk.CTkLabel(card2, text="Due Soon")
    card2_title.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=18, weight="bold")
    )
    card2_title.place(x=20, y=18)

    card2_value = ctk.CTkLabel(card2, text="4")
    card2_value.configure(
        fg_color="transparent",
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=28, weight="bold")
    )
    card2_value.place(x=20, y=52)

    card2_sub = ctk.CTkLabel(card2, text="Upcoming reminders")
    card2_sub.configure(
        fg_color="transparent",
        text_color="#9A9A9A",
        font=ctk.CTkFont(size=12)
    )
    card2_sub.place(x=20, y=86)

    card3 = ctk.CTkFrame(app_window, fg_color="#2C3138", width=250, height=120, corner_radius=8)
    card3.place(x=795, y=110)

    card3_title = ctk.CTkLabel(card3, text="Secure Vault")
    card3_title.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=18, weight="bold")
    )
    card3_title.place(x=20, y=18)

    card3_value = ctk.CTkLabel(card3, text="Ready")
    card3_value.configure(
        fg_color="transparent",
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=28, weight="bold")
    )
    card3_value.place(x=20, y=52)

    card3_sub = ctk.CTkLabel(card3, text="Encrypted storage online")
    card3_sub.configure(
        fg_color="transparent",
        text_color="#9A9A9A",
        font=ctk.CTkFont(size=12)
    )
    card3_sub.place(x=20, y=86)

    activity_panel = ctk.CTkFrame(app_window, fg_color="#2C3138", width=790, height=360, corner_radius=8)
    activity_panel.place(x=255, y=260)

    activity_title = ctk.CTkLabel(activity_panel, text="Overview")
    activity_title.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=20, weight="bold")
    )
    activity_title.place(x=22, y=18)

    line1 = ctk.CTkLabel(activity_panel, text="• Password vault synced")
    line1.configure(
        fg_color="transparent",
        text_color="#D8D8D8",
        font=ctk.CTkFont(size=14)
    )
    line1.place(x=24, y=72)

    line2 = ctk.CTkLabel(activity_panel, text="• Notifications system standing by")
    line2.configure(
        fg_color="transparent",
        text_color="#D8D8D8",
        font=ctk.CTkFont(size=14)
    )
    line2.place(x=24, y=108)

    line3 = ctk.CTkLabel(activity_panel, text="• Family data categories ready")
    line3.configure(
        fg_color="transparent",
        text_color="#D8D8D8",
        font=ctk.CTkFont(size=14)
    )
    line3.place(x=24, y=144)

    line4 = ctk.CTkLabel(activity_panel, text="• Main dashboard loaded successfully")
    line4.configure(
        fg_color="transparent",
        text_color="#D8D8D8",
        font=ctk.CTkFont(size=14)
    )
    line4.place(x=24, y=180)

    footer = ctk.CTkLabel(app_window, text="N0Ctrl Studios 2026")
    footer.configure(
        fg_color="transparent",
        width=120,
        height=24,
        text_color="#333333",
        font=ctk.CTkFont(size=12)
    )
    footer.place(x=925, y=648)


def open_application_window(user_data=None):
    main.withdraw()

    app_window = ctk.CTkToplevel(main)
    app_window.configure(fg_color="#23272D")
    app_window.title("EverNest")
    center_toplevel(app_window, 820, 520)
    app_window.resizable(False, False)
    app_window.lift()
    app_window.attributes("-topmost", True)
    app_window.after(200, lambda: app_window.attributes("-topmost", False))
    app_window.focus_force()

    def close_app():
        app_window.destroy()
        main.destroy()

    app_window.protocol("WM_DELETE_WINDOW", close_app)

    loading_frame = ctk.CTkFrame(app_window, fg_color="#23272D", corner_radius=0)
    loading_frame.place(x=0, y=0, relwidth=1, relheight=1)

    spinner_label = ctk.CTkLabel(loading_frame, text="◜")
    spinner_label.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=62, weight="bold")
    )
    spinner_label.place(relx=0.5, rely=0.42, anchor="center")

    loading_label = ctk.CTkLabel(loading_frame, text="Loading...")
    loading_label.configure(
        fg_color="transparent",
        text_color="#96abff",
        font=ctk.CTkFont(size=22, weight="bold")
    )
    loading_label.place(relx=0.5, rely=0.57, anchor="center")

    spinner_frames = ["◜", "◠", "◝", "◞", "◡", "◟"]
    spinner_running = {"value": True}

    def animate_spinner(index=0):
        if not spinner_running["value"]:
            return
        spinner_label.configure(text=spinner_frames[index % len(spinner_frames)])
        app_window.after(120, lambda: animate_spinner(index + 1))

    animate_spinner()

    delay_ms = random.randint(3, 5) * 1000

    def finish_loading():
        spinner_running["value"] = False
        render_main_application(app_window, user_data)

    app_window.after(delay_ms, finish_loading)


def login_request(email, password):
    try:
        response = requests.post(
            f"{API_BASE}/login",
            json={"login": email, "password": password},
            timeout=10
        )
        data = response.json()

        if response.ok and data.get("success"):
            main.after(0, close_signup_if_open)
            main.after(0, lambda: open_application_window(data.get("user")))
        else:
            main.after(0, lambda: label.configure(
                text=data.get("message", "Login failed"),
                text_color="#ff6b6b"
            ))

    except requests.RequestException:
        main.after(0, lambda: label.configure(
            text="Server connection failed",
            text_color="#ff6b6b"
        ))
    except ValueError:
        main.after(0, lambda: label.configure(
            text="Invalid server response",
            text_color="#ff6b6b"
        ))
    finally:
        main.after(0, lambda: submit.configure(state="normal"))


def submit_login():
    email = entry.get().strip()
    password = entry1.get().strip()

    if not email or not password:
        label.configure(text="Enter email and password", text_color="#ff6b6b")
        return

    label.configure(text="Signing In...", text_color="#96abff")
    submit.configure(state="disabled")

    threading.Thread(target=login_request, args=(email, password), daemon=True).start()


def signup_request(username, email, password, signup_window, status_label, signup_submit):
    try:
        response = requests.post(
            f"{API_BASE}/signup",
            json={
                "username": username,
                "email": email,
                "password": password
            },
            timeout=10
        )
        data = response.json()

        if response.ok and data.get("success"):
            def success_ui():
                status_label.configure(text="Account created successfully", text_color="#4CFF7A")
                label.configure(text="Sign In", text_color="#96abff")
                signup_window.after(1200, signup_window.destroy)

            main.after(0, success_ui)
        else:
            main.after(0, lambda: status_label.configure(
                text=data.get("message", "Signup failed"),
                text_color="#ff6b6b"
            ))

    except requests.RequestException:
        main.after(0, lambda: status_label.configure(
            text="Server connection failed",
            text_color="#ff6b6b"
        ))
    except ValueError:
        main.after(0, lambda: status_label.configure(
            text="Invalid server response",
            text_color="#ff6b6b"
        ))
    finally:
        main.after(0, lambda: signup_submit.configure(state="normal"))


def open_signup():
    global active_signup_window

    if active_signup_window is not None:
        try:
            if active_signup_window.winfo_exists():
                active_signup_window.lift()
                active_signup_window.focus_force()
                return
        except Exception:
            active_signup_window = None

    signup_window = ctk.CTkToplevel(main)
    active_signup_window = signup_window

    signup_window.configure(fg_color="#23272D")
    signup_window.title("Create Account")

    window_width = 320
    window_height = 300

    main.update_idletasks()
    main_x = main.winfo_x()
    main_y = main.winfo_y()
    main_width = main.winfo_width()
    main_height = main.winfo_height()

    x = main_x + (main_width // 2) - (window_width // 2)
    y = main_y + (main_height // 2) - (window_height // 2)

    signup_window.geometry(f"{window_width}x{window_height}+{x}+{y}")
    signup_window.resizable(False, False)

    signup_window.transient(main)
    signup_window.lift()
    signup_window.attributes("-topmost", True)
    signup_window.after(200, lambda: signup_window.attributes("-topmost", False))
    signup_window.focus_force()
    signup_window.grab_set()

    def on_signup_close():
        global active_signup_window
        active_signup_window = None
        signup_window.destroy()

    signup_window.protocol("WM_DELETE_WINDOW", on_signup_close)

    signup_label = ctk.CTkLabel(master=signup_window, text="Create Account")
    signup_label.configure(
        fg_color="transparent",
        width=100,
        height=40,
        text_color="#96abff",
        font=ctk.CTkFont()
    )
    signup_label.place(x=95, y=20)

    username_entry = ctk.CTkEntry(master=signup_window, placeholder_text="Username")
    username_entry.configure(
        fg_color="#343739",
        width=200,
        height=40,
        text_color="#96abff",
        corner_radius=5,
        border_width=2,
        border_color="#5a5b5b"
    )
    username_entry.place(x=60, y=70)

    email_entry = ctk.CTkEntry(master=signup_window, placeholder_text="Email")
    email_entry.configure(
        fg_color="#343739",
        width=200,
        height=40,
        text_color="#96abff",
        corner_radius=5,
        border_width=2,
        border_color="#5a5b5b"
    )
    email_entry.place(x=60, y=120)

    password_entry = ctk.CTkEntry(master=signup_window, placeholder_text="Password", show="*")
    password_entry.configure(
        fg_color="#343739",
        width=200,
        height=40,
        text_color="#96abff",
        corner_radius=5,
        border_width=2,
        border_color="#5a5b5b"
    )
    password_entry.place(x=60, y=170)

    status_label = ctk.CTkLabel(master=signup_window, text="")
    status_label.configure(
        fg_color="transparent",
        width=220,
        height=20,
        text_color="#96abff",
        font=ctk.CTkFont()
    )
    status_label.place(x=50, y=215)

    def submit_signup():
        username = username_entry.get().strip()
        email = email_entry.get().strip()
        password = password_entry.get().strip()

        if not username or not email or not password:
            status_label.configure(text="Fill out all fields", text_color="#ff6b6b")
            return

        status_label.configure(text="Creating account...", text_color="#96abff")
        signup_submit.configure(state="disabled")

        threading.Thread(
            target=signup_request,
            args=(username, email, password, signup_window, status_label, signup_submit),
            daemon=True
        ).start()

    signup_submit = ctk.CTkButton(master=signup_window, text="Create Account", command=submit_signup)
    signup_submit.configure(
        fg_color="#029CFF",
        hover_color="#1e538d",
        width=120,
        height=28,
        text_color="#000000",
        corner_radius=5
    )
    signup_submit.place(x=100, y=250)


label = ctk.CTkLabel(master=main, text="Sign In")
label.configure(fg_color="transparent", width=100, height=40, text_color="#96abff", font=ctk.CTkFont())
label.place(x=569, y=38)

entry = ctk.CTkEntry(master=main, placeholder_text="Email")
entry.configure(fg_color="#343739", width=160, height=40, text_color="#96abff", corner_radius=5, border_width=2, border_color="#5a5b5b")
entry.place(x=540, y=80)

entry1 = ctk.CTkEntry(master=main, placeholder_text="Password", show="*")
entry1.configure(fg_color="#343739", width=160, height=40, text_color="#96abff", corner_radius=5, border_width=2, border_color="#5a5b5b")
entry1.place(x=541, y=132)

submit = ctk.CTkButton(master=main, text="Submit", command=submit_login)
submit.configure(fg_color="#029CFF", hover_color="#1e538d", width=98, height=19, text_color="#000000", corner_radius=5)
submit.place(x=573, y=190)

sign_up = ctk.CTkButton(master=main, text="Sign Up", command=open_signup)
sign_up.configure(fg_color="transparent", hover_color="#1e538d", width=98, height=16, text_color="#96abff", corner_radius=5)
sign_up.place(x=574, y=212)

label1 = ctk.CTkLabel(master=main, text="N0Ctrl Studios 2026")
label1.configure(fg_color="transparent", width=100, height=33, text_color="#333333", font=ctk.CTkFont())
label1.place(x=616, y=365)

background = ImageLabel(master=main, image_path=os.path.join(BASE_DIR, "assets", "images", "background.jpg"), text="", compound=ctk.TOP, mode="cover")
background.configure(fg_color="transparent", width=508, height=411, text_color="#fff", font=ctk.CTkFont())
background.place(x=-5, y=-2)


main.mainloop()