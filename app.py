import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import re
import io

st.set_page_config(page_title="Feria Educación, Arte, Ciencia y Tecnología", page_icon="🔬", layout="wide")

# ---------------------------------------------------------
# 1. INICIALIZACIÓN Y CACHÉ OPTIMIZADO
# ---------------------------------------------------------
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        key_dict = dict(st.secrets["textkey"])
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()

DICCIONARIO_ESCUELAS = {
    "eet 1": "Escuela Técnica N° 1",
    "e.e.t. n°1": "Escuela Técnica N° 1",
    "otto krause": "Escuela Técnica N° 1 Otto Krause",
    "cfp 7": "Centro de Formación Profesional N° 7"
}

def normalizar_escuela(nombre_raw):
    if pd.isna(nombre_raw) or not str(nombre_raw).strip():
        return "Sin Especificar"
    clean = str(nombre_raw).strip().lower()
    return DICCIONARIO_ESCUELAS.get(clean, str(nombre_raw).strip())

def agrupar_nivel(nivel_raw):
    if pd.isna(nivel_raw):
        return "OTRO"
    n = str(nivel_raw).upper()
    if "INICIAL" in n:
        return "INICIAL"
    elif "PRIMARIO" in n or "PRIMARIA" in n:
        return "PRIMARIA"
    elif "SECUNDARIO" in n or "SECUNDARIA" in n or "SUPERIOR" in n or "PROFESORADO" in n or "DOCENTE" in n or "CENTES" in n:
        return "SECUNDARIA / SUPERIOR"
    return "OTRO"

def normalizar_lista(val):
    if isinstance(val, list):
        return [str(x) for x in val if x]
    elif pd.notna(val) and val:
        return [str(val)]
    return []

def limpiar_clave_firestore(texto):
    clean = str(texto).replace("\n", " ").replace("\r", " ")
    clean = re.sub(r'[.~*/\[\]#$]', '_', clean)
    return clean.strip()[:150]

# ---------------------------------------------------------
# 2. FUNCIONES DE CONSULTA Y GESTIÓN
# ---------------------------------------------------------
@st.cache_data(ttl=120)
def obtener_proyectos_cached():
    docs = db.collection("proyectos").stream()
    data = []
    for doc in docs:
        d = doc.to_dict() | {"id_doc": doc.id}
        d["evaluadores_asignados"] = normalizar_lista(d.get("evaluadores_asignados"))
        data.append(d)
        
    df = pd.DataFrame(data)
    if not df.empty:
        if "nivel_agrupado" not in df.columns:
            df["nivel_agrupado"] = df["nivel_raw"].apply(agrupar_nivel) if "nivel_raw" in df.columns else "Sin Agrupar"
        if "escuela_estandarizada" not in df.columns:
            df["escuela_estandarizada"] = "Sin Especificar"
        if "anio_edicion" not in df.columns:
            df["anio_edicion"] = "2026"
    return df

@st.cache_data(ttl=120)
def obtener_usuarios_cached():
    docs = db.collection("Usuarios").stream()
    data = [doc.to_dict() | {"id_doc": doc.id} for doc in docs]
    df = pd.DataFrame(data)
    
    if not df.empty:
        if "estado_cuenta" not in df.columns:
            df["estado_cuenta"] = "Activo"
        else:
            df["estado_cuenta"] = df["estado_cuenta"].fillna("Activo")
            
        if "rol" not in df.columns:
            df["rol"] = "evaluador"
            
        if "nombre_completo" not in df.columns:
            df["nombre_completo"] = df["email"]
            
        if "cv_url" not in df.columns:
            df["cv_url"] = ""
    return df

@st.cache_data(ttl=60)
def obtener_asistencias_cached():
    asist_docs = db.collection("asistencias").stream()
    return pd.DataFrame([a.to_dict() for a in asist_docs])

def obtener_proyectos_evaluador(email):
    docs = db.collection("proyectos").where("evaluadores_asignados", "array_contains", email).stream()
    proys = []
    for doc in docs:
        d = doc.to_dict() | {"id_doc": doc.id}
        d["evaluadores_asignados"] = normalizar_lista(d.get("evaluadores_asignados"))
        proys.append(d)
    return proys

def autorregistro_usuario(email, password, nombre):
    clean_email = email.strip().lower()
    doc_id = clean_email.replace("@", "_at_").replace(".", "_")
    doc_ref = db.collection("Usuarios").document(doc_id)
    
    if doc_ref.get().exists:
        return False, "El correo electrónico ya se encuentra registrado."
    
    doc_ref.set({
        "email": clean_email,
        "password": password.strip(),
        "nombre_completo": nombre.strip(),
        "rol": "evaluador",
        "estado_cuenta": "Pendiente",
        "nivel_especialidad": "Sin Definir",
        "dias_disponibles": [],
        "cv_url": "",
        "comentarios_interanuales": []
    })
    st.cache_data.clear()
    return True, "Solicitud enviada. Un administrador activará su cuenta en breve."

def aprobar_usuario(doc_id, rol, nivel_especialidad, dias_disponibles):
    db.collection("Usuarios").document(doc_id).update({
        "rol": rol,
        "estado_cuenta": "Activo",
        "nivel_especialidad": nivel_especialidad,
        "dias_disponibles": dias_disponibles
    })
    st.cache_data.clear()

def eliminar_usuario_firestore(doc_id):
    db.collection("Usuarios").document(doc_id).delete()
    st.cache_data.clear()

def desasignar_evaluador_de_proyectos(email, anio_edicion):
    docs = db.collection("proyectos").where("evaluadores_asignados", "array_contains", email).where("anio_edicion", "==", str(anio_edicion).strip()).stream()
    batch = db.batch()
    cont = 0
    for doc in docs:
        d = doc.to_dict()
        actuales = d.get("evaluadores_asignados", [])
        nuevos = [e for e in actuales if e != email]
        batch.update(doc.reference, {
            "evaluadores_asignados": nuevos,
            "estado_evaluacion": "Asignado" if nuevos else "Pendiente"
        })
        cont += 1
    batch.commit()
    st.cache_data.clear()
    return cont

def actualizar_dias_evaluador(doc_id, nuevos_dias):
    db.collection("Usuarios").document(doc_id).update({
        "dias_disponibles": nuevos_dias
    })
    st.cache_data.clear()

def guardar_cv_evaluador(doc_id, cv_link):
    db.collection("Usuarios").document(doc_id).update({
        "cv_url": cv_link
    })
    st.cache_data.clear()

def agregar_comentario_evaluador(doc_id, comentario_texto, autor, anio_edicion):
    doc_ref = db.collection("Usuarios").document(doc_id)
    u = doc_ref.get().to_dict()
    comentarios = u.get("comentarios_interanuales", [])
    if not isinstance(comentarios, list):
        comentarios = []
    
    comentarios.append({
        "fecha": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "anio_edicion": str(anio_edicion).strip(),
        "autor": autor,
        "texto": comentario_texto
    })
    doc_ref.update({"comentarios_interanuales": comentarios})
    st.cache_data.clear()

def guardar_asistencia(proyecto_id, docente, capacitacion, presente, anio_edicion):
    db.collection("asistencias").add({
        "proyecto_id": proyecto_id,
        "docente": docente,
        "capacitacion": capacitacion,
        "presente": presente,
        "anio_edicion": str(anio_edicion).strip(),
        "fecha": firestore.SERVER_TIMESTAMP
    })
    st.cache_data.clear()

def asignar_evaluadores_manual(proyecto_id, lista_evaluadores):
    db.collection("proyectos").document(proyecto_id).update({
        "evaluadores_asignados": lista_evaluadores,
        "estado_evaluacion": "Asignado" if lista_evaluadores else "Pendiente"
    })
    st.cache_data.clear()

def asignacion_automatica(tamano_grupo, filtro_nivel, filtro_dia, anio_edicion):
    df_p = obtener_proyectos_cached()
    df_u = obtener_usuarios_cached()
    
    if df_p.empty or df_u.empty:
        return 0, "No hay proyectos o evaluadores cargados."
        
    evaluadores = df_u[(df_u["rol"] == "evaluador") & (df_u["estado_cuenta"] == "Activo")].to_dict('records')
    eval_filtrados = [
        e for e in evaluadores 
        if e.get("nivel_especialidad") == filtro_nivel 
        and filtro_dia in e.get("dias_disponibles", [])
    ]
    
    if len(eval_filtrados) < tamano_grupo:
        return 0, f"Insuficientes evaluadores habilitados ({len(eval_filtrados)}) para formar {tamano_grupo}s."
    
    df_target = df_p[(df_p["nivel_agrupado"] == filtro_nivel) & (df_p["anio_edicion"] == str(anio_edicion).strip())]
    proyectos_target = df_target.to_dict('records')
    asig_count = 0
    batch = db.batch()
    
    for i, p in enumerate(proyectos_target):
        seleccionados = [eval_filtrados[(i*tamano_grupo + j) % len(eval_filtrados)]["email"] for j in range(tamano_grupo)]
        doc_ref = db.collection("proyectos").document(p["id_doc"])
        batch.update(doc_ref, {
            "evaluadores_asignados": seleccionados,
            "estado_evaluacion": "Asignado",
            "dia_evaluacion": filtro_dia
        })
        asig_count += 1
        if asig_count % 400 == 0:
            batch.commit()
            batch = db.batch()
            
    if asig_count % 400 != 0:
        batch.commit()
        
    st.cache_data.clear()
    return asig_count, f"Se asignaron exitosamente {asig_count} proyectos de {filtro_nivel} para la edición {anio_edicion}."

def procesar_e_ingresar_csv(df, anio_edicion):
    batch = db.batch()
    contador = 0
    clean_anio = str(anio_edicion).strip()
    
    def col_search(keywords):
        for c in df.columns:
            if any(k in str(c).lower() for k in keywords):
                return c
        return None

    c_titulo = col_search(['título del proyecto', 'titulo'])
    c_escuela = col_search(['nombre completo del establecimiento', 'escuela', 'establecimiento'])
    c_distrito = col_search(['distrito escolar', 'distrito'])
    c_cue = col_search(['cue'])
    c_nivel = col_search(['nivel educativo', 'nivel'])
    c_docente = col_search(['docente a cargo del proyecto', 'docente', 'nombre'])
    c_dni_docente = col_search(['dni - docente', 'dni docente', 'dni'])
    c_email = col_search(['correo electrónico - docente', 'email', 'correo'])
    c_resumen = col_search(['resumen del proyecto', 'resumen'])
    c_pdf = col_search(['subí el proyecto', 'pdf'])
    c_youtube = col_search(['link de video de youtube', 'youtube'])

    for idx, row in df.iterrows():
        doc_id = f"PROY-{clean_anio}-{idx+1:03d}"
        doc_ref = db.collection("proyectos").document(doc_id)
        
        escuela_raw = row.get(c_escuela, "") if c_escuela else ""
        nivel_raw = row.get(c_nivel, "") if c_nivel else ""
        
        docente_nombre_raw = str(row.get(c_docente, "")).strip() if c_docente else ""
        docente_dni_raw = str(row.get(c_dni_docente, "")).strip() if c_dni_docente else ""
        docente_email_raw = str(row.get(c_email, "")).strip() if c_email else ""

        datos_completos_excel = {limpiar_clave_firestore(col): ("" if pd.isna(row.get(col)) else str(row.get(col)).strip()) for col in df.columns}

        doc_data = {
            "titulo": str(row.get(c_titulo, "")).strip() if c_titulo else "Sin Título",
            "escuela_raw": str(escuela_raw),
            "escuela_estandarizada": normalizar_escuela(escuela_raw),
            "distrito": str(row.get(c_distrito, "")) if c_distrito else "",
            "cue": str(row.get(c_cue, "")) if c_cue else "",
            "nivel_raw": str(nivel_raw),
            "nivel_agrupado": agrupar_nivel(nivel_raw),
            "docente_cargo": docente_nombre_raw,
            "docente_dni": docente_dni_raw,
            "docente_email": docente_email_raw,
            "resumen": str(row.get(c_resumen, "")) if c_resumen else "",
            "drive_pdf": str(row.get(c_pdf, "")) if c_pdf else "",
            "youtube_url": str(row.get(c_youtube, "")) if c_youtube else "",
            "evaluadores_asignados": [],
            "estado_evaluacion": "Pendiente",
            "devolucion": "",
            "dia_evaluacion": "",
            "anio_edicion": clean_anio,
            "formulario_respuestas_completas": datos_completos_excel
        }
        
        batch.set(doc_ref, doc_data)
        contador += 1
        
        if contador % 400 == 0:
            batch.commit()
            batch = db.batch()
            
    if contador % 400 != 0:
        batch.commit()
        
    st.cache_data.clear()
    return contador

# ---------------------------------------------------------
# 3. AUTENTICACIÓN Y AUTORREGISTRO
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.sidebar.title("🔬 Portal Feria de Ciencias")
    modo_acceso = st.sidebar.radio("Acceso", ["Iniciar Sesión", "Registrarse (Nuevo Evaluador)"])
    
    if modo_acceso == "Iniciar Sesión":
        email = st.sidebar.text_input("Correo electrónico")
        password = st.sidebar.text_input("Contraseña", type="password")
        
        if st.sidebar.button("Entrar", type="primary"):
            users = db.collection("Usuarios").where("email", "==", email.strip().lower()).where("password", "==", password.strip()).get()
            if users:
                u_data = users[0].to_dict()
                estado = u_data.get("estado_cuenta", "Activo")
                if estado == "Pendiente":
                    st.sidebar.warning("Su cuenta aún está pendiente de activación por un Administrador.")
                else:
                    st.session_state["logged_in"] = True
                    st.session_state["user_email"] = u_data["email"]
                    st.session_state["user_role"] = u_data["rol"]
                    st.session_state["user_doc_id"] = users[0].id
                    st.rerun()
            else:
                st.sidebar.error("Credenciales incorrectas")
    else:
        st.sidebar.subheader("➕ Solicitud de Registro")
        reg_nombre = st.sidebar.text_input("Nombre y Apellido")
        reg_email = st.sidebar.text_input("Correo electrónico")
        reg_pass = st.sidebar.text_input("Contraseña deseada", type="password")
        
        if st.sidebar.button("Enviar Registro"):
            if reg_email and reg_pass and reg_nombre:
                exito, msg = autorregistro_usuario(reg_email, reg_pass, reg_nombre)
                if exito:
                    st.sidebar.success(msg)
                else:
                    st.sidebar.error(msg)
            else:
                st.sidebar.warning("Complete todos los campos.")
                
    st.info("Por favor, ingrese sus credenciales para acceder al sistema.")
    st.stop()

# ---------------------------------------------------------
# 4. ROLES, EDICIÓN Y VISTAS
# ---------------------------------------------------------
rol = st.session_state["user_role"]
user_email = st.session_state["user_email"]
user_doc_id = st.session_state.get("user_doc_id", "")

st.sidebar.write(f"Usuario: **{user_email}**")
st.sidebar.write(f"Rol Actual: **{rol.upper()}**")

st.sidebar.divider()
anio_input_raw = st.sidebar.text_input("🗓️ Escribir Año / Edición", value="2026")
anio_edicion_actual = str(anio_input_raw).strip() if anio_input_raw.strip() else "2026"

if st.sidebar.button("Cerrar Sesión"):
    st.session_state["logged_in"] = False
    st.rerun()

# --- VISTAS SEGÚN PERFIL Y ROL ---

# 1. PERFIL: ADMIN Y REFERENTE
if rol in ["admin", "referente"]:
    st.title(f"📊 Feria de Ciencias ({anio_edicion_actual}) - Panel {rol.capitalize()}")
    df_all_proyectos = obtener_proyectos_cached()
    df_proyectos = df_all_proyectos[df_all_proyectos["anio_edicion"] == anio_edicion_actual] if not df_all_proyectos.empty else pd.DataFrame()

    if rol == "admin":
        tabs_list = [
            "📤 Cargar CSV Forms", 
            "📌 Fichas de Proyectos", 
            "🎟️ Asistencia y Certificados", 
            "👥 Asignación y Duplas", 
            "📊 Reportes Personalizados",
            "🏅 Ficha Evaluadores & Historial",
            "👤 Solicitudes & Usuarios"
        ]
    else:
        tabs_list = [
            "📌 Fichas de Proyectos", 
            "🎟️ Asistencia y Certificados", 
            "📊 Reportes Personalizados"
        ]
        
    tabs = st.tabs(tabs_list)
    tab_idx = 0

    # --- TAB EXCLUSIVA ADMIN: CARGA CSV ---
    if rol == "admin":
        with tabs[tab_idx]:
            st.subheader(f"Carga Masiva de Respuestas de Forms - Edición {anio_edicion_actual}")
            archivo_subido = st.file_uploader("Seleccionar archivo CSV o Excel", type=["csv", "xlsx"])
            if archivo_subido is not None:
                try:
                    df_raw = pd.read_csv(archivo_subido) if archivo_subido.name.endswith('.csv') else pd.read_excel(archivo_subido)
                    st.write(f"📁 **Archivo detectado:** `{archivo_subido.name}` con **{len(df_raw)}** filas.")
                    
                    if st.button(f"🚀 Importar a Firebase para Edición {anio_edicion_actual}", type="primary"):
                        with st.spinner("Procesando proyectos..."):
                            total = procesar_e_ingresar_csv(df_raw, anio_edicion_actual)
                            st.toast(f"✅ ¡Proyectos {anio_edicion_actual} cargados con éxito!", icon="🎉")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error al procesar el archivo: {e}")
        tab_idx += 1

    # --- TAB FICHAS DE PROYECTOS (ADMIN Y REFERENTE) ---
    with tabs[tab_idx]:
        st.subheader(f"Fichas de Proyectos ({anio_edicion_actual})")
        if not df_proyectos.empty:
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                filtro_grupo_nivel = st.selectbox("Nivel Educativo Agrupado", ["TODOS", "INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"])
            with col_f2:
                escuelas_opts = ["TODAS"] + list(df_proyectos['escuela_estandarizada'].unique())
                filtro_escuela = st.selectbox("Escuela Estandarizada", escuelas_opts)
            with col_f3:
                busqueda_txt = st.text_input("Buscar por título o ID")

            df_cards = df_proyectos.copy()
            if filtro_grupo_nivel != "TODOS":
                df_cards = df_cards[df_cards['nivel_agrupado'] == filtro_grupo_nivel]
            if filtro_escuela != "TODAS":
                df_cards = df_cards[df_cards['escuela_estandarizada'] == filtro_escuela]
            if busqueda_txt:
                df_cards = df_cards[df_cards['titulo'].astype(str).str.contains(busqueda_txt, case=False, na=False) | df_cards['id_doc'].astype(str).str.contains(busqueda_txt, case=False, na=False)]

            st.write(f"Mostrando **{len(df_cards)}** proyectos de la edición {anio_edicion_actual}")
            st.divider()

            for idx, p in df_cards.iterrows():
                with st.expander(f"🏷️ [{p.get('id_doc')}] {p.get('titulo', 'Sin Título')} | Nivel: {p.get('nivel_agrupado', 'N/A')}"):
                    evals = normalizar_lista(p.get('evaluadores_asignados'))
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Escuela:** {p.get('escuela_estandarizada', 'N/A')}")
                        st.markdown(f"**Distrito Escolar:** {p.get('distrito', 'N/A')} | **CUE:** {p.get('cue', 'N/A')}")
                        docente_txt = p.get('docente_cargo', '') if p.get('docente_cargo') else p.get('docente_email', 'N/A')
                        st.markdown(f"**Docente a Cargo:** {docente_txt} (DNI: {p.get('docente_dni', 'N/A')})")
                    with c2:
                        st.markdown(f"**Evaluadores Asignados:** {', '.join(evals) if evals else '⚠️ Sin Asignar'}")
                        st.markdown(f"**Estado:** {p.get('estado_evaluacion', 'Pendiente')}")
                    
                    st.markdown("**Resumen:**")
                    st.info(p.get('resumen', 'Sin resumen cargado.'))
                    
                    r1, r2 = st.columns(2)
                    if p.get('drive_pdf'): r1.markdown(f"📄 [Ver PDF]({p.get('drive_pdf')})")
                    if p.get('youtube_url'): r2.markdown(f"🎬 [Ver Video]({p.get('youtube_url')})")
                    
                    if p.get('formulario_respuestas_completas'):
                        with st.popover("📋 Ver todas las respuestas del formulario (127 campos)"):
                            st.json(p.get('formulario_respuestas_completas'))

                    if rol == "admin" and p.get('devolucion'):
                        st.markdown("**Devolución Cualitativa:**")
                        st.success(p.get('devolucion'))
        else:
            st.info(f"No hay proyectos cargados para la edición {anio_edicion_actual}.")
    tab_idx += 1

    # --- TAB ASISTENCIA (ADMIN Y REFERENTE) ---
    with tabs[tab_idx]:
        st.subheader(f"Registro de Asistencia y Acreditación de Puntaje ({anio_edicion_actual})")
        if not df_proyectos.empty:
            st.markdown("##### 🔍 Búsqueda Avanzada de Proyectos / Docentes")
            opciones_busqueda = []
            mapa_proyectos = {}
            
            for idx, p in df_proyectos.iterrows():
                p_id = str(p.get("id_doc", ""))
                p_tit = str(p.get("titulo", "Sin Título"))
                p_doc = str(p.get("docente_cargo", "")) if str(p.get("docente_cargo", "")).strip() else str(p.get("docente_email", ""))
                p_dni = str(p.get("docente_dni", ""))
                
                label = f"{p_id} | {p_tit[:40]}... | Docente: {p_doc} (DNI: {p_dni})"
                opciones_busqueda.append(label)
                mapa_proyectos[label] = p
                
            seleccion_label = st.selectbox(
                f"Buscar Proyecto ({anio_edicion_actual}) por Nombre, ID o DNI de Docente:",
                options=opciones_busqueda
            )
            proyecto_sel = mapa_proyectos[seleccion_label]
            
            st.divider()
            col_regist, col_cond = st.columns([1, 1.2])
            
            with col_regist:
                st.markdown("##### 🎟️ Registrar Asistencia Puntual")
                doc_nombre = proyecto_sel.get('docente_cargo') if proyecto_sel.get('docente_cargo') else proyecto_sel.get('docente_email')
                st.write(f"**Proyecto:** `{proyecto_sel.get('id_doc')}` - {proyecto_sel.get('titulo')}")
                st.write(f"**Docente Inscrito:** {doc_nombre} (DNI: {proyecto_sel.get('docente_dni')})")
                
                docente_asistente = st.text_input("Nombre / DNI del Docente Asistente", value=f"{doc_nombre} - DNI: {proyecto_sel.get('docente_dni')}")
                cap_nom = st.selectbox("Instancia de Evaluación / Asistencia", [
                    "Capacitación 1 - General", 
                    "Capacitación 2 - Metodología", 
                    "Feria de Ciencias - Instancia Exposición / Stand 1",
                    "Feria de Ciencias - Instancia Exposición / Stand 2"
                ])
                pres = st.checkbox("Presente", value=True)
                
                if st.button("Guardar Registro de Asistencia", type="primary"):
                    guardar_asistencia(proyecto_sel.get('id_doc'), docente_asistente, cap_nom, pres, anio_edicion_actual)
                    st.toast("✅ Asistencia registrada", icon="💾")
                    st.success("Guardado con éxito en Firebase.")

            with col_cond:
                st.markdown("##### 📜 Parámetros Configurables para Acreditación de Puntaje")
                c_req1, c_req2 = st.columns(2)
                
                # Bloqueo de edición si no es Administrador
                es_disabled_ref = (rol != "admin")
                
                with c_req1:
                    req_capacitaciones = st.number_input("Capacitaciones obligatorias", min_value=0, max_value=5, value=2, disabled=es_disabled_ref)
                with c_req2:
                    req_instancias_feria = st.number_input("Instancias de Feria requeridas", min_value=0, max_value=5, value=1, disabled=es_disabled_ref)
                    
                if es_disabled_ref:
                    st.caption("🔒 *Solo el Administrador puede modificar los parámetros de acreditación.*")

                df_asist_all = obtener_asistencias_cached()
                if not df_asist_all.empty and "proyecto_id" in df_asist_all.columns:
                    st.markdown(f"**Historial del Proyecto `{proyecto_sel.get('id_doc')}`:**")
                    asist_proy = df_asist_all[df_asist_all["proyecto_id"] == proyecto_sel.get('id_doc')]
                    if not asist_proy.empty:
                        st.dataframe(asist_proy[["capacitacion", "docente", "presente", "fecha"]], use_container_width=True)
                    else:
                        st.info("Sin registros de asistencia acumulados para este proyecto.")
            
            st.divider()
            st.markdown("##### 📊 Reporte Global de Acreditación y Certificados de Puntaje")
            
            df_asist_all = obtener_asistencias_cached()
            if not df_asist_all.empty and "proyecto_id" in df_asist_all.columns:
                asist_validas = df_asist_all[df_asist_all["presente"] == True]
                resumen_cert = []
                for _, p_row in df_proyectos.iterrows():
                    p_id = p_row.get("id_doc")
                    sub_a = asist_validas[asist_validas["proyecto_id"] == p_id]
                    
                    caps_asistidas = len(sub_a[sub_a["capacitacion"].str.contains("Capacitación", na=False)]["capacitacion"].unique())
                    feria_asistida = len(sub_a[sub_a["capacitacion"].str.contains("Feria", na=False)]["capacitacion"].unique())
                    
                    cumple_caps = caps_asistidas >= req_capacitaciones
                    cumple_feria = feria_asistida >= req_instancias_feria
                    otorgar_puntaje = "SÍ" if (cumple_caps and cumple_feria) else "NO"
                    
                    docente_display = str(p_row.get("docente_cargo", "")).strip()
                    if not docente_display:
                        docente_display = str(p_row.get("docente_email", "")).strip()
                    if p_row.get("docente_dni"):
                        docente_display += f" (DNI: {p_row.get('docente_dni')})"
                    
                    resumen_cert.append({
                        "ID Proyecto": p_id,
                        "Título": p_row.get("titulo"),
                        "Escuela": p_row.get("escuela_estandarizada"),
                        "Docente": docente_display,
                        "Capacitaciones": f"{caps_asistidas}/{req_capacitaciones}",
                        "Instancias Feria": f"{feria_asistida}/{req_instancias_feria}",
                        "Acredita Puntaje": otorgar_puntaje
                    })
                    
                df_resumen_cert = pd.DataFrame(resumen_cert)
                st.dataframe(df_resumen_cert, use_container_width=True)
                
                output_cert = io.BytesIO()
                with pd.ExcelWriter(output_cert, engine='xlsxwriter') as writer_c:
                    df_resumen_cert.to_excel(writer_c, sheet_name='Certificacion_Puntaje', index=False)
                    
                st.download_button(
                    label=f"📥 Descargar Reporte Oficial de Acreditación y Certificados ({anio_edicion_actual}).xlsx",
                    data=output_cert.getvalue(),
                    file_name=f"Reporte_Certificacion_Puntaje_{anio_edicion_actual}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
    tab_idx += 1

    # --- TAB EXCLUSIVA ADMIN: ASIGNACIÓN Y DESASIGNACIÓN DE EVALUADORES ---
    if rol == "admin":
        with tabs[tab_idx]:
            st.subheader(f"Asignación / Desasignación de Evaluadores ({anio_edicion_actual})")
            st.markdown("#### ⚡ Asignación Automática")
            col_a1, col_a2, col_a3, col_a4 = st.columns(4)
            with col_a1:
                modo_grupo = st.radio("Formato", ["Duplas (2)", "Triejas (3)"])
                tamano = 2 if "Duplas" in modo_grupo else 3
            with col_a2:
                auto_nivel = st.selectbox("Nivel", ["INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"])
            with col_a3:
                auto_dia = st.selectbox("Día", ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"])
            with col_a4:
                st.write("")
                st.write("")
                if st.button("Generar y Asignar", type="primary"):
                    cant, msg = asignacion_automatica(tamano, auto_nivel, auto_dia, anio_edicion_actual)
                    if cant > 0:
                        st.toast("✅ Asignación automática completada", icon="🎯")
                        st.rerun()
                    else:
                        st.error(msg)

            st.divider()
            st.markdown("#### 🛠️ Asignación / Desasignación Manual por Proyecto")
            if not df_proyectos.empty:
                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    proj_id_asig = st.selectbox("Seleccionar Proyecto", df_proyectos['id_doc'].tolist(), key="asig_manual")
                    p_selected = df_proyectos[df_proyectos['id_doc'] == proj_id_asig].iloc[0]
                    st.caption(f"Nivel: **{p_selected.get('nivel_agrupado')}** | Escuela: **{p_selected.get('escuela_estandarizada')}**")
                
                with col_m2:
                    df_users = obtener_usuarios_cached()
                    evaluadores_list = df_users[(df_users["rol"] == "evaluador") & (df_users["estado_cuenta"] == "Activo")]["email"].tolist() if not df_users.empty else []
                    evals_actuales = normalizar_lista(p_selected.get('evaluadores_asignados'))

                    evals_seleccionados = st.multiselect(
                        "Evaluadores Asignados (Borre del cuadro para desasignar):", 
                        options=evaluadores_list, 
                        default=[e for e in evals_actuales if e in evaluadores_list]
                    )

                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    if st.button("💾 Guardar Cambios de Asignación"):
                        asignar_evaluadores_manual(proj_id_asig, evals_seleccionados)
                        st.toast("✅ Asignación actualizada", icon="💾")
                        st.rerun()
                with col_b2:
                    if st.button("❌ Quitar Todos los Evaluadores de este Proyecto"):
                        asignar_evaluadores_manual(proj_id_asig, [])
                        st.toast("🚫 Proyecto desasignado por completo", icon="🧹")
                        st.rerun()
        tab_idx += 1

    # --- TAB REPORTES PERSONALIZADOS ---
    with tabs[tab_idx]:
        st.subheader(f"Reportes Excel - Edición {anio_edicion_actual}")
        tipo_reporte = st.selectbox("Tipo de Reporte", [
            "Reporte Consolidado General",
            "Reporte por Nivel Educativo (Inicial / Primaria / Secundaria)",
            "Reporte de Asignación de Evaluadores y Duplas",
            "Reporte de Asistencia a Capacitación o Día Específico"
        ])
        
        if not df_proyectos.empty:
            df_rep = df_proyectos.copy()
            if rol == "referente" and "devolucion" in df_rep.columns:
                df_rep = df_rep.drop(columns=["devolucion"])

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                if tipo_reporte == "Reporte Consolidado General":
                    df_rep.to_excel(writer, sheet_name='Proyectos_General', index=False)
                elif tipo_reporte == "Reporte por Nivel Educativo (Inicial / Primaria / Secundaria)":
                    for n in ["INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"]:
                        sub_df = df_rep[df_rep['nivel_agrupado'] == n]
                        sub_df.to_excel(writer, sheet_name=n.replace(" / ", "_"), index=False)
                elif tipo_reporte == "Reporte de Asignación de Evaluadores y Duplas":
                    cols_eval = ["id_doc", "titulo", "nivel_agrupado", "escuela_estandarizada", "evaluadores_asignados", "dia_evaluacion", "estado_evaluacion"]
                    cols_exist = [c for c in cols_eval if c in df_rep.columns]
                    df_rep[cols_exist].to_excel(writer, sheet_name='Asignaciones', index=False)
                elif tipo_reporte == "Reporte de Asistencia a Capacitación o Día Específico":
                    df_asist = obtener_asistencias_cached()
                    if not df_asist.empty:
                        eventos_disponibles = ["TODOS LOS EVENTOS"] + df_asist["capacitacion"].unique().tolist()
                        evento_filtro = st.selectbox("Filtrar por Capacitación o Día de Feria:", eventos_disponibles)
                        
                        if evento_filtro != "TODOS LOS EVENTOS":
                            df_asist = df_asist[df_asist["capacitacion"] == evento_filtro]
                        
                        df_asist.to_excel(writer, sheet_name='Asistencias_Filtradas', index=False)

            st.download_button(
                label=f"📥 Descargar {tipo_reporte} ({anio_edicion_actual}).xlsx",
                data=output.getvalue(),
                file_name=f"{tipo_reporte.replace(' ', '_')}_{anio_edicion_actual}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    tab_idx += 1

    # --- TAB EXCLUSIVA ADMIN: FICHA DE EVALUADORES Y DESASIGNACIÓN MASIVA ---
    if rol == "admin":
        with tabs[tab_idx]:
            st.subheader("🏅 Ficha de Desempeño, Días, CV y Desasignaciones")
            df_u = obtener_usuarios_cached()
            
            if not df_u.empty:
                df_evals = df_u[df_u["rol"] == "evaluador"]
                if not df_evals.empty:
                    eval_sel_email = st.selectbox("Seleccionar Evaluador para ver Ficha:", df_evals["email"].tolist())
                    eval_data = df_evals[df_evals["email"] == eval_sel_email].iloc[0]
                    eval_id = eval_data["id_doc"]
                    
                    st.divider()
                    st.markdown(f"### 📋 Legajo de Evaluador: `{eval_data.get('nombre_completo', eval_sel_email)}`")
                    
                    col_info1, col_info2, col_info3 = st.columns(3)
                    with col_info1:
                        st.write(f"**Email:** {eval_sel_email}")
                        st.write(f"**Estado:** `{eval_data.get('estado_cuenta', 'Activo')}`")
                        if eval_data.get('cv_url'):
                            st.markdown(f"📄 [Abrir CV del Evaluador]({eval_data.get('cv_url')})")
                        else:
                            st.info("Sin CV adjunto.")
                    with col_info2:
                        st.write(f"**Especialidad:** {eval_data.get('nivel_especialidad', 'N/A')}")
                        dias_actuales = eval_data.get('dias_disponibles', [])
                        if not isinstance(dias_actuales, list):
                            dias_actuales = []
                        st.write(f"**Días Actuales:** {', '.join(dias_actuales)}")
                    with col_info3:
                        cant_evals_actual = len(df_proyectos[df_proyectos["evaluadores_asignados"].apply(lambda x: eval_sel_email in x)]) if not df_proyectos.empty else 0
                        st.metric(f"Proyectos Asignados ({anio_edicion_actual})", cant_evals_actual)

                    if cant_evals_actual > 0:
                        if st.button(f"🚫 Desasignar a {eval_sel_email} de TODOS los proyectos de {anio_edicion_actual}"):
                            cant_desasig = desasignar_evaluador_de_proyectos(eval_sel_email, anio_edicion_actual)
                            st.toast(f"✅ Se quitó al evaluador de {cant_desasig} proyectos", icon="🧹")
                            st.rerun()

                    st.markdown("#### ✏️ Modificar Días Disponibles del Evaluador")
                    dias_editados = st.multiselect(
                        "Seleccionar días de disponibilidad:",
                        options=["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"],
                        default=[d for d in dias_actuales if d in ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]],
                        key=f"edit_dias_{eval_id}"
                    )
                    
                    if st.button("Actualizar Días en Ficha", key=f"btn_save_dias_{eval_id}"):
                        actualizar_dias_evaluador(eval_id, dias_editados)
                        st.toast("✅ Días actualizados en legajo", icon="📅")
                        st.rerun()

                    st.markdown("#### 📜 Historial Interanual de Proyectos Evaluados")
                    proys_evaluador = df_all_proyectos[df_all_proyectos["evaluadores_asignados"].apply(lambda x: eval_sel_email in x)] if not df_all_proyectos.empty else pd.DataFrame()
                    if not proys_evaluador.empty:
                        cols_m = [c for c in ["id_doc", "anio_edicion", "titulo", "nivel_agrupado", "escuela_estandarizada", "estado_evaluacion"] if c in proys_evaluador.columns]
                        st.dataframe(proys_evaluador[cols_m], use_container_width=True)
                    else:
                        st.info("Sin registros de proyectos evaluados aún.")

                    st.divider()
                    st.markdown("#### 💬 Bitácora / Comentarios de Desempeño Interanual")
                    comentarios = eval_data.get("comentarios_interanuales", [])
                    if isinstance(comentarios, list) and comentarios:
                        for c in comentarios:
                            st.write(f"📌 **[{c.get('fecha')}] Edición {c.get('anio_edicion', '2026')} ({c.get('autor')}):** {c.get('texto')}")
                    else:
                        st.info("No hay comentarios asentados aún para este evaluador.")
                        
                    with st.form(key=f"form_comentario_{eval_id}"):
                        nuevo_comentario = st.text_area(f"Agregar observación sobre el desempeño para la edición {anio_edicion_actual}:")
                        submit_com = st.form_submit_button("Guardar Comentario en Ficha")
                        if submit_com and nuevo_comentario:
                            agregar_comentario_evaluador(eval_id, nuevo_comentario, user_email, anio_edicion_actual)
                            st.toast("✅ Comentario guardado en legajo", icon="📝")
                            st.rerun()
        tab_idx += 1

    # --- TAB EXCLUSIVA ADMIN: SOLICITUDES, USUARIOS Y ELIMINACIÓN DE CUENTAS ---
    if rol == "admin":
        with tabs[tab_idx]:
            st.subheader("👤 Solicitudes de Registro y Control de Usuarios")
            df_u = obtener_usuarios_cached()
            
            if not df_u.empty:
                pendientes = df_u[df_u["estado_cuenta"] == "Pendiente"]
                st.markdown("##### ⏳ Solicitudes Pendientes de Aprobación")
                if not pendientes.empty:
                    for idx, u in pendientes.iterrows():
                        with st.expander(f"👤 Solicitud: {u.get('nombre_completo', 'Sin Nombre')} ({u.get('email')})"):
                            col_p1, col_p2, col_p3 = st.columns(3)
                            with col_p1:
                                rol_aprob = st.selectbox("Asignar Rol", ["evaluador", "referente", "admin"], key=f"r_{u['id_doc']}")
                            with col_p2:
                                nivel_aprob = st.selectbox("Especialidad Nivel", ["INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"], key=f"n_{u['id_doc']}")
                            with col_p3:
                                dias_aprob = st.multiselect("Días Disponibles", ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"], default=["Lunes"], key=f"d_{u['id_doc']}")
                                
                            if st.button("✅ Habilitar y Activar Cuenta", key=f"btn_ap_{u['id_doc']}", type="primary"):
                                aprobar_usuario(u['id_doc'], rol_aprob, nivel_aprob, dias_aprob)
                                st.toast(f"✅ Cuenta activada para {u.get('email')}", icon="🎉")
                                st.rerun()
                else:
                    st.success("No hay solicitudes de registro pendientes.")
                
                st.divider()
                st.markdown("##### 📋 Todos los Usuarios Registrados")
                cols_u = [c for c in ["id_doc", "email", "nombre_completo", "rol", "estado_cuenta", "nivel_especialidad"] if c in df_u.columns]
                st.dataframe(df_u[cols_u], use_container_width=True)

                st.divider()
                st.markdown("##### 🗑️ Eliminar Cuenta de Usuario")
                user_del_email = st.selectbox("Seleccionar cuenta a eliminar:", df_u["email"].tolist(), key="sel_del_user")
                u_del_data = df_u[df_u["email"] == user_del_email].iloc[0]
                
                st.warning(f"⚠️ **Atención:** Se eliminará de forma permanente el usuario `{user_del_email}` ({u_del_data.get('nombre_completo')}).")
                
                if st.button(f"🗑️ Confirmar Eliminar Cuenta `{user_del_email}`", type="primary"):
                    eliminar_usuario_firestore(u_del_data["id_doc"])
                    st.toast("✅ Usuario eliminado correctamente", icon="🗑️")
                    st.rerun()

# 2. PERFIL: EVALUADOR
elif rol == "evaluador":
    st.title(f"📝 Portal de Evaluación Pedagógica ({anio_edicion_actual})")
    
    with st.sidebar.expander("📄 Mi Curriculum Vitae (CV)"):
        cv_link_input = st.text_input("Enlace a CV (Google Drive / Dropbox):")
        if st.button("Guardar Enlace CV"):
            if cv_link_input and user_doc_id:
                guardar_cv_evaluador(user_doc_id, cv_link_input)
                st.toast("✅ Enlace de CV guardado", icon="📄")
                st.rerun()

    proyectos = obtener_proyectos_evaluador(user_email)
    proyectos_anio = [p for p in proyectos if p.get("anio_edicion", "2026") == anio_edicion_actual]
    
    if proyectos_anio:
        st.info(f"Tenés **{len(proyectos_anio)}** proyecto(s) asignado(s) para la edición {anio_edicion_actual}.")
        for p in proyectos_anio:
            with st.expander(f"📌 [{p.get('id_doc')}] {p.get('titulo', 'Sin Título')}"):
                evals = normalizar_lista(p.get('evaluadores_asignados'))
                st.write(f"**Nivel Educativo:** {p.get('nivel_agrupado', 'N/A')}")
                st.write(f"**Escuela:** {p.get('escuela_estandarizada', 'N/A')}")
                st.write(f"**Evaluadores del Equipo:** {', '.join(evals)}")
                
                st.markdown("**Resumen:**")
                st.info(p.get('resumen', 'Sin resumen cargado.'))
                
                col1, col2 = st.columns(2)
                with col1:
                    if p.get('drive_pdf'): st.markdown(f"📄 [Abrir PDF]({p.get('drive_pdf')})")
                with col2:
                    if p.get('youtube_url'): st.markdown(f"🎬 [Ver Video]({p.get('youtube_url')})")
                
                st.divider()
                st.subheader("Devolución Pedagógica Cualitativa")
                
                devolucion_actual = p.get('devolucion', '')
                devolucion = st.text_area(
                    "Registro de observaciones y retroalimentación pedagógica para el proyecto (sin puntaje):", 
                    value=str(devolucion_actual), 
                    key=f"d_{p['id_doc']}"
                )
                
                if st.button("Guardar Devolución", key=f"btn_{p['id_doc']}"):
                    db.collection("proyectos").document(p['id_doc']).update({
                        "devolucion": devolucion,
                        "estado_evaluacion": "Evaluado"
                    })
                    st.cache_data.clear()
                    st.toast("✅ Devolución guardada con éxito", icon="💾")
                    st.success("Guardado en Firebase.")
    else:
        st.info(f"No tenés proyectos asignados para la edición {anio_edicion_actual}.")
