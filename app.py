import os
import sqlite3
from datetime import datetime
import time
import gradio as gr

DB_PATH = "chat_network.db"

# 1. Base de datos SQLite
def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Tabla de usuarios con rol
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_active REAL DEFAULT 0
        )
    ''')
    # Tabla de salas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            room_code TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    # Tabla de mensajes
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
    # Tabla de presencia en salas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presence (
            room_code TEXT NOT NULL,
            username TEXT NOT NULL,
            last_seen REAL NOT NULL,
            PRIMARY KEY (room_code, username)
        )
    ''')
    # Crear sala por defecto
    cursor.execute("INSERT OR IGNORE INTO rooms (room_code, created_by, created_at) VALUES ('GENERAL', 'Sistema', datetime('now'))")
    conn.commit()
    conn.close()

init_db()

# 2. Consultas y Gestión
def get_all_rooms():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code, created_by, created_at FROM rooms ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

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

def create_new_room(new_room_name, username):
    room = new_room_name.strip().upper().replace(" ", "_")
    if not room:
        return "⚠️ Escribe un nombre para la sala.", gr.update()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms WHERE room_code = ?', (room,))
    if cursor.fetchone():
        conn.close()
        return f"ℹ️ La sala **{room}** ya existe.", gr.update()
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    cursor.execute('INSERT INTO rooms (room_code, created_by, created_at) VALUES (?, ?, ?)', (room, username, now))
    conn.commit()
    conn.close()
    
    rooms = [r[0] for r in get_all_rooms()]
    return f"✅ Sala **{room}** creada exitosamente.", gr.update(choices=rooms, value=room)

# 3. Autenticación y Registro
def auth_user(username_raw, password_raw, role_choice, action_type):
    username = username_raw.strip()
    password = password_raw.strip()
    
    if not username or not password:
        return (
            "⚠️ Por favor llena todos los campos de usuario y contraseña.",
            gr.update(), gr.update(), gr.update(), "", "", gr.update(), []
        )
    
    conn = get_db()
    cursor = conn.cursor()
    now_ts = time.time()
    
    if action_type == "login":
        cursor.execute('SELECT password, role FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return "❌ El usuario no existe. Regístrate primero.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), []
        
        db_pass, db_role = row[0], row[1]
        if db_pass != password:
            conn.close()
            return "❌ Contraseña incorrecta.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), []
        
        cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (now_ts, username))
        conn.commit()
        conn.close()
        user_role = db_role
    else:
        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return "⚠️ El nombre de usuario ya está en uso. Elige otro o inicia sesión.", gr.update(), gr.update(), gr.update(), "", "", gr.update(), []
        
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        cursor.execute('INSERT INTO users (username, password, role, created_at, last_active) VALUES (?, ?, ?, ?, ?)',
                       (username, password, role_choice, now_str, now_ts))
        conn.commit()
        conn.close()
        user_role = role_choice
    
    rooms = [r[0] for r in get_all_rooms()]
    admin_table = get_all_users_admin() if user_role == "Admin" else []
    
    return (
        f"✅ Bienvenido, **{username}** ({user_role})",
        gr.update(visible=False), # Ocultar Login
        gr.update(visible=True),  # Mostrar Hub de salas
        gr.update(visible=(user_role == "Admin")), # Mostrar Panel Admin si corresponde
        username,
        user_role,
        gr.update(choices=rooms, value=rooms[0] if rooms else "GENERAL"),
        admin_table
    )

# 4. Sincronización en Vivo y Chat
def sync_room_live(room_code, username, role):
    if not username:
        return "", "", [], []
    
    current_time = time.time()
    conn = get_db()
    cursor = conn.cursor()
    
    # Actualizar latido del usuario
    cursor.execute('UPDATE users SET last_active = ? WHERE username = ?', (current_time, username))
    
    if room_code:
        cursor.execute('''
            INSERT INTO presence (room_code, username, last_seen) VALUES (?, ?, ?)
            ON CONFLICT(room_code, username) DO UPDATE SET last_seen = excluded.last_seen
        ''', (room_code, username, current_time))
        conn.commit()
        
        cursor.execute('SELECT username, last_seen FROM presence WHERE room_code = ?', (room_code,))
        user_rows = cursor.fetchall()
        
        cursor.execute('SELECT author, text, timestamp FROM messages WHERE room_code = ? ORDER BY id ASC', (room_code,))
        msg_rows = cursor.fetchall()
    else:
        user_rows, msg_rows = [], []
        
    conn.close()
    
    # Participantes de la sala
    users_status = []
    for u, last_seen in user_rows:
        is_online = (current_time - last_seen) < 15
        icon = "🟢" if is_online else "⚪"
        users_status.append(f"{icon} **{u}**")
        
    chat_header = f"### 👤 Conectado: **{username}** `[{role}]` &nbsp;&nbsp;|&nbsp;&nbsp; 🚪 Sala: **{room_code}**"
    presence_text = "**En esta sala:** " + (" • ".join(users_status) if users_status else "*Sin miembros activos*")
    
    chat_history = []
    for author, text, timestamp in msg_rows:
        if author == username:
            chat_history.append({"role": "user", "content": f"{text}\n\n*({timestamp})*"})
        else:
            chat_history.append({"role": "assistant", "content": f"**{author}**:\n{text}\n\n*({timestamp})*"})
            
    admin_table = get_all_users_admin() if role == "Admin" else []
    
    return chat_header, presence_text, chat_history, admin_table

def send_msg(room_code, username, text, role):
    if not room_code or not text.strip():
        _, _, chat_hist, _ = sync_room_live(room_code, username, role)
        return chat_hist, ""
    
    timestamp = datetime.now().strftime('%H:%M - %d/%m/%Y')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO messages (room_code, author, text, timestamp) VALUES (?, ?, ?, ?)',
        (room_code, username, text.strip(), timestamp)
    )
    conn.commit()
    conn.close()
    
    _, _, chat_hist, _ = sync_room_live(room_code, username, role)
    return chat_hist, ""

def clear_room_history(room_code, username, role):
    if room_code:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE room_code = ?', (room_code,))
        conn.commit()
        conn.close()
    return sync_room_live(room_code, username, role)[:3]

# 5. Estilos Visuales Amigables
custom_css = """
.auth-box {
    max-width: 500px;
    margin: 40px auto;
    padding: 24px;
    border-radius: 16px;
    background: #ffffff;
    box-shadow: 0 10px 25px rgba(0,0,0,0.05);
}
.room-selector-card {
    padding: 16px;
    border: 2px solid #0d9488;
    border-radius: 14px;
    background: #f0fdfa;
    margin-bottom: 12px;
}
"""

theme = gr.themes.Soft(
    primary_hue="teal",
    secondary_hue="slate",
    neutral_hue="slate",
    radius_size="lg"
)

# 6. Interfaz Principal
with gr.Blocks(title="Chat Multi-Salas con Roles", theme=theme, css=custom_css) as demo:
    session_user = gr.State("")
    session_role = gr.State("Usuario")
    session_room = gr.State("")

    # VISTA 1: INICIO DE SESIÓN Y REGISTRO
    with gr.Group(elem_classes=["auth-box"]) as login_view:
        gr.Markdown("# 🌿 Acceso al Sistema de Chat")
        gr.Markdown("Ingresa tus credenciales o regístrate para comenzar.")
        
        user_input = gr.Textbox(label="Usuario", placeholder="Tu nombre de usuario")
        pass_input = gr.Textbox(label="Contraseña", placeholder="Tu contraseña", type="password")
        role_input = gr.Radio(label="Tipo de Cuenta / Rol", choices=["Usuario", "Admin"], value="Usuario")
        
        with gr.Row():
            btn_login = gr.Button("🔑 Iniciar Sesión", variant="primary", scale=1)
            btn_register = gr.Button("✨ Registrar Cuenta", variant="secondary", scale=1)
            
        login_status = gr.Markdown("")

    # VISTA 2: PANEL DE CONTROL DE SALAS (HUB PRINCIPAL)
    with gr.Group(visible=False) as hub_view:
        with gr.Row():
            user_hub_title = gr.Markdown("## 🏢 Centro de Salas y Equipos")
            btn_logout = gr.Button("🚪 Cerrar Sesión", variant="stop", scale=1)

        # Panel de Administrador (Solo visible para Admin)
        with gr.Group(visible=False) as admin_panel:
            gr.Markdown("### 🛡️ Panel de Supervisión (Acceso Exclusivo de Administrador)")
            admin_users_table = gr.Dataframe(
                headers=["Usuario", "Contraseña", "Rol", "Estado", "Fecha Registro"],
                interactive=False,
                label="Usuarios Registrados y Estado de Conexión"
            )
            gr.Markdown("---")

        with gr.Row():
            # Columna: Seleccionar e ingresar
            with gr.Column(scale=2, elem_classes=["room-selector-card"]):
                gr.Markdown("### 📂 Selecciona una Sala Existente")
                room_picker = gr.Radio(
                    label="Salas disponibles en la base de datos",
                    choices=[],
                    value=None
                )
                btn_enter = gr.Button("🚀 Entrar a esta Sala", variant="primary", size="lg")
            
            # Columna: Crear nueva sala
            with gr.Column(scale=1):
                gr.Markdown("### ➕ Crear Nueva Sala")
                new_room_txt = gr.Textbox(label="Nombre del Grupo o Sala", placeholder="Ej: PROYECTO_FINAL")
                btn_create = gr.Button("Crear y Registrar Sala")
                create_status = gr.Markdown("")

    # VISTA 3: SALA DE CHAT EN VIVO
    with gr.Group(visible=False) as chat_view:
        with gr.Row():
            chat_info_header = gr.Markdown("### 👤 Conectado: ... | 🚪 Sala: ...")
            btn_back_to_hub = gr.Button("⬅️ Volver al Panel de Salas", variant="secondary", scale=1)
            
        presence_bar = gr.Markdown("**En esta sala:** ...")
        chatbot = gr.Chatbot(label="Mensajes de la Sala", height=460)
        
        with gr.Row():
            msg_input = gr.Textbox(show_label=False, placeholder="Escribe tu mensaje...", scale=5)
            btn_send = gr.Button("Enviar", variant="primary", scale=1)
            
        btn_clear = gr.Button("🗑️ Vaciar Historial de esta Sala", variant="stop")

    # Sincronizador Automático (cada 2 segundos)
    refresh_timer = gr.Timer(value=2)
    refresh_timer.tick(
        sync_room_live,
        inputs=[session_room, session_user, session_role],
        outputs=[chat_info_header, presence_bar, chatbot, admin_users_table]
    )

    # Handlers de Autenticación
    btn_login.click(
        lambda u, p, r: auth_user(u, p, r, "login"),
        inputs=[user_input, pass_input, role_input],
        outputs=[login_status, login_view, hub_view, admin_panel, session_user, session_role, room_picker, admin_users_table]
    )
    btn_register.click(
        lambda u, p, r: auth_user(u, p, r, "register"),
        inputs=[user_input, pass_input, role_input],
        outputs=[login_status, login_view, hub_view, admin_panel, session_user, session_role, room_picker, admin_users_table]
    )

    # Handler Cerrar Sesión
    def logout_action():
        return "", gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), "", "Usuario", "", []
    
    btn_logout.click(
        logout_action,
        outputs=[login_status, login_view, hub_view, chat_view, session_user, session_role, session_room, chatbot]
    )

    # Handler Crear Sala
    def handle_create(name, user):
        msg, update_picker = create_new_room(name, user)
        return msg, "", update_picker
    
    btn_create.click(
        handle_create,
        inputs=[new_room_txt, session_user],
        outputs=[create_status, new_room_txt, room_picker]
    )

    # Handler Entrar a Sala
    def enter_room_action(room, user, role):
        if not room:
            return "", gr.update(), gr.update(), "", "", []
        h, p, m, _ = sync_room_live(room, user, role)
        return room, gr.update(visible=False), gr.update(visible=True), h, p, m
    
    btn_enter.click(
        enter_room_action,
        inputs=[room_picker, session_user, session_role],
        outputs=[session_room, hub_view, chat_view, chat_info_header, presence_bar, chatbot]
    )

    # Handler Volver al Hub de Salas
    def go_back(user, role):
        rooms = [r[0] for r in get_all_rooms()]
        admin_t = get_all_users_admin() if role == "Admin" else []
        return "", gr.update(visible=True), gr.update(visible=False), gr.update(choices=rooms), admin_t
    
    btn_back_to_hub.click(
        go_back,
        inputs=[session_user, session_role],
        outputs=[session_room, hub_view, chat_view, room_picker, admin_users_table]
    )

    # Handlers de Envío y Limpieza
    btn_send.click(send_msg, inputs=[session_room, session_user, msg_input, session_role], outputs=[chatbot, msg_input])
    msg_input.submit(send_msg, inputs=[session_room, session_user, msg_input, session_role], outputs=[chatbot, msg_input])
    btn_clear.click(clear_room_history, inputs=[session_room, session_user, session_role], outputs=[chat_info_header, presence_bar, chatbot])

# Puerto para Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
