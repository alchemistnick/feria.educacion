import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import json
import io

st.set_page_config(page_title="Feria de Ciencias 2026", page_icon="🔬", layout="wide")

# ---------------------------------------------------------
# 1. INICIALIZACIÓN DE FIREBASE VIA STREAMLIT SECRETS
# ---------------------------------------------------------
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        key_dict = json.loads(st.secrets["textkey"])
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()

# Diccionario para estandarizar escuelas comunes
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

# ---------------------------------------------------------
# 2. FUNCIONES DE BASE DE DATOS
# ---------------------------------------------------------
def obtener_proyectos():
    docs = db.collection("proyectos").stream()
    return pd.DataFrame([doc.to_dict() | {"id_doc": doc.id} for doc in docs])

def obtener_proyectos_evaluador(email):
    docs = db.collection("proyectos").where("evaluador_asignado", "==", email).stream()
    return [doc.to_dict() | {"id_doc": doc.id} for doc in docs]

def guardar_asistencia(proyecto_id, docente, capacitacion, presente):
    db.collection("asistencias").add({
        "proyecto_id": proyecto_id,
        "docente": docente,
        "capacitacion": capacitacion,
        "presente": presente,
        "fecha": firestore.SERVER_TIMESTAMP
    })

def asignar_evaluador(proyecto_id, evaluador_email):
    db.collection("proyectos").document(proyecto_id).update({
        "evaluador_asignado": evaluador_email,
        "estado_evaluacion": "Asignado"
    })

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
        
        doc_data = {
            "titulo": str(row.get(c_titulo, "")).strip() if c_titulo else "Sin Título",
            "escuela_raw": str(escuela_raw),
            "escuela_estandarizada": normalizar_escuela(escuela_raw),
            "distrito": str(row.get(c_distrito, "")) if c_distrito else "",
            "cue": str(row.get(c_cue, "")) if c_cue else "",
            "nivel": str(row.get(c_nivel, "")) if c_nivel else "",
            "docente_cargo": str(row.get(c_docente, "")) if c_docente else "",
            "docente_email": str(row.get(c_email, "")) if c_email else "",
            "resumen": str(row.get(c_resumen, "")) if c_resumen else "",
            "drive_pdf": str(row.get(c_pdf, "")) if c_pdf else "",
            "youtube_url": str(row.get(c_youtube, "")) if c_youtube else "",
            "evaluador_asignado": "",
            "estado_evaluacion": "Pendiente",
            "puntaje": 0,
            "devolucion": ""
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
        users = db.collection("usuarios").where("email", "==", email).where("password", "==", password).get()
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
    st.title(f"📊 Panel de Gestión - {rol.capitalize()}")
    df_proyectos = obtener_proyectos()

    tabs_list = ["📋 Proyectos", "🎟️ Asistencia", "👥 Evaluadores", "📥 Reportes Excel"]
    if rol == "admin":
        tabs_list.insert(0, "📤 Cargar CSV Forms")
        
    tabs = st.tabs(tabs_list)

    # --- TAB EXCLUSIVA ADMIN: CARGA DE CSV ---
    if rol == "admin":
        with tabs[0]:
            st.subheader("Carga Masiva de Respuestas de Forms (.csv / .xlsx)")
            st.info("Subí el archivo descargado de Forms. Mapeará las columnas y estandarizará las escuelas en Firebase.")
            
            archivo_subido = st.file_uploader("Seleccionar archivo CSV o Excel", type=["csv", "xlsx"])
            
            if archivo_subido is not None:
                try:
                    if archivo_subido.name.endswith('.csv'):
                        df_raw = pd.read_csv(archivo_subido)
                    else:
                        df_raw = pd.read_excel(archivo_subido)
                        
                    st.write(f"📁 **Archivo detectado:** `{archivo_subido.name}` con **{len(df_raw)}** filas.")
                    st.dataframe(df_raw.head(3), use_container_width=True)
                    
                    if st.button("🚀 Confirmar e Importar a Firebase", type="primary"):
                        with st.spinner("Procesando e ingresando proyectos a Firebase..."):
                            total_cargados = procesar_e_ingresar_csv(df_raw)
                            st.success(f"¡Se importaron correctamente {total_cargados} proyectos!")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error al leer el archivo: {e}")

    # --- TAB PROYECTOS ---
    idx_proj = 1 if rol == "admin" else 0
    with tabs[idx_proj]:
        st.subheader("Búsqueda y Filtros")
        if not df_proyectos.empty:
            cols_base = ["id_doc", "titulo", "escuela_estandarizada", "distrito", "nivel", "docente_cargo"]
            cols_visibles = cols_base + (["evaluador_asignado", "puntaje", "devolucion", "estado_evaluacion"] if rol == "admin" else ["evaluador_asignado", "estado_evaluacion"])
            
            cols_existentes = [c for c in cols_visibles if c in df_proyectos.columns]
            escuelas = df_proyectos['escuela_estandarizada'].unique() if 'escuela_estandarizada' in df_proyectos.columns else []
            
            esc_sel = st.multiselect("Filtrar por Escuela", escuelas)
            df_mostrar = df_proyectos.copy()
            if esc_sel:
                df_mostrar = df_mostrar[df_mostrar['escuela_estandarizada'].isin(esc_sel)]
                
            st.dataframe(df_mostrar[cols_existentes], use_container_width=True)

    # --- TAB ASISTENCIA ---
    idx_asist = 2 if rol == "admin" else 1
    with tabs[idx_asist]:
        st.subheader("Registro de Asistencia a Capacitaciones")
        if not df_proyectos.empty:
            proj_id = st.selectbox("Seleccionar Proyecto", df_proyectos['id_doc'].tolist())
            docente_nom = st.text_input("Nombre del Docente")
            cap_nom = st.selectbox("Instancia", ["Capacitación 1", "Capacitación 2", "Capacitación 3"])
            pres = st.checkbox("Presente", value=True)
            
            if st.button("Registrar Asistencia"):
                guardar_asistencia(proj_id, docente_nom, cap_nom, pres)
                st.success("Asistencia registrada.")

    # --- TAB EVALUADORES ---
    idx_eval = 3 if rol == "admin" else 2
    with tabs[idx_eval]:
        st.subheader("Asignación de Evaluadores")
        if not df_proyectos.empty:
            proj_id_asig = st.selectbox("Proyecto ID", df_proyectos['id_doc'].tolist(), key="asig_p")
            eval_email = st.text_input("Email del Evaluador")
            
            if st.button("Guardar Asignación"):
                asignar_evaluador(proj_id_asig, eval_email)
                st.success(f"Asignado a {eval_email}")

    # --- TAB REPORTES ---
    idx_rep = 4 if rol == "admin" else 3
    with tabs[idx_rep]:
        st.subheader("Exportar Reporte")
        if not df_proyectos.empty:
            df_export = df_proyectos.copy()
            if rol == "referente":
                df_export = df_export.drop(columns=[c for c in ["puntaje", "devolucion"] if c in df_export.columns])

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_export.to_excel(writer, sheet_name='Proyectos', index=False)
            
            st.download_button(
                label=f"📥 Descargar Excel ({rol.upper()})",
                data=output.getvalue(),
                file_name=f"Reporte_Feria_{rol}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# --- VISTA: EVALUADOR ---
elif rol == "evaluador":
    st.title("📝 Portal del Evaluador")
    proyectos = obtener_proyectos_evaluador(user_email)
    
    if proyectos:
        for p in proyectos:
            with st.expander(f"📌 {p.get('titulo', 'Sin Título')}"):
                st.write(f"**Nivel:** {p.get('nivel', 'N/A')}")
                st.write(f"**Resumen:** {p.get('resumen', 'Sin resumen')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if p.get('drive_pdf'): st.markdown(f"📄 [PDF Informe]({p.get('drive_pdf')})")
                with col2:
                    if p.get('youtube_url'): st.markdown(f"🎬 [Video YouTube]({p.get('youtube_url')})")
                
                st.divider()
                puntaje = st.number_input("Puntaje (0-100)", 0, 100, int(p.get('puntaje', 0)), key=f"p_{p['id_doc']}")
                devolucion = st.text_area("Devolución Pedagógica", str(p.get('devolucion', '')), key=f"d_{p['id_doc']}")
                
                if st.button("Guardar Evaluación", key=f"btn_{p['id_doc']}"):
                    db.collection("proyectos").document(p['id_doc']).update({
                        "puntaje": puntaje,
                        "devolucion": devolucion,
                        "estado_evaluacion": "Evaluado"
                    })
                    st.success("Guardado en Firebase.")
    else:
        st.info("No tenés proyectos asignados.")
