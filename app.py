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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
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
    cursor.execute("INSERT OR IGNORE INTO rooms (room_code, created_by, created_at) VALUES ('GENERAL', 'Sistema', datetime('now'))")
    conn.commit()
    conn.close()

init_db()

# 2. Consultas y Gestión
def get_available_rooms_info():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code, created_by FROM rooms ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def create_new_room(new_room_name, username):
    room = new_room_name.strip().upper()
    if not room:
        return "⚠️ Escribe un nombre para la sala.", ""
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms WHERE room_code = ?', (room,))
    if cursor.fetchone():
        conn.close()
        return f"ℹ️ La sala **{room}** ya existe.", ""
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    cursor.execute('INSERT INTO rooms (room_code, created_by, created_at) VALUES (?, ?, ?)', (room, username, now))
    conn.commit()
    conn.close()
    
    return f"✅ Sala **{room}** creada exitosamente.", ""

# 3. Autenticación
def auth_user(username_raw, password_raw, action_type):
    username = username_raw.strip()
    password = password_raw.strip()
    
    if not username or not password:
        return "⚠️ Ingresa usuario y contraseña.", gr.update(), gr.update(), ""
    
    conn = get_db()
    cursor = conn.cursor()
    
    if action_type == "login":
        cursor.execute('SELECT password FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row or row[0] != password:
            conn.close()
            return "❌ Credenciales incorrectas.", gr.update(), gr.update(), ""
    else:
        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return "⚠️ El usuario ya existe.", gr.update(), gr.update(), ""
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        cursor.execute('INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)', (username, password, now))
        conn.commit()
    
    conn.close()
    
    return (
        f"✅ Conectado como **{username}**",
        gr.update(visible=False),  # Ocultar login
        gr.update(visible=True),   # Mostrar hub de salas
        username
    )

# 4. Sincronización en Vivo
def sync_room_live(room_code, username):
    if not room_code or not username:
        return "", "", []
    
    current_time = time.time()
    conn = get_db()
    cursor = conn.cursor()
    
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
        is_online = (current_time - last_seen) < 10
        icon = "🟢" if is_online else "⚪"
        state = "En línea" if is_online else "Ausente"
        users_status.append(f"{icon} **{u}** ({state})")
    
    chat_info_md = f"### 👤 Mi Usuario: `{username}` &nbsp;&nbsp;|&nbsp;&nbsp; 🚪 Sala: `{room_code}`"
    presence_info_md = "**Miembros:** " + (" • ".join(users_status) if users_status else "*Sin participantes*")
    
    chat_history = []
    for author, text, timestamp in msg_rows:
        if author == username:
            chat_history.append({
                "role": "user",
                "content": f"{text}\n\n*({timestamp})*"
            })
        else:
            chat_history.append({
                "role": "assistant",
                "content": f"**{author}**:\n{text}\n\n*({timestamp})*"
            })
        
    return chat_info_md, presence_info_md, chat_history

def send_msg(room_code, username, text):
    if not room_code or not text.strip():
        _, _, chat_hist = sync_room_live(room_code, username)
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
    
    _, _, chat_hist = sync_room_live(room_code, username)
    return chat_hist, ""

def clear_room_history(room_code, username):
    if room_code:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE room_code = ?', (room_code,))
        conn.commit()
        conn.close()
    return sync_room_live(room_code, username)

# 5. Tema Visual Personalizado
custom_css = """
.room-card {
    border: 2px solid #0d9488 !important;
    border-radius: 12px !important;
    padding: 16px !important;
    height: 120px !important;
    font-size: 1.1rem !important;
    font-weight: 700 !important;
    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
}
.room-card:hover {
    transform: translateY(-4px) !important;
    box-shadow: 0 8px 16px rgba(13, 148, 136, 0.25) !important;
}
"""

friendly_theme = gr.themes.Soft(
    primary_hue="teal",
    secondary_hue="slate",
    neutral_hue="slate",
    spacing_size="sm",
    radius_size="lg"
)

# 6. Interfaz Gráfica
with gr.Blocks(title="Chat Teams Style", theme=friendly_theme, css=custom_css) as demo:
    session_user = gr.State("")
    session_room = gr.State("")

    # VISTA 1: INICIO DE SESIÓN / REGISTRO
    with gr.Group() as login_view:
        gr.Markdown("# 🌿 Bienvenido a la Plataforma de Chat")
        gr.Markdown("Inicia sesión o regístrate para acceder a tus salas de trabajo.")
        with gr.Row():
            user_input = gr.Textbox(label="Nombre de Usuario", placeholder="Ej: Francis", scale=2)
            pass_input = gr.Textbox(label="Contraseña", placeholder="••••••••", type="password", scale=2)
        
        with gr.Row():
            btn_login = gr.Button("🔑 Ingresar al Sistema", variant="primary")
            btn_register = gr.Button("✨ Crear Cuenta Nueva", variant="secondary")
        
        login_status = gr.Markdown("")

    # VISTA 2: PANEL DE CONTROL Y CUADRÍCULA DE SALAS (ESTILO TEAMS)
    with gr.Group(visible=False) as hub_view:
        with gr.Row():
            hub_user_header = gr.Markdown("## 🏢 Panel de Salas")
            btn_logout = gr.Button("🚪 Cerrar Sesión", scale=1, variant="stop")

        with gr.Accordion("➕ Crear Nueva Sala / Equipo", open=False):
            with gr.Row():
                new_room_input = gr.Textbox(label="Nombre de la nueva sala", placeholder="Ej: INGENIERIA_SOFTWARE", scale=4)
                btn_create_room = gr.Button("Crear y Publicar", variant="primary", scale=1)
            room_feedback = gr.Markdown("")

        gr.Markdown("### 📌 Selecciona una Sala para entrar a la conversación:")

        # Generador de tarjetas / cuadrícula
        @gr.render(inputs=[hub_view])
        def render_room_cards(_):
            rooms = get_available_rooms_info()
            with gr.Row():
                for room_code, creator in rooms:
                    with gr.Column(scale=1, min_width=240):
                        label_text = f"📂 {room_code}\n(Creador: {creator})"
                        card_btn = gr.Button(label_text, elem_classes=["room-card"], variant="secondary")
                        
                        def enter_room(r=room_code):
                            return r, gr.update(visible=False), gr.update(visible=True)
                        
                        card_btn.click(
                            enter_room,
                            outputs=[session_room, hub_view, chat_view]
                        )

    # VISTA 3: SALA DE CHAT ACTIVA
    with gr.Group(visible=False) as chat_view:
        with gr.Row():
            chat_info_header = gr.Markdown("### 👤 Usuario: ... | 🚪 Sala: ...")
            btn_back_to_hub = gr.Button("⬅️ Volver a las Salas", variant="secondary", scale=1)
        
        presence_info_bar = gr.Markdown("**Miembros:** ...")
        chatbot = gr.Chatbot(label="Historial de Conversación", height=450)
        
        with gr.Row():
            msg_input = gr.Textbox(show_label=False, placeholder="Escribe un mensaje...", scale=5)
            btn_send = gr.Button("Enviar", variant="primary", scale=1)
            
        with gr.Row():
            btn_clear = gr.Button("🗑️ Vaciar Historial de esta Sala", variant="stop")

    # Sincronización Automática
    refresh_timer = gr.Timer(value=2)
    refresh_timer.tick(
        sync_room_live,
        inputs=[session_room, session_user],
        outputs=[chat_info_header, presence_info_bar, chatbot]
    )

    # Handlers de Autenticación
    btn_login.click(
        lambda u, p: auth_user(u, p, "login"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, hub_view, session_user]
    )
    btn_register.click(
        lambda u, p: auth_user(u, p, "register"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, hub_view, session_user]
    )
    
    def logout_action():
        return (
            "",
            gr.update(visible=True),   # Mostrar login
            gr.update(visible=False),  # Ocultar hub
            gr.update(visible=False),  # Ocultar chat
            "",
            "",
            []
        )
    btn_logout.click(
        logout_action,
        outputs=[login_status, login_view, hub_view, chat_view, session_user, session_room, chatbot]
    )

    # Handlers de Salas y Navegación
    def handle_create_room(name, user):
        msg, _ = create_new_room(name, user)
        return msg, "", gr.update()
    
    btn_create_room.click(
        handle_create_room,
        inputs=[new_room_input, session_user],
        outputs=[room_feedback, new_room_input, hub_view]
    )

    def go_back_to_hub():
        return "", gr.update(visible=True), gr.update(visible=False), []

    btn_back_to_hub.click(
        go_back_to_hub,
        outputs=[session_room, hub_view, chat_view, chatbot]
    )

    # Handlers de Mensajería
    btn_send.click(send_msg, inputs=[session_room, session_user, msg_input], outputs=[chatbot, msg_input])
    msg_input.submit(send_msg, inputs=[session_room, session_user, msg_input], outputs=[chatbot, msg_input])
    btn_clear.click(clear_room_history, inputs=[session_room, session_user], outputs=[chat_info_header, presence_info_bar, chatbot])

# Configuración de puerto para Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
