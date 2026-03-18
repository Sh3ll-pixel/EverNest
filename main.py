import os
import sys
import random
import datetime
import calendar as cal_mod
import webbrowser
import base64
import io
import customtkinter as ctk
from tkinter import filedialog
from PIL import Image, ImageTk, ImageDraw
from pyuiWidgets.imageLabel import ImageLabel
import requests
import threading


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


API_BASE = "https://evernest-swz9.onrender.com"
active_signup_window = None

# Subscription cache
_sub_cache = {"subscribed": False, "checked": False}

# Profile picture cache  {user_id_str: CTkImage or None}
_pfp_cache = {}

# Accent color system
_accent_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".evernest_accent.txt")
_accent = {"color": "#3b5bdb", "widgets": []}  # widgets = list of (widget, role) to update live

# Notification tracking — global so it persists across tab switches
# Stores tuples of (event_id, date_str) to allow recurring events on different days
_notified_events = set()

def _load_accent_color():
    try:
        if os.path.exists(_accent_config_path):
            with open(_accent_config_path, "r") as f:
                c = f.read().strip()
                if c.startswith("#") and len(c) in (4, 7):
                    _accent["color"] = c
    except Exception:
        pass

def _save_accent_color(color):
    _accent["color"] = color
    try:
        with open(_accent_config_path, "w") as f:
            f.write(color)
    except Exception:
        pass

def _apply_accent_to_widgets():
    """Update all registered widgets with the current accent color."""
    color = _accent["color"]
    # Compute a slightly darker hover color
    try:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        hover = f"#{max(0,r-30):02x}{max(0,g-30):02x}{max(0,b-30):02x}"
    except Exception:
        hover = color

    for widget, role in _accent["widgets"]:
        try:
            if not widget.winfo_exists():
                continue
            if role == "button":
                widget.configure(fg_color=color, hover_color=hover)
            elif role == "active_nav":
                widget.configure(fg_color=color)
            elif role == "text":
                widget.configure(text_color=color)
            elif role == "progress":
                widget.configure(progress_color=color)
        except Exception:
            pass

def register_accent_widget(widget, role="button"):
    """Register a widget to be updated when accent color changes."""
    _accent["widgets"].append((widget, role))

_load_accent_color()


def b64_to_ctk_image(b64_string, size=(40, 40)):
    """Convert a base64 JPEG string into a circular CTkImage."""
    try:
        img_data = base64.b64decode(b64_string)
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)

        # Create circular mask
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size[0], size[1]), fill=255)
        img.putalpha(mask)

        return ctk.CTkImage(light_image=img, dark_image=img, size=size)
    except Exception:
        return None


def pick_and_upload_profile_picture(user_data, callback=None):
    """Open file dialog, resize, upload to server, call callback(b64) on success."""
    filepath = filedialog.askopenfilename(
        title="Choose a profile picture",
        filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp")]
    )
    if not filepath:
        return

    def _do():
        try:
            img = Image.open(filepath).convert("RGB")
            # Resize to 128x128 to keep it small
            img.thumbnail((128, 128), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            user_id = user_data.get("id") or user_data.get("username", "")
            resp = requests.post(
                f"{API_BASE}/profile/upload_picture",
                json={"user_id": user_id, "image": b64},
                timeout=15
            )
            if resp.ok and resp.json().get("success"):
                # Update cache
                _pfp_cache[str(user_id)] = b64
                if callback:
                    main.after(0, lambda: callback(b64))
        except Exception as e:
            print(f"Profile picture upload failed: {e}")

    threading.Thread(target=_do, daemon=True).start()


def fetch_my_profile_picture(user_data, callback=None):
    """Fetch current user's profile picture from server."""
    user_id = user_data.get("id") or user_data.get("username", "")
    uid_str = str(user_id)

    if uid_str in _pfp_cache:
        if callback:
            callback(_pfp_cache[uid_str])
        return

    def _do():
        try:
            resp = requests.get(
                f"{API_BASE}/profile/picture",
                params={"user_id": user_id}, timeout=8
            )
            if resp.ok:
                b64 = resp.json().get("image")
                _pfp_cache[uid_str] = b64
                if callback:
                    main.after(0, lambda: callback(b64))
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True).start()

main = ctk.CTk()
main.configure(fg_color="#0c0e14")
main.title("EverNest")
main.geometry("800x440")
main.update_idletasks()
main.geometry("+0+0")
main.resizable(False, False)


def center_toplevel(window, width, height):
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x  = int((sw / 2) - (width / 2))
    y  = int((sh / 2) - (height / 2))
    window.geometry(f"{width}x{height}+{x}+{y}")


def close_signup_if_open():
    global active_signup_window
    try:
        if active_signup_window is not None and active_signup_window.winfo_exists():
            active_signup_window.destroy()
    except Exception:
        pass
    active_signup_window = None


# =============================================================================
#  MAIN APPLICATION SHELL
# =============================================================================

def render_main_application(app_window, user_data=None):
    for widget in app_window.winfo_children():
        widget.destroy()

    app_window.configure(fg_color="#1a1d23")
    center_toplevel(app_window, 1100, 680)

    # ── Sidebar ──────────────────────────────────────────────────────────────
    sidebar = ctk.CTkFrame(app_window, fg_color="#13161b", width=220, height=680, corner_radius=0)
    sidebar.place(x=0, y=0)

    # Thin accent line on right edge of sidebar
    sidebar_accent_line = ctk.CTkFrame(app_window, fg_color=_accent["color"], width=2, height=680, corner_radius=0)
    sidebar_accent_line.place(x=219, y=0)
    register_accent_widget(sidebar_accent_line, "active_nav")

    brand_label = ctk.CTkLabel(
        sidebar, text="⬡  EverNest",
        fg_color="transparent", width=180, height=40,
        text_color=_accent["color"], font=ctk.CTkFont(size=22, weight="bold")
    )
    brand_label.place(x=20, y=24)
    register_accent_widget(brand_label, "text")

    # Subtle divider below logo
    ctk.CTkFrame(sidebar, fg_color="#252830", height=1, width=180).place(x=20, y=72)

    username = user_data.get("username", "User") if user_data else "User"
    email    = user_data.get("email", "")        if user_data else ""

    content_frame = ctk.CTkFrame(app_window, fg_color="#1a1d23", width=880, height=680, corner_radius=0)
    content_frame.place(x=220, y=0)
    content_frame.pack_propagate(False)

    active_tab  = {"name": None}
    nav_buttons = {}
    NAV_ITEMS = [
        ("Dashboard",  "📊"),
        ("Financial",  "🏦"),
        ("Calendar",   "📅"),
        ("Budget",     "💰"),
        ("Notes",      "📝"),
        ("My Family",  "👨‍👩‍👧"),
        ("Subscribe",  "⭐"),
        ("Settings",   "⚙️"),
    ]

    def set_active_nav(tab_name):
        for name, btn in nav_buttons.items():
            if name == tab_name:
                btn.configure(fg_color="#1e2a42", text_color=_accent["color"],
                              border_width=0)
            else:
                btn.configure(fg_color="transparent", text_color="#6b7280",
                              border_width=0)

    def switch_tab(tab_name):
        if active_tab["name"] == tab_name:
            return
        active_tab["name"] = tab_name
        set_active_nav(tab_name)
        for w in content_frame.winfo_children():
            w.destroy()
        if tab_name == "Dashboard":
            render_dashboard_tab(content_frame, username, email, user_data, switch_tab_fn=switch_tab)
        elif tab_name == "Financial":
            render_financial_tab(content_frame, user_data, switch_tab_fn=switch_tab)
        elif tab_name == "Calendar":
            render_calendar_tab(content_frame, user_data, app_window)
        elif tab_name == "My Family":
            render_family_tab(content_frame, user_data, switch_tab_fn=switch_tab)
        elif tab_name == "Budget":
            render_budget_tab(content_frame, user_data, switch_tab_fn=switch_tab)
        elif tab_name == "Notes":
            render_notes_tab(content_frame, user_data)
        elif tab_name == "Subscribe":
            render_subscribe_tab(content_frame, user_data)
        elif tab_name == "Settings":
            render_settings_tab(content_frame, user_data, switch_tab)

    y_pos = 90
    for item, icon in NAV_ITEMS:
        btn = ctk.CTkButton(
            sidebar, text=f"  {icon}   {item}",
            fg_color="transparent", hover_color="#1e2a42",
            width=180, height=38, text_color="#6b7280", corner_radius=8,
            anchor="w", font=ctk.CTkFont(size=13),
            command=lambda t=item: switch_tab(t)
        )
        btn.place(x=20, y=y_pos)
        nav_buttons[item] = btn
        y_pos += 42

    # ── User profile at bottom ───────────────────────────────────────────────
    ctk.CTkFrame(sidebar, fg_color="#252830", height=1, width=180).place(x=20, y=580)

    # Profile picture / initials badge
    initials = username[0].upper() if username else "U"
    badge = ctk.CTkFrame(sidebar, fg_color="#1e2a42", width=36, height=36, corner_radius=18)
    badge.place(x=20, y=596)
    badge_label = ctk.CTkLabel(badge, text=initials, fg_color="transparent",
                  text_color="#7B93DB", font=ctk.CTkFont(size=14, weight="bold"),
                  width=36, height=36, image=None)
    badge_label.place(x=0, y=0)

    # Load profile picture async
    def _on_pfp_loaded(b64):
        if b64:
            img = b64_to_ctk_image(b64, size=(36, 36))
            if img:
                badge_label.configure(image=img, text="")

    fetch_my_profile_picture(user_data, callback=_on_pfp_loaded)

    ctk.CTkLabel(sidebar, text=username, fg_color="transparent",
                  text_color="#9ca3af", font=ctk.CTkFont(size=12)).place(x=64, y=596)
    ctk.CTkLabel(sidebar, text=email if len(email) < 24 else email[:21] + "…",
                  fg_color="transparent",
                  text_color="#4b5563", font=ctk.CTkFont(size=10)).place(x=64, y=614)

    def close_app():
        app_window.destroy()
        main.destroy()

    ctk.CTkButton(
        sidebar, text="↩  Log Out", command=close_app,
        fg_color="transparent", hover_color="#2a1a1a",
        width=180, height=30, text_color="#6b7280",
        corner_radius=8, anchor="w",
        font=ctk.CTkFont(size=12)
    ).place(x=20, y=644)

    switch_tab("Dashboard")


# =============================================================================
# Settings Tab
# =============================================================================
def render_settings_tab(parent, user_data=None, switch_tab_fn=None):
    import tkinter as tk
 
    user_id  = ""
    username = ""
    email    = ""
    if user_data:
        user_id  = str(user_data.get("id") or user_data.get("username", ""))
        username = user_data.get("username", "")
        email    = user_data.get("email", "")
 
    SUPPORT_EMAIL  = "support@evernest.pro"
    SUPPORT_WEBSITE = "www.EverNest.pro"
 
    # ── Header ────────────────────────────────────────────────────────────────
    ctk.CTkLabel(parent, text="Settings", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=24, weight="bold")).place(x=30, y=20)
 
    status_var = ctk.StringVar(value="")
    status_lbl = ctk.CTkLabel(parent, textvariable=status_var, fg_color="transparent",
                               text_color="#4CFF7A", font=ctk.CTkFont(size=12))
    status_lbl.place(x=30, y=56)
 
    # ── Scrollable content ────────────────────────────────────────────────────
    scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent", width=840, height=580)
    scroll.place(x=14, y=76)
 
    def section_header(text):
        ctk.CTkLabel(scroll, text=text, fg_color="transparent",
                      text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")
                      ).pack(anchor="w", padx=4, pady=(20, 4))
        ctk.CTkFrame(scroll, fg_color="#1e2438", height=1).pack(fill="x", padx=4, pady=(0, 10))
 
    def setting_row(label, widget_fn):
        row = ctk.CTkFrame(scroll, fg_color="#1f2328", corner_radius=8)
        row.pack(fill="x", padx=4, pady=3)
        ctk.CTkLabel(row, text=label, fg_color="transparent",
                      text_color="#d1d5db", font=ctk.CTkFont(size=13),
                      width=200, anchor="w").pack(side="left", padx=16, pady=12)
        widget_fn(row)
        return row
 
    # =========================================================================
    #  PROFILE
    # =========================================================================
    section_header("👤  Profile")

    # Profile Picture
    def pfp_widget(row):
        # Preview circle
        pfp_preview_frame = ctk.CTkFrame(row, fg_color="#1e2a42", width=40, height=40, corner_radius=20)
        pfp_preview_frame.pack(side="left", padx=(0, 10))

        initials_char = username[0].upper() if username else "U"
        pfp_preview_lbl = ctk.CTkLabel(pfp_preview_frame, text=initials_char,
                                        fg_color="transparent", text_color="#7B93DB",
                                        font=ctk.CTkFont(size=16, weight="bold"),
                                        width=40, height=40)
        pfp_preview_lbl.place(x=0, y=0)

        # Load existing picture
        def _on_existing_loaded(b64):
            if b64:
                img = b64_to_ctk_image(b64, size=(40, 40))
                if img:
                    pfp_preview_lbl.configure(image=img, text="")
        fetch_my_profile_picture(user_data, callback=_on_existing_loaded)

        def _on_upload_done(b64):
            if b64:
                img = b64_to_ctk_image(b64, size=(40, 40))
                if img:
                    pfp_preview_lbl.configure(image=img, text="")
                status_var.set("\u2713  Profile picture updated")

        ctk.CTkButton(row, text="Change Photo", width=110, height=32,
                       fg_color="#343739", hover_color="#1e538d",
                       text_color="#7B93DB", corner_radius=8,
                       font=ctk.CTkFont(size=12),
                       command=lambda: pick_and_upload_profile_picture(
                           user_data, callback=_on_upload_done)
                       ).pack(side="left", padx=(0, 6))

        def _remove_pfp():
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/profile/upload_picture",
                                          json={"user_id": user_id, "image": ""},
                                          timeout=10)
                    if resp.ok:
                        _pfp_cache[str(user_id)] = None
                        parent.after(0, lambda: [
                            pfp_preview_lbl.configure(image=None, text=initials_char),
                            status_var.set("\u2713  Profile picture removed")
                        ])
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(row, text="Remove", width=70, height=32,
                       fg_color="transparent", hover_color="#2a1520",
                       text_color="#ff6b6b", corner_radius=8,
                       font=ctk.CTkFont(size=11),
                       border_width=1, border_color="#ff6b6b",
                       command=_remove_pfp
                       ).pack(side="left")

    setting_row("Profile Picture", pfp_widget)

    # Username
    def username_widget(row):
        e = ctk.CTkEntry(row, placeholder_text=username,
                          fg_color="#363C45", width=220, height=32,
                          text_color="#7B93DB", corner_radius=8,
                          border_width=1, border_color="#2a2f38")
        e.insert(0, username)
        e.pack(side="left", padx=(0, 8))
        def save():
            new_val = e.get().strip()
            if not new_val:
                return
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/settings/update_profile",
                                          json={"user_id": user_id, "username": new_val},
                                          timeout=10)
                    if resp.ok:
                        parent.after(0, lambda: status_var.set("✓  Username updated"))
                    else:
                        parent.after(0, lambda: status_var.set("Failed to update username."))
                except Exception as ex:
                    parent.after(0, lambda: status_var.set(f"Error: {ex}"))
            threading.Thread(target=_do, daemon=True).start()
        ctk.CTkButton(row, text="Save", width=60, height=32,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       text_color="#000", corner_radius=8,
                       command=save).pack(side="left")
    setting_row("Username", username_widget)
 
    # Email
    def email_widget(row):
        e = ctk.CTkEntry(row, placeholder_text=email,
                          fg_color="#363C45", width=220, height=32,
                          text_color="#7B93DB", corner_radius=8,
                          border_width=1, border_color="#2a2f38")
        e.insert(0, email)
        e.pack(side="left", padx=(0, 8))
        def save():
            new_val = e.get().strip()
            if not new_val or "@" not in new_val:
                status_var.set("Enter a valid email.")
                return
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/settings/update_profile",
                                          json={"user_id": user_id, "email": new_val},
                                          timeout=10)
                    if resp.ok:
                        parent.after(0, lambda: status_var.set("✓  Email updated"))
                    else:
                        parent.after(0, lambda: status_var.set("Failed to update email."))
                except Exception as ex:
                    parent.after(0, lambda: status_var.set(f"Error: {ex}"))
            threading.Thread(target=_do, daemon=True).start()
        ctk.CTkButton(row, text="Save", width=60, height=32,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       text_color="#000", corner_radius=8,
                       command=save).pack(side="left")
    setting_row("Email", email_widget)
 
    # Password
    def password_widget(row):
        curr = ctk.CTkEntry(row, placeholder_text="Current password", show="*",
                             fg_color="#363C45", width=160, height=32,
                             text_color="#7B93DB", corner_radius=8,
                             border_width=1, border_color="#2a2f38")
        curr.pack(side="left", padx=(0, 6))
        new_pw = ctk.CTkEntry(row, placeholder_text="New password", show="*",
                               fg_color="#363C45", width=160, height=32,
                               text_color="#7B93DB", corner_radius=8,
                               border_width=1, border_color="#2a2f38")
        new_pw.pack(side="left", padx=(0, 8))
        def save():
            c = curr.get().strip()
            n = new_pw.get().strip()
            if not c or not n:
                status_var.set("Fill in both password fields.")
                return
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/settings/change_password",
                                          json={"user_id": user_id,
                                                "current_password": c,
                                                "new_password": n},
                                          timeout=10)
                    data = resp.json()
                    if resp.ok and data.get("success"):
                        parent.after(0, lambda: [status_var.set("✓  Password changed"),
                                                  curr.delete(0, "end"),
                                                  new_pw.delete(0, "end")])
                    else:
                        parent.after(0, lambda: status_var.set(
                            data.get("message", "Failed to change password.")))
                except Exception as ex:
                    parent.after(0, lambda: status_var.set(f"Error: {ex}"))
            threading.Thread(target=_do, daemon=True).start()
        ctk.CTkButton(row, text="Save", width=60, height=32,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       text_color="#000", corner_radius=8,
                       command=save).pack(side="left")
    setting_row("Change Password", password_widget)
 
    # =========================================================================
    #  SUBSCRIPTION
    # =========================================================================
    section_header("💳  Subscription")
 
    sub_status_var = ctk.StringVar(value="Checking…")
    sub_end_var    = ctk.StringVar(value="")
 
    def sub_status_widget(row):
        ctk.CTkLabel(row, textvariable=sub_status_var, fg_color="transparent",
                      text_color="#4CFF7A", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(row, textvariable=sub_end_var, fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)).pack(side="left")
    setting_row("Status", sub_status_widget)
 
    def sub_actions_widget(row):
        ctk.CTkButton(row, text="Manage Subscription", width=160, height=32,
                       fg_color="#252830", hover_color="#2b4bc8",
                       text_color="#7B93DB", corner_radius=8,
                       command=lambda: switch_tab_fn("Subscribe") if switch_tab_fn else None
                       ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Cancel Subscription", width=160, height=32,
                       fg_color="transparent", hover_color="#2a1520",
                       text_color="#ff6b6b", corner_radius=8,
                       border_width=1, border_color="#ff6b6b",
                       command=lambda: cancel_subscription()
                       ).pack(side="left")
    setting_row("Manage", sub_actions_widget)
 
    def load_sub_status():
        def _do():
            try:
                resp = requests.get(f"{API_BASE}/subscription/status",
                                     params={"user_id": user_id}, timeout=6)
                if resp.ok:
                    data = resp.json()
                    if data.get("subscribed"):
                        end = (data.get("subscription_end") or "")[:10]
                        parent.after(0, lambda: [
                            sub_status_var.set("✓  Active"),
                            sub_end_var.set(f"Renews {end}" if end else "")
                        ])
                    else:
                        parent.after(0, lambda: [
                            sub_status_var.set("Not subscribed"),
                            sub_status_lbl_ref[0].configure(text_color="#9A9A9A")
                            if sub_status_lbl_ref else None
                        ])
            except Exception:
                parent.after(0, lambda: sub_status_var.set("Unavailable"))
        threading.Thread(target=_do, daemon=True).start()
 
    sub_status_lbl_ref = [None]
    load_sub_status()
 
    def cancel_subscription():
        def _do():
            try:
                resp = requests.post(f"{API_BASE}/subscription/cancel",
                                      json={"user_id": user_id}, timeout=10)
                if resp.ok:
                    parent.after(0, lambda: [
                        status_var.set("Subscription cancelled. Access ends at period end."),
                        status_lbl.configure(text_color="#FFD700"),
                        load_sub_status()
                    ])
                else:
                    parent.after(0, lambda: status_var.set("Could not cancel. Contact support."))
            except Exception as ex:
                parent.after(0, lambda: status_var.set(f"Error: {ex}"))
        threading.Thread(target=_do, daemon=True).start()
 
    # =========================================================================
    #  APPEARANCE
    # =========================================================================
    section_header("🎨  Appearance")

    # Accent color
    ACCENT_COLORS = [
        ("#3b5bdb", "Blue"),
        ("#4CFF7A", "Green"),
        ("#FF6B6B", "Red"),
        ("#FFD700", "Gold"),
        ("#C084FC", "Purple"),
        ("#FF9F40", "Orange"),
        ("#7B93DB", "Lavender"),
        ("#38bdf8", "Sky"),
        ("#fb7185", "Pink"),
    ]

    def accent_widget(row):
        accent_rings = []

        def pick_accent(hex_color, name):
            _save_accent_color(hex_color)
            _apply_accent_to_widgets()
            # Update selection rings
            for ring, c in accent_rings:
                ring.configure(fg_color="#ffffff" if c == hex_color else "transparent")
            status_var.set(f"✓  Accent color set to {name} — switch tabs to see it")

        for color, name in ACCENT_COLORS:
            ring = ctk.CTkFrame(row, fg_color="#ffffff" if color == _accent["color"] else "transparent",
                                 width=32, height=32, corner_radius=16)
            ring.pack(side="left", padx=2, pady=6)
            inner = ctk.CTkButton(ring, text="", width=24, height=24,
                                   fg_color=color, hover_color=color,
                                   corner_radius=12,
                                   command=lambda c=color, n=name: pick_accent(c, n))
            inner.place(relx=0.5, rely=0.5, anchor="center")
            accent_rings.append((ring, color))

    setting_row("Accent Color", accent_widget)
 
    # =========================================================================
    #  BANK ACCOUNT
    # =========================================================================
    section_header("🏦  Bank Account")
 
    def remove_bank_widget(row):
        ctk.CTkLabel(row, text="Disconnect your linked Plaid bank account.",
                      fg_color="transparent", text_color="#9A9A9A",
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 16))
        ctk.CTkButton(row, text="Remove Bank Account", width=180, height=32,
                       fg_color="transparent", hover_color="#2a1520",
                       text_color="#ff6b6b", corner_radius=8,
                       border_width=1, border_color="#ff6b6b",
                       command=remove_bank).pack(side="left")
    setting_row("Linked Bank", remove_bank_widget)
 
    def remove_bank():
        def _do():
            try:
                resp = requests.post(f"{API_BASE}/settings/remove_bank",
                                      json={"user_id": user_id}, timeout=10)
                if resp.ok:
                    parent.after(0, lambda: status_var.set(
                        "✓  Bank account disconnected."))
                else:
                    parent.after(0, lambda: status_var.set("Failed to remove bank."))
            except Exception as ex:
                parent.after(0, lambda: status_var.set(f"Error: {ex}"))
        threading.Thread(target=_do, daemon=True).start()
 
    # =========================================================================
    #  NOTIFICATIONS
    # =========================================================================
    section_header("🔔  Notifications")
 
    def notif_widget(row):
        var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(row, text="Enable popup reminders for calendar events",
                       variable=var, fg_color="#252830",
                       progress_color="#3b5bdb",
                       text_color="#d1d5db",
                       font=ctk.CTkFont(size=13)).pack(side="left", padx=8, pady=10)
    setting_row("Reminders", notif_widget)
 
    # =========================================================================
    #  REPORT A BUG
    # =========================================================================
    section_header("🐛  Report a Bug")
 
    def bug_widget(row):
        e = ctk.CTkEntry(row, placeholder_text="Describe the bug…",
                          fg_color="#363C45", width=420, height=32,
                          text_color="#7B93DB", corner_radius=8,
                          border_width=1, border_color="#2a2f38")
        e.pack(side="left", padx=(0, 8))
        def submit():
            desc = e.get().strip()
            if not desc:
                status_var.set("Please describe the bug first.")
                return
            def _do():
                try:
                    requests.post(f"{API_BASE}/settings/report_bug",
                                   json={"user_id": user_id, "description": desc},
                                   timeout=10)
                    parent.after(0, lambda: [
                        status_var.set("✓  Bug report submitted. Thank you!"),
                        e.delete(0, "end")
                    ])
                except Exception:
                    parent.after(0, lambda: status_var.set("Could not submit report."))
            threading.Thread(target=_do, daemon=True).start()
        ctk.CTkButton(row, text="Submit", width=80, height=32,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       text_color="#000", corner_radius=8,
                       command=submit).pack(side="left")
    setting_row("Bug Report", bug_widget)
 
    # =========================================================================
    #  CONTACT US
    # =========================================================================
    section_header("📬  Contact Us")
 
    def contact_email_widget(row):
        ctk.CTkLabel(row, text=SUPPORT_EMAIL, fg_color="transparent",
                      text_color="#7B93DB", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 12))
        def copy_email():
            parent.clipboard_clear()
            parent.clipboard_append(SUPPORT_EMAIL)
            status_var.set("✓  Email copied to clipboard")
        ctk.CTkButton(row, text="Copy Email", width=100, height=30,
                       fg_color="#252830", hover_color="#2b4bc8",
                       text_color="#7B93DB", corner_radius=8,
                       command=copy_email).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Visit Website", width=110, height=30,
                       fg_color="#252830", hover_color="#2b4bc8",
                       text_color="#7B93DB", corner_radius=8,
                       command=lambda: webbrowser.open(SUPPORT_WEBSITE)
                       ).pack(side="left")
    setting_row("Support", contact_email_widget)
 
    # =========================================================================
    #  DELETE ACCOUNT
    # =========================================================================
    section_header("⚠️  Danger Zone")
 
    def delete_widget(row):
        confirm_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row, text="I understand this is permanent and cannot be undone",
                         variable=confirm_var, fg_color="#FF6B6B",
                         hover_color="#2a1520", text_color="#9A9A9A",
                         font=ctk.CTkFont(size=12),
                         checkbox_width=16, checkbox_height=16
                         ).pack(side="left", padx=(0, 16), pady=10)
        def delete():
            if not confirm_var.get():
                status_var.set("Check the confirmation box first.")
                status_lbl.configure(text_color="#FF6B6B")
                return
            def _do():
                try:
                    resp = requests.delete(f"{API_BASE}/settings/delete_account",
                                            json={"user_id": user_id}, timeout=10)
                    if resp.ok:
                        parent.after(0, lambda: [
                            status_var.set("Account deleted."),
                            parent.after(2000, lambda: main.destroy())
                        ])
                    else:
                        parent.after(0, lambda: status_var.set("Failed to delete account."))
                except Exception as ex:
                    parent.after(0, lambda: status_var.set(f"Error: {ex}"))
            threading.Thread(target=_do, daemon=True).start()
        ctk.CTkButton(row, text="Delete My Account", width=160, height=32,
                       fg_color="transparent", hover_color="#2a1520",
                       text_color="#ff6b6b", corner_radius=8,
                       border_width=1, border_color="#ff6b6b",
                       command=delete).pack(side="left")
    setting_row("Delete Account", delete_widget)
 
    # =========================================================================
    #  ABOUT
    # =========================================================================
    section_header("ℹ️  About")
 
    def about_widget(row):
        ctk.CTkLabel(row, text="EverNest  v1.0.0   —   N0Ctrl Studios 2026",
                      fg_color="transparent", text_color="#9A9A9A",
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 20))
        ctk.CTkButton(row, text="Visit Website", width=110, height=30,
                       fg_color="#252830", hover_color="#2b4bc8",
                       text_color="#7B93DB", corner_radius=8,
                       command=lambda: webbrowser.open(SUPPORT_WEBSITE)
                       ).pack(side="left")
    setting_row("Version", about_widget)

# =============================================================================
#  DASHBOARD TAB
# =============================================================================

def render_dashboard_tab(parent, username, email, user_data=None, switch_tab_fn=None):
    import tkinter as tk

    user_id = ""
    if user_data:
        user_id = str(user_data.get("id") or user_data.get("username", ""))

    # ── Time-based greeting ───────────────────────────────────────────────────
    hour = datetime.datetime.now().hour
    if hour < 12:
        greeting = f"Good morning, {username}"
    elif hour < 17:
        greeting = f"Good afternoon, {username}"
    else:
        greeting = f"Good evening, {username}"

    ctk.CTkLabel(
        parent, text=greeting,
        fg_color="transparent", width=400, height=32,
        text_color="#e5e7eb", font=ctk.CTkFont(size=22, weight="bold")
    ).place(x=32, y=16)

    today_str = datetime.date.today().strftime("%A, %B %d, %Y")
    ctk.CTkLabel(
        parent, text=today_str,
        fg_color="transparent", width=300, height=18,
        text_color="#4b5563", font=ctk.CTkFont(size=12)
    ).place(x=32, y=46)

    # ── Setup banner for new users (hidden by default) ────────────────────────
    setup_banner = ctk.CTkFrame(parent, fg_color="#1a2232", width=400, height=36,
                                  corner_radius=8, border_width=1, border_color="#263354")
    setup_banner.place(x=440, y=20)
    setup_banner.place_forget()

    banner_label = ctk.CTkLabel(setup_banner, text="", fg_color="transparent",
                                 text_color="#8b9cf7", font=ctk.CTkFont(size=11))
    banner_label.place(x=12, y=7)

    banner_btn = ctk.CTkButton(setup_banner, text="Set up →", width=70, height=24,
                                fg_color=_accent["color"], hover_color="#2b4bc8",
                                text_color="#ffffff", corner_radius=6,
                                font=ctk.CTkFont(size=10, weight="bold"),
                                command=lambda: switch_tab_fn("Settings") if switch_tab_fn else None)
    banner_btn.place(x=318, y=6)

    # ── Stat cards ────────────────────────────────────────────────────────────
    card_data = [
        ("💵", "Net Worth",  "Loading…"),
        ("📆", "Next Event", "Loading…"),
        ("🌡️", "Weather",    "Loading…"),
    ]
    cx = 32
    card_labels = {}
    for icon, title, default_val in card_data:
        card = ctk.CTkFrame(parent, fg_color="#1f2328", width=270, height=100,
                             corner_radius=10, border_width=1, border_color="#2a2f38")
        card.place(x=cx, y=70)

        ctk.CTkLabel(card, text=icon, fg_color="transparent",
                     font=ctk.CTkFont(size=18)).place(x=14, y=10)
        ctk.CTkLabel(card, text=title, fg_color="transparent",
                     text_color="#6b7280", font=ctk.CTkFont(size=11)).place(x=40, y=12)

        val = ctk.CTkLabel(card, text=default_val, fg_color="transparent",
                     text_color="#e5e7eb", font=ctk.CTkFont(size=20, weight="bold"))
        val.place(x=14, y=42)

        sub = ctk.CTkLabel(card, text="", fg_color="transparent",
                     text_color="#4b5563", font=ctk.CTkFont(size=10))
        sub.place(x=14, y=74)

        card_labels[title] = {"val": val, "sub": sub}
        cx += 282

    # ── Net Worth Chart (left) ────────────────────────────────────────────────
    chart_frame = ctk.CTkFrame(parent, fg_color="#1f2328", width=420, height=280,
                                corner_radius=10, border_width=1, border_color="#2a2f38")
    chart_frame.place(x=32, y=184)

    ctk.CTkLabel(chart_frame, text="Net Worth — Last 30 Days", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).place(x=16, y=10)

    chart_canvas = tk.Canvas(chart_frame, width=390, height=220, bg="#1f2328",
                              highlightthickness=0, bd=0)
    chart_canvas.place(x=14, y=46)

    def draw_line_chart(snapshots):
        chart_canvas.delete("all")
        cw, ch = 390, 220
        pad_l, pad_r, pad_t, pad_b = 55, 15, 10, 28

        if not snapshots or len(snapshots) < 2:
            chart_canvas.create_text(cw // 2, ch // 2, text="Not enough data yet",
                                      fill="#4b5563", font=("Arial", 12))
            chart_canvas.create_text(cw // 2, ch // 2 + 20,
                                      text="Check back tomorrow",
                                      fill="#3d4456", font=("Arial", 10))
            return

        values = [s["net_worth"] for s in snapshots]
        dates  = [s["date"] for s in snapshots]
        v_min  = min(values)
        v_max  = max(values)
        if v_max == v_min:
            v_max = v_min + 100

        draw_w = cw - pad_l - pad_r
        draw_h = ch - pad_t - pad_b

        # Grid lines + Y labels
        for i in range(5):
            y = pad_t + draw_h - (i / 4) * draw_h
            val = v_min + (i / 4) * (v_max - v_min)
            chart_canvas.create_line(pad_l, y, cw - pad_r, y, fill="#2a2f38", width=1)
            label = f"${val/1000:.1f}k" if val >= 1000 else f"${val:.0f}"
            chart_canvas.create_text(pad_l - 6, y, text=label, fill="#4b5563",
                                      font=("Arial", 9), anchor="e")

        # Points
        points = []
        for i, v in enumerate(values):
            x = pad_l + (i / max(len(values) - 1, 1)) * draw_w
            y = pad_t + draw_h - ((v - v_min) / (v_max - v_min)) * draw_h
            points.append((x, y))

        accent = _accent["color"]
        if len(points) >= 2:
            # Fill under curve
            fill_pts = list(points) + [(points[-1][0], pad_t + draw_h),
                                        (points[0][0], pad_t + draw_h)]
            chart_canvas.create_polygon(
                *[c for p in fill_pts for c in p],
                fill=accent, outline="", stipple="gray25")
            # Line
            chart_canvas.create_line(
                *[c for p in points for c in p],
                fill=accent, width=2, smooth=True)

        for x, y in points:
            chart_canvas.create_oval(x-3, y-3, x+3, y+3,
                                      fill=accent, outline="#1f2328", width=1)

        # X labels
        for idx in [0, len(dates)//2, len(dates)-1]:
            if idx < len(dates):
                x = pad_l + (idx / max(len(dates)-1, 1)) * draw_w
                try:
                    d = datetime.date.fromisoformat(dates[idx])
                    label = d.strftime("%b %d")
                except Exception:
                    label = dates[idx][-5:]
                chart_canvas.create_text(x, ch-8, text=label, fill="#4b5563",
                                          font=("Arial", 9))

    # ── Upcoming Events (right) ───────────────────────────────────────────────
    events_frame = ctk.CTkFrame(parent, fg_color="#1f2328", width=410, height=280,
                                  corner_radius=10, border_width=1, border_color="#2a2f38")
    events_frame.place(x=466, y=184)

    ctk.CTkLabel(events_frame, text="Upcoming — Next 7 Days", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).place(x=16, y=10)

    events_scroll = ctk.CTkScrollableFrame(events_frame, fg_color="transparent",
                                             width=378, height=224)
    events_scroll.place(x=8, y=40)

    def render_upcoming_events(events):
        for w in events_scroll.winfo_children():
            w.destroy()
        if not events:
            ctk.CTkLabel(events_scroll, text="No events in the next 7 days",
                          fg_color="transparent", text_color="#4b5563",
                          font=ctk.CTkFont(size=12)).pack(pady=20)
            if switch_tab_fn:
                ctk.CTkButton(events_scroll, text="＋  Add an event",
                               fg_color=_accent["color"], hover_color="#2b4bc8",
                               width=180, height=30, text_color="#ffffff",
                               corner_radius=8, font=ctk.CTkFont(size=12),
                               command=lambda: switch_tab_fn("Calendar")).pack(pady=4)
            return

        today = datetime.date.today()
        for ev in events[:8]:
            row = ctk.CTkFrame(events_scroll, fg_color="#161a1f", corner_radius=6, height=44)
            row.pack(fill="x", pady=2, padx=2)
            row.pack_propagate(False)

            color = ev.get("color", "#7B93DB")
            ctk.CTkFrame(row, fg_color=color, width=4, height=44, corner_radius=2).place(x=0, y=0)

            try:
                ev_date = datetime.date.fromisoformat(ev.get("event_date", ""))
                if ev_date == today:
                    date_text = "Today"
                elif ev_date == today + datetime.timedelta(days=1):
                    date_text = "Tmrw"
                else:
                    date_text = ev_date.strftime("%a %d")
            except Exception:
                date_text = "—"

            ctk.CTkLabel(row, text=date_text, fg_color="transparent",
                          text_color="#6b7280", font=ctk.CTkFont(size=10, weight="bold"),
                          width=42).place(x=10, y=4)
            ctk.CTkLabel(row, text=ev.get("title", ""), fg_color="transparent",
                          text_color="#d1d5db", font=ctk.CTkFont(size=12, weight="bold"),
                          anchor="w").place(x=58, y=4)
            time_text = ev.get("event_time", "")
            type_text = ev.get("event_type", "")
            meta = f"{type_text}  •  {time_text}" if time_text else type_text
            ctk.CTkLabel(row, text=meta, fg_color="transparent",
                          text_color="#4b5563", font=ctk.CTkFont(size=10),
                          anchor="w").place(x=58, y=24)

    # ── Budget Progress (bottom left) ─────────────────────────────────────────
    budget_frame = ctk.CTkFrame(parent, fg_color="#1f2328", width=420, height=178,
                                  corner_radius=10, border_width=1, border_color="#2a2f38")
    budget_frame.place(x=32, y=478)

    ctk.CTkLabel(budget_frame, text="Budget This Month", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).place(x=16, y=10)

    budget_content = ctk.CTkFrame(budget_frame, fg_color="transparent", width=396, height=130)
    budget_content.place(x=12, y=38)

    def render_budget_progress(budget_data, transactions):
        for w in budget_content.winfo_children():
            w.destroy()
        if not budget_data or budget_data.get("income", 0) <= 0:
            ctk.CTkLabel(budget_content, text="No budget set up yet",
                          fg_color="transparent", text_color="#4b5563",
                          font=ctk.CTkFont(size=12)).pack(pady=16)
            if switch_tab_fn:
                ctk.CTkButton(budget_content, text="Set up budget →",
                               fg_color=_accent["color"], hover_color="#2b4bc8",
                               width=140, height=28, text_color="#ffffff",
                               corner_radius=6, font=ctk.CTkFont(size=11),
                               command=lambda: switch_tab_fn("Budget")).pack(pady=4)
            return

        income     = budget_data.get("income", 0)
        categories = budget_data.get("categories", {})
        spent_by_cat = {}
        for txn in transactions:
            cat = (txn.get("personal_finance_category", {}) or {}).get("primary", "OTHER")
            amt = abs(txn.get("amount", 0))
            spent_by_cat[cat] = spent_by_cat.get(cat, 0) + amt
        total_spent = sum(spent_by_cat.values())

        overall_row = ctk.CTkFrame(budget_content, fg_color="transparent")
        overall_row.pack(fill="x", padx=4, pady=(0, 6))
        pct = min(total_spent / income, 1.0) if income > 0 else 0
        color = "#4ade80" if pct < 0.75 else ("#FFD700" if pct < 0.95 else "#f87171")

        ctk.CTkLabel(overall_row, text=f"Total Spent: ${total_spent:,.0f} / ${income:,.0f}",
                      fg_color="transparent", text_color="#d1d5db",
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkLabel(overall_row, text=f"{pct*100:.0f}%",
                      fg_color="transparent", text_color=color,
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="right")

        bar_bg = ctk.CTkFrame(budget_content, fg_color="#161a1f", height=10, corner_radius=5)
        bar_bg.pack(fill="x", padx=4, pady=(0, 8))
        if pct > 0:
            ctk.CTkFrame(bar_bg, fg_color=color,
                          width=max(int(392 * pct), 4), height=10,
                          corner_radius=5).place(x=0, y=0)

        if categories:
            sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:3]
            for cat_name, budgeted in sorted_cats:
                spent = spent_by_cat.get(cat_name.upper(), spent_by_cat.get(cat_name, 0))
                cat_pct = min(spent / budgeted, 1.0) if budgeted > 0 else 0
                cat_color = "#4ade80" if cat_pct < 0.75 else ("#FFD700" if cat_pct < 0.95 else "#f87171")
                row = ctk.CTkFrame(budget_content, fg_color="transparent", height=22)
                row.pack(fill="x", padx=4, pady=1)
                ctk.CTkLabel(row, text=f"{cat_name}: ${spent:,.0f}/${budgeted:,.0f}",
                              fg_color="transparent", text_color="#6b7280",
                              font=ctk.CTkFont(size=10)).pack(side="left")
                ctk.CTkLabel(row, text=f"{cat_pct*100:.0f}%",
                              fg_color="transparent", text_color=cat_color,
                              font=ctk.CTkFont(size=10, weight="bold")).pack(side="right")

    # ── Weather (bottom right) ────────────────────────────────────────────────
    weather_card = ctk.CTkFrame(parent, fg_color="#1f2328", width=410, height=178,
                                 corner_radius=10, border_width=1, border_color="#2a2f38")
    weather_card.place(x=466, y=478)

    ctk.CTkLabel(weather_card, text="Local Weather", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).place(x=16, y=10)

    weather_icon = ctk.CTkLabel(weather_card, text="…", fg_color="transparent",
                                 text_color="#f0f1f3", font=ctk.CTkFont(size=42))
    weather_icon.place(x=16, y=40)
    weather_temp = ctk.CTkLabel(weather_card, text="--°F", fg_color="transparent",
                                 text_color="#e5e7eb", font=ctk.CTkFont(size=26, weight="bold"))
    weather_temp.place(x=80, y=48)
    weather_desc = ctk.CTkLabel(weather_card, text="Fetching…", fg_color="transparent",
                                 text_color="#6b7280", font=ctk.CTkFont(size=12))
    weather_desc.place(x=80, y=80)
    weather_city = ctk.CTkLabel(weather_card, text="", fg_color="transparent",
                                 text_color="#5b6ef7", font=ctk.CTkFont(size=11))
    weather_city.place(x=16, y=108)
    weather_details = ctk.CTkLabel(weather_card, text="", fg_color="transparent",
                                    text_color="#4b5563", font=ctk.CTkFont(size=11))
    weather_details.place(x=16, y=130)
    weather_extra = ctk.CTkLabel(weather_card, text="", fg_color="transparent",
                                  text_color="#4b5563", font=ctk.CTkFont(size=11))
    weather_extra.place(x=16, y=150)

    WMO_ICONS = {
        0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",48:"🌫️",51:"🌦️",53:"🌦️",55:"🌧️",
        61:"🌧️",63:"🌧️",65:"🌧️",71:"🌨️",73:"🌨️",75:"❄️",
        80:"🌦️",81:"🌧️",82:"⛈️",95:"⛈️",96:"⛈️",99:"⛈️",
    }
    WMO_DESCS = {
        0:"Clear Sky",1:"Mostly Clear",2:"Partly Cloudy",3:"Overcast",
        45:"Foggy",48:"Foggy",51:"Light Drizzle",53:"Drizzle",55:"Heavy Drizzle",
        61:"Light Rain",63:"Rain",65:"Heavy Rain",71:"Light Snow",73:"Snow",75:"Heavy Snow",
        80:"Showers",81:"Heavy Showers",82:"Violent Showers",
        95:"Thunderstorm",96:"Thunderstorm",99:"Thunderstorm",
    }

    # ── Fetch all data ────────────────────────────────────────────────────────
    def fetch_all_data():
        has_bank = False
        has_events = False
        has_budget = False
        has_pfp = bool(_pfp_cache.get(str(user_id)))

        # 1) Net worth + record snapshot
        try:
            resp = requests.get(f"{API_BASE}/plaid/accounts",
                                 params={"user_id": user_id}, timeout=10)
            if resp.ok:
                accounts = resp.json().get("accounts", [])
                if accounts:
                    has_bank = True
                    net_worth = sum((a.get("balances", {}).get("current") or 0) for a in accounts)
                    nw_text  = f"${net_worth:,.2f}"
                    nw_color = "#4ade80" if net_worth >= 0 else "#f87171"
                    try:
                        requests.post(f"{API_BASE}/balance/snapshot",
                                       json={"user_id": user_id, "net_worth": net_worth}, timeout=6)
                    except Exception:
                        pass
                else:
                    nw_text, nw_color = "—", "#4b5563"
            else:
                nw_text, nw_color = "—", "#4b5563"
        except Exception:
            nw_text, nw_color = "—", "#4b5563"

        def _up_nw():
            card_labels["Net Worth"]["val"].configure(text=nw_text, text_color=nw_color)
            card_labels["Net Worth"]["sub"].configure(
                text="Across all accounts" if has_bank else "Connect a bank to start")
        parent.after(0, _up_nw)

        # 2) Balance history for chart
        try:
            resp = requests.get(f"{API_BASE}/balance/history",
                                 params={"user_id": user_id, "days": 30}, timeout=10)
            if resp.ok:
                snapshots = resp.json().get("snapshots", [])
                parent.after(0, lambda s=snapshots: draw_line_chart(s))
        except Exception:
            pass

        # 3) Upcoming events (next 7 days)
        try:
            today = datetime.date.today()
            week_end = today + datetime.timedelta(days=7)
            all_events = []
            resp = requests.get(f"{API_BASE}/calendar/events",
                                 params={"user_id": user_id, "year": today.year, "month": today.month},
                                 timeout=10)
            if resp.ok:
                all_events.extend(resp.json().get("events", []))
            if week_end.month != today.month:
                resp2 = requests.get(f"{API_BASE}/calendar/events",
                                      params={"user_id": user_id, "year": week_end.year, "month": week_end.month},
                                      timeout=10)
                if resp2.ok:
                    all_events.extend(resp2.json().get("events", []))

            today_s = today.isoformat()
            end_s   = week_end.isoformat()
            upcoming = sorted(
                [e for e in all_events if today_s <= e.get("event_date", "") <= end_s],
                key=lambda e: (e.get("event_date", ""), e.get("event_time", ""))
            )
            has_events = len(upcoming) > 0

            if upcoming:
                nxt = upcoming[0]
                try:
                    ed = datetime.date.fromisoformat(nxt.get("event_date", ""))
                    dl = "Today" if ed == today else ("Tomorrow" if ed == today + datetime.timedelta(days=1) else ed.strftime("%a %b %d"))
                except Exception:
                    dl = "Upcoming"
                ev_card_text = dl
            else:
                ev_card_text = "Nothing soon"
                nxt = None

            def _up_ev():
                card_labels["Next Event"]["val"].configure(
                    text=ev_card_text, font=ctk.CTkFont(size=16, weight="bold"))
                card_labels["Next Event"]["sub"].configure(
                    text=nxt.get("title", "") if nxt else "Add events in Calendar")
                render_upcoming_events(upcoming)
            parent.after(0, _up_ev)
        except Exception:
            parent.after(0, lambda: render_upcoming_events([]))

        # 4) Budget + transactions
        try:
            resp = requests.get(f"{API_BASE}/budget",
                                 params={"user_id": user_id}, timeout=10)
            budget = resp.json().get("budget") if resp.ok else None
            if budget and budget.get("income", 0) > 0:
                has_budget = True
            txns = []
            try:
                resp2 = requests.get(f"{API_BASE}/plaid/transactions",
                                      params={"user_id": user_id}, timeout=10)
                if resp2.ok:
                    txns = resp2.json().get("transactions", [])
            except Exception:
                pass
            parent.after(0, lambda b=budget, t=txns: render_budget_progress(b, t))
        except Exception:
            parent.after(0, lambda: render_budget_progress(None, []))

        # 5) Profile picture
        if not has_pfp:
            try:
                resp = requests.get(f"{API_BASE}/profile/picture",
                                     params={"user_id": user_id}, timeout=6)
                if resp.ok and resp.json().get("image"):
                    has_pfp = True
            except Exception:
                pass

        # 6) Setup banner
        incomplete = []
        if not has_pfp:   incomplete.append(("profile picture", "Settings"))
        if not has_bank:  incomplete.append(("bank account", "Financial"))
        if not has_events: incomplete.append(("calendar events", "Calendar"))
        if not has_budget: incomplete.append(("budget", "Budget"))

        if incomplete:
            hint, target = f"Finish setup: add {incomplete[0][0]}", incomplete[0][1]
            def _show_banner():
                banner_label.configure(text=hint)
                banner_btn.configure(command=lambda t=target: switch_tab_fn(t) if switch_tab_fn else None)
                setup_banner.place(x=440, y=20)
            parent.after(0, _show_banner)

    def fetch_weather():
        try:
            loc = requests.get("http://ip-api.com/json/", timeout=6).json()
            lat, lon = loc.get("lat"), loc.get("lon")
            city, region = loc.get("city", ""), loc.get("regionName", "")
            w = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,weathercode,windspeed_10m,relativehumidity_2m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit", "windspeed_unit": "mph",
                "forecast_days": 1, "timezone": "auto",
            }, timeout=6).json()
            cur = w.get("current", {})
            daily = w.get("daily", {})
            temp = cur.get("temperature_2m", "--")
            code = cur.get("weathercode", 0)
            wind = cur.get("windspeed_10m", "--")
            hum  = cur.get("relativehumidity_2m", "--")
            hi   = daily.get("temperature_2m_max", [None])[0]
            lo   = daily.get("temperature_2m_min", [None])[0]
            icon = WMO_ICONS.get(code, "🌡️")
            desc = WMO_DESCS.get(code, "Unknown")
            def _up():
                weather_icon.configure(text=icon)
                weather_temp.configure(text=f"{temp:.0f}°F")
                weather_desc.configure(text=desc)
                weather_city.configure(text=f"{city}, {region}")
                card_labels["Weather"]["val"].configure(text=f"{temp:.0f}°F")
                card_labels["Weather"]["sub"].configure(text=f"{desc} — {city}")
                if hi and lo:
                    weather_details.configure(text=f"High {hi:.0f}°F  /  Low {lo:.0f}°F")
                weather_extra.configure(text=f"💨 {wind:.0f} mph   💧 {hum}%")
            parent.after(0, _up)
        except Exception:
            parent.after(0, lambda: weather_desc.configure(text="Weather unavailable"))

    threading.Thread(target=fetch_all_data, daemon=True).start()
    threading.Thread(target=fetch_weather, daemon=True).start()

# =============================================================================
# Family Tab
# =============================================================================
# =============================================================================
#  MY FAMILY TAB — paste into main.py alongside other render_*_tab functions
#
#  Also update switch_tab():
#    elif tab_name == "My Family":
#        render_family_tab(content_frame, user_data)
# =============================================================================

def render_family_tab(parent, user_data=None, switch_tab_fn=None):
    # Check subscription — always re-verify from API unless already confirmed
    if _sub_cache["checked"] and _sub_cache["subscribed"]:
        pass  # confirmed subscriber, proceed
    else:
        def _check_sub():
            subscribed = check_subscription(user_data, force=True)
            if not subscribed:
                parent.after(0, lambda: show_paywall_overlay(parent, user_data, switch_tab_fn))
        threading.Thread(target=_check_sub, daemon=True).start()

    user_id = 0
    if user_data:
        user_id = user_data.get("id") or 0

    state = {
        "family":           None,
        "pending_invites":  [],
        "pending_outgoing": [],
    }

    # ── Header ────────────────────────────────────────────────────────────────
    ctk.CTkLabel(parent, text="My Family", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=24, weight="bold")).place(x=30, y=20)

    status_var = ctk.StringVar(value="")
    ctk.CTkLabel(parent, textvariable=status_var, fg_color="transparent",
                 text_color="#4CFF7A", font=ctk.CTkFont(size=12)).place(x=30, y=56)

    # ── Main content area (swaps between no-family and family views) ──────────
    content = ctk.CTkFrame(parent, fg_color="transparent", width=840, height=560)
    content.place(x=30, y=78)

    def clear_content():
        for w in content.winfo_children():
            w.destroy()

    # ─────────────────────────────────────────────────────────────────────────
    #  VIEW A: No family yet
    # ─────────────────────────────────────────────────────────────────────────
    def render_no_family():
        clear_content()

        ctk.CTkLabel(content, text="You're not in a family yet.",
                      fg_color="transparent", text_color="#9A9A9A",
                      font=ctk.CTkFont(size=15)).place(x=0, y=10)

        # Create family card
        create_card = ctk.CTkFrame(content, fg_color="#1f2328", width=380, height=180, corner_radius=8)
        create_card.place(x=0, y=50)

        ctk.CTkLabel(create_card, text="Create a Family",
                      fg_color="transparent", text_color="#7B93DB",
                      font=ctk.CTkFont(size=16, weight="bold")).place(x=20, y=16)
        ctk.CTkLabel(create_card, text="Start a household and invite your family members.",
                      fg_color="transparent", text_color="#9A9A9A",
                      font=ctk.CTkFont(size=12)).place(x=20, y=44)

        ctk.CTkLabel(create_card, text="Family Name", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)).place(x=20, y=74)
        family_name_entry = ctk.CTkEntry(create_card, placeholder_text="e.g. The Smiths",
                                          fg_color="#363C45", width=280, height=34,
                                          text_color="#7B93DB", corner_radius=8,
                                          border_width=1, border_color="#2a2f38")
        family_name_entry.place(x=20, y=94)

        err_lbl = ctk.CTkLabel(create_card, text="", fg_color="transparent",
                                text_color="#ff6b6b", font=ctk.CTkFont(size=11))
        err_lbl.place(x=20, y=132)

        def do_create():
            name = family_name_entry.get().strip() or "My Family"
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/family/create",
                                          json={"user_id": user_id, "name": name}, timeout=10)
                    data = resp.json()
                    if resp.ok and data.get("success"):
                        parent.after(0, load_family)
                    else:
                        parent.after(0, lambda: err_lbl.configure(
                            text=data.get("message", "Failed to create family.")))
                except Exception as e:
                    parent.after(0, lambda: err_lbl.configure(text=str(e)))
            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(create_card, text="Create Family", command=do_create,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       width=160, height=32, text_color="#f0f1f3", corner_radius=8
                       ).place(x=20, y=136)

        # Pending invites card
        if state["pending_invites"]:
            inv_card = ctk.CTkFrame(content, fg_color="#1f2328", width=380, height=240, corner_radius=8)
            inv_card.place(x=400, y=50)

            ctk.CTkLabel(inv_card, text="Pending Invitations",
                          fg_color="transparent", text_color="#FFD700",
                          font=ctk.CTkFont(size=16, weight="bold")).place(x=20, y=16)

            y_off = 52
            for inv in state["pending_invites"]:
                ctk.CTkLabel(inv_card,
                              text=f"🏠  {inv['family_name']}  —  from {inv['invited_by']}",
                              fg_color="transparent", text_color="#d1d5db",
                              font=ctk.CTkFont(size=13)).place(x=20, y=y_off)

                def do_accept(invite_id=inv["invite_id"]):
                    respond_invite(invite_id, True)

                def do_decline(invite_id=inv["invite_id"]):
                    respond_invite(invite_id, False)

                ctk.CTkButton(inv_card, text="Accept", command=do_accept,
                               fg_color="#3b5bdb", hover_color="#2b4bc8",
                               width=90, height=28, text_color="#f0f1f3", corner_radius=8
                               ).place(x=20, y=y_off + 26)
                ctk.CTkButton(inv_card, text="Decline", command=do_decline,
                               fg_color="#252830", hover_color="#2a1520",
                               width=90, height=28, text_color="#ff6b6b", corner_radius=8
                               ).place(x=120, y=y_off + 26)
                y_off += 72

    # ─────────────────────────────────────────────────────────────────────────
    #  VIEW B: In a family
    # ─────────────────────────────────────────────────────────────────────────
    def render_family():
        clear_content()
        family = state["family"]
        if not family:
            return

        # Family name header
        ctk.CTkLabel(content, text=f"🏠  {family['name']}",
                      fg_color="transparent", text_color="#f0f1f3",
                      font=ctk.CTkFont(size=18, weight="bold")).place(x=0, y=0)

        # ── Members panel ─────────────────────────────────────────────────────
        members_card = ctk.CTkFrame(content, fg_color="#1f2328", width=380, height=320, corner_radius=8)
        members_card.place(x=0, y=40)

        ctk.CTkLabel(members_card, text="Members", fg_color="transparent",
                      text_color="#7B93DB", font=ctk.CTkFont(size=15, weight="bold")).place(x=20, y=14)
        ctk.CTkFrame(members_card, fg_color="#2a2f38", height=1, width=340).place(x=20, y=38)

        y_off = 50
        for member in family["members"]:
            color  = member.get("color", "#7B93DB")
            is_me  = member.get("is_me", False)
            name   = member["username"] + (" (you)" if is_me else "")
            pfp_b64 = member.get("profile_picture")

            # Profile picture or colored initials circle
            avatar_frame = ctk.CTkFrame(members_card, fg_color=color,
                                         width=32, height=32, corner_radius=16)
            avatar_frame.place(x=20, y=y_off)

            member_initial = member["username"][0].upper() if member["username"] else "?"
            avatar_lbl = ctk.CTkLabel(avatar_frame, text=member_initial,
                                       fg_color="transparent", text_color="#ffffff",
                                       font=ctk.CTkFont(size=12, weight="bold"),
                                       width=32, height=32)
            avatar_lbl.place(x=0, y=0)

            if pfp_b64:
                pfp_img = b64_to_ctk_image(pfp_b64, size=(32, 32))
                if pfp_img:
                    avatar_lbl.configure(image=pfp_img, text="")

            ctk.CTkLabel(members_card, text=name,
                          fg_color="transparent", text_color="#d1d5db",
                          font=ctk.CTkFont(size=13, weight="bold" if is_me else "normal")
                          ).place(x=62, y=y_off + 1)

            ctk.CTkLabel(members_card, text=member["email"],
                          fg_color="transparent", text_color="#4b5060",
                          font=ctk.CTkFont(size=11)).place(x=62, y=y_off + 20)

            y_off += 52

        # Leave family button
        def do_leave():
            def _do():
                try:
                    requests.post(f"{API_BASE}/family/leave",
                                   json={"user_id": user_id}, timeout=10)
                    parent.after(0, load_family)
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(members_card, text="Leave Family", command=do_leave,
                       fg_color="transparent", hover_color="#2a1520",
                       width=140, height=28, text_color="#ff6b6b",
                       corner_radius=8, border_width=1, border_color="#ff6b6b"
                       ).place(x=20, y=y_off + 10)

        # ── Invite panel ──────────────────────────────────────────────────────
        invite_card = ctk.CTkFrame(content, fg_color="#1f2328", width=380, height=200, corner_radius=8)
        invite_card.place(x=400, y=40)

        ctk.CTkLabel(invite_card, text="Invite Someone",
                      fg_color="transparent", text_color="#7B93DB",
                      font=ctk.CTkFont(size=15, weight="bold")).place(x=20, y=14)
        ctk.CTkLabel(invite_card,
                      text="They'll see the invitation next time they log in.",
                      fg_color="transparent", text_color="#9A9A9A",
                      font=ctk.CTkFont(size=12)).place(x=20, y=42)

        invite_entry = ctk.CTkEntry(invite_card, placeholder_text="Their email address",
                                     fg_color="#363C45", width=280, height=34,
                                     text_color="#7B93DB", corner_radius=8,
                                     border_width=1, border_color="#2a2f38")
        invite_entry.place(x=20, y=70)

        invite_err = ctk.CTkLabel(invite_card, text="", fg_color="transparent",
                                   text_color="#ff6b6b", font=ctk.CTkFont(size=11))
        invite_err.place(x=20, y=110)

        def do_invite():
            email = invite_entry.get().strip().lower()
            if not email:
                invite_err.configure(text="Enter an email address.")
                return
            def _do():
                try:
                    resp = requests.post(f"{API_BASE}/family/invite",
                                          json={"user_id": user_id, "email": email},
                                          timeout=10)
                    data = resp.json()
                    msg  = data.get("message", "")
                    if resp.ok and data.get("success"):
                        parent.after(0, lambda: [
                            invite_err.configure(text=f"✓ {msg}", text_color="#4CFF7A"),
                            invite_entry.delete(0, "end"),
                            load_family()
                        ])
                    else:
                        parent.after(0, lambda: invite_err.configure(
                            text=msg, text_color="#ff6b6b"))
                except Exception as e:
                    parent.after(0, lambda: invite_err.configure(text=str(e)))
            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(invite_card, text="Send Invite", command=do_invite,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       width=140, height=32, text_color="#f0f1f3", corner_radius=8
                       ).place(x=20, y=152)

        # ── Pending outgoing invites ───────────────────────────────────────────
        if state["pending_outgoing"]:
            out_card = ctk.CTkFrame(content, fg_color="#1f2328", width=380, height=160, corner_radius=8)
            out_card.place(x=400, y=254)

            ctk.CTkLabel(out_card, text="Pending Invites Sent",
                          fg_color="transparent", text_color="#FFD700",
                          font=ctk.CTkFont(size=13, weight="bold")).place(x=20, y=14)

            y2 = 42
            for inv in state["pending_outgoing"]:
                ctk.CTkLabel(out_card, text=f"⏳  {inv['email']}",
                              fg_color="transparent", text_color="#9A9A9A",
                              font=ctk.CTkFont(size=12)).place(x=20, y=y2)
                y2 += 28

    # ─────────────────────────────────────────────────────────────────────────
    #  Actions
    # ─────────────────────────────────────────────────────────────────────────
    def respond_invite(invite_id, accept):
        def _do():
            try:
                requests.post(f"{API_BASE}/family/invite/respond",
                               json={"user_id": user_id, "invite_id": invite_id,
                                     "accept": accept}, timeout=10)
                parent.after(0, load_family)
                if accept:
                    parent.after(0, lambda: status_var.set("✓ You joined the family!"))
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def load_family():
        status_var.set("Loading…")
        def _do():
            try:
                resp = requests.get(f"{API_BASE}/family/info",
                                     params={"user_id": user_id}, timeout=10)
                if resp.ok:
                    data = resp.json()
                    state["family"]           = data.get("family")
                    state["pending_invites"]  = data.get("pending_invites", [])
                    state["pending_outgoing"] = data.get("pending_outgoing", [])

                    def _update():
                        status_var.set("")
                        if state["family"]:
                            render_family()
                        else:
                            render_no_family()
                    parent.after(0, _update)
            except Exception as e:
                parent.after(0, lambda: status_var.set(f"Error: {e}"))
        threading.Thread(target=_do, daemon=True).start()

    load_family()

# =============================================================================
#  FINANCIAL TAB (Plaid)
# =============================================================================
def render_financial_tab(parent, user_data=None, switch_tab_fn=None):
    # Check subscription — always re-verify from API unless already confirmed
    if _sub_cache["checked"] and _sub_cache["subscribed"]:
        pass  # confirmed subscriber, proceed
    else:
        def _check_sub():
            subscribed = check_subscription(user_data, force=True)
            if not subscribed:
                parent.after(0, lambda: show_paywall_overlay(parent, user_data, switch_tab_fn))
        threading.Thread(target=_check_sub, daemon=True).start()

    ctk.CTkLabel(parent, text="Financial Overview", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=24, weight="bold")).place(x=30, y=28)
    ctk.CTkLabel(parent, text="Connect and manage your bank accounts via Plaid",
                 fg_color="transparent", text_color="#5B8DEF",
                 font=ctk.CTkFont(size=13)).place(x=30, y=62)

    status_var = ctk.StringVar(value="")
    ctk.CTkLabel(parent, textvariable=status_var, fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=12), width=500).place(x=30, y=92)

    accounts_panel = ctk.CTkScrollableFrame(parent, fg_color="#1f2328", width=380, height=460, corner_radius=8)
    accounts_panel.place(x=30, y=122)
    ctk.CTkLabel(accounts_panel, text="Bank Accounts", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=18, pady=(14, 6))
    accounts_inner = ctk.CTkFrame(accounts_panel, fg_color="transparent")
    accounts_inner.pack(fill="both", expand=True, padx=8, pady=4)

    tx_panel = ctk.CTkScrollableFrame(parent, fg_color="#1f2328", width=420, height=460, corner_radius=8)
    tx_panel.place(x=430, y=122)
    ctk.CTkLabel(tx_panel, text="Recent Transactions", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=18, pady=(14, 6))
    tx_inner = ctk.CTkFrame(tx_panel, fg_color="transparent")
    tx_inner.pack(fill="both", expand=True, padx=8, pady=4)

    def clear_frame(frame):
        for w in frame.winfo_children():
            w.destroy()

    def render_empty(frame, msg):
        ctk.CTkLabel(frame, text=msg, fg_color="transparent",
                     text_color="#4b5060", font=ctk.CTkFont(size=13)).pack(anchor="w", padx=10, pady=8)

    def render_accounts(accounts):
        clear_frame(accounts_inner)
        if not accounts:
            render_empty(accounts_inner, "No accounts linked yet.")
            return
        for acct in accounts:
            row   = ctk.CTkFrame(accounts_inner, fg_color="#363C45", corner_radius=8)
            row.pack(fill="x", pady=5, padx=4)
            name  = acct.get("name", "Unknown")
            mask  = acct.get("mask", "")
            atype = acct.get("type", "").capitalize()
            bal   = acct.get("balances", {})
            avail = bal.get("available")
            curr  = bal.get("current")
            iso   = bal.get("iso_currency_code", "USD")
            ctk.CTkLabel(row, text=f"{name}  ···{mask}" if mask else name,
                         fg_color="transparent", text_color="#d1d5db",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(10, 2))
            ctk.CTkLabel(row, text=atype, fg_color="transparent",
                         text_color="#9A9A9A", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14)
            bal_text = (f"Available: {iso} {avail:,.2f}" if avail is not None
                        else f"Balance: {iso} {curr:,.2f}" if curr is not None else "Balance: —")
            ctk.CTkLabel(row, text=bal_text, fg_color="transparent",
                         text_color="#4CFF7A", font=ctk.CTkFont(size=13)).pack(anchor="w", padx=14, pady=(2, 10))

    def render_transactions(transactions):
        clear_frame(tx_inner)
        if not transactions:
            render_empty(tx_inner, "Connect a bank account to see transactions.")
            return
        for tx in transactions[:40]:
            row    = ctk.CTkFrame(tx_inner, fg_color="#363C45", corner_radius=8)
            row.pack(fill="x", pady=4, padx=4)
            name   = tx.get("name", "Unknown")
            amount = tx.get("amount", 0)
            date   = tx.get("date", "")
            cat    = ", ".join(tx.get("category", [])[:2]) if tx.get("category") else ""
            iso    = tx.get("iso_currency_code", "USD")
            color  = "#FF6B6B" if amount >= 0 else "#4CFF7A"
            prefix = "-" if amount >= 0 else "+"
            top_row = ctk.CTkFrame(row, fg_color="transparent")
            top_row.pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(top_row, text=name[:30] + ("…" if len(name) > 30 else ""),
                         fg_color="transparent", text_color="#d1d5db",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
            ctk.CTkLabel(top_row, text=f"{prefix}{iso} {abs(amount):,.2f}",
                         fg_color="transparent", text_color=color,
                         font=ctk.CTkFont(size=12, weight="bold")).pack(side="right")
            bot_row = ctk.CTkFrame(row, fg_color="transparent")
            bot_row.pack(fill="x", padx=12, pady=(0, 8))
            ctk.CTkLabel(bot_row, text=cat, fg_color="transparent",
                         text_color="#9A9A9A", font=ctk.CTkFont(size=11)).pack(side="left")
            ctk.CTkLabel(bot_row, text=date, fg_color="transparent",
                         text_color="#4b5060", font=ctk.CTkFont(size=11)).pack(side="right")

    render_empty(accounts_inner, "No accounts linked yet.")
    render_empty(tx_inner, "Connect a bank account to see transactions.")

    def connect_bank():
        connect_btn.configure(state="disabled")
        status_var.set("Requesting Plaid Link token…")
        def _do():
            try:
                user_id = ""
                if user_data:
                    user_id = str(user_data.get("id") or user_data.get("username", ""))
                payload = {"user_id": user_id}
                resp = requests.post(f"{API_BASE}/plaid/create_link_token", json=payload, timeout=15)
                data = resp.json()
                if resp.ok and data.get("link_token"):
                    link_url = f"{API_BASE}/plaid/link?user_id={user_id}"
                    parent.after(0, lambda: status_var.set(
                        "Browser opened — complete the Plaid connection, then click Refresh."))
                    webbrowser.open(link_url)
                else:
                    msg = data.get("error") or data.get("message") or "Failed to get link token."
                    parent.after(0, lambda: status_var.set(f"Error: {msg}"))
            except requests.RequestException as e:
                parent.after(0, lambda: status_var.set(f"Network error: {e}"))
            finally:
                parent.after(0, lambda: connect_btn.configure(state="normal"))
        threading.Thread(target=_do, daemon=True).start()

    def refresh_data():
        refresh_btn.configure(state="disabled")
        status_var.set("Fetching account data…")
        def _do():
            try:
                params = {}
                if user_data:
                    params["user_id"] = user_data.get("id") or user_data.get("username", "")
                acct_resp = requests.get(f"{API_BASE}/plaid/accounts", params=params, timeout=15)
                tx_resp   = requests.get(f"{API_BASE}/plaid/transactions", params=params, timeout=15)
                accounts     = acct_resp.json().get("accounts", []) if acct_resp.ok else []
                transactions = tx_resp.json().get("transactions", []) if tx_resp.ok else []
                def _update():
                    render_accounts(accounts)
                    render_transactions(transactions)
                    net = sum((a.get("balances", {}).get("current") or 0) for a in accounts)
                    status_var.set(f"✓  {len(accounts)} account(s) — Net balance: ${net:,.2f}")
                    refresh_btn.configure(state="normal")
                parent.after(0, _update)
            except requests.RequestException as e:
                parent.after(0, lambda: status_var.set(f"Network error: {e}"))
                parent.after(0, lambda: refresh_btn.configure(state="normal"))
        threading.Thread(target=_do, daemon=True).start()

    btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
    btn_frame.place(x=30, y=598)
    connect_btn = ctk.CTkButton(btn_frame, text="＋  Connect Bank Account", command=connect_bank,
                                 fg_color="#3b5bdb", hover_color="#2b4bc8",
                                 width=200, height=36, text_color="#f0f1f3", corner_radius=8)
    connect_btn.pack(side="left", padx=(0, 12))
    refresh_btn = ctk.CTkButton(btn_frame, text="⟳  Refresh Data", command=refresh_data,
                                 fg_color="#252830", hover_color="#2b4bc8",
                                 width=150, height=36, text_color="#7B93DB", corner_radius=8)
    refresh_btn.pack(side="left")
    refresh_data()

#==============================================================================
# BUDGET TAB
# =============================================================================
# Plaid category → budget category mapping
PLAID_CAT_MAP = {
    "Food and Drink":              "Food & Dining",
    "Restaurants":                 "Food & Dining",
    "Groceries":                   "Groceries",
    "Supermarkets and Groceries":  "Groceries",
    "Travel":                      "Transport",
    "Transportation":              "Transport",
    "Gas Stations":                "Transport",
    "Shops":                       "Shopping",
    "Shopping":                    "Shopping",
    "Entertainment":               "Entertainment",
    "Recreation":                  "Entertainment",
    "Health and Fitness":          "Health",
    "Healthcare":                  "Health",
    "Pharmacies":                  "Health",
    "Utilities":                   "Utilities",
    "Service":                     "Services",
    "Transfer":                    "Transfers",
    "Payment":                     "Bills & Payments",
    "Bank Fees":                   "Fees",
}

BUDGET_CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transport",
    "Shopping",
    "Entertainment",
    "Health",
    "Utilities",
    "Services",
    "Bills & Payments",
    "Fees",
    "Other",
]

PAYDAY_OPTIONS = ["Weekly", "Bi-Weekly", "Semi-Monthly", "Monthly"]


def render_budget_tab(parent, user_data=None, switch_tab_fn=None):
    # Check subscription — always re-verify from API unless already confirmed
    if _sub_cache["checked"] and _sub_cache["subscribed"]:
        pass  # confirmed subscriber, proceed
    else:
        def _check_sub():
            subscribed = check_subscription(user_data, force=True)
            if not subscribed:
                parent.after(0, lambda: show_paywall_overlay(parent, user_data, switch_tab_fn))
        threading.Thread(target=_check_sub, daemon=True).start()

    user_id = ""
    if user_data:
        user_id = str(user_data.get("id") or user_data.get("username", ""))

    # ── Top header ────────────────────────────────────────────────────────────
    ctk.CTkLabel(parent, text="Budget Planner", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=24, weight="bold")).place(x=30, y=18)
    ctk.CTkLabel(parent, text="Set your budget — we'll track it automatically from your bank.",
                 fg_color="transparent", text_color="#5B8DEF",
                 font=ctk.CTkFont(size=13)).place(x=30, y=52)

    status_var = ctk.StringVar(value="")
    status_lbl = ctk.CTkLabel(parent, textvariable=status_var, fg_color="transparent",
                               text_color="#4CFF7A", font=ctk.CTkFont(size=11), width=400)
    status_lbl.place(x=30, y=652)

    # ── Summary bar (top row of cards) ────────────────────────────────────────
    summary_frame = ctk.CTkFrame(parent, fg_color="transparent", width=860, height=72)
    summary_frame.place(x=30, y=78)

    summary_labels = {}
    summary_cards  = [
        ("income_card",  "Monthly Income",  "—",      "#4CFF7A"),
        ("spent_card",   "Spent This Month", "—",      "#FF6B6B"),
        ("budget_card",  "Total Budgeted",   "—",      "#7B93DB"),
        ("remain_card",  "Remaining",        "—",      "#FFD700"),
    ]
    sx = 0
    for key, title, val, color in summary_cards:
        card = ctk.CTkFrame(summary_frame, fg_color="#1f2328", width=204, height=68, corner_radius=8)
        card.place(x=sx, y=0)
        ctk.CTkLabel(card, text=title, fg_color="transparent", text_color="#9A9A9A",
                     font=ctk.CTkFont(size=11)).place(x=12, y=8)
        lbl = ctk.CTkLabel(card, text=val, fg_color="transparent", text_color=color,
                            font=ctk.CTkFont(size=18, weight="bold"))
        lbl.place(x=12, y=30)
        summary_labels[key] = lbl
        sx += 216

    # ── Left panel: Budget Setup ───────────────────────────────────────────────
    left_panel = ctk.CTkScrollableFrame(parent, fg_color="#1f2328",
                                         width=360, height=490, corner_radius=8)
    left_panel.place(x=30, y=158)

    ctk.CTkLabel(left_panel, text="Budget Setup", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=15, weight="bold")).pack(
                 anchor="w", padx=16, pady=(14, 2))
    ctk.CTkFrame(left_panel, fg_color="#2a2f38", height=1).pack(fill="x", padx=16, pady=(0, 10))

    # Income
    ctk.CTkLabel(left_panel, text="Monthly Income ($)", fg_color="transparent",
                 text_color="#9A9A9A", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16)
    income_entry = ctk.CTkEntry(left_panel, placeholder_text="e.g. 4500",
                                 fg_color="#363C45", width=328, height=34,
                                 text_color="#7B93DB", corner_radius=8,
                                 border_width=1, border_color="#2a2f38")
    income_entry.pack(anchor="w", padx=16, pady=(2, 10))

    # Payday
    ctk.CTkLabel(left_panel, text="Pay Frequency", fg_color="transparent",
                 text_color="#9A9A9A", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16)
    payday_var  = ctk.StringVar(value="Bi-Weekly")
    payday_menu = ctk.CTkOptionMenu(left_panel, values=PAYDAY_OPTIONS, variable=payday_var,
                                     fg_color="#363C45", width=328, height=34,
                                     text_color="#7B93DB", button_color="#2a2f38",
                                     button_hover_color="#2b4bc8", corner_radius=8)
    payday_menu.pack(anchor="w", padx=16, pady=(2, 10))

    # Next payday
    ctk.CTkLabel(left_panel, text="Next Payday (YYYY-MM-DD)", fg_color="transparent",
                 text_color="#9A9A9A", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16)
    payday_entry = ctk.CTkEntry(left_panel, placeholder_text="e.g. 2026-03-28",
                                 fg_color="#363C45", width=328, height=34,
                                 text_color="#7B93DB", corner_radius=8,
                                 border_width=1, border_color="#2a2f38")
    payday_entry.pack(anchor="w", padx=16, pady=(2, 14))

    # Divider
    ctk.CTkLabel(left_panel, text="Spending Categories", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).pack(
                 anchor="w", padx=16, pady=(0, 4))
    ctk.CTkFrame(left_panel, fg_color="#2a2f38", height=1).pack(fill="x", padx=16, pady=(0, 8))

    # Category budget entries
    cat_entries = {}
    for cat in BUDGET_CATEGORIES:
        row = ctk.CTkFrame(left_panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=2)
        ctk.CTkLabel(row, text=cat, fg_color="transparent", text_color="#d1d5db",
                     font=ctk.CTkFont(size=12), width=170, anchor="w").pack(side="left")
        ent = ctk.CTkEntry(row, placeholder_text="$0",
                            fg_color="#363C45", width=140, height=30,
                            text_color="#7B93DB", corner_radius=8,
                            border_width=1, border_color="#2a2f38")
        ent.pack(side="right")
        cat_entries[cat] = ent

    # Bills section
    ctk.CTkFrame(left_panel, fg_color="#2a2f38", height=1).pack(fill="x", padx=16, pady=(12, 6))
    ctk.CTkLabel(left_panel, text="Fixed Bills", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")).pack(
                 anchor="w", padx=16, pady=(0, 6))

    bills_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
    bills_frame.pack(fill="x", padx=16, pady=(0, 4))
    bills_rows  = []   # list of (name_entry, amount_entry, frame)

    def add_bill_row(name="", amount=""):
        row = ctk.CTkFrame(bills_frame, fg_color="#363C45", corner_radius=8)
        row.pack(fill="x", pady=3)
        name_ent = ctk.CTkEntry(row, placeholder_text="Bill name",
                                 fg_color="#1f2328", width=170, height=28,
                                 text_color="#7B93DB", corner_radius=4,
                                 border_width=0)
        name_ent.pack(side="left", padx=(8, 4), pady=6)
        if name:
            name_ent.insert(0, name)
        amt_ent = ctk.CTkEntry(row, placeholder_text="$0",
                                fg_color="#1f2328", width=90, height=28,
                                text_color="#7B93DB", corner_radius=4,
                                border_width=0)
        amt_ent.pack(side="left", padx=(0, 4), pady=6)
        if amount:
            amt_ent.insert(0, str(amount))

        def remove():
            bills_rows[:] = [(n, a, f) for n, a, f in bills_rows if f is not row]
            row.destroy()

        ctk.CTkButton(row, text="✕", width=24, height=24, fg_color="transparent",
                       hover_color="#2a1520", text_color="#ff6b6b", corner_radius=4,
                       command=remove).pack(side="right", padx=6)
        bills_rows.append((name_ent, amt_ent, row))

    ctk.CTkButton(left_panel, text="＋  Add Bill", width=160, height=28,
                   fg_color="#252830", hover_color="#2b4bc8",
                   text_color="#7B93DB", corner_radius=8,
                   command=add_bill_row).pack(anchor="w", padx=16, pady=(4, 14))

    # Save button
    save_btn = ctk.CTkButton(left_panel, text="💾  Save Budget", width=328, height=36,
                              fg_color="#3b5bdb", hover_color="#2b4bc8",
                              text_color="#f0f1f3", corner_radius=8,
                              command=lambda: save_budget())
    save_btn.pack(anchor="w", padx=16, pady=(0, 16))

    # ── Right panel: Spending Tracker ─────────────────────────────────────────
    right_panel = ctk.CTkScrollableFrame(parent, fg_color="#1f2328",
                                          width=448, height=490, corner_radius=8)
    right_panel.place(x=408, y=158)

    ctk.CTkLabel(right_panel, text="This Month's Spending", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=15, weight="bold")).pack(
                 anchor="w", padx=16, pady=(14, 2))
    ctk.CTkFrame(right_panel, fg_color="#2a2f38", height=1).pack(fill="x", padx=16, pady=(0, 10))

    spending_inner = ctk.CTkFrame(right_panel, fg_color="transparent")
    spending_inner.pack(fill="both", expand=True, padx=8)

    # ── Helpers ───────────────────────────────────────────────────────────────
    budget_state = {
        "income":      0,
        "payday_freq": "Bi-Weekly",
        "next_payday": "",
        "categories":  {},   # cat -> budgeted amount
        "bills":       [],   # [{name, amount}]
        "actuals":     {},   # cat -> actual spent
    }

    def map_plaid_category(plaid_cats):
        for c in plaid_cats:
            if c in PLAID_CAT_MAP:
                return PLAID_CAT_MAP[c]
        return "Other"

    def update_summary():
        income     = budget_state["income"]
        total_bud  = sum(budget_state["categories"].values())
        total_bud += sum(b["amount"] for b in budget_state["bills"])
        total_spent = sum(budget_state["actuals"].values())
        remaining   = income - total_spent

        summary_labels["income_card"].configure(text=f"${income:,.0f}")
        summary_labels["budget_card"].configure(text=f"${total_bud:,.0f}")
        summary_labels["spent_card"].configure(text=f"${total_spent:,.0f}")
        rem_color = "#4CFF7A" if remaining >= 0 else "#FF6B6B"
        summary_labels["remain_card"].configure(text=f"${remaining:,.0f}", text_color=rem_color)

    def render_spending():
        for w in spending_inner.winfo_children():
            w.destroy()

        all_cats = list(budget_state["categories"].keys())
        # Also show any actuals categories not in budget
        for cat in budget_state["actuals"]:
            if cat not in all_cats:
                all_cats.append(cat)

        if not all_cats and not budget_state["bills"]:
            ctk.CTkLabel(spending_inner, text="Save a budget to see your spending breakdown.",
                          fg_color="transparent", text_color="#4b5060",
                          font=ctk.CTkFont(size=13)).pack(anchor="w", padx=10, pady=16)
            return

        # Category rows
        for cat in all_cats:
            budgeted = budget_state["categories"].get(cat, 0)
            actual   = budget_state["actuals"].get(cat, 0)
            pct      = min((actual / budgeted * 100) if budgeted > 0 else 100, 100)
            over     = actual > budgeted and budgeted > 0

            row = ctk.CTkFrame(spending_inner, fg_color="#363C45", corner_radius=8)
            row.pack(fill="x", pady=4, padx=2)

            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(top, text=cat, fg_color="transparent", text_color="#d1d5db",
                          font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
            amt_color = "#FF6B6B" if over else "#4CFF7A"
            ctk.CTkLabel(top, text=f"${actual:,.0f} / ${budgeted:,.0f}",
                          fg_color="transparent", text_color=amt_color,
                          font=ctk.CTkFont(size=12)).pack(side="right")

            # Progress bar
            bar_bg = ctk.CTkFrame(row, fg_color="#1f2328", width=410, height=8, corner_radius=4)
            bar_bg.pack(padx=12, pady=(2, 8))
            bar_fill_w = max(int(410 * pct / 100), 0)
            if bar_fill_w > 0:
                bar_color = "#FF6B6B" if over else "#3b5bdb"
                ctk.CTkFrame(bar_bg, fg_color=bar_color,
                              width=bar_fill_w, height=8, corner_radius=4).place(x=0, y=0)

        # Fixed bills
        if budget_state["bills"]:
            ctk.CTkFrame(spending_inner, fg_color="#2a2f38", height=1).pack(
                fill="x", padx=8, pady=(8, 4))
            ctk.CTkLabel(spending_inner, text="Fixed Bills", fg_color="transparent",
                          text_color="#7B93DB", font=ctk.CTkFont(size=12, weight="bold")).pack(
                          anchor="w", padx=10, pady=(0, 4))
            for bill in budget_state["bills"]:
                brow = ctk.CTkFrame(spending_inner, fg_color="#363C45", corner_radius=8)
                brow.pack(fill="x", pady=3, padx=2)
                ctk.CTkLabel(brow, text=bill["name"], fg_color="transparent",
                              text_color="#d1d5db", font=ctk.CTkFont(size=12)).pack(
                              side="left", padx=12, pady=8)
                ctk.CTkLabel(brow, text=f"${bill['amount']:,.0f}/mo",
                              fg_color="transparent", text_color="#FF6B6B",
                              font=ctk.CTkFont(size=12, weight="bold")).pack(
                              side="right", padx=12)

        update_summary()

    def load_budget():
        status_var.set("Loading budget…")
        def _do():
            try:
                resp = requests.get(f"{API_BASE}/budget", params={"user_id": user_id}, timeout=10)
                if resp.ok:
                    data = resp.json().get("budget") or {}
                    if data:
                        budget_state["income"]      = data.get("income", 0)
                        budget_state["payday_freq"] = data.get("payday_freq", "Bi-Weekly")
                        budget_state["next_payday"] = data.get("next_payday", "")
                        budget_state["categories"]  = data.get("categories", {})
                        budget_state["bills"]       = data.get("bills", [])

                        def _fill():
                            income_entry.delete(0, "end")
                            income_entry.insert(0, str(budget_state["income"]))
                            payday_var.set(budget_state["payday_freq"])
                            payday_entry.delete(0, "end")
                            payday_entry.insert(0, budget_state["next_payday"])
                            for cat, amt in budget_state["categories"].items():
                                if cat in cat_entries:
                                    cat_entries[cat].delete(0, "end")
                                    cat_entries[cat].insert(0, str(amt))
                            for bill in budget_state["bills"]:
                                add_bill_row(bill.get("name", ""), bill.get("amount", 0))
                        parent.after(0, _fill)
            except Exception:
                pass
            parent.after(0, fetch_actuals)

        threading.Thread(target=_do, daemon=True).start()

    def fetch_actuals():
        status_var.set("Fetching transactions…")
        def _do():
            try:
                resp = requests.get(f"{API_BASE}/plaid/transactions",
                                     params={"user_id": user_id}, timeout=15)
                if resp.ok:
                    transactions = resp.json().get("transactions", [])
                    actuals = {}
                    today   = datetime.date.today()
                    month_start = today.replace(day=1).isoformat()

                    for tx in transactions:
                        # Only include debits (positive amount = money out in Plaid)
                        if tx.get("amount", 0) <= 0:
                            continue
                        tx_date = tx.get("date", "")
                        if tx_date < month_start:
                            continue
                        plaid_cats = tx.get("category") or []
                        cat = map_plaid_category(plaid_cats)
                        actuals[cat] = actuals.get(cat, 0) + tx.get("amount", 0)

                    budget_state["actuals"] = actuals

                def _update():
                    render_spending()
                    status_var.set("✓  Up to date")
                parent.after(0, _update)
            except Exception as e:
                parent.after(0, lambda: status_var.set(f"Could not load transactions: {e}"))

        threading.Thread(target=_do, daemon=True).start()

    def save_budget():
        save_btn.configure(state="disabled")
        try:
            income = float(income_entry.get().strip().replace("$", "").replace(",", "") or 0)
        except ValueError:
            income = 0

        cats = {}
        for cat, ent in cat_entries.items():
            try:
                val = float(ent.get().strip().replace("$", "").replace(",", "") or 0)
            except ValueError:
                val = 0
            if val > 0:
                cats[cat] = val

        bills = []
        for name_ent, amt_ent, _ in bills_rows:
            name = name_ent.get().strip()
            try:
                amt = float(amt_ent.get().strip().replace("$", "").replace(",", "") or 0)
            except ValueError:
                amt = 0
            if name:
                bills.append({"name": name, "amount": amt})

        budget_state["income"]      = income
        budget_state["payday_freq"] = payday_var.get()
        budget_state["next_payday"] = payday_entry.get().strip()
        budget_state["categories"]  = cats
        budget_state["bills"]       = bills

        payload = {
            "user_id":     user_id,
            "income":      income,
            "payday_freq": payday_var.get(),
            "next_payday": payday_entry.get().strip(),
            "categories":  cats,
            "bills":       bills,
        }

        def _do():
            try:
                requests.post(f"{API_BASE}/budget", json=payload, timeout=10)
                parent.after(0, lambda: status_var.set("✓  Budget saved"))
                parent.after(0, render_spending)
            except Exception as e:
                parent.after(0, lambda: status_var.set(f"Save failed: {e}"))
            finally:
                parent.after(0, lambda: save_btn.configure(state="normal"))

        threading.Thread(target=_do, daemon=True).start()

    # Refresh button
    ctk.CTkButton(parent, text="⟳  Refresh Spending", width=160, height=30,
                   fg_color="#252830", hover_color="#2b4bc8",
                   text_color="#7B93DB", corner_radius=8,
                   command=fetch_actuals).place(x=696, y=18)

    load_budget()


# =============================================================================
#  CALENDAR TAB
# =============================================================================

TYPE_COLORS = {
    "Appointment": "#5B8DEF",
    "Bill":        "#FF6B6B",
    "Meeting":     "#4CFF7A",
    "Reminder":    "#FFD700",
    "Other":       "#7B93DB",
}

EVENT_COLORS = [
    ("#5B8DEF", "Blue"),
    ("#FF6B6B", "Red"),
    ("#4CFF7A", "Green"),
    ("#FFD700", "Gold"),
    ("#C084FC", "Purple"),
    ("#FF9F40", "Orange"),
    ("#7B93DB", "Lavender"),
    ("#38bdf8", "Sky"),
    ("#fb7185", "Pink"),
]

RECURRENCE_OPTIONS = ["None", "Daily", "Weekly", "Bi-Weekly", "Monthly", "Yearly"]


def render_calendar_tab(parent, user_data=None, app_window=None):
    today        = datetime.date.today()
    state        = {"year": today.year, "month": today.month, "selected_date": None}
    events_cache = {}

    user_id = ""
    if user_data:
        user_id = str(user_data.get("id") or user_data.get("username", ""))

    # Status bar
    status_var = ctk.StringVar(value="")
    ctk.CTkLabel(parent, textvariable=status_var, fg_color="transparent",
                 text_color="#5B8DEF", font=ctk.CTkFont(size=11), width=300).place(x=30, y=654)

    # Header
    prev_btn = ctk.CTkButton(parent, text="‹", width=34, height=32,
                              fg_color="#1f2328", hover_color="#2b4bc8",
                              text_color="#7B93DB", corner_radius=8,
                              font=ctk.CTkFont(size=18), command=lambda: navigate(-1))
    prev_btn.place(x=30, y=20)

    month_label = ctk.CTkLabel(parent, text="", fg_color="transparent",
                                text_color="#7B93DB",
                                font=ctk.CTkFont(size=22, weight="bold"), width=220)
    month_label.place(x=72, y=22)

    next_btn = ctk.CTkButton(parent, text="›", width=34, height=32,
                              fg_color="#1f2328", hover_color="#2b4bc8",
                              text_color="#7B93DB", corner_radius=8,
                              font=ctk.CTkFont(size=18), command=lambda: navigate(1))
    next_btn.place(x=300, y=20)

    add_btn = ctk.CTkButton(parent, text="＋  Add Event", width=140, height=32,
                             fg_color="#3b5bdb", hover_color="#2b4bc8",
                             text_color="#f0f1f3", corner_radius=8,
                             command=lambda: open_event_dialog(state["selected_date"] or today))
    add_btn.place(x=710, y=20)

    # Day-of-week headers
    DAYS   = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    CELL_W = 76
    CELL_H = 84
    GRID_X = 30
    GRID_Y = 62

    for i, d in enumerate(DAYS):
        ctk.CTkLabel(parent, text=d, fg_color="transparent",
                     text_color="#5B8DEF", font=ctk.CTkFont(size=12, weight="bold"),
                     width=CELL_W).place(x=GRID_X + i * CELL_W, y=GRID_Y)

    grid_frame = ctk.CTkFrame(parent, fg_color="transparent",
                               width=CELL_W * 7, height=CELL_H * 6)
    grid_frame.place(x=GRID_X, y=GRID_Y + 26)

    # Side panel
    side_panel = ctk.CTkFrame(parent, fg_color="#1f2328", width=278, height=570, corner_radius=8)
    side_panel.place(x=582, y=60)
    side_panel.pack_propagate(False)

    side_title = ctk.CTkLabel(side_panel, text="Select a day", fg_color="transparent",
                               text_color="#7B93DB", font=ctk.CTkFont(size=15, weight="bold"))
    side_title.pack(anchor="w", padx=16, pady=(14, 4))

    ctk.CTkFrame(side_panel, fg_color="#2a2f38", height=1).pack(fill="x", padx=16, pady=(0, 8))

    side_scroll = ctk.CTkScrollableFrame(side_panel, fg_color="transparent", width=250, height=460)
    side_scroll.pack(fill="both", expand=True, padx=6, pady=4)

    def get_event_color(ev):
        c = ev.get("color", "")
        if c:
            return c
        return TYPE_COLORS.get(ev.get("event_type", "Other"), "#7B93DB")

    # ── Grid rendering with event bars ────────────────────────────────────────
    def render_grid():
        for w in grid_frame.winfo_children():
            w.destroy()

        year  = state["year"]
        month = state["month"]
        month_label.configure(text=datetime.date(year, month, 1).strftime("%B %Y"))

        cal_mod.setfirstweekday(6)
        weeks = cal_mod.monthcalendar(year, month)

        for row, week in enumerate(weeks):
            for col, day in enumerate(week):
                x = col * CELL_W
                y = row * CELL_H

                cell = ctk.CTkFrame(grid_frame, fg_color="#1f2328",
                                    width=CELL_W - 3, height=CELL_H - 3, corner_radius=8)
                cell.place(x=x + 1, y=y + 1)

                if day == 0:
                    cell.configure(fg_color="#141820")
                    continue

                date_obj = datetime.date(year, month, day)
                date_str = date_obj.isoformat()
                is_today = (date_obj == today)
                is_sel   = (date_obj == state["selected_date"])

                if is_sel:
                    cell.configure(fg_color="#1e2d52")
                elif is_today:
                    cell.configure(fg_color="#1e2438")

                num_bg = "#3b5bdb" if is_today else "transparent"
                num_tc = "#000000" if is_today else ("#7B93DB" if is_sel else "#f0f1f3")
                num_lbl = ctk.CTkLabel(cell, text=str(day), fg_color=num_bg,
                                        text_color=num_tc,
                                        font=ctk.CTkFont(size=12, weight="bold"),
                                        width=22, height=22, corner_radius=11)
                num_lbl.place(x=5, y=4)

                # Event bars instead of dots
                day_events = events_cache.get(date_str, [])
                bar_y = 28
                for ev in day_events[:3]:
                    color = get_event_color(ev)
                    is_private = not ev.get("family_shared", True)
                    bar = ctk.CTkFrame(cell, fg_color=color,
                                        width=CELL_W - 10, height=14, corner_radius=3)
                    bar.place(x=3, y=bar_y)
                    # Show truncated title on bar (with lock for private)
                    title_text = ev.get("title", "")
                    prefix = "🔒" if is_private else ""
                    max_chars = 6 if is_private else 8
                    if len(title_text) > max_chars:
                        title_text = title_text[:max_chars - 1] + "…"
                    ctk.CTkLabel(bar, text=f"{prefix}{title_text}", fg_color="transparent",
                                  text_color="#000000",
                                  font=ctk.CTkFont(size=8, weight="bold"),
                                  width=CELL_W - 14, height=14, anchor="w"
                                  ).place(x=2, y=0)
                    bar.bind("<Button-1>", lambda e, d=date_obj: on_click(d))
                    bar_y += 16

                # Overflow indicator
                if len(day_events) > 3:
                    ctk.CTkLabel(cell, text=f"+{len(day_events) - 3} more",
                                  fg_color="transparent", text_color="#4b5060",
                                  font=ctk.CTkFont(size=8),
                                  width=CELL_W - 10, height=10).place(x=5, y=bar_y)

                def on_click(d=date_obj):
                    state["selected_date"] = d
                    render_grid()
                    show_day_events(d)

                for widget in [cell, num_lbl]:
                    widget.bind("<Button-1>", lambda e, d=date_obj: on_click(d))
                    widget.configure(cursor="hand2")

    # ── Side panel event list ─────────────────────────────────────────────────
    def show_day_events(date_obj):
        for w in side_scroll.winfo_children():
            w.destroy()

        date_str = date_obj.isoformat()
        side_title.configure(text=date_obj.strftime("%A, %b %d %Y"))
        events = events_cache.get(date_str, [])

        if not events:
            ctk.CTkLabel(side_scroll, text="No events this day.",
                          fg_color="transparent", text_color="#4b5060",
                          font=ctk.CTkFont(size=13)).pack(anchor="w", padx=8, pady=10)
        else:
            for ev in events:
                color = get_event_color(ev)
                card  = ctk.CTkFrame(side_scroll, fg_color="#363C45", corner_radius=8)
                card.pack(fill="x", pady=4, padx=2)

                ctk.CTkFrame(card, fg_color=color, width=4, height=70, corner_radius=2).place(x=0, y=0)

                info = ctk.CTkFrame(card, fg_color="transparent")
                info.pack(fill="x", padx=(10, 6), pady=(8, 0))

                ctk.CTkLabel(info, text=ev.get("title", ""), fg_color="transparent",
                              text_color="#d1d5db", font=ctk.CTkFont(size=13, weight="bold"),
                              anchor="w").pack(fill="x")

                # Type badge with color
                ctk.CTkLabel(info, text=f"  {ev.get('event_type', 'Other')}  ",
                              fg_color=color, text_color="#000000",
                              font=ctk.CTkFont(size=10, weight="bold"),
                              corner_radius=4, height=18).pack(anchor="w", pady=(2, 0))

                if ev.get("event_time"):
                    ctk.CTkLabel(info, text=f"🕐  {ev['event_time']}",
                                  fg_color="transparent", text_color="#9A9A9A",
                                  font=ctk.CTkFont(size=11)).pack(anchor="w")

                if ev.get("note"):
                    ctk.CTkLabel(info, text=ev["note"], fg_color="transparent",
                                  text_color="#666B75", font=ctk.CTkFont(size=11),
                                  wraplength=210, anchor="w").pack(fill="x")

                # Recurrence badge
                rec = ev.get("recurrence", "None")
                if rec and rec != "None":
                    ctk.CTkLabel(info, text=f"🔁  {rec}",
                                  fg_color="transparent", text_color="#8b9cf7",
                                  font=ctk.CTkFont(size=10)).pack(anchor="w")

                # Shared badge
                if not ev.get("family_shared", True):
                    ctk.CTkLabel(info, text="🔒  Private",
                                  fg_color="transparent", text_color="#6b7280",
                                  font=ctk.CTkFont(size=10)).pack(anchor="w")

                notif = ev.get("notify_before", "None")
                if notif and notif != "None":
                    ctk.CTkLabel(info, text=f"🔔  {notif} before",
                                  fg_color="transparent", text_color="#FFD700",
                                  font=ctk.CTkFont(size=11)).pack(anchor="w")

                # Action buttons row
                btn_row = ctk.CTkFrame(card, fg_color="transparent")
                btn_row.pack(fill="x", padx=8, pady=(2, 6))

                is_mine = ev.get("is_mine", True)
                ev_id = ev.get("id")

                if is_mine:
                    ctk.CTkButton(btn_row, text="✎ Edit", width=60, height=20,
                                   fg_color="transparent", hover_color="#1e2a42",
                                   text_color="#5B8DEF", corner_radius=4,
                                   font=ctk.CTkFont(size=11),
                                   command=lambda eid=ev_id, e=ev, d=date_obj:
                                       open_event_dialog(d, edit_event=e)
                                   ).pack(side="left")

                    ctk.CTkButton(btn_row, text="✕ Delete", width=70, height=20,
                                   fg_color="transparent", hover_color="#2a1520",
                                   text_color="#ff6b6b", corner_radius=4,
                                   font=ctk.CTkFont(size=11),
                                   command=lambda eid=ev_id, d=date_obj: delete_event(eid, d)
                                   ).pack(side="right")

        ctk.CTkButton(side_scroll, text="＋  Add Event for this day",
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       width=230, height=30, text_color="#f0f1f3", corner_radius=8,
                       command=lambda: open_event_dialog(date_obj)).pack(pady=(10, 4))

    # ── Load events ───────────────────────────────────────────────────────────
    def load_events():
        status_var.set("Loading…")
        def _do():
            try:
                resp = requests.get(
                    f"{API_BASE}/calendar/events",
                    params={"user_id": user_id, "year": state["year"], "month": state["month"]},
                    timeout=10
                )
                data   = resp.json() if resp.ok else {}
                events = data.get("events", [])
                new_cache = {}
                for ev in events:
                    ds = ev.get("event_date", "")
                    new_cache.setdefault(ds, []).append(ev)

                def _update():
                    events_cache.clear()
                    events_cache.update(new_cache)
                    render_grid()
                    status_var.set("")
                    if state["selected_date"]:
                        show_day_events(state["selected_date"])

                parent.after(0, _update)
            except Exception as e:
                parent.after(0, lambda: status_var.set(f"Error: {e}"))

        threading.Thread(target=_do, daemon=True).start()

    def navigate(direction):
        m = state["month"] + direction
        y = state["year"]
        if m > 12: m, y = 1,  y + 1
        if m < 1:  m, y = 12, y - 1
        state["month"] = m
        state["year"]  = y
        load_events()

    # ── Add / Edit event dialog ───────────────────────────────────────────────
    def open_event_dialog(date_obj, edit_event=None):
        is_edit = edit_event is not None
        dialog = ctk.CTkToplevel(parent)
        dialog.configure(fg_color="#13161b")
        dialog.title("Edit Event" if is_edit else "Add Event")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.after(200, lambda: dialog.attributes("-topmost", False))

        dw, dh = 400, 720
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        dialog.geometry(f"{dw}x{dh}+{px + pw//2 - dw//2}+{py + ph//2 - dh//2}")

        # ── Scrollable form ───────────────────────────────────────────────────
        form = ctk.CTkScrollableFrame(dialog, fg_color="transparent", width=370, height=670)
        form.place(x=8, y=8)

        ctk.CTkLabel(form, text="Edit Event" if is_edit else "Add Event",
                      fg_color="transparent",
                      text_color="#7B93DB", font=ctk.CTkFont(size=18, weight="bold")
                      ).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(form, text=date_obj.strftime("%B %d, %Y"), fg_color="transparent",
                      text_color="#5B8DEF", font=ctk.CTkFont(size=13)
                      ).pack(anchor="w", padx=12, pady=(0, 10))

        # ── BASICS ────────────────────────────────────────────────────────────
        # Title
        ctk.CTkLabel(form, text="Title *", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w", padx=12)
        title_entry = ctk.CTkEntry(form, placeholder_text="Event title",
                                    fg_color="#1f2328", width=338, height=36,
                                    text_color="#7B93DB", corner_radius=8,
                                    border_width=1, border_color="#2a2f38")
        title_entry.pack(padx=12, pady=(0, 6))
        if is_edit:
            title_entry.insert(0, edit_event.get("title", ""))

        # Type + Time side by side
        row1 = ctk.CTkFrame(form, fg_color="transparent")
        row1.pack(fill="x", padx=12, pady=(0, 6))

        type_col = ctk.CTkFrame(row1, fg_color="transparent")
        type_col.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkLabel(type_col, text="Type", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w")
        type_var = ctk.StringVar(value=edit_event.get("event_type", "Appointment") if is_edit else "Appointment")
        ctk.CTkOptionMenu(
            type_col, values=["Appointment", "Bill", "Meeting", "Reminder", "Other"],
            variable=type_var, fg_color="#1f2328", width=162, height=36,
            text_color="#7B93DB", button_color="#2a2f38",
            button_hover_color="#2b4bc8", corner_radius=8
        ).pack(anchor="w")

        time_col = ctk.CTkFrame(row1, fg_color="transparent")
        time_col.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ctk.CTkLabel(time_col, text="Time", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w")
        time_entry = ctk.CTkEntry(time_col, placeholder_text="e.g. 2:30 PM",
                                   fg_color="#1f2328", width=162, height=36,
                                   text_color="#7B93DB", corner_radius=8,
                                   border_width=1, border_color="#2a2f38")
        time_entry.pack(anchor="w")
        if is_edit and edit_event.get("event_time"):
            time_entry.insert(0, edit_event["event_time"])

        # ── COLOR ─────────────────────────────────────────────────────────────
        ctk.CTkLabel(form, text="Event Color", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w", padx=12)

        color_frame = ctk.CTkFrame(form, fg_color="transparent", height=40)
        color_frame.pack(fill="x", padx=12, pady=(0, 6))
        color_var = {"value": edit_event.get("color", "") if is_edit else ""}
        color_rings = []

        def set_color(hex_color):
            color_var["value"] = hex_color
            for ring, inner, c in color_rings:
                ring.configure(fg_color="#ffffff" if c == hex_color else "transparent")

        for hex_c, name in EVENT_COLORS:
            ring = ctk.CTkFrame(color_frame, fg_color="transparent",
                                 width=32, height=32, corner_radius=16)
            ring.pack(side="left", padx=1)
            inner = ctk.CTkButton(ring, text="", width=24, height=24,
                                   fg_color=hex_c, hover_color=hex_c,
                                   corner_radius=12,
                                   command=lambda c=hex_c: set_color(c))
            inner.place(relx=0.5, rely=0.5, anchor="center")
            color_rings.append((ring, inner, hex_c))

        ctk.CTkButton(color_frame, text="Auto", width=40, height=28,
                       fg_color="#1f2328", hover_color="#2a2f38",
                       text_color="#7B93DB", corner_radius=8,
                       font=ctk.CTkFont(size=10),
                       command=lambda: set_color("")
                       ).pack(side="left", padx=(6, 0))

        if color_var["value"]:
            set_color(color_var["value"])

        # ── OPTIONS SECTION ───────────────────────────────────────────────────
        ctk.CTkFrame(form, fg_color="#1e2438", height=1).pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(form, text="\u2699  Options", fg_color="transparent",
                      text_color="#7B93DB", font=ctk.CTkFont(size=13, weight="bold")
                      ).pack(anchor="w", padx=12, pady=(4, 6))

        # Recurrence
        ctk.CTkLabel(form, text="Repeat", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w", padx=12)
        recur_var = ctk.StringVar(value=edit_event.get("recurrence", "None") if is_edit else "None")
        ctk.CTkOptionMenu(
            form, values=RECURRENCE_OPTIONS,
            variable=recur_var, fg_color="#1f2328", width=338, height=36,
            text_color="#7B93DB", button_color="#2a2f38",
            button_hover_color="#2b4bc8", corner_radius=8
        ).pack(padx=12, pady=(0, 6))

        # Notify Before
        ctk.CTkLabel(form, text="Notify Before", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w", padx=12)
        notify_var = ctk.StringVar(value=edit_event.get("notify_before", "None") if is_edit else "None")
        ctk.CTkOptionMenu(
            form, values=["None", "5 minutes", "15 minutes", "30 minutes", "1 hour", "1 day"],
            variable=notify_var, fg_color="#1f2328", width=338, height=36,
            text_color="#7B93DB", button_color="#2a2f38",
            button_hover_color="#2b4bc8", corner_radius=8
        ).pack(padx=12, pady=(0, 6))

        # Family shared toggle
        shared_var = ctk.BooleanVar(
            value=edit_event.get("family_shared", True) if is_edit else True
        )
        share_frame = ctk.CTkFrame(form, fg_color="#1f2328", corner_radius=8)
        share_frame.pack(fill="x", padx=12, pady=(2, 6))
        ctk.CTkSwitch(share_frame, text="  Share with family",
                        variable=shared_var, fg_color="#363C45",
                        progress_color="#3b5bdb",
                        font=ctk.CTkFont(size=13), text_color="#d1d5db"
                        ).pack(anchor="w", padx=14, pady=10)
        ctk.CTkLabel(share_frame, text="When off, only you can see this event",
                      fg_color="transparent", text_color="#4b5060",
                      font=ctk.CTkFont(size=10)
                      ).pack(anchor="w", padx=14, pady=(0, 8))

        # ── NOTE ──────────────────────────────────────────────────────────────
        ctk.CTkFrame(form, fg_color="#1e2438", height=1).pack(fill="x", padx=12, pady=(4, 4))
        ctk.CTkLabel(form, text="Note (optional)", fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)
                      ).pack(anchor="w", padx=12)
        note_entry = ctk.CTkEntry(form, placeholder_text="Optional note",
                                   fg_color="#1f2328", width=338, height=36,
                                   text_color="#7B93DB", corner_radius=8,
                                   border_width=1, border_color="#2a2f38")
        note_entry.pack(padx=12, pady=(0, 6))
        if is_edit and edit_event.get("note"):
            note_entry.insert(0, edit_event["note"])

        # Error / status
        err_label = ctk.CTkLabel(form, text="", fg_color="transparent",
                                  text_color="#ff6b6b", font=ctk.CTkFont(size=11))
        err_label.pack(anchor="w", padx=12)

        def submit():
            title = title_entry.get().strip()
            if not title:
                err_label.configure(text="Title is required.")
                return
            payload = {
                "user_id":       user_id,
                "title":         title,
                "event_date":    date_obj.isoformat(),
                "event_type":    type_var.get(),
                "event_time":    time_entry.get().strip(),
                "notify_before": notify_var.get(),
                "note":          note_entry.get().strip(),
                "color":         color_var["value"],
                "recurrence":    recur_var.get(),
                "family_shared": shared_var.get(),
            }
            def _do():
                try:
                    if is_edit:
                        resp = requests.put(
                            f"{API_BASE}/calendar/events/{edit_event['id']}",
                            json=payload, timeout=10
                        )
                    else:
                        resp = requests.post(
                            f"{API_BASE}/calendar/events",
                            json=payload, timeout=10
                        )
                    if resp.ok:
                        parent.after(0, lambda: [dialog.destroy(), load_events()])
                    else:
                        parent.after(0, lambda: err_label.configure(text="Failed to save. Try again."))
                except Exception as e:
                    parent.after(0, lambda: err_label.configure(text=f"Error: {e}"))
            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(form, text="Save Changes" if is_edit else "Save Event",
                       command=submit,
                       fg_color="#3b5bdb", hover_color="#2b4bc8",
                       width=338, height=40, text_color="#f0f1f3", corner_radius=8,
                       font=ctk.CTkFont(size=13, weight="bold")
                       ).pack(padx=12, pady=(6, 12))

    def delete_event(event_id, date_obj):
        def _do():
            try:
                requests.delete(f"{API_BASE}/calendar/events/{event_id}", timeout=10)
                parent.after(0, load_events)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    # Notification checker — runs every 60 seconds
    NOTIFY_DELTAS = {
        "5 minutes":  datetime.timedelta(minutes=5),
        "15 minutes": datetime.timedelta(minutes=15),
        "30 minutes": datetime.timedelta(minutes=30),
        "1 hour":     datetime.timedelta(hours=1),
        "1 day":      datetime.timedelta(days=1),
    }

    def check_notifications():
        if not parent.winfo_exists():
            return
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        for date_str, events in list(events_cache.items()):
            for ev in events:
                ev_id         = ev.get("id")
                notify_before = ev.get("notify_before", "None")
                # Track by (event_id, date) so recurring events notify on each day
                notif_key = (ev_id, date_str)
                if notif_key in _notified_events or not notify_before or notify_before == "None":
                    continue
                ev_time = ev.get("event_time", "")
                if not ev_time:
                    continue
                # Only check events happening today
                if date_str != today_str:
                    continue
                ev_dt = None
                for fmt in ["%I:%M %p", "%H:%M", "%I:%M%p"]:
                    try:
                        ev_dt = datetime.datetime.strptime(f"{date_str} {ev_time}", f"%Y-%m-%d {fmt}")
                        break
                    except ValueError:
                        continue
                if not ev_dt:
                    continue
                delta     = NOTIFY_DELTAS.get(notify_before, datetime.timedelta(0))
                notify_at = ev_dt - delta
                if notify_at <= now <= ev_dt:
                    _notified_events.add(notif_key)
                    parent.after(0, lambda e=ev, nb=notify_before: show_notification_popup(e, nb))
        parent.after(60000, check_notifications)

    def show_notification_popup(ev, notify_before):
        popup = ctk.CTkToplevel()
        popup.configure(fg_color="#13161b")
        popup.title("EverNest Reminder")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        pw, ph = 330, 150
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        popup.geometry(f"{pw}x{ph}+{sw - pw - 20}+{sh - ph - 60}")

        color = get_event_color(ev)
        ctk.CTkFrame(popup, fg_color=color, width=330, height=4, corner_radius=0).place(x=0, y=0)
        ctk.CTkLabel(popup, text=f"🔔  {notify_before} before", fg_color="transparent",
                      text_color="#FFD700", font=ctk.CTkFont(size=11)).place(x=16, y=14)
        ctk.CTkLabel(popup, text=ev.get("title", ""), fg_color="transparent",
                      text_color="#f0f1f3", font=ctk.CTkFont(size=16, weight="bold")).place(x=16, y=36)
        meta = ev.get("event_type", "")
        if ev.get("event_time"):
            meta += f"  •  {ev['event_time']}"
        ctk.CTkLabel(popup, text=meta, fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=12)).place(x=16, y=62)
        ctk.CTkButton(popup, text="Dismiss", command=popup.destroy,
                       fg_color="#1f2328", hover_color="#2a1520",
                       width=100, height=28, text_color="#7B93DB",
                       corner_radius=8).place(x=115, y=106)
        popup.after(12000, lambda: popup.destroy() if popup.winfo_exists() else None)

    load_events()
    parent.after(60000, check_notifications)
# =============================================================================
# Notes Tab
# =============================================================================
def render_notes_tab(parent, user_data=None):
    import tkinter as tk
    import tkinter.font as tkfont
 
    user_id = ""
    if user_data:
        user_id = str(user_data.get("id") or user_data.get("username", ""))
 
    state = {
        "notes":     [],
        "active_id": None,
        "dirty":     False,
        "checkboxes": [],   # list of (widget, BooleanVar) embedded in text
    }
 
    # ── Layout ────────────────────────────────────────────────────────────────
    list_panel = ctk.CTkFrame(parent, fg_color="#13161b", width=240, height=660, corner_radius=0)
    list_panel.place(x=0, y=0)
    list_panel.pack_propagate(False)
 
    editor_panel = ctk.CTkFrame(parent, fg_color="#1a1d23", width=636, height=660, corner_radius=0)
    editor_panel.place(x=240, y=0)
 
    # ── List panel ────────────────────────────────────────────────────────────
    ctk.CTkLabel(list_panel, text="Notes", fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=20, weight="bold")).place(x=16, y=16)
 
    ctk.CTkButton(list_panel, text="＋  New Note", width=200, height=30,
                   fg_color="#3b5bdb", hover_color="#2b4bc8",
                   text_color="#f0f1f3", corner_radius=8,
                   font=ctk.CTkFont(size=12),
                   command=lambda: new_note()).place(x=16, y=52)
 
    search_entry = ctk.CTkEntry(list_panel, placeholder_text="🔍  Search…",
                                 fg_color="#1f2328", width=208, height=30,
                                 text_color="#7B93DB", corner_radius=8,
                                 border_width=1, border_color="#2a2f38")
    search_entry.place(x=16, y=92)
 
    notes_scroll = ctk.CTkScrollableFrame(list_panel, fg_color="transparent",
                                           width=222, height=530)
    notes_scroll.place(x=8, y=130)
 
    # ── Editor: title ─────────────────────────────────────────────────────────
    title_entry = ctk.CTkEntry(editor_panel, placeholder_text="Title",
                                fg_color="transparent", width=580, height=44,
                                text_color="#f0f1f3", corner_radius=0, border_width=0,
                                font=ctk.CTkFont(size=22, weight="bold"))
    title_entry.place(x=16, y=10)
 
    # ── Toolbar row 1: formatting ─────────────────────────────────────────────
    toolbar = ctk.CTkFrame(editor_panel, fg_color="#1f2328", width=608, height=38, corner_radius=8)
    toolbar.place(x=14, y=58)
 
    # ── Toolbar row 2: font controls ──────────────────────────────────────────
    toolbar2 = ctk.CTkFrame(editor_panel, fg_color="#1f2328", width=608, height=34, corner_radius=8)
    toolbar2.place(x=14, y=100)
 
    # ── Text body ─────────────────────────────────────────────────────────────
    text_outer = ctk.CTkFrame(editor_panel, fg_color="#1f2328", width=608, height=468, corner_radius=8)
    text_outer.place(x=14, y=138)
 
    body_text = tk.Text(
        text_outer,
        bg="#1f2328", fg="#d1d5db",
        insertbackground="#7B93DB",
        font=("Segoe UI", 13),
        relief="flat", bd=0,
        wrap="word",
        selectbackground="#2b4bc8",
        selectforeground="#f0f1f3",
        padx=14, pady=12,
        undo=True,
        spacing1=2, spacing3=2,
    )
    body_text.pack(fill="both", expand=True)
 
    # Scrollbar
    sb = tk.Scrollbar(text_outer, command=body_text.yview, bg="#1f2328",
                       troughcolor="#1f2328", activebackground="#2a2f38")
    body_text.configure(yscrollcommand=sb.set)
 
    # ── Configure base tags ───────────────────────────────────────────────────
    FONTS    = ["Segoe UI", "Arial", "Georgia", "Courier New", "Verdana"]
    SIZES    = [10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32]
    FG_COLORS = ["#f0f1f3", "#FF6B6B", "#FFD700", "#4CFF7A", "#5B8DEF",
                  "#FF9F40", "#C084FC", "#7B93DB", "#9A9A9A", "#000000"]
    HL_COLORS = ["#FFD700", "#4CFF7A", "#FF6B6B", "#5B8DEF", "#FF9F40",
                  "#C084FC", "transparent"]
 
    def make_font_tag(family, size, bold=False, italic=False, underline=False, strikethrough=False):
        style = []
        if bold:      style.append("bold")
        if italic:    style.append("italic")
        tag = f"font_{family}_{size}_{'_'.join(style) or 'normal'}"
        weight = "bold"   if bold   else "normal"
        slant  = "italic" if italic else "roman"
        body_text.tag_configure(tag,
            font=(family, size, weight, slant),
            underline=1 if underline else 0,
            overstrike=1 if strikethrough else 0)
        return tag
 
    # Static tags
    body_text.tag_configure("bold",        font=("Segoe UI", 13, "bold"))
    body_text.tag_configure("italic",      font=("Segoe UI", 13, "italic"))
    body_text.tag_configure("underline",   underline=1)
    body_text.tag_configure("strikethrough", overstrike=1)
    body_text.tag_configure("h1",          font=("Segoe UI", 24, "bold"), foreground="#7B93DB")
    body_text.tag_configure("h2",          font=("Segoe UI", 18, "bold"), foreground="#7B93DB")
    body_text.tag_configure("h3",          font=("Segoe UI", 14, "bold"), foreground="#7B93DB")
    body_text.tag_configure("bullet",      lmargin1=20, lmargin2=32)
    body_text.tag_configure("indent",      lmargin1=40, lmargin2=52)
 
    # ── Current format state ──────────────────────────────────────────────────
    fmt = {
        "family": "Segoe UI",
        "size":   13,
        "bold":   False,
        "italic": False,
        "underline":   False,
        "strikethrough": False,
        "fg_color": None,
        "hl_color": None,
    }
 
    def mark_dirty(*a):
        state["dirty"] = True
 
    body_text.bind("<<Modified>>", mark_dirty)
    title_entry.bind("<KeyRelease>", mark_dirty)
 
    # ── Apply tag to selection ────────────────────────────────────────────────
    def toggle_tag(tag):
        try:
            s = body_text.index("sel.first")
            e = body_text.index("sel.last")
            if tag in body_text.tag_names(s):
                body_text.tag_remove(tag, s, e)
            else:
                body_text.tag_add(tag, s, e)
        except tk.TclError:
            pass
        mark_dirty()
 
    def apply_line_tag(tag):
        ls = body_text.index("insert linestart")
        le = body_text.index("insert lineend+1c")
        body_text.tag_add(tag, ls, le)
        mark_dirty()
 
    def apply_color_tag(color, prefix="fg"):
        try:
            s = body_text.index("sel.first")
            e = body_text.index("sel.last")
        except tk.TclError:
            return
        tag = f"{prefix}_{color.replace('#','')}"
        if prefix == "fg":
            body_text.tag_configure(tag, foreground=color)
        else:
            body_text.tag_configure(tag, background=color if color != "transparent" else "")
        body_text.tag_add(tag, s, e)
        mark_dirty()
 
    def apply_font_to_selection(family=None, size=None):
        try:
            s = body_text.index("sel.first")
            e = body_text.index("sel.last")
        except tk.TclError:
            return
        fam  = family or fmt["family"]
        sz   = size   or fmt["size"]
        tag  = f"custom_{fam}_{sz}"
        body_text.tag_configure(tag, font=(fam, sz))
        body_text.tag_add(tag, s, e)
        if family: fmt["family"] = fam
        if size:   fmt["size"]   = sz
        mark_dirty()
 
    # ── Color picker popup ────────────────────────────────────────────────────
    def show_color_picker(colors, callback, anchor_widget):
        popup = tk.Toplevel(body_text)
        popup.overrideredirect(True)
        popup.configure(bg="#1f2328")
        popup.attributes("-topmost", True)
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height() + 2
        popup.geometry(f"+{x}+{y}")
 
        frame = tk.Frame(popup, bg="#1f2328", padx=4, pady=4)
        frame.pack()
 
        for i, color in enumerate(colors):
            display = color if color != "transparent" else "#1a1d23"
            def on_click(c=color):
                callback(c)
                popup.destroy()
            btn = tk.Button(frame, bg=display, width=2, height=1,
                             relief="flat", cursor="hand2", command=on_click,
                             highlightthickness=1, highlightbackground="#2a2f38")
            btn.grid(row=0, column=i, padx=2, pady=2)
 
        popup.bind("<FocusOut>", lambda e: popup.destroy())
        popup.focus_set()
 
    # ── Insert checkbox bullet ────────────────────────────────────────────────
    def insert_checkbox():
        var = tk.BooleanVar(value=False)
 
        def on_toggle():
            mark_dirty()
            # strike through the line text after the checkbox
            idx = body_text.index(f"{cb_window}.first + 1c") if cb_window else None
 
        cb = tk.Checkbutton(body_text, variable=var, bg="#1f2328",
                             activebackground="#1f2328",
                             selectcolor="#2b4bc8",
                             fg="#7B93DB", relief="flat",
                             cursor="hand2", command=on_toggle)
        cb_window = body_text.window_create("insert", window=cb)
        body_text.insert("insert", " ")
        state["checkboxes"].append((cb, var))
        mark_dirty()
 
    # ── Insert bullet ─────────────────────────────────────────────────────────
    def insert_bullet():
        body_text.insert("insert linestart", "• ")
        apply_line_tag("bullet")
        mark_dirty()
 
    # ── Toolbar row 1 buttons ─────────────────────────────────────────────────
    def tb_btn(parent_frame, text, cmd, x, width=30, color="#7B93DB"):
        b = ctk.CTkButton(parent_frame, text=text, width=width, height=28,
                           fg_color="transparent", hover_color="#2a2f38",
                           text_color=color, corner_radius=4,
                           font=ctk.CTkFont(size=12, weight="bold"),
                           command=cmd)
        b.place(x=x, y=4)
        return b
 
    x = 6
    tb_btn(toolbar, "B",  lambda: toggle_tag("bold"),          x, 28); x += 32
    tb_btn(toolbar, "I",  lambda: toggle_tag("italic"),         x, 28); x += 32
    tb_btn(toolbar, "U",  lambda: toggle_tag("underline"),      x, 28); x += 32
    tb_btn(toolbar, "S̶",  lambda: toggle_tag("strikethrough"),  x, 28); x += 34
 
    # Divider
    ctk.CTkFrame(toolbar, fg_color="#2a2f38", width=1, height=24).place(x=x+2, y=6); x += 10
 
    tb_btn(toolbar, "H1", lambda: apply_line_tag("h1"), x, 34); x += 38
    tb_btn(toolbar, "H2", lambda: apply_line_tag("h2"), x, 34); x += 38
    tb_btn(toolbar, "H3", lambda: apply_line_tag("h3"), x, 34); x += 38
 
    ctk.CTkFrame(toolbar, fg_color="#2a2f38", width=1, height=24).place(x=x+2, y=6); x += 10
 
    tb_btn(toolbar, "•",  insert_bullet,    x, 28); x += 32
    tb_btn(toolbar, "☑",  insert_checkbox,  x, 28); x += 38
 
    ctk.CTkFrame(toolbar, fg_color="#2a2f38", width=1, height=24).place(x=x+2, y=6); x += 10
 
    # Text color button
    fg_btn = ctk.CTkButton(toolbar, text="A", width=30, height=28,
                            fg_color="transparent", hover_color="#2a2f38",
                            text_color="#FF6B6B", corner_radius=4,
                            font=ctk.CTkFont(size=13, weight="bold"),
                            command=lambda: show_color_picker(
                                FG_COLORS,
                                lambda c: apply_color_tag(c, "fg"),
                                fg_btn
                            ))
    fg_btn.place(x=x, y=4); x += 34
 
    # Highlight button
    hl_btn = ctk.CTkButton(toolbar, text="▐H", width=34, height=28,
                            fg_color="transparent", hover_color="#2a2f38",
                            text_color="#FFD700", corner_radius=4,
                            font=ctk.CTkFont(size=12, weight="bold"),
                            command=lambda: show_color_picker(
                                HL_COLORS,
                                lambda c: apply_color_tag(c, "hl"),
                                hl_btn
                            ))
    hl_btn.place(x=x, y=4); x += 40
 
    ctk.CTkFrame(toolbar, fg_color="#2a2f38", width=1, height=24).place(x=x+2, y=6); x += 10
 
    # Save / Copy / Delete
    ctk.CTkButton(toolbar, text="Save", width=46, height=28,
                   fg_color="transparent", hover_color="#2a2f38",
                   text_color="#4CFF7A", corner_radius=4,
                   font=ctk.CTkFont(size=12),
                   command=lambda: save_note()).place(x=x, y=4); x += 50

    ctk.CTkButton(toolbar, text="Copy", width=46, height=28,
                   fg_color="transparent", hover_color="#2a2f38",
                   text_color="#7B93DB", corner_radius=4,
                   font=ctk.CTkFont(size=12),
                   command=lambda: copy_note()).place(x=x, y=4); x += 50

    ctk.CTkButton(toolbar, text="Delete", width=54, height=28,
                   fg_color="transparent", hover_color="#2a1520",
                   text_color="#ff6b6b", corner_radius=4,
                   font=ctk.CTkFont(size=12),
                   command=lambda: delete_note()).place(x=x, y=4)
 
    # ── Toolbar row 2: font family + size ─────────────────────────────────────
    ctk.CTkLabel(toolbar2, text="Font:", fg_color="transparent",
                 text_color="#9A9A9A", font=ctk.CTkFont(size=11)).place(x=8, y=8)
 
    font_var = ctk.StringVar(value="Segoe UI")
    font_menu = ctk.CTkOptionMenu(toolbar2, values=FONTS, variable=font_var,
                                   fg_color="#363C45", width=130, height=26,
                                   text_color="#7B93DB", button_color="#2a2f38",
                                   button_hover_color="#2b4bc8", corner_radius=4,
                                   command=lambda f: apply_font_to_selection(family=f))
    font_menu.place(x=46, y=4)
 
    ctk.CTkLabel(toolbar2, text="Size:", fg_color="transparent",
                 text_color="#9A9A9A", font=ctk.CTkFont(size=11)).place(x=186, y=8)
 
    size_var = ctk.StringVar(value="13")
    size_menu = ctk.CTkOptionMenu(toolbar2, values=[str(s) for s in SIZES],
                                   variable=size_var,
                                   fg_color="#363C45", width=72, height=26,
                                   text_color="#7B93DB", button_color="#2a2f38",
                                   button_hover_color="#2b4bc8", corner_radius=4,
                                   command=lambda s: apply_font_to_selection(size=int(s)))
    size_menu.place(x=218, y=4)
 
    # Meta label
    meta_label = ctk.CTkLabel(editor_panel, text="", fg_color="transparent",
                               text_color="#4b5060", font=ctk.CTkFont(size=11))
    meta_label.place(x=16, y=614)
 
    # ── Note list rendering ───────────────────────────────────────────────────
    def render_note_list(filter_text=""):
        for w in notes_scroll.winfo_children():
            w.destroy()
 
        filtered = [n for n in state["notes"]
                    if filter_text.lower() in n.get("title", "").lower()
                    or filter_text.lower() in n.get("body", "").lower()]
 
        if not filtered:
            ctk.CTkLabel(notes_scroll, text="No notes yet.\nClick ＋ New Note.",
                          fg_color="transparent", text_color="#4b5060",
                          font=ctk.CTkFont(size=12), justify="center").pack(pady=20)
            return
 
        for note in filtered:
            is_active = (note["id"] == state["active_id"])
            card = ctk.CTkFrame(notes_scroll, fg_color="#1e2d52" if is_active else "#1f2328",
                                 width=210, height=62, corner_radius=8)
            card.pack(fill="x", pady=3, padx=2)
            card.pack_propagate(False)
 
            title_text = note.get("title", "Untitled")
            preview    = (note.get("body", "") or "")[:40].replace("\n", " ")
 
            ctk.CTkLabel(card, text=f"📝  {title_text[:22]}",
                          fg_color="transparent", text_color="#d1d5db",
                          font=ctk.CTkFont(size=12, weight="bold"),
                          anchor="w").place(x=10, y=8)
            ctk.CTkLabel(card, text=preview[:36] or "Empty note",
                          fg_color="transparent", text_color="#4b5060",
                          font=ctk.CTkFont(size=11), anchor="w").place(x=10, y=32)
 
            card.bind("<Button-1>", lambda e, n=note: open_note(n))
            for child in card.winfo_children():
                child.bind("<Button-1>", lambda e, n=note: open_note(n))
                child.configure(cursor="hand2")
            card.configure(cursor="hand2")
 
    search_entry.bind("<KeyRelease>",
                       lambda e: render_note_list(search_entry.get().strip()))
 
    # ── Open note ─────────────────────────────────────────────────────────────
    def open_note(note):
        placeholder.place_forget()
        if state["dirty"] and state["active_id"] is not None:
            save_note(silent=True)
 
        state["active_id"] = note["id"]
        state["dirty"]     = False
 
        # Clear checkboxes
        for cb, _ in state["checkboxes"]:
            try: cb.destroy()
            except: pass
        state["checkboxes"].clear()
 
        title_entry.delete(0, "end")
        title_entry.insert(0, note.get("title", ""))
        body_text.delete("1.0", "end")
        body_text.insert("1.0", note.get("body", ""))
 
        updated = note.get("updated_at", "")
        meta_label.configure(text=f"Last edited: {updated[:10] if updated else '—'}")
        state["dirty"] = False
        body_text.edit_reset()
        render_note_list()
 
    # ── Save note ─────────────────────────────────────────────────────────────
    def save_note(silent=False):
        if state["active_id"] is None:
            return
        title = title_entry.get().strip() or "Untitled"
        body  = body_text.get("1.0", "end-1c")
 
        payload = {
            "user_id":         user_id,
            "title":           title,
            "body":            body,
            "note_type":       "note",
            "checklist_items": [],
        }
 
        def _do():
            try:
                requests.put(f"{API_BASE}/notes/{state['active_id']}",
                              json=payload, timeout=10)
                parent.after(0, load_notes)
                if not silent:
                    parent.after(0, lambda: meta_label.configure(
                        text=f"Saved ✓  {datetime.date.today()}"))
            except Exception as e:
                if not silent:
                    parent.after(0, lambda: meta_label.configure(text=f"Save failed: {e}"))
 
        state["dirty"] = False
        threading.Thread(target=_do, daemon=True).start()
 
    # ── Copy note ─────────────────────────────────────────────────────────────
    def copy_note():
        content = title_entry.get() + "\n\n" + body_text.get("1.0", "end-1c")
        parent.clipboard_clear()
        parent.clipboard_append(content)
        meta_label.configure(text="Copied to clipboard ✓")
 
    # ── Delete note ───────────────────────────────────────────────────────────
    def delete_note():
        if state["active_id"] is None:
            return
        nid = state["active_id"]
        def _do():
            try:
                requests.delete(f"{API_BASE}/notes/{nid}", timeout=10)
                state["active_id"] = None
                parent.after(0, lambda: [
                    title_entry.delete(0, "end"),
                    body_text.delete("1.0", "end"),
                    placeholder.place(x=0, y=0),
                    load_notes()
                ])
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()
 
    # ── New note ──────────────────────────────────────────────────────────────
    def new_note():
        payload = {
            "user_id":         user_id,
            "title":           "Untitled",
            "body":            "",
            "note_type":       "note",
            "checklist_items": [],
        }
        def _do():
            try:
                resp = requests.post(f"{API_BASE}/notes", json=payload, timeout=10)
                if resp.ok:
                    note = resp.json().get("note", {})
                    parent.after(0, lambda: [load_notes(), open_note(note)])
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()
 
    # ── Load notes ────────────────────────────────────────────────────────────
    def load_notes():
        def _do():
            try:
                resp = requests.get(f"{API_BASE}/notes",
                                     params={"user_id": user_id}, timeout=10)
                if resp.ok:
                    state["notes"] = resp.json().get("notes", [])
                    parent.after(0, lambda: render_note_list(search_entry.get().strip()))
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()
 
    # ── Placeholder ───────────────────────────────────────────────────────────
    placeholder = ctk.CTkLabel(editor_panel, text="Select or create a note",
                                fg_color="#1a1d23", text_color="#4b5060",
                                font=ctk.CTkFont(size=16), width=636, height=660)
    placeholder.place(x=0, y=0)
 
    load_notes()
 
    # ── Auto-save every 30s ───────────────────────────────────────────────────
    def auto_save():
        if not parent.winfo_exists():
            return
        if state["dirty"] and state["active_id"] is not None:
            save_note(silent=True)
        parent.after(30000, auto_save)
 
    parent.after(30000, auto_save)
 

# =============================================================================
#  PLACEHOLDER TAB
# =============================================================================

def render_placeholder_tab(parent, title, message):
    ctk.CTkLabel(parent, text=title, fg_color="transparent",
                 text_color="#7B93DB", font=ctk.CTkFont(size=24, weight="bold")).place(x=30, y=28)
    ctk.CTkLabel(parent, text=message, fg_color="transparent",
                 text_color="#5B8DEF", font=ctk.CTkFont(size=14)).place(x=30, y=68)


# =============================================================================
#  LOADING SCREEN
# =============================================================================

def open_application_window(user_data=None):
    main.withdraw()
    app_window = ctk.CTkToplevel(main)
    app_window.configure(fg_color="#0c0e14")
    app_window.title("EverNest")
    center_toplevel(app_window, 820, 520)
    app_window.resizable(True, True)
    app_window.lift()
    app_window.attributes("-topmost", True)
    app_window.after(200, lambda: app_window.attributes("-topmost", False))
    app_window.focus_force()

    def close_app():
        app_window.destroy()
        main.destroy()
    app_window.protocol("WM_DELETE_WINDOW", close_app)

    loading_frame = ctk.CTkFrame(app_window, fg_color="#0c0e14", corner_radius=0)
    loading_frame.place(x=0, y=0, relwidth=1, relheight=1)

    spinner_label = ctk.CTkLabel(loading_frame, text="◜", fg_color="transparent",
                                  text_color="#5b6ef7", font=ctk.CTkFont(size=62, weight="bold"))
    spinner_label.place(relx=0.5, rely=0.42, anchor="center")

    loading_label = ctk.CTkLabel(loading_frame, text="Loading…", fg_color="transparent",
                                  text_color="#8b9cf7", font=ctk.CTkFont(size=22, weight="bold"))
    loading_label.place(relx=0.5, rely=0.57, anchor="center")

    spinner_frames  = ["◜", "◠", "◝", "◞", "◡", "◟"]
    spinner_running = {"value": True}

    def animate_spinner(index=0):
        if not spinner_running["value"]:
            return
        spinner_label.configure(text=spinner_frames[index % len(spinner_frames)])
        app_window.after(120, lambda: animate_spinner(index + 1))

    animate_spinner()

    def finish_loading():
        spinner_running["value"] = False
        render_main_application(app_window, user_data)

    app_window.after(random.randint(3, 5) * 1000, finish_loading)


# =============================================================================
#  LOGIN / SIGNUP
# =============================================================================

def login_request(email, password):
    try:
        response = requests.post(f"{API_BASE}/login",
                                  json={"login": email, "password": password}, timeout=10)
        data = response.json()
        if response.ok and data.get("success"):
            main.after(0, close_signup_if_open)
            main.after(0, lambda: open_application_window(data.get("user")))
        else:
            main.after(0, lambda: label.configure(
                text=data.get("message", "Login failed"), text_color="#f87171"))
    except requests.RequestException:
        main.after(0, lambda: label.configure(text="Server connection failed", text_color="#f87171"))
    except ValueError:
        main.after(0, lambda: label.configure(text="Invalid server response", text_color="#f87171"))
    finally:
        main.after(0, lambda: submit.configure(state="normal"))


def submit_login():
    email    = entry.get().strip()
    password = entry1.get().strip()
    if not email or not password:
        label.configure(text="Enter email and password", text_color="#f87171")
        return
    label.configure(text="Signing in…", text_color="#8b9cf7")
    submit.configure(state="disabled")
    threading.Thread(target=login_request, args=(email, password), daemon=True).start()


def signup_request(username, email, password, signup_window, status_label, signup_submit):
    try:
        response = requests.post(f"{API_BASE}/signup",
                                  json={"username": username, "email": email, "password": password},
                                  timeout=10)
        data = response.json()
        if response.ok and data.get("success"):
            def success_ui():
                status_label.configure(text="Account created — sign in below", text_color="#4ade80")
                label.configure(text="Welcome back", text_color="#e2e8f0")
                signup_window.after(1200, signup_window.destroy)
            main.after(0, success_ui)
        else:
            main.after(0, lambda: status_label.configure(
                text=data.get("message", "Signup failed"), text_color="#f87171"))
    except requests.RequestException:
        main.after(0, lambda: status_label.configure(text="Server connection failed", text_color="#f87171"))
    except ValueError:
        main.after(0, lambda: status_label.configure(text="Invalid server response", text_color="#f87171"))
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

    signup_window        = ctk.CTkToplevel(main)
    active_signup_window = signup_window
    signup_window.configure(fg_color="#0c0e14")
    signup_window.title("Create Account")

    ww, wh = 360, 420
    main.update_idletasks()
    mx, my = main.winfo_x(), main.winfo_y()
    mw, mh = main.winfo_width(), main.winfo_height()
    signup_window.geometry(f"{ww}x{wh}+{mx + mw//2 - ww//2}+{my + mh//2 - wh//2}")
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

    # ── Glass panel ───────────────────────────────────────────────────────────
    glass = ctk.CTkFrame(signup_window, fg_color="#12141c", width=320, height=380,
                          corner_radius=16, border_width=1, border_color="#1e2235")
    glass.place(x=20, y=20)

    # Frost accent
    ctk.CTkFrame(glass, fg_color="#1a1e2e", width=318, height=2,
                  corner_radius=0).place(x=1, y=1)

    # ── Branding ──────────────────────────────────────────────────────────────
    ctk.CTkLabel(glass, text="EverNest", fg_color="transparent",
                  text_color="#8b9cf7", font=ctk.CTkFont(size=12, weight="bold")
                  ).place(x=28, y=20)

    ctk.CTkLabel(glass, text="Create your account", fg_color="transparent",
                  width=260, height=28, text_color="#e2e8f0",
                  font=ctk.CTkFont(size=18, weight="bold")).place(x=24, y=46)

    ctk.CTkLabel(glass, text="Get started with EverNest today",
                  fg_color="transparent", width=260, height=16,
                  text_color="#4a5568", font=ctk.CTkFont(size=11)).place(x=27, y=74)

    # ── Username ──────────────────────────────────────────────────────────────
    ctk.CTkLabel(glass, text="Username", fg_color="transparent",
                  text_color="#8892a8", font=ctk.CTkFont(size=11)).place(x=28, y=108)

    username_entry = ctk.CTkEntry(glass, placeholder_text="Choose a username",
                                   fg_color="#181b24", width=264, height=40,
                                   text_color="#e2e8f0", corner_radius=10,
                                   border_width=1, border_color="#262b3a",
                                   placeholder_text_color="#3d4456",
                                   font=ctk.CTkFont(size=12))
    username_entry.place(x=28, y=128)

    # ── Email ─────────────────────────────────────────────────────────────────
    ctk.CTkLabel(glass, text="Email", fg_color="transparent",
                  text_color="#8892a8", font=ctk.CTkFont(size=11)).place(x=28, y=178)

    email_entry = ctk.CTkEntry(glass, placeholder_text="you@example.com",
                                fg_color="#181b24", width=264, height=40,
                                text_color="#e2e8f0", corner_radius=10,
                                border_width=1, border_color="#262b3a",
                                placeholder_text_color="#3d4456",
                                font=ctk.CTkFont(size=12))
    email_entry.place(x=28, y=198)

    # ── Password ──────────────────────────────────────────────────────────────
    ctk.CTkLabel(glass, text="Password", fg_color="transparent",
                  text_color="#8892a8", font=ctk.CTkFont(size=11)).place(x=28, y=248)

    password_entry = ctk.CTkEntry(glass, placeholder_text="••••••••", show="*",
                                   fg_color="#181b24", width=264, height=40,
                                   text_color="#e2e8f0", corner_radius=10,
                                   border_width=1, border_color="#262b3a",
                                   placeholder_text_color="#3d4456",
                                   font=ctk.CTkFont(size=12))
    password_entry.place(x=28, y=268)

    # ── Status label ──────────────────────────────────────────────────────────
    status_label = ctk.CTkLabel(glass, text="", fg_color="transparent",
                                 width=264, height=18, text_color="#8b9cf7",
                                 font=ctk.CTkFont(size=11))
    status_label.place(x=28, y=316)

    # ── Submit button ─────────────────────────────────────────────────────────
    def submit_signup():
        uname = username_entry.get().strip()
        em    = email_entry.get().strip()
        pw    = password_entry.get().strip()
        if not uname or not em or not pw:
            status_label.configure(text="Please fill out all fields", text_color="#f87171")
            return
        status_label.configure(text="Creating account...", text_color="#8b9cf7")
        signup_submit.configure(state="disabled")
        threading.Thread(target=signup_request,
                          args=(uname, em, pw, signup_window, status_label, signup_submit),
                          daemon=True).start()

    signup_submit = ctk.CTkButton(glass, text="Create Account", command=submit_signup,
                                   fg_color="#5b6ef7", hover_color="#4a5ce0",
                                   width=264, height=42, text_color="#ffffff",
                                   corner_radius=10,
                                   font=ctk.CTkFont(size=13, weight="bold"))
    signup_submit.place(x=28, y=338)

# =============================================================================
# Paywall
# =============================================================================
# ── Subscription check ────────────────────────────────────────────────────────
def check_subscription(user_data, force=False):
    if _sub_cache["checked"] and not force:
        return _sub_cache["subscribed"]
    if not user_data:
        return False
    user_id = user_data.get("id") or user_data.get("username", "")
    try:
        resp = requests.get(
            f"{API_BASE}/subscription/status",
            params={"user_id": user_id},
            timeout=6
        )
        if resp.ok:
            result = resp.json().get("subscribed", False)
            _sub_cache["subscribed"] = result
            _sub_cache["checked"]    = True
            return result
    except Exception:
        pass
    return False
 
 
# ── Paywall overlay ───────────────────────────────────────────────────────────
def show_paywall_overlay(parent, user_data, switch_tab_fn):
    """
    Draws a full lock overlay over the parent frame.
    switch_tab_fn is the switch_tab function from render_main_application.
    """
    overlay = ctk.CTkFrame(parent, fg_color="#0d1017", width=880, height=680, corner_radius=0)
    overlay.place(x=0, y=0)
 
    # Gradient-like top accent bar
    ctk.CTkFrame(overlay, fg_color="#3b5bdb", width=880, height=3, corner_radius=0).place(x=0, y=0)
 
    # Lock icon
    ctk.CTkLabel(overlay, text="🔒", fg_color="transparent",
                  font=ctk.CTkFont(size=56)).place(relx=0.5, rely=0.28, anchor="center")
 
    # Title
    ctk.CTkLabel(overlay, text="EverNest Pro Required",
                  fg_color="transparent", text_color="#FFFFFF",
                  font=ctk.CTkFont(size=26, weight="bold")).place(relx=0.5, rely=0.40, anchor="center")
 
    # Subtitle
    ctk.CTkLabel(overlay,
                  text="This feature is included with EverNest Pro.\nUnlock bank connections, budgeting, and family sharing for $9.99/mo.",
                  fg_color="transparent", text_color="#9A9A9A",
                  font=ctk.CTkFont(size=14),
                  justify="center").place(relx=0.5, rely=0.50, anchor="center")
 
    # Features list
    features_frame = ctk.CTkFrame(overlay, fg_color="#141820",
                                   width=440, height=110, corner_radius=12)
    features_frame.place(relx=0.5, rely=0.64, anchor="center")
 
    features = [
        "✓  Connect bank accounts via Plaid",
        "✓  Smart budget tracker with auto-fill",
        "✓  Family sharing & shared calendar",
    ]
    for i, feat in enumerate(features):
        ctk.CTkLabel(features_frame, text=feat, fg_color="transparent",
                      text_color="#4CFF7A", font=ctk.CTkFont(size=13)
                      ).place(x=20, y=12 + i * 30)
 
    # Upgrade button
    ctk.CTkButton(overlay, text="⚡  Upgrade to Pro — $9.99/mo",
                   width=260, height=44,
                   fg_color="#3b5bdb", hover_color="#2b4bc8",
                   text_color="#f0f1f3", corner_radius=10,
                   font=ctk.CTkFont(size=14, weight="bold"),
                   command=lambda: switch_tab_fn("Subscribe")
                   ).place(relx=0.5, rely=0.80, anchor="center")
 
    # Already subscribed — re-check from API and remove overlay if valid
    def refresh_subscription():
        def _do():
            subscribed = check_subscription(user_data, force=True)
            if subscribed:
                parent.after(0, lambda: overlay.destroy())
            else:
                parent.after(0, lambda: [
                    refresh_btn.configure(
                        text="No active subscription found — try again",
                        text_color="#ff6b6b", state="normal")
                ])
        refresh_btn.configure(text="Checking…", state="disabled")
        threading.Thread(target=_do, daemon=True).start()

    refresh_btn = ctk.CTkButton(overlay, text="I already subscribed — Refresh",
                   width=260, height=28,
                   fg_color="transparent", hover_color="#13161b",
                   text_color="#5B8DEF", corner_radius=8,
                   font=ctk.CTkFont(size=12),
                   command=refresh_subscription)
    refresh_btn.place(relx=0.5, rely=0.88, anchor="center")
 
 
# ── Subscribe tab ─────────────────────────────────────────────────────────────
def render_subscribe_tab(parent, user_data=None):
    user_id = ""
    if user_data:
        user_id = str(user_data.get("id") or user_data.get("username", ""))
 
    # Check if already subscribed
    status_var  = ctk.StringVar(value="Checking subscription status…")
    already_sub = {"value": False}
 
    # Header
    ctk.CTkLabel(parent, text="EverNest Pro", fg_color="transparent",
                  text_color="#7B93DB", font=ctk.CTkFont(size=28, weight="bold")).place(x=30, y=24)
    ctk.CTkLabel(parent, text="Unlock the full EverNest experience for your household.",
                  fg_color="transparent", text_color="#5B8DEF",
                  font=ctk.CTkFont(size=13)).place(x=30, y=62)
 
    # Status label
    status_lbl = ctk.CTkLabel(parent, textvariable=status_var,
                               fg_color="transparent", text_color="#4CFF7A",
                               font=ctk.CTkFont(size=12))
    status_lbl.place(x=30, y=90)
 
    # ── Pricing card ──────────────────────────────────────────────────────────
    card = ctk.CTkFrame(parent, fg_color="#13161b", width=400, height=460, corner_radius=16)
    card.place(x=30, y=118)
 
    # Top accent
    ctk.CTkFrame(card, fg_color="#3b5bdb", width=400, height=3, corner_radius=0).place(x=0, y=0)
 
    ctk.CTkLabel(card, text="Pro", fg_color="transparent",
                  text_color="#7B93DB", font=ctk.CTkFont(size=20, weight="bold")).place(x=24, y=20)
 
    ctk.CTkLabel(card, text="$9.99", fg_color="transparent",
                  text_color="#f0f1f3", font=ctk.CTkFont(size=48, weight="bold")).place(x=24, y=48)
    ctk.CTkLabel(card, text="per month — cancel any time",
                  fg_color="transparent", text_color="#9A9A9A",
                  font=ctk.CTkFont(size=12)).place(x=24, y=102)
 
    features = [
        "✓  Connect bank accounts via Plaid",
        "✓  Smart budget tracker with auto-fill",
        "✓  Family sharing (invite your household)",
        "✓  Shared calendar color coded per person",
        "✓  Combined family financial overview",
        "✓  All future Pro features included",
    ]
    for i, feat in enumerate(features):
        ctk.CTkLabel(card, text=feat, fg_color="transparent",
                      text_color="#d1d5db" if "✓" in feat else "#9A9A9A",
                      font=ctk.CTkFont(size=13)).place(x=24, y=132 + i * 30)
 
    # Stripe button
    stripe_btn = ctk.CTkButton(card, text="💳  Pay with Card (Stripe)",
                                width=350, height=42,
                                fg_color="#3b5bdb", hover_color="#2b4bc8",
                                text_color="#f0f1f3", corner_radius=8,
                                font=ctk.CTkFont(size=13, weight="bold"),
                                command=lambda: subscribe_stripe())
    stripe_btn.place(x=24, y=326)
 
    # PayPal button (removed for now)
    #paypal_btn = ctk.CTkButton(card, text="🅿  Pay with PayPal",
    #                            width=350, height=42,
    #                           fg_color="#FFD700", hover_color="#e6c200",
    #                            text_color="#f0f1f3", corner_radius=8,
    #                            font=ctk.CTkFont(size=13, weight="bold"),
    #                            command=lambda: subscribe_paypal())
    #paypal_btn.place(x=24, y=378)
 
    # ── What's included panel ─────────────────────────────────────────────────
    info_card = ctk.CTkFrame(parent, fg_color="#13161b", width=380, height=460, corner_radius=16)
    info_card.place(x=454, y=118)
 
    ctk.CTkLabel(info_card, text="What happens after you subscribe",
                  fg_color="transparent", text_color="#7B93DB",
                  font=ctk.CTkFont(size=15, weight="bold")).place(x=24, y=20)
 
    steps = [
        ("1", "Click a payment button", "You'll be taken to a secure Stripe or PayPal checkout page in your browser."),
        ("2", "Complete payment", "Enter your card or log into PayPal. Takes about 30 seconds."),
        ("3", "Come back to EverNest", "Close the browser tab and click 'Already subscribed' or reopen the app."),
        ("4", "Full access unlocked", "Budget, Financial, and My Family tabs are immediately available."),
    ]
    y_off = 60
    for num, title, desc in steps:
        ctk.CTkFrame(info_card, fg_color="#3b5bdb", width=24, height=24, corner_radius=12
                      ).place(x=24, y=y_off)
        ctk.CTkLabel(info_card, text=num, fg_color="transparent",
                      text_color="#f0f1f3", font=ctk.CTkFont(size=11, weight="bold")
                      ).place(x=30, y=y_off + 2)
        ctk.CTkLabel(info_card, text=title, fg_color="transparent",
                      text_color="#f0f1f3", font=ctk.CTkFont(size=13, weight="bold")
                      ).place(x=60, y=y_off)
        ctk.CTkLabel(info_card, text=desc, fg_color="transparent",
                      text_color="#9A9A9A", font=ctk.CTkFont(size=11),
                      wraplength=270, anchor="w", justify="left"
                      ).place(x=60, y=y_off + 20)
        y_off += 80
 
    ctk.CTkLabel(info_card,
                  text="🔒  Payments are processed securely.\nEverNest never stores your card details.",
                  fg_color="transparent", text_color="#4b5060",
                  font=ctk.CTkFont(size=11), justify="left").place(x=24, y=y_off + 10)
 
    # ── Actions ───────────────────────────────────────────────────────────────
    def subscribe_stripe():
        stripe_btn.configure(state="disabled", text="Opening checkout…")
        def _do():
            try:
                resp = requests.post(
                    f"{API_BASE}/subscription/stripe/create-session",
                    json={"user_id": user_id}, timeout=15
                )
                data = resp.json()
                url  = data.get("url")
                if url:
                    webbrowser.open(url)
                    parent.after(0, lambda: status_var.set(
                        "✓  Checkout opened in browser. Complete payment then reopen EverNest."))
                else:
                    parent.after(0, lambda: status_var.set(
                        f"Error: {data.get('error', 'Could not create session')}"))
            except Exception as e:
                parent.after(0, lambda: status_var.set(f"Error: {e}"))
            finally:
                parent.after(0, lambda: stripe_btn.configure(
                    state="normal", text="💳  Pay with Card (Stripe)"))
        threading.Thread(target=_do, daemon=True).start()
 
    #def subscribe_paypal():
    #    paypal_btn.configure(state="disabled", text="Opening PayPal…")
    #    def _do():
    #        try:
    #            resp = requests.post(
    #                f"{API_BASE}/subscription/paypal/create",
    #                json={"user_id": user_id}, timeout=15
    #            )
    #            data = resp.json()
    #            url  = data.get("url")
    #            if url:
    #                webbrowser.open(url)
    #                parent.after(0, lambda: status_var.set(
    #                    "✓  PayPal opened in browser. Complete payment then reopen EverNest."))
    #            else:
    #                parent.after(0, lambda: status_var.set(
    #                    f"Error: {data.get('error', 'Could not create PayPal subscription')}"))
    #        except Exception as e:
    #            parent.after(0, lambda: status_var.set(f"Error: {e}"))
    #        finally:
    #            parent.after(0, lambda: paypal_btn.configure(
    #                state="normal", text="🅿  Pay with PayPal"))
    #    threading.Thread(target=_do, daemon=True).start()
 
    def check_status():
        def _do():
            try:
                resp = requests.get(
                    f"{API_BASE}/subscription/status",
                    params={"user_id": user_id}, timeout=6
                )
                if resp.ok:
                    data = resp.json()
                    if data.get("subscribed"):
                        end  = data.get("subscription_end", "")[:10]
                        parent.after(0, lambda: status_var.set(
                            f"✓  Active — renews {end}"))
                        parent.after(0, lambda: status_lbl.configure(text_color="#4CFF7A"))
                        already_sub["value"] = True
                        # Update global cache so other tabs unlock immediately
                        _sub_cache["subscribed"] = True
                        _sub_cache["checked"]    = True
                    else:
                        parent.after(0, lambda: status_var.set(
                            "No active subscription."))
                        parent.after(0, lambda: status_lbl.configure(text_color="#9A9A9A"))
            except Exception:
                parent.after(0, lambda: status_var.set("Could not check status."))
        threading.Thread(target=_do, daemon=True).start()
 
    check_status()

# =============================================================================
#  LOGIN SCREEN
# =============================================================================

main.configure(fg_color="#0c0e14")

# Background image — left side
background = ImageLabel(
    master=main,
    image_path=resource_path(os.path.join("assets", "images", "background.jpg")),
    text="", compound=ctk.TOP, mode="cover"
)
background.configure(fg_color="transparent", width=530, height=460,
                     text_color="#fff", font=ctk.CTkFont())
background.place(x=-5, y=-2)

# ── Frosted glass login panel (right side) ────────────────────────────────────
# Outer glow border — faint bright edge to simulate glass reflection
glass_border = ctk.CTkFrame(master=main, fg_color="#1e2235", width=276, height=442,
                             corner_radius=0)
glass_border.place(x=524, y=-1)

# Main glass panel
login_panel = ctk.CTkFrame(master=main, fg_color="#12141c", width=272, height=440,
                            corner_radius=0)
login_panel.place(x=526, y=0)

# Inner frost accent — very subtle lighter strip at top
frost_accent = ctk.CTkFrame(login_panel, fg_color="#1a1e2e", width=272, height=2,
                              corner_radius=0)
frost_accent.place(x=0, y=0)

# ── Branding ──────────────────────────────────────────────────────────────────
ctk.CTkLabel(login_panel, text="EverNest", fg_color="transparent",
             text_color="#8b9cf7", font=ctk.CTkFont(size=13, weight="bold")
             ).place(x=40, y=36)

# ── Title ─────────────────────────────────────────────────────────────────────
label = ctk.CTkLabel(login_panel, text="Welcome back", fg_color="transparent",
                     width=200, height=30, text_color="#e2e8f0",
                     font=ctk.CTkFont(size=22, weight="bold"))
label.place(x=34, y=68)

subtitle = ctk.CTkLabel(login_panel, text="Sign in to your account",
                         fg_color="transparent", width=200, height=18,
                         text_color="#4a5568", font=ctk.CTkFont(size=12))
subtitle.place(x=37, y=98)

# ── Email field ───────────────────────────────────────────────────────────────
ctk.CTkLabel(login_panel, text="Email", fg_color="transparent",
             text_color="#8892a8", font=ctk.CTkFont(size=11)
             ).place(x=40, y=138)

entry = ctk.CTkEntry(login_panel, placeholder_text="you@example.com",
                      fg_color="#181b24", width=196, height=42,
                      text_color="#e2e8f0", corner_radius=10,
                      border_width=1, border_color="#262b3a",
                      placeholder_text_color="#3d4456",
                      font=ctk.CTkFont(size=12))
entry.place(x=38, y=160)

# ── Password field ────────────────────────────────────────────────────────────
ctk.CTkLabel(login_panel, text="Password", fg_color="transparent",
             text_color="#8892a8", font=ctk.CTkFont(size=11)
             ).place(x=40, y=214)

entry1 = ctk.CTkEntry(login_panel, placeholder_text="••••••••", show="*",
                       fg_color="#181b24", width=196, height=42,
                       text_color="#e2e8f0", corner_radius=10,
                       border_width=1, border_color="#262b3a",
                       placeholder_text_color="#3d4456",
                       font=ctk.CTkFont(size=12))
entry1.place(x=38, y=236)

entry.bind("<Return>", lambda e: submit_login())
entry1.bind("<Return>", lambda e: submit_login())

# ── Sign In button ────────────────────────────────────────────────────────────
submit = ctk.CTkButton(login_panel, text="Sign In", command=submit_login,
                        fg_color="#5b6ef7", hover_color="#4a5ce0",
                        width=196, height=42, text_color="#ffffff",
                        corner_radius=10,
                        font=ctk.CTkFont(size=13, weight="bold"))
submit.place(x=38, y=300)

# ── Divider ───────────────────────────────────────────────────────────────────
ctk.CTkFrame(login_panel, fg_color="#1e2235", width=196, height=1,
             corner_radius=0).place(x=38, y=356)

# ── Create Account button ────────────────────────────────────────────────────
sign_up = ctk.CTkButton(login_panel, text="Don't have an account? Sign Up",
                         command=open_signup,
                         fg_color="transparent", hover_color="#181d2a",
                         width=196, height=32, text_color="#6875a0",
                         corner_radius=8,
                         font=ctk.CTkFont(size=11))
sign_up.place(x=38, y=368)

# ── Footer ────────────────────────────────────────────────────────────────────
label1 = ctk.CTkLabel(login_panel, text="© N0Ctrl Studios 2026",
                       fg_color="transparent", width=196, height=20,
                       text_color="#252a36", font=ctk.CTkFont(size=9))
label1.place(x=38, y=412)

main.mainloop()
