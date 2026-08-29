import os
import sqlite3
from datetime import datetime, timezone, timedelta
import time
import json
import gradio as gr

DB_PATH = "chat_network.db"
BACKUP_JSON = "chat_backup.json"

# Zona horaria local (Ecuador / GMT-5)
TZ_LOCAL = timezone(timedelta(hours=-5))

def get_current_time_str():
    return datetime.now(TZ_LOCAL).strftime("%H:%M")

def get_current_datetime_str():
    return datetime.now(TZ_LOCAL).strftime("%Y-%m-%d %H:%M")

# 1. Base de datos SQLite
def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_active REAL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presence (
            username TEXT PRIMARY KEY,
            last_seen REAL NOT NULL
        )
    ''')
    
    # Crear Administrador por defecto
    now_str = get_current_datetime_str()
    cursor.execute('''
        INSERT OR IGNORE INTO users (username, password, role, created_at, last_active)
        VALUES ('administrador', 'admin', 'Admin', ?, 0)
    ''', (now_str,))
    conn.commit()
    conn.close()
    
    restore_from_backup()

def save_to_backup():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT username, password, role, created_at FROM users")
        users = [{"u": r[0], "p": r[1], "r": r[2], "c": r[3]} for r in cursor.fetchall()]
        
        cursor.execute("SELECT author, text, timestamp FROM messages")
        msgs = [{"author": r[0], "text": r[1], "ts": r[2]} for r in cursor.fetchall()]
        conn.close()
        
        data = {"users": users, "messages": msgs}
        with open(BACKUP_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

def restore_from_backup():
    if not os.path.exists(BACKUP_JSON):
        return
    try:
        with open(BACKUP_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        conn = get_db()
        cursor = conn.cursor()
        for u in data.get("users", []):
            cursor.execute("INSERT OR IGNORE INTO users (username, password, role, created_at, last_active) VALUES (?, ?, ?, ?, 0)",
                           (u["u"], u["p"], u["r"], u["c"]))
        for m in data.get("messages", []):
            cursor.execute("INSERT OR IGNORE INTO messages (author, text, timestamp) VALUES (?, ?, ?)",
                           (m["author"], m["text"], m["ts"]))
        conn.commit()
        conn.close()
    except Exception:
        pass

init_db()

# 2. Consultas y Gestión de Usuarios
def get_all_users_admin():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT username, password, role, created_at, last_active FROM users ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    
    current_time = time.time()
    table_data = []
    for u, p, r, cat, la in rows:
        is_online = (current_time - (la or 0)) < 15
        status = "🟢 En línea" if is_online else "⚪ Desconectado"
        table_data.append([u, p, r, status, cat])
    return table_data

def get_deletable_users_list():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username != 'administrador' ORDER BY username ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# 3. Autenticación Directa
def auth_user(username_raw, password_raw, action_type):
    username = username_raw.strip()
    password = password_raw.strip()
    
    if not username or not password:
        return (
            "⚠️ Por favor ingresa tu usuario y contraseña.",
            gr.update(), gr.update(), gr.update(), "", "Usuario", gr.update(), []
        )
    
    conn = get_db()
    cursor = conn.cursor()
    now_ts = time.time()
    
    if action_type == "login":
        cursor.execute('SELECT password, role FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return "❌ El usuario no existe. Regístrate primero.", gr.update(), gr.update(), gr.update(), "", "Usuario", gr.update(), []
        
        db_pass, db_role = row[0], row[1]
        if db_pass != password:
            conn.close()
            return "❌ Contraseña incorrecta.", gr.update(), gr.update(), gr.update(), "", "Usuario", gr.update(), []
        
        cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (now_ts, username))
        conn.commit()
        conn.close()
        user_role = db_role
    else:
        if username.lower() == "administrador":
            conn.close()
            return "⚠️ La cuenta administrador ya existe. Inicia sesión directamente.", gr.update(), gr.update(), gr.update(), "", "Usuario", gr.update(), []
        
        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return "⚠️ El nombre de usuario ya está registrado.", gr.update(), gr.update(), gr.update(), "", "Usuario", gr.update(), []
        
        now_str = get_current_datetime_str()
        cursor.execute('INSERT INTO users (username, password, role, created_at, last_active) VALUES (?, ?, ?, ?, ?)',
                       (username, password, 'Usuario', now_str, now_ts))
        conn.commit()
        conn.close()
        save_to_backup()
        user_role = "Usuario"
    
    admin_table = get_all_users_admin() if user_role == "Admin" else []
    admin_del_choices = get_deletable_users_list() if user_role == "Admin" else []
    
    return (
        "",
        gr.update(visible=False),
        gr.update(visible=True),
        gr.update(visible=(user_role == "Admin")),
        username,
        user_role,
        gr.Dropdown(choices=admin_del_choices, value=admin_del_choices[0] if admin_del_choices else None),
        admin_table
    )

# 4. Sincronización en Vivo y Chat
def sync_chat_live(username, role):
    if not username:
        return gr.update(), gr.update(), gr.update(), gr.update()
    
    current_time = time.time()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (current_time, username))
    
    cursor.execute('''
        INSERT INTO presence (username, last_seen) VALUES (?, ?)
        ON CONFLICT(username) DO UPDATE SET last_seen = excluded.last_seen
    ''', (username, current_time))
    conn.commit()
    
    cursor.execute('SELECT username, last_seen FROM presence')
    user_rows = cursor.fetchall()
    
    cursor.execute('SELECT author, text, timestamp FROM messages ORDER BY id ASC')
    msg_rows = cursor.fetchall()
    conn.close()
    
    users_status = []
    for u, last_seen in user_rows:
        is_online = (current_time - last_seen) < 15
        icon = "🟢" if is_online else "⚪"
        users_status.append(f"{icon} **{u}**")
        
    chat_header = f"### 👤 Conectado como: **{username}**"
    presence_text = "**Usuarios:** " + (" • ".join(users_status) if users_status else "*Sin actividad*")
    
    chat_history = []
    for author, text, timestamp in msg_rows:
        if author == username:
            content = f"{text}\n\n<div class='msg-time msg-time-right'>{timestamp} ✓✓</div>"
            chat_history.append({"role": "user", "content": content})
        else:
            content = f"<span class='msg-author'>{author}</span>\n\n{text}\n\n<div class='msg-time'>{timestamp}</div>"
            chat_history.append({"role": "assistant", "content": content})
            
    admin_table = get_all_users_admin() if role == "Admin" else []
    
    return chat_header, presence_text, chat_history, admin_table

def send_msg(username, text, role):
    if not username or not text.strip():
        _, _, chat_hist, _ = sync_chat_live(username, role)
        return chat_hist, ""
    
    timestamp = get_current_time_str()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO messages (author, text, timestamp) VALUES (?, ?, ?)',
        (username, text.strip(), timestamp)
    )
    conn.commit()
    conn.close()
    save_to_backup()
    
    _, _, chat_hist, _ = sync_chat_live(username, role)
    return chat_hist, ""

def clear_all_history(username, role):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM messages')
    conn.commit()
    conn.close()
    save_to_backup()
    return sync_chat_live(username, role)[:3]

def delete_own_account(username):
    if not username or username == "administrador":
        return "⚠️ La cuenta administrador no puede eliminarse.", gr.update(), gr.update()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM presence WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    save_to_backup()
    
    return "🗑️ Cuenta eliminada.", gr.update(visible=True), gr.update(visible=False)

def admin_delete_user(target_user):
    if not target_user:
        return "⚠️ Selecciona un usuario.", gr.update(), []
    if target_user == "administrador":
        return "❌ No puedes eliminar la cuenta de administrador principal.", gr.update(), get_all_users_admin()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (target_user,))
    cursor.execute("DELETE FROM presence WHERE username = ?", (target_user,))
    conn.commit()
    conn.close()
    save_to_backup()
    
    updated_users = get_deletable_users_list()
    updated_table = get_all_users_admin()
    return f"✅ Usuario **{target_user}** eliminado.", gr.Dropdown(choices=updated_users, value=updated_users[0] if updated_users else None), updated_table

# 5. Estilos Visuales
custom_css = """
.auth-box { max-width: 460px; margin: 50px auto; padding: 28px; border-radius: 16px; background: #ffffff; box-shadow: 0 10px 30px rgba(0,0,0,0.06); }
.admin-card { padding: 16px; border-radius: 12px; background: #f8fafc; border: 1px solid #cbd5e1; margin-bottom: 16px; }
.danger-box { margin-top: 20px; padding: 14px; border: 1px dashed #ef4444; border-radius: 12px; background: #fef2f2; }
.msg-author { font-weight: 700; color: #0f766e; display: block; margin-bottom: 2px; font-size: 0.95rem; }
.msg-time { font-size: 0.72rem; color: #64748b; text-align: right; margin-top: 4px; line-height: 1; }
.msg-time-right { color: #047857; }
"""

theme = gr.themes.Soft(primary_hue="teal", secondary_hue="slate", neutral_hue="slate", radius_size="lg")

# 6. Interfaz Directa
with gr.Blocks(title="Chat en Red", theme=theme, css=custom_css) as demo:
    session_user = gr.State("")
    session_role = gr.State("Usuario")

    # VISTA 1: INICIO DE SESIÓN / REGISTRO
    with gr.Group(elem_classes=["auth-box"]) as login_view:
        gr.Markdown("# 💬 Ingreso al Chat")
        gr.Markdown("Escribe tu nombre de usuario y contraseña para entrar al instante.")
        
        user_input = gr.Textbox(label="Usuario", placeholder="Ingresa tu nombre")
        pass_input = gr.Textbox(label="Contraseña", placeholder="Tu contraseña", type="password")
        
        with gr.Row():
            btn_login = gr.Button("🔑 Entrar", variant="primary", scale=1)
            btn_register = gr.Button("✨ Registrarse", variant="secondary", scale=1)
            
        login_status = gr.Markdown("")

    # VISTA 2: CHAT DIRECTO Y PANEL
    with gr.Group(visible=False) as chat_view:
        with gr.Row():
            chat_info_header = gr.Markdown("### 👤 Conectado como: ...", scale=3)
            btn_logout = gr.Button("🔒 Cerrar Sesión", variant="stop", scale=1)

        # Panel de Administrador
        with gr.Group(visible=False, elem_classes=["admin-card"]) as admin_panel:
            gr.Markdown("### 🛡️ Panel de Control (Admin)")
            admin_users_table = gr.Dataframe(
                headers=["Usuario", "Contraseña", "Rol", "Estado", "Fecha Registro"],
                interactive=False,
                label="Registro de Usuarios"
            )
            with gr.Row():
                admin_select_user_del = gr.Dropdown(label="Eliminar usuario del sistema", choices=[], scale=3)
                btn_admin_del_user = gr.Button("🗑️ Eliminar Usuario", variant="stop", scale=2)
            admin_del_status = gr.Markdown("")

        presence_bar = gr.Markdown("**Usuarios:** ...")
        chatbot = gr.Chatbot(label="Chat Grupal", height=500, sanitize_html=False)
        
        with gr.Row():
            msg_input = gr.Textbox(show_label=False, placeholder="Escribe un mensaje aquí...", scale=5)
            btn_send = gr.Button("Enviar", variant="primary", scale=1)
            
        with gr.Row():
            btn_clear = gr.Button("🗑️ Limpiar Mensajes", variant="secondary", scale=1)

        with gr.Group(elem_classes=["danger-box"]):
            with gr.Row():
                gr.Markdown("¿Deseas eliminar permanentemente tu cuenta?")
                btn_delete_own = gr.Button("Dar de baja mi cuenta", variant="stop", scale=1)

    # Actualización en vivo (cada 2 segundos)
    refresh_timer = gr.Timer(value=2)
    refresh_timer.tick(
        sync_chat_live,
        inputs=[session_user, session_role],
        outputs=[chat_info_header, presence_bar, chatbot, admin_users_table]
    )

    # Handlers Login / Registro
    btn_login.click(
        lambda u, p: auth_user(u, p, "login"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, chat_view, admin_panel, session_user, session_role, admin_select_user_del, admin_users_table]
    )
    btn_register.click(
        lambda u, p: auth_user(u, p, "register"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, chat_view, admin_panel, session_user, session_role, admin_select_user_del, admin_users_table]
    )

    # Logout
    def logout_action(user):
        if user:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM presence WHERE username = ?", (user,))
            conn.commit()
            conn.close()
        return "", gr.update(visible=True), gr.update(visible=False), "", "Usuario", []
    
    btn_logout.click(
        logout_action,
        inputs=[session_user],
        outputs=[login_status, login_view, chat_view, session_user, session_role, chatbot]
    )

    # Envío de mensajes
    btn_send.click(send_msg, inputs=[session_user, msg_input, session_role], outputs=[chatbot, msg_input])
    msg_input.submit(send_msg, inputs=[session_user, msg_input, session_role], outputs=[chatbot, msg_input])

    # Limpiar chat
    btn_clear.click(clear_all_history, inputs=[session_user, session_role], outputs=[chat_info_header, presence_bar, chatbot])

    # Eliminación de cuentas
    btn_delete_own.click(
        delete_own_account,
        inputs=[session_user],
        outputs=[login_status, login_view, chat_view]
    )
    btn_admin_del_user.click(
        admin_delete_user,
        inputs=[admin_select_user_del],
        outputs=[admin_del_status, admin_select_user_del, admin_users_table]
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
