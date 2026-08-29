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
        CREATE TABLE IF NOT EXISTS rooms (
            room_code TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (room_code) REFERENCES rooms(room_code)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presence (
            room_code TEXT NOT NULL,
            username TEXT NOT NULL,
            last_seen REAL NOT NULL,
            PRIMARY KEY (room_code, username)
        )
    ''')
    
    now_str = get_current_datetime_str()
    cursor.execute('''
        INSERT OR IGNORE INTO users (username, password, role, created_at, last_active)
        VALUES ('administrador', 'admin', 'Admin', ?, 0)
    ''', (now_str,))
    
    cursor.execute("INSERT OR IGNORE INTO rooms (room_code, created_by, created_at) VALUES ('GENERAL', 'Sistema', ?)", (now_str,))
    conn.commit()
    conn.close()
    
    restore_from_backup()

def save_to_backup():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT username, password, role, created_at FROM users")
        users = [{"u": r[0], "p": r[1], "r": r[2], "c": r[3]} for r in cursor.fetchall()]
        
        cursor.execute("SELECT room_code, created_by, created_at FROM rooms")
        rooms = [{"code": r[0], "by": r[1], "c": r[2]} for r in cursor.fetchall()]
        
        cursor.execute("SELECT room_code, author, text, timestamp FROM messages")
        msgs = [{"room": r[0], "author": r[1], "text": r[2], "ts": r[3]} for r in cursor.fetchall()]
        conn.close()
        
        data = {"users": users, "rooms": rooms, "messages": msgs}
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
        for r in data.get("rooms", []):
            cursor.execute("INSERT OR IGNORE INTO rooms (room_code, created_by, created_at) VALUES (?, ?, ?)",
                           (r["code"], r["by"], r["c"]))
        for m in data.get("messages", []):
            cursor.execute("INSERT OR IGNORE INTO messages (room_code, author, text, timestamp) VALUES (?, ?, ?, ?)",
                           (m["room"], m["author"], m["text"], m["ts"]))
        conn.commit()
        conn.close()
    except Exception:
        pass

init_db()

# 2. Consultas
def get_all_room_names():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows] if rows else ["GENERAL"]

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

def create_new_room(new_room_name, username):
    room = new_room_name.strip().upper().replace(" ", "_")
    rooms = get_all_room_names()
    if not room:
        return "⚠️ Escribe un nombre para la sala.", gr.Radio(choices=rooms, value=rooms[0] if rooms else None)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms WHERE room_code = ?', (room,))
    if cursor.fetchone():
        conn.close()
        return f"ℹ️ La sala **{room}** ya existe.", gr.Radio(choices=rooms, value=room)
    
    now = get_current_datetime_str()
    cursor.execute('INSERT INTO rooms (room_code, created_by, created_at) VALUES (?, ?, ?)', (room, username or 'Sistema', now))
    conn.commit()
    conn.close()
    save_to_backup()
    
    updated_rooms = get_all_room_names()
    return f"✅ Sala **{room}** creada exitosamente.", gr.Radio(choices=updated_rooms, value=room)

# 3. Autenticación
def auth_user(username_raw, password_raw, action_type):
    username = username_raw.strip()
    password = password_raw.strip()
    
    if not username or not password:
        return (
            "⚠️ Por favor ingresa tu usuario y contraseña.",
            gr.update(), gr.update(), gr.update(), "", "", gr.update(), [], gr.update()
        )
    
    conn = get_db()
    cursor = conn.cursor()
    now_ts = time.time()
    
    if action_type == "login":
        cursor.execute('SELECT password, role FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return "❌ El usuario no existe. Regístrate primero.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), [], gr.update()
        
        db_pass, db_role = row[0], row[1]
        if db_pass != password:
            conn.close()
            return "❌ Contraseña incorrecta.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), [], gr.update()
        
        cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (now_ts, username))
        conn.commit()
        conn.close()
        user_role = db_role
    else:
        if username.lower() == "administrador":
            conn.close()
            return "⚠️ La cuenta administrador ya existe. Inicia sesión directamente.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), [], gr.update()
        
        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return "⚠️ El nombre de usuario ya está registrado.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), [], gr.update()
        
        now_str = get_current_datetime_str()
        cursor.execute('INSERT INTO users (username, password, role, created_at, last_active) VALUES (?, ?, ?, ?, ?)',
                       (username, password, 'Usuario', now_str, now_ts))
        conn.commit()
        conn.close()
        save_to_backup()
        user_role = "Usuario"
    
    rooms = get_all_room_names()
    admin_table = get_all_users_admin() if user_role == "Admin" else []
    admin_del_choices = get_deletable_users_list() if user_role == "Admin" else []
    default_room = rooms[0] if rooms else "GENERAL"
    
    return (
        f"✅ Bienvenido, **{username}**",
        gr.update(visible=False),
        gr.update(visible=True),
        gr.update(visible=(user_role == "Admin")),
        username,
        user_role,
        gr.Radio(choices=rooms, value=default_room),
        admin_table,
        gr.Dropdown(choices=admin_del_choices, value=admin_del_choices[0] if admin_del_choices else None)
    )

# 4. Sincronización y Chat
def sync_room_live(room_code, username, role):
    if not username or not room_code:
        return gr.update(), gr.update(), gr.update(), gr.update()
    
    current_time = time.time()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (current_time, username))
    
    cursor.execute('''
        INSERT INTO presence (room_code, username, last_seen) VALUES (?, ?, ?)
        ON CONFLICT(room_code, username) DO UPDATE SET last_seen = excluded.last_seen
    ''', (room_code, username, current_time))
    conn.commit()
    
    cursor.execute('SELECT username, last_seen FROM presence WHERE room_code = ?', (room_code,))
    user_rows = cursor.fetchall()
    
    cursor.execute('SELECT author, text, timestamp FROM messages WHERE room_code = ? ORDER BY id ASC', (room_code,))
    msg_rows = cursor.fetchall()
    conn.close()
    
    users_status = []
    for u, last_seen in user_rows:
        is_online = (current_time - last_seen) < 15
        icon = "🟢" if is_online else "⚪"
        users_status.append(f"{icon} **{u}**")
        
    chat_header = f"### 👤 Usuario: **{username}** &nbsp;&nbsp;|&nbsp;&nbsp; 🚪 Sala: **{room_code}**"
    presence_text = "**Participantes:** " + (" • ".join(users_status) if users_status else "*Sin miembros activos*")
    
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

def send_msg(room_code, username, text, role):
    if not room_code or not text.strip():
        _, _, chat_hist, _ = sync_room_live(room_code, username, role)
        return chat_hist, ""
    
    timestamp = get_current_time_str()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO messages (room_code, author, text, timestamp) VALUES (?, ?, ?, ?)',
        (room_code, username, text.strip(), timestamp)
    )
    conn.commit()
    conn.close()
    save_to_backup()
    
    _, _, chat_hist, _ = sync_room_live(room_code, username, role)
    return chat_hist, ""

def leave_current_room(room_code, username, role):
    if room_code and username:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM presence WHERE room_code = ? AND username = ?', (room_code, username))
        conn.commit()
        conn.close()
    
    rooms = get_all_room_names()
    admin_t = get_all_users_admin() if role == "Admin" else []
    admin_del_choices = get_deletable_users_list() if role == "Admin" else []
    return "", gr.update(visible=True), gr.update(visible=False), gr.Radio(choices=rooms, value=rooms[0] if rooms else None), admin_t, [], gr.Dropdown(choices=admin_del_choices)

def clear_room_history(room_code, username, role):
    if room_code:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE room_code = ?', (room_code,))
        conn.commit()
        conn.close()
        save_to_backup()
    return sync_room_live(room_code, username, role)[:3]

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
    
    return "🗑️ Tu cuenta ha sido eliminada correctamente.", gr.update(visible=True), gr.update(visible=False)

def admin_delete_user(target_user):
    if not target_user:
        return "⚠️ Selecciona un usuario para eliminar.", gr.update(), []
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
    return f"✅ Cuenta **{target_user}** eliminada definitivamente.", gr.Dropdown(choices=updated_users, value=updated_users[0] if updated_users else None), updated_table

# 5. Estilos
custom_css = """
.auth-box { max-width: 480px; margin: 40px auto; padding: 28px; border-radius: 16px; background: #ffffff; box-shadow: 0 10px 30px rgba(0,0,0,0.06); }
.room-selector-card { padding: 20px; border: 2px solid #0d9488; border-radius: 14px; background: #f0fdfa; }
.admin-card { padding: 16px; border-radius: 12px; background: #f8fafc; border: 1px solid #cbd5e1; margin-bottom: 16px; }
.danger-box { margin-top: 24px; padding: 16px; border: 1px dashed #ef4444; border-radius: 12px; background: #fef2f2; }
.msg-author { font-weight: 700; color: #0f766e; display: block; margin-bottom: 2px; font-size: 0.95rem; }
.msg-time { font-size: 0.72rem; color: #64748b; text-align: right; margin-top: 4px; line-height: 1; }
.msg-time-right { color: #047857; }
"""

theme = gr.themes.Soft(primary_hue="teal", secondary_hue="slate", neutral_hue="slate", radius_size="lg")

# 6. Interfaz
with gr.Blocks(title="Chat en Red", theme=theme, css=custom_css) as demo:
    session_user = gr.State("")
    session_role = gr.State("Usuario")
    session_room = gr.State("")

    # VISTA 1: LOGIN / REGISTRO
    with gr.Group(elem_classes=["auth-box"]) as login_view:
        gr.Markdown("# 🌿 Acceso al Chat")
        gr.Markdown("Ingresa con tu usuario y contraseña, o regístrate para crear una cuenta.")
        
        user_input = gr.Textbox(label="Usuario", placeholder="Tu nombre de usuario")
        pass_input = gr.Textbox(label="Contraseña", placeholder="Tu contraseña", type="password")
        
        with gr.Row():
            btn_login = gr.Button("🔑 Iniciar Sesión", variant="primary", scale=1)
            btn_register = gr.Button("✨ Registrarse", variant="secondary", scale=1)
            
        login_status = gr.Markdown("")

    # VISTA 2: HUB
    with gr.Group(visible=False) as hub_view:
        with gr.Row():
            hub_title = gr.Markdown("## 🏢 Centro de Salas")
            btn_logout_hub = gr.Button("🔒 Cerrar Sesión", variant="stop", scale=1)

        with gr.Group(visible=False, elem_classes=["admin-card"]) as admin_panel:
            gr.Markdown("### 🛡️ Panel de Supervisión (Administrador)")
            admin_users_table = gr.Dataframe(
                headers=["Usuario", "Contraseña", "Rol", "Estado", "Fecha Registro"],
                interactive=False,
                label="Registro de Usuarios y Contraseñas"
            )
            with gr.Row():
                admin_select_user_del = gr.Dropdown(label="Seleccionar usuario a borrar", choices=[], scale=3)
                btn_admin_del_user = gr.Button("🗑️ Eliminar Usuario", variant="stop", scale=2)
            admin_del_status = gr.Markdown("")

        with gr.Row():
            with gr.Column(scale=2, elem_classes=["room-selector-card"]):
                gr.Markdown("### 📂 Selecciona una Sala:")
                room_picker = gr.Radio(label="Salas disponibles", choices=["GENERAL"], value="GENERAL")
                btn_enter = gr.Button("🚀 Entrar a la Sala", variant="primary", size="lg")
            
            with gr.Column(scale=1):
                gr.Markdown("### ➕ Crear Nueva Sala")
                new_room_txt = gr.Textbox(label="Nombre de la sala", placeholder="Ej: PROYECTO_FINAL")
                btn_create = gr.Button("Crear Sala")
                create_status = gr.Markdown("")

        with gr.Group(elem_classes=["danger-box"]):
            gr.Markdown("#### ⚠️ Opciones de Cuenta")
            with gr.Row():
                gr.Markdown("Si deseas dar de baja tu usuario y borrar tus accesos:")
                btn_delete_own = gr.Button("🗑️ Eliminar Mi Cuenta", variant="stop", scale=1)

    # VISTA 3: SALA EN VIVO
    with gr.Group(visible=False) as chat_view:
        with gr.Row():
            chat_info_header = gr.Markdown("### 👤 Usuario: ... | 🚪 Sala: ...", scale=3)
            btn_leave_room = gr.Button("🚪 Salir de la Sala", variant="secondary", scale=1)
            btn_logout_chat = gr.Button("🔒 Cerrar Sesión", variant="stop", scale=1)
            
        presence_bar = gr.Markdown("**Participantes:** ...")
        chatbot = gr.Chatbot(label="Historial de Conversación", height=480, sanitize_html=False)
        
        with gr.Row():
            msg_input = gr.Textbox(show_label=False, placeholder="Escribe un mensaje...", scale=5)
            btn_send = gr.Button("Enviar", variant="primary", scale=1)
            
        btn_clear = gr.Button("🗑️ Vaciar Historial de esta Sala", variant="stop")

    # Sincronizador Automático
    refresh_timer = gr.Timer(value=2)
    refresh_timer.tick(
        sync_room_live,
        inputs=[session_room, session_user, session_role],
        outputs=[chat_info_header, presence_bar, chatbot, admin_users_table]
    )

    # Handlers Login / Registro
    btn_login.click(
        lambda u, p: auth_user(u, p, "login"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, hub_view, admin_panel, session_user, session_role, room_picker, admin_users_table, admin_select_user_del]
    )
    btn_register.click(
        lambda u, p: auth_user(u, p, "register"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, hub_view, admin_panel, session_user, session_role, room_picker, admin_users_table, admin_select_user_del]
    )

    # Logout
    def logout_action():
        return "", gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), "", "Usuario", "", []
    
    btn_logout_hub.click(
        logout_action,
        outputs=[login_status, login_view, hub_view, chat_view, session_user, session_role, session_room, chatbot]
    )
    btn_logout_chat.click(
        logout_action,
        outputs=[login_status, login_view, hub_view, chat_view, session_user, session_role, session_room, chatbot]
    )

    # Salir de Sala
    btn_leave_room.click(
        leave_current_room,
        inputs=[session_room, session_user, session_role],
        outputs=[session_room, hub_view, chat_view, room_picker, admin_users_table, chatbot, admin_select_user_del]
    )

    # Crear Sala
    def handle_create(name, user):
        msg, picker_update = create_new_room(name, user)
        return msg, "", picker_update
    
    btn_create.click(
        handle_create,
        inputs=[new_room_txt, session_user],
        outputs=[create_status, new_room_txt, room_picker]
    )

    # Entrar a Sala
    def enter_room_action(room, user, role):
        target_room = room or "GENERAL"
        h, p, m, _ = sync_room_live(target_room, user, role)
        return target_room, gr.update(visible=False), gr.update(visible=True), h, p, m
    
    btn_enter.click(
        enter_room_action,
        inputs=[room_picker, session_user, session_role],
        outputs=[session_room, hub_view, chat_view, chat_info_header, presence_bar, chatbot]
    )

    # Eliminaciones
    btn_delete_own.click(
        delete_own_account,
        inputs=[session_user],
        outputs=[login_status, login_view, hub_view]
    )

    btn_admin_del_user.click(
        admin_delete_user,
        inputs=[admin_select_user_del],
        outputs=[admin_del_status, admin_select_user_del, admin_users_table]
    )

    # Mensajería
    btn_send.click(send_msg, inputs=[session_room, session_user, msg_input, session_role], outputs=[chatbot, msg_input])
    msg_input.submit(send_msg, inputs=[session_room, session_user, msg_input, session_role], outputs=[chatbot, msg_input])
    btn_clear.click(clear_room_history, inputs=[session_room, session_user, session_role], outputs=[chat_info_header, presence_bar, chatbot])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
