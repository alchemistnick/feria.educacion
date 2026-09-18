import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import io
import random

st.set_page_config(page_title="Feria de Ciencias 2026", page_icon="🔬", layout="wide")

# ---------------------------------------------------------
# 1. INICIALIZACIÓN DE FIREBASE VIA STREAMLIT SECRETS
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

# ---------------------------------------------------------
# 2. FUNCIONES DE BASE DE DATOS
# ---------------------------------------------------------
def obtener_proyectos():
    docs = db.collection("proyectos").stream()
    return pd.DataFrame([doc.to_dict() | {"id_doc": doc.id} for doc in docs])

def obtener_proyectos_evaluador(email):
    docs = db.collection("proyectos").where("evaluadores_asignados", "array_contains", email).stream()
    return [doc.to_dict() | {"id_doc": doc.id} for doc in docs]

def obtener_usuarios():
    docs = db.collection("Usuarios").stream()
    return pd.DataFrame([doc.to_dict() | {"id_doc": doc.id} for doc in docs])

def registrar_usuario(email, password, rol, nivel_especialidad, dias_disponibles):
    existe = db.collection("Usuarios").where("email", "==", email.strip().lower()).get()
    if existe:
        return False, "El correo electrónico ya se encuentra registrado."
    
    db.collection("Usuarios").add({
        "email": email.strip().lower(),
        "password": password.strip(),
        "rol": rol,
        "nivel_especialidad": nivel_especialidad,
        "dias_disponibles": dias_disponibles
    })
    return True, f"Usuario {email} creado correctamente."

def guardar_asistencia(proyecto_id, docente, capacitacion, presente):
    db.collection("asistencias").add({
        "proyecto_id": proyecto_id,
        "docente": docente,
        "capacitacion": capacitacion,
        "presente": presente,
        "fecha": firestore.SERVER_TIMESTAMP
    })

def asignar_evaluadores_manual(proyecto_id, lista_evaluadores):
    db.collection("proyectos").document(proyecto_id).update({
        "evaluadores_asignados": lista_evaluadores,
        "estado_evaluacion": "Asignado" if lista_evaluadores else "Pendiente"
    })

def asignacion_automatica(tamano_grupo, filtro_nivel, filtro_dia):
    proyectos_docs = db.collection("proyectos").stream()
    proyectos = [d.to_dict() | {"id_doc": d.id} for d in proyectos_docs]
    
    usuarios_docs = db.collection("Usuarios").where("rol", "==", "evaluador").stream()
    evaluadores = [u.to_dict() for u in usuarios_docs]
    
    # Filtrar evaluadores por nivel y día
    eval_filtrados = [
        e for e in evaluadores 
        if e.get("nivel_especialidad") == filtro_nivel 
        and filtro_dia in e.get("dias_disponibles", [])
    ]
    
    if len(eval_filtrados) < tamano_grupo:
        return 0, f"No hay suficientes evaluadores de {filtro_nivel} disponibles el día {filtro_dia} para formar {tamano_grupo}s."
    
    proyectos_target = [p for p in proyectos if p.get("nivel_agrupado") == filtro_nivel]
    
    asig_count = 0
    batch = db.batch()
    
    for i, p in enumerate(proyectos_target):
        # Selección circular / aleatoria de evaluadores
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
        
    return asig_count, f"Se asignaron exitosamente {asig_count} proyectos de {filtro_nivel} en {tamano_grupo}s para el día {filtro_dia}."

def procesar_e_ingresar_csv(df):
    batch = db.batch()
    contador = 0
    
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
    c_docente = col_search(['docente a cargo del proyecto', 'docente'])
    c_email = col_search(['correo electrónico - docente', 'email'])
    c_resumen = col_search(['resumen del proyecto', 'resumen'])
    c_pdf = col_search(['subí el proyecto', 'pdf'])
    c_youtube = col_search(['link de video de youtube', 'youtube'])

    for idx, row in df.iterrows():
        doc_id = f"PROY-{idx+1:03d}"
        doc_ref = db.collection("proyectos").document(doc_id)
        
        escuela_raw = row.get(c_escuela, "") if c_escuela else ""
        nivel_raw = row.get(c_nivel, "") if c_nivel else ""
        
        doc_data = {
            "titulo": str(row.get(c_titulo, "")).strip() if c_titulo else "Sin Título",
            "escuela_raw": str(escuela_raw),
            "escuela_estandarizada": normalizar_escuela(escuela_raw),
            "distrito": str(row.get(c_distrito, "")) if c_distrito else "",
            "cue": str(row.get(c_cue, "")) if c_cue else "",
            "nivel_raw": str(nivel_raw),
            "nivel_agrupado": agrupar_nivel(nivel_raw),
            "docente_cargo": str(row.get(c_docente, "")) if c_docente else "",
            "docente_email": str(row.get(c_email, "")) if c_email else "",
            "resumen": str(row.get(c_resumen, "")) if c_resumen else "",
            "drive_pdf": str(row.get(c_pdf, "")) if c_pdf else "",
            "youtube_url": str(row.get(c_youtube, "")) if c_youtube else "",
            "evaluadores_asignados": [],
            "estado_evaluacion": "Pendiente",
            "devolucion": "",
            "dia_evaluacion": ""
        }
        
        batch.set(doc_ref, doc_data)
        contador += 1
        
        if contador % 400 == 0:
            batch.commit()
            batch = db.batch()
            
    if contador % 400 != 0:
        batch.commit()
        
    return contador

# ---------------------------------------------------------
# 3. AUTENTICACIÓN
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.sidebar.title("🔐 Acceso al Sistema")
    email = st.sidebar.text_input("Correo electrónico")
    password = st.sidebar.text_input("Contraseña", type="password")
    
    if st.sidebar.button("Iniciar Sesión"):
        users = db.collection("Usuarios").where("email", "==", email.strip().lower()).where("password", "==", password.strip()).get()
        if users:
            u_data = users[0].to_dict()
            st.session_state["logged_in"] = True
            st.session_state["user_email"] = u_data["email"]
            st.session_state["user_role"] = u_data["rol"]
            st.rerun()
        else:
            st.sidebar.error("Credenciales incorrectas")
    st.info("Por favor, ingrese sus credenciales para continuar.")
    st.stop()

# ---------------------------------------------------------
# 4. ROLES Y VISTAS
# ---------------------------------------------------------
rol = st.session_state["user_role"]
user_email = st.session_state["user_email"]

st.sidebar.write(f"Usuario: **{user_email}**")
st.sidebar.write(f"Rol: **{rol.upper()}**")

if st.sidebar.button("Cerrar Sesión"):
    st.session_state["logged_in"] = False
    st.rerun()

# --- VISTA: ADMIN & REFERENTE ---
if rol in ["admin", "referente"]:
    st.title(f"📊 Panel de Gestión Feria de Ciencias - {rol.capitalize()}")
    df_proyectos = obtener_proyectos()

    tabs_list = ["📌 Fichas de Proyectos", "🎟️ Asistencia", "👥 Asignación y Duplas", "📊 Reportes Personalizados"]
    if rol == "admin":
        tabs_list.insert(0, "📤 Cargar CSV Forms")
        tabs_list.append("👤 Registrar Usuarios / Evaluadores")
        
    tabs = st.tabs(tabs_list)

    # --- TAB EXCLUSIVA ADMIN: CARGA DE CSV ---
    if rol == "admin":
        with tabs[0]:
            st.subheader("Carga Masiva de Respuestas de Forms (.csv / .xlsx)")
            archivo_subido = st.file_uploader("Seleccionar archivo CSV o Excel", type=["csv", "xlsx"])
            
            if archivo_subido is not None:
                try:
                    df_raw = pd.read_csv(archivo_subido) if archivo_subido.name.endswith('.csv') else pd.read_excel(archivo_subido)
                    st.write(f"📁 **Archivo detectado:** `{archivo_subido.name}` con **{len(df_raw)}** filas.")
                    
                    if st.button("🚀 Confirmar e Importar a Firebase", type="primary"):
                        with st.spinner("Procesando proyectos..."):
                            total_cargados = procesar_e_ingresar_csv(df_raw)
                            st.success(f"Se importaron {total_cargados} proyectos con éxito.")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error al procesar el archivo: {e}")

    # --- TAB FICHAS DE PROYECTOS (VISTA DE TARJETAS NO TABULAR) ---
    idx_proj = 1 if rol == "admin" else 0
    with tabs[idx_proj]:
        st.subheader("Fichas de Proyectos Inscritos")
        
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
                df_cards = df_cards[df_cards['titulo'].str.contains(busqueda_txt, case=False, na=False) | df_cards['id_doc'].str.contains(busqueda_txt, case=False, na=False)]

            st.write(f"Mostrando **{len(df_cards)}** proyectos")
            st.divider()

            # Renderizado en Fichas
            for idx, p in df_cards.iterrows():
                with st.expander(f"🏷️ [{p.get('id_doc')}] {p.get('titulo', 'Sin Título')} | Nivel: {p.get('nivel_agrupado', 'N/A')}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Escuela:** {p.get('escuela_estandarizada', 'N/A')}")
                        st.markdown(f"**Distrito Escolar:** {p.get('distrito', 'N/A')} | **CUE:** {p.get('cue', 'N/A')}")
                        st.markdown(f"**Nivel Original:** {p.get('nivel_raw', 'N/A')}")
                        st.markdown(f"**Docente a Cargo:** {p.get('docente_cargo', 'N/A')} ({p.get('docente_email', 'N/A')})")
                    with c2:
                        st.markdown(f"**Evaluadores Asignados:** {', '.join(p.get('evaluadores_asignados', [])) if p.get('evaluadores_asignados') else '⚠️ Sin Asignar'}")
                        st.markdown(f"**Día de Evaluación:** {p.get('dia_evaluacion', 'Sin Definir')}")
                        st.markdown(f"**Estado de Evaluación:** {p.get('estado_evaluacion', 'Pendiente')}")
                    
                    st.markdown("**Resumen:**")
                    st.info(p.get('resumen', 'Sin resumen cargado.'))
                    
                    st.markdown("**Recursos Adjuntos:**")
                    r1, r2 = st.columns(2)
                    if p.get('drive_pdf'):
                        r1.markdown(f"📄 [Ver Informe Pedagógico / PDF]({p.get('drive_pdf')})")
                    if p.get('youtube_url'):
                        r2.markdown(f"🎬 [Ver Video en YouTube]({p.get('youtube_url')})")
                    
                    if rol == "admin" and p.get('devolucion'):
                        st.markdown("**Devolución Cualitativa de Evaluación:**")
                        st.success(p.get('devolucion'))

    # --- TAB ASISTENCIA ---
    idx_asist = 2 if rol == "admin" else 1
    with tabs[idx_asist]:
        st.subheader("Registro de Asistencia a Capacitaciones por Proyecto y Docente")
        if not df_proyectos.empty:
            proj_id = st.selectbox("Seleccionar Proyecto", df_proyectos['id_doc'].tolist())
            docente_nom = st.text_input("Nombre / DNI del Docente")
            cap_nom = st.selectbox("Instancia", ["Capacitación 1 - General", "Capacitación 2 - Metodología", "Capacitación 3 - Stand"])
            pres = st.checkbox("Presente", value=True)
            
            if st.button("Guardar Asistencia"):
                guardar_asistencia(proj_id, docente_nom, cap_nom, pres)
                st.success("Asistencia registrada en Firebase.")

    # --- TAB ASIGNACIÓN DE EVALUADORES Y DUPLAS/TRIEJAS ---
    idx_eval = 3 if rol == "admin" else 2
    with tabs[idx_eval]:
        st.subheader("Asignación por Nivel, Día y Conformación de Duplas/Trietas")
        
        st.markdown("#### ⚡ Asignación Automática")
        col_a1, col_a2, col_a3, col_a4 = st.columns(4)
        with col_a1:
            modo_grupo = st.radio("Formato de Evaluación", ["Duplas (2)", "Triejas (3)"])
            tamano = 2 if "Duplas" in modo_grupo else 3
        with col_a2:
            auto_nivel = st.selectbox("Seleccionar Nivel", ["INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"])
        with col_a3:
            auto_dia = st.selectbox("Día de Evaluación", ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"])
        with col_a4:
            st.write("")
            st.write("")
            if st.button("Generar Grupos y Asignar", type="primary"):
                cant, msg = asignacion_automatica(tamano, auto_nivel, auto_dia)
                if cant > 0:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

        st.divider()
        st.markdown("#### 🛠️ Asignación / Ajuste Manual de Proyectos")
        if not df_proyectos.empty:
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                proj_id_asig = st.selectbox("Seleccionar Proyecto", df_proyectos['id_doc'].tolist(), key="asig_manual")
                p_selected = df_proyectos[df_proyectos['id_doc'] == proj_id_asig].iloc[0]
                st.caption(f"Nivel: **{p_selected.get('nivel_agrupado')}** | Escuela: **{p_selected.get('escuela_estandarizada')}**")
            
            with col_m2:
                df_users = obtener_usuarios()
                evaluadores_list = []
                if not df_users.empty and "rol" in df_users.columns:
                    evaluadores_list = df_users[df_users["rol"] == "evaluador"]["email"].tolist()
                
                evals_actuales = p_selected.get('evaluadores_asignados', [])
                if not isinstance(evals_actuales, list):
                    evals_actuales = []

                evals_seleccionados = st.multiselect(
                    "Seleccionar Evaluadores (Dupla o Trieja)", 
                    options=evaluadores_list, 
                    default=[e for e in evals_actuales if e in evaluadores_list]
                )

            if st.button("Guardar Asignación Manual"):
                asignar_evaluadores_manual(proj_id_asig, evals_seleccionados)
                st.success(f"Asignación actualizada para el proyecto {proj_id_asig}.")
                st.rerun()

    # --- TAB REPORTES PERSONALIZADOS ---
    idx_rep = 4 if rol == "admin" else 3
    with tabs[idx_rep]:
        st.subheader("Generación y Descarga de Reportes Especiales")
        
        tipo_reporte = st.selectbox("Seleccionar Tipo de Reporte", [
            "Reporte Consolidado General",
            "Reporte por Nivel Educativo (Inicial / Primaria / Secundaria)",
            "Reporte de Asignación de Evaluadores y Duplas",
            "Reporte de Asistencia a Capacitaciones"
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
                    
                elif tipo_reporte == "Reporte de Asistencia a Capacitaciones":
                    asist_docs = db.collection("asistencias").stream()
                    df_asist = pd.DataFrame([a.to_dict() for a in asist_docs])
                    if not df_asist.empty:
                        df_asist.to_excel(writer, sheet_name='Asistencias', index=False)
                    else:
                        pd.DataFrame([{"Mensaje": "Sin asistencias cargadas"}]).to_excel(writer, sheet_name='Asistencias', index=False)

            st.download_button(
                label=f"📥 Descargar {tipo_reporte} (.xlsx)",
                data=output.getvalue(),
                file_name=f"{tipo_reporte.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # --- TAB REGISTRO DE USUARIOS Y DISPONIBILIDAD HORARIA ---
    if rol == "admin":
        with tabs[5]:
            st.subheader("Alta de Evaluadores y Carga de Disponibilidad")
            
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                st.markdown("##### ➕ Registrar Evaluador / Referente")
                nuevo_email = st.text_input("Correo electrónico")
                nuevo_pass = st.text_input("Contraseña", type="password")
                nuevo_rol = st.selectbox("Rol", ["evaluador", "referente", "admin"])
                
                # Campos específicos para Evaluadores
                nivel_esp = st.selectbox("Nivel de Especialidad", ["INICIAL", "PRIMARIA", "SECUNDARIA / SUPERIOR"])
                dias_disp = st.multiselect("Días Disponibles", ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"], default=["Lunes", "Martes"])
                
                if st.button("Crear Usuario", type="primary"):
                    if nuevo_email and nuevo_pass:
                        exito, mensaje = registrar_usuario(nuevo_email, nuevo_pass, nuevo_rol, nivel_esp, dias_disp)
                        if exito:
                            st.success(mensaje)
                            st.rerun()
                        else:
                            st.error(mensaje)
                    else:
                        st.warning("Completar todos los campos obligatorios.")
                        
            with col_u2:
                st.markdown("##### 📋 Nomina de Evaluadores y Registrados")
                df_usuarios = obtener_usuarios()
                if not df_usuarios.empty:
                    cols_usr = [c for c in ["email", "rol", "nivel_especialidad", "dias_disponibles"] if c in df_usuarios.columns]
                    st.dataframe(df_usuarios[cols_usr], use_container_width=True)

# --- VISTA: EVALUADOR ---
elif rol == "evaluador":
    st.title("📝 Portal de Evaluación Pedagógica")
    proyectos = obtener_proyectos_evaluador(user_email)
    
    if proyectos:
        st.info(f"Tenés **{len(proyectos)}** proyecto(s) asignado(s) para evaluar.")
        for p in proyectos:
            with st.expander(f"📌 [{p.get('id_doc')}] {p.get('titulo', 'Sin Título')}"):
                st.write(f"**Nivel Educativo:** {p.get('nivel_agrupado', 'N/A')}")
                st.write(f"**Escuela:** {p.get('escuela_estandarizada', 'N/A')}")
                st.write(f"**Evaluadores del Equipo:** {', '.join(p.get('evaluadores_asignados', []))}")
                
                st.markdown("**Resumen:**")
                st.info(p.get('resumen', 'Sin resumen cargado.'))
                
                col1, col2 = st.columns(2)
                with col1:
                    if p.get('drive_pdf'): st.markdown(f"📄 [Abrir Informe Pedagógico / PDF]({p.get('drive_pdf')})")
                with col2:
                    if p.get('youtube_url'): st.markdown(f"🎬 [Ver Video en YouTube]({p.get('youtube_url')})")
                
                st.divider()
                st.subheader("Devolución Pedagógica Cualitativa")
                
                devolucion_actual = p.get('devolucion', '')
                devolucion = st.text_area(
                    "Registro de observaciones, devoluciones y retroalimentación pedagógica para el proyecto (sin puntaje numérico):", 
                    value=str(devolucion_actual), 
                    key=f"d_{p['id_doc']}"
                )
                
                if st.button("Guardar Devolución", key=f"btn_{p['id_doc']}"):
                    db.collection("proyectos").document(p['id_doc']).update({
                        "devolucion": devolucion,
                        "estado_evaluacion": "Evaluado"
                    })
                    st.success("Devolución guardada en Firebase.")
    else:
        st.info("No tenés proyectos asignados actualmente.")
