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
def get_available_rooms():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def create_new_room(new_room_name, username):
    room = new_room_name.strip().upper()
    if not room:
        return gr.update(), "⚠️ Escribe un nombre para la sala.", ""
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT room_code FROM rooms WHERE room_code = ?', (room,))
    if cursor.fetchone():
        conn.close()
        return gr.update(), f"ℹ️ La sala **{room}** ya existe.", ""
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    cursor.execute('INSERT INTO rooms (room_code, created_by, created_at) VALUES (?, ?, ?)', (room, username, now))
    conn.commit()
    conn.close()
    
    rooms = get_available_rooms()
    return gr.update(choices=rooms, value=room), f"✅ Sala **{room}** creada exitosamente.", ""

# 3. Autenticación
def auth_user(username_raw, password_raw, action_type):
    username = username_raw.strip()
    password = password_raw.strip()
    
    if not username or not password:
        return "⚠️ Ingresa usuario y contraseña.", gr.update(), gr.update(), "", gr.update()
    
    conn = get_db()
    cursor = conn.cursor()
    
    if action_type == "login":
        cursor.execute('SELECT password FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row or row[0] != password:
            conn.close()
            return "❌ Credenciales incorrectas.", gr.update(), gr.update(), "", gr.update()
    else:
        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return "⚠️ El usuario ya existe.", gr.update(), gr.update(), "", gr.update()
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        cursor.execute('INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)', (username, password, now))
        conn.commit()
    
    conn.close()
    rooms = get_available_rooms()
    
    return (
        f"✅ Conectado como **{username}**",
        gr.update(visible=False),
        gr.update(visible=True),
        username,
        gr.update(choices=rooms, value=rooms[0] if rooms else None)
    )

# 4. Sincronización en Vivo y Orientación de Burbujas
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
    
    chat_info_md = f"### 👤 Usuario: `{username}` &nbsp;&nbsp;|&nbsp;&nbsp; 🚪 Sala: `{room_code}`"
    presence_info_md = "**Miembros en sala:** " + (" • ".join(users_status) if users_status else "*Sin participantes*")
    
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

def logout_user():
    return (
        "",
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        "",
        "",
        [],
        "",
        ""
    )

# 5. Tema Visual
friendly_theme = gr.themes.Soft(
    primary_hue="teal",
    secondary_hue="slate",
    neutral_hue="slate",
    spacing_size="sm",
    radius_size="lg"
)

# 6. Interfaz Gráfica
with gr.Blocks(title="Chat en Red", theme=friendly_theme) as demo:
    session_user = gr.State("")
    session_room = gr.State("")

    with gr.Group() as login_view:
        gr.Markdown("# 🌿 Bienvenido a la Comunidad de Chat")
        gr.Markdown("Inicia sesión o regístrate para acceder a tus salas.")
        with gr.Row():
            user_input = gr.Textbox(label="Nombre de Usuario", placeholder="Ej: Francis", scale=2)
            pass_input = gr.Textbox(label="Contraseña", placeholder="••••••••", type="password", scale=2)
        
        with gr.Row():
            btn_login = gr.Button("🔑 Ingresar al Chat", variant="primary")
            btn_register = gr.Button("✨ Crear Cuenta Nueva", variant="secondary")
        
        login_status = gr.Markdown("")

    with gr.Group(visible=False) as app_view:
        with gr.Row():
            with gr.Column(scale=1):
                with gr.Accordion("⚙️ Opciones Generales y Salas", open=False):
                    gr.Markdown("#### 📂 Seleccionar Sala")
                    room_dropdown = gr.Dropdown(label="Salas Disponibles", choices=[], interactive=True)
                    btn_enter_room = gr.Button("🚀 Entrar a la Sala", variant="primary")
                    
                    gr.Markdown("---")
                    gr.Markdown("#### ➕ Crear Nueva Sala")
                    new_room_input = gr.Textbox(label="Nombre de Sala", placeholder="Ej: SALA_DE_ESTUDIO")
                    btn_create_room = gr.Button("Crear y Registrar")
                    room_feedback = gr.Markdown("")
                    
                    gr.Markdown("---")
                    btn_clear = gr.Button("🗑️ Vaciar Mensajes de esta Sala")
                    btn_logout = gr.Button("🚪 Salir de la Cuenta", variant="stop")

            with gr.Column(scale=3):
                no_room_banner = gr.Markdown(
                    "### 👈 Abre las **Opciones Generales** a la izquierda para elegir o crear una sala.",
                    visible=True
                )
                
                with gr.Group(visible=False) as chat_area:
                    chat_info_header = gr.Markdown("### 👤 Usuario: ... | 🚪 Sala: ...")
                    presence_info_bar = gr.Markdown("**Miembros:** ...")
                    chatbot = gr.Chatbot(label="Conversación en Vivo", height=460)
                    
                    with gr.Row():
                        msg_input = gr.Textbox(show_label=False, placeholder="Escribe un mensaje...", scale=5)
                        btn_send = gr.Button("Enviar", variant="primary", scale=1)

    refresh_timer = gr.Timer(value=2)
    refresh_timer.tick(
        sync_room_live,
        inputs=[session_room, session_user],
        outputs=[chat_info_header, presence_info_bar, chatbot]
    )

    btn_login.click(
        lambda u, p: auth_user(u, p, "login"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, app_view, session_user, room_dropdown]
    )
    btn_register.click(
        lambda u, p: auth_user(u, p, "register"),
        inputs=[user_input, pass_input],
        outputs=[login_status, login_view, app_view, session_user, room_dropdown]
    )
    btn_logout.click(
        logout_user,
        outputs=[login_status, login_view, app_view, chat_area, session_user, session_room, chatbot, chat_info_header, presence_info_bar]
    )

    btn_create_room.click(
        create_new_room,
        inputs=[new_room_input, session_user],
        outputs=[room_dropdown, room_feedback, new_room_input]
    )
    
    def on_enter_room_action(selected_room, user):
        if not selected_room:
            return "", gr.update(), gr.update(), "", "", []
        head_md, pres_md, hist = sync_room_live(selected_room, user)
        return (
            selected_room,
            gr.update(visible=False),
            gr.update(visible=True),
            head_md,
            pres_md,
            hist
        )

    btn_enter_room.click(
        on_enter_room_action,
        inputs=[room_dropdown, session_user],
        outputs=[session_room, no_room_banner, chat_area, chat_info_header, presence_info_bar, chatbot]
    )

    btn_send.click(send_msg, inputs=[session_room, session_user, msg_input], outputs=[chatbot, msg_input])
    msg_input.submit(send_msg, inputs=[session_room, session_user, msg_input], outputs=[chatbot, msg_input])
    btn_clear.click(clear_room_history, inputs=[session_room, session_user], outputs=[chat_info_header, presence_info_bar, chatbot])

# Configuración de puerto y host para Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
