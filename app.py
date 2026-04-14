import streamlit as st
from supabase import create_client, Client
from datetime import datetime, date, timedelta
import pandas as pd
import calendar

# Configurazione pagina
st.set_page_config(
    page_title="Gestione Permessi",
    page_icon="📅",
    layout="wide"
)

# Inizializza Supabase
@st.cache_resource
def init_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = init_supabase()

# Costanti DEFAULT (modificabili dall'utente)
ORE_GIORNO_MEDIO = 7.8
MATURAZIONE_DEFAULT = {
    "FERIE": 14.66,
    "ROL": 2.99,
    "EX FEST": 2.66
}

# Funzioni di utilità
def ore_a_giorni(ore):
    """Converte ore in giorni lavorativi"""
    return round(ore / ORE_GIORNO_MEDIO, 2)

def giorni_a_ore(giorni, giorno_settimana=None):
    """Converte giorni in ore considerando il giorno della settimana"""
    if giorno_settimana == 4:  # Venerdì
        return giorni * 7
    return giorni * 8

def calcola_ore_range(data_inizio, data_fine, ore_per_giorno):
    """Calcola ore totali per un range di date escludendo sabato e domenica"""
    ore_totali = 0
    giorni_utilizzati = []
    current = data_inizio
    
    while current <= data_fine:
        # Salta weekend
        if current.weekday() < 5:  # 0-4 = Lun-Ven
            ore_giorno = ore_per_giorno
            if current.weekday() == 4:  # Venerdì
                ore_giorno = min(ore_per_giorno, 7)
            ore_totali += ore_giorno
            giorni_utilizzati.append((current, ore_giorno))
        current += timedelta(days=1)
    
    return ore_totali, giorni_utilizzati

# Funzioni autenticazione
def registra_utente(email, password, nome):
    """Registra nuovo utente"""
    try:
        # Crea utente in Supabase Auth
        response = supabase.auth.sign_up({
            "email": email,
            "password": password
        })
        
        if response.user:
            # Inserisci dati utente nella tabella users
            supabase.table("users").insert({
                "id": response.user.id,
                "email": email,
                "nome": nome
            }).execute()
            
            # Inserisci configurazione default maturazioni
            for tipo, ore in MATURAZIONE_DEFAULT.items():
                supabase.table("configurazioni").insert({
                    "user_id": response.user.id,
                    "chiave": f"maturazione_{tipo.lower().replace(' ', '_')}",
                    "valore": str(ore)
                }).execute()
            
            return True, "Registrazione completata! Controlla la tua email per verificare l'account."
        return False, "Errore durante la registrazione"
    except Exception as e:
        return False, f"Errore: {str(e)}"

def login_utente(email, password):
    """Login utente"""
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if response.user:
            st.session_state.user_id = response.user.id
            st.session_state.user_email = email
            
            # Recupera nome utente
            user_data = supabase.table("users").select("nome").eq("id", response.user.id).execute()
            if user_data.data:
                st.session_state.user_nome = user_data.data[0]["nome"]
            
            return True
        return False
    except Exception as e:
        st.error(f"Errore login: {str(e)}")
        return False

def logout_utente():
    """Logout utente"""
    supabase.auth.sign_out()
    for key in list(st.session_state.keys()):
        del st.session_state[key]

# Funzioni configurazione
def get_maturazioni_utente(user_id):
    """Recupera maturazioni personalizzate utente"""
    config = supabase.table("configurazioni").select("*").eq("user_id", user_id).execute()
    
    maturazioni = MATURAZIONE_DEFAULT.copy()
    for conf in config.data:
        if conf["chiave"] == "maturazione_ferie":
            maturazioni["FERIE"] = float(conf["valore"])
        elif conf["chiave"] == "maturazione_rol":
            maturazioni["ROL"] = float(conf["valore"])
        elif conf["chiave"] == "maturazione_ex_fest":
            maturazioni["EX FEST"] = float(conf["valore"])
    
    return maturazioni

def aggiorna_maturazione_utente(user_id, tipo_permesso, nuovo_valore):
    """Aggiorna maturazione personalizzata"""
    chiave = f"maturazione_{tipo_permesso.lower().replace(' ', '_')}"
    
    # Verifica se esiste già
    existing = supabase.table("configurazioni").select("id").eq("user_id", user_id).eq("chiave", chiave).execute()
    
    if existing.data:
        # Aggiorna
        supabase.table("configurazioni").update({"valore": str(nuovo_valore)}).eq("user_id", user_id).eq("chiave", chiave).execute()
    else:
        # Inserisci nuovo
        supabase.table("configurazioni").insert({
            "user_id": user_id,
            "chiave": chiave,
            "valore": str(nuovo_valore)
        }).execute()

# Funzioni database
def get_saldo_utente(user_id):
    """Recupera saldo corrente per tipo e anno"""
    movimenti = supabase.table("movimenti").select("*").eq("user_id", user_id).eq("cancellato", False).execute()
    
    saldo = {}
    
    for mov in movimenti.data:
        tipo = mov["tipo_permesso"]
        anno = mov["anno_maturazione"]
        ore = mov["ore"]
        
        key = f"{tipo}_{anno}"
        if key not in saldo:
            saldo[key] = {"tipo": tipo, "anno": anno, "ore": 0}
        
        if mov["tipo_movimento"] in ["MATURAZIONE", "RETTIFICA_POSITIVA", "SALDO_INIZIALE"]:
            saldo[key]["ore"] += ore
        else:  # UTILIZZO, RETRIBUZIONE, RETTIFICA_NEGATIVA
            saldo[key]["ore"] -= ore
    
    return saldo

def inserisci_saldo_iniziale(user_id, tipo_permesso, ore, mese_riferimento, anno_riferimento):
    """Inserisce saldo iniziale per un tipo di permesso"""
    supabase.table("movimenti").insert({
        "user_id": user_id,
        "tipo_permesso": tipo_permesso,
        "tipo_movimento": "SALDO_INIZIALE",
        "ore": ore,
        "data_movimento": f"{anno_riferimento}-{mese_riferimento:02d}-01",
        "anno_maturazione": anno_riferimento,
        "note": f"Saldo iniziale {tipo_permesso} - {calendar.month_name[mese_riferimento]} {anno_riferimento}"
    }).execute()

def aggiungi_maturazione_mensile(user_id, mese, anno, maturazioni_custom=None):
    """Aggiunge maturazione mensile (automatica o personalizzata)"""
    if maturazioni_custom is None:
        maturazioni_custom = get_maturazioni_utente(user_id)
        
    for tipo, ore in maturazioni_custom.items():
        supabase.table("movimenti").insert({
            "user_id": user_id,
            "tipo_permesso": tipo,
            "tipo_movimento": "MATURAZIONE",
            "ore": ore,
            "data_movimento": f"{anno}-{mese:02d}-01",
            "anno_maturazione": anno,
            "note": f"Maturazione {tipo} - {calendar.month_name[mese]} {anno}"
        }).execute()

def inserisci_permesso(user_id, tipo_permesso, data_inizio, data_fine, ore_per_giorno, note=""):
    """Inserisce permesso per range di date"""
    ore_totali, giorni_utilizzati = calcola_ore_range(data_inizio, data_fine, ore_per_giorno)
    
    if ore_totali == 0:
        return False, "Nessun giorno lavorativo selezionato (solo weekend)"
    
    # Verifica disponibilità
    saldo = get_saldo_utente(user_id)
    
    # Scala prima da anni precedenti
    anni_disponibili = sorted([s["anno"] for s in saldo.values() if s["tipo"] == tipo_permesso and s["ore"] > 0])
    
    ore_rimanenti = ore_totali
    dettaglio_scalatura = []
    
    for anno in anni_disponibili:
        key = f"{tipo_permesso}_{anno}"
        if ore_rimanenti <= 0:
            break
            
        ore_disponibili = saldo.get(key, {}).get("ore", 0)
        if ore_disponibili > 0:
            ore_da_scalare = min(ore_rimanenti, ore_disponibili)
            dettaglio_scalatura.append({
                "anno": anno,
                "ore": ore_da_scalare
            })
            ore_rimanenti -= ore_da_scalare
    
    if ore_rimanenti > 0:
        return False, f"Ore insufficienti! Richieste: {ore_totali}h, Disponibili: {ore_totali - ore_rimanenti}h"
    
    # Inserisci movimenti
    for dettaglio in dettaglio_scalatura:
        for data_giorno, ore_giorno in giorni_utilizzati:
            proporzione = ore_giorno / ore_totali
            ore_da_scalare_giorno = round(dettaglio["ore"] * proporzione, 2)
            
            if ore_da_scalare_giorno > 0:
                supabase.table("movimenti").insert({
                    "user_id": user_id,
                    "tipo_permesso": tipo_permesso,
                    "tipo_movimento": "UTILIZZO",
                    "ore": ore_da_scalare_giorno,
                    "data_movimento": data_giorno.isoformat(),
                    "anno_maturazione": dettaglio["anno"],
                    "note": note if note else f"Permesso {tipo_permesso}"
                }).execute()
    
    return True, f"Permesso inserito! Ore utilizzate: {ore_totali}h ({ore_a_giorni(ore_totali)} giorni)"

def get_storico_movimenti(user_id, filtro_tipo=None, filtro_anno=None):
    """Recupera storico completo movimenti"""
    query = supabase.table("movimenti").select("*").eq("user_id", user_id).order("data_movimento", desc=True)
    
    if filtro_tipo:
        query = query.eq("tipo_permesso", filtro_tipo)
    if filtro_anno:
        query = query.eq("anno_maturazione", filtro_anno)
    
    result = query.execute()
    return result.data

def cancella_movimento(movimento_id, user_id):
    """Cancella movimento (soft delete)"""
    supabase.table("movimenti").update({"cancellato": True}).eq("id", movimento_id).eq("user_id", user_id).execute()

def retribuisci_permessi_anno_precedente(user_id, anno_da_retribuire):
    """Retribuisce ROL e EX FEST dell'anno precedente non godute"""
    saldo = get_saldo_utente(user_id)
    
    retribuiti = []
    for key, value in saldo.items():
        if value["anno"] == anno_da_retribuire and value["tipo"] in ["ROL", "EX FEST"] and value["ore"] > 0:
            # Inserisci movimento retribuzione
            supabase.table("movimenti").insert({
                "user_id": user_id,
                "tipo_permesso": value["tipo"],
                "tipo_movimento": "RETRIBUZIONE",
                "ore": value["ore"],
                "data_movimento": date.today().isoformat(),
                "anno_maturazione": anno_da_retribuire,
                "note": f"Retribuzione {value['tipo']} {anno_da_retribuire} (non godute)"
            }).execute()
            
            retribuiti.append(f"{value['tipo']}: {value['ore']}h ({ore_a_giorni(value['ore'])} gg)")
    
    return retribuiti

# UI - Login/Registrazione
def show_login():
    st.title("🔐 Gestione Permessi")
    
    tab1, tab2 = st.tabs(["Login", "Registrazione"])
    
    with tab1:
        st.subheader("Accedi")
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        
        if st.button("Accedi", type="primary"):
            if login_utente(email, password):
                st.success("Login effettuato!")
                st.rerun()
            else:
                st.error("Credenziali errate")
    
    with tab2:
        st.subheader("Crea Account")
        nome = st.text_input("Nome completo")
        email_reg = st.text_input("Email", key="reg_email")
        password_reg = st.text_input("Password", type="password", key="reg_password")
        password_conf = st.text_input("Conferma Password", type="password")
        
        if st.button("Registrati"):
            if password_reg != password_conf:
                st.error("Le password non coincidono")
            elif len(password_reg) < 6:
                st.error("La password deve essere di almeno 6 caratteri")
            else:
                success, message = registra_utente(email_reg, password_reg, nome)
                if success:
                    st.success(message)
                else:
                    st.error(message)

# UI - Setup iniziale
def show_setup_iniziale():
    """Mostra setup per nuovo utente"""
    st.title("🚀 Configurazione Iniziale")
    st.write("Benvenuto! Configura il tuo saldo iniziale per iniziare a usare l'app.")
    
    # Verifica se ha già movimenti
    movimenti = supabase.table("movimenti").select("id").eq("user_id", st.session_state.user_id).limit(1).execute()
    
    if movimenti.data:
        # Ha già movimenti, vai al dashboard normale
        st.session_state.setup_completato = True
        st.rerun()
        return
    
    st.subheader("📊 Inserisci i tuoi saldi attuali")
    
    col1, col2 = st.columns(2)
    
    with col1:
        mese_rif = st.selectbox("Mese di riferimento", range(1, 13), 
                               format_func=lambda x: calendar.month_name[x],
                               index=date.today().month - 1)
        anno_rif = st.number_input("Anno di riferimento", 
                                 min_value=2020, max_value=2030, 
                                 value=date.today().year)
    
    with col2:
        st.info(f"💡 **Come funziona:**\n\nInserisci le ore che hai **a fine {calendar.month_name[mese_rif]} {anno_rif}**.\n\nDal mese successivo inizieranno le maturazioni automatiche!")
    
    st.subheader("💰 Saldi attuali")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.write("**🏖️ FERIE**")
        ferie_ore = st.number_input("Ore FERIE", min_value=0.0, max_value=500.0, value=0.0, step=0.5, key="ferie")
        st.caption(f"≈ {ore_a_giorni(ferie_ore):.1f} giorni")
    
    with col2:
        st.write("**⏰ ROL**")
        rol_ore = st.number_input("Ore ROL", min_value=0.0, max_value=200.0, value=0.0, step=0.25, key="rol")
        st.caption(f"≈ {ore_a_giorni(rol_ore):.1f} giorni")
    
    with col3:
        st.write("**🎉 EX FEST**")
        ex_ore = st.number_input("Ore EX FEST", min_value=0.0, max_value=200.0, value=0.0, step=0.25, key="ex")
        st.caption(f"≈ {ore_a_giorni(ex_ore):.1f} giorni")
    
    if st.button("✅ Conferma Setup Iniziale", type="primary"):
        # Inserisci saldi iniziali
        try:
            if ferie_ore > 0:
                inserisci_saldo_iniziale(st.session_state.user_id, "FERIE", ferie_ore, mese_rif, anno_rif)
            if rol_ore > 0:
                inserisci_saldo_iniziale(st.session_state.user_id, "ROL", rol_ore, mese_rif, anno_rif)
            if ex_ore > 0:
                inserisci_saldo_iniziale(st.session_state.user_id, "EX FEST", ex_ore, mese_rif, anno_rif)
            
            st.session_state.setup_completato = True
            st.success("✅ Setup completato! Benvenuto!")
            st.rerun()
            
        except Exception as e:
            st.error(f"Errore durante il setup: {str(e)}")
    
    st.divider()
    if st.button("⏭️ Salta Setup (inserirò dopo)"):
        st.session_state.setup_completato = True
        st.rerun()

# UI - Dashboard principale
def show_dashboard():
    st.title(f"📅 Benvenuto, {st.session_state.user_nome}!")
    
    if st.button("Logout", key="logout_btn"):
        logout_utente()
        st.rerun()
    
    # Recupera saldo
    saldo = get_saldo_utente(st.session_state.user_id)
    
    # Organizza saldo per tipo
    saldo_per_tipo = {"FERIE": {}, "ROL": {}, "EX FEST": {}}
    for key, value in saldo.items():
        tipo = value["tipo"]
        anno = value["anno"]
        ore = value["ore"]
        if ore > 0:
            saldo_per_tipo[tipo][anno] = ore
    
    # Dashboard saldo
    st.header("💰 Saldo Attuale")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.subheader("🏖️ FERIE")
        totale_ferie = sum(saldo_per_tipo["FERIE"].values())
        st.metric("Totale", f"{totale_ferie:.2f}h", f"{ore_a_giorni(totale_ferie):.1f} gg")
        for anno, ore in sorted(saldo_per_tipo["FERIE"].items()):
            st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
    
    with col2:
        st.subheader("⏰ ROL")
        totale_rol = sum(saldo_per_tipo["ROL"].values())
        st.metric("Totale", f"{totale_rol:.2f}h", f"{ore_a_giorni(totale_rol):.1f} gg")
        for anno, ore in sorted(saldo_per_tipo["ROL"].items()):
            st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
    
    with col3:
        st.subheader("🎉 EX FEST")
        totale_ex = sum(saldo_per_tipo["EX FEST"].values())
        st.metric("Totale", f"{totale_ex:.2f}h", f"{ore_a_giorni(totale_ex):.1f} gg")
        for anno, ore in sorted(saldo_per_tipo["EX FEST"].items()):
            st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
    
    # Alert retribuzione
    oggi = date.today()
    if oggi.month == 3:
        anno_precedente = oggi.year - 1
        rol_anno_prec = saldo_per_tipo["ROL"].get(anno_precedente, 0)
        ex_anno_prec = saldo_per_tipo["EX FEST"].get(anno_precedente, 0)
        
        if rol_anno_prec > 0 or ex_anno_prec > 0:
            st.warning(f"⚠️ Attenzione! Hai ROL/EX FEST del {anno_precedente} da retribuire!")
            if st.button("💰 Retribuisci permessi anno precedente"):
                retribuiti = retribuisci_permessi_anno_precedente(st.session_state.user_id, anno_precedente)
                if retribuiti:
                    st.success("Retribuiti: " + ", ".join(retribuiti))
                    st.rerun()
    
    # Tabs funzionalità
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["➕ Inserisci Permesso", "📊 Storico", "🔧 Gestione", "📈 Maturazioni", "⚙️ Configurazione"])
    
    with tab1:
        show_inserisci_permesso()
    
    with tab2:
        show_storico()
    
    with tab3:
        show_gestione()
    
    with tab4:
        show_maturazioni()
    
    with tab5:
        show_configurazione()

def show_inserisci_permesso():
    st.subheader("➕ Inserisci Permesso")
    
    col1, col2 = st.columns(2)
    
    with col1:
        tipo_permesso = st.selectbox("Tipo permesso", ["FERIE", "ROL", "EX FEST"])
        data_inizio = st.date_input("Data inizio", value=date.today())
        data_fine = st.date_input("Data fine", value=date.today())
    
    with col2:
        ore_per_giorno = st.number_input("Ore per giorno", min_value=0.5, max_value=8.0, value=8.0, step=0.5)
        note = st.text_area("Note (opzionale)")
    
    if data_inizio > data_fine:
        st.error("La data di inizio deve essere precedente alla data di fine")
    else:
        ore_totali, giorni = calcola_ore_range(data_inizio, data_fine, ore_per_giorno)
        st.info(f"📊 Ore totali: {ore_totali}h ({ore_a_giorni(ore_totali):.1f} giorni) - Giorni lavorativi: {len(giorni)}")
        
        if st.button("Inserisci Permesso", type="primary"):
            success, message = inserisci_permesso(
                st.session_state.user_id,
                tipo_permesso,
                data_inizio,
                data_fine,
                ore_per_giorno,
                note
            )
            
            if success:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

def show_storico():
    st.subheader("📊 Storico Movimenti")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        filtro_tipo = st.selectbox("Filtra per tipo", ["Tutti", "FERIE", "ROL", "EX FEST"])
    
    with col2:
        anni_disponibili = list(range(date.today().year - 2, date.today().year + 2))
        filtro_anno = st.selectbox("Filtra per anno maturazione", ["Tutti"] + anni_disponibili)
    
    with col3:
        mostra_cancellati = st.checkbox("Mostra cancellati")
    
    # Recupera movimenti
    movimenti = get_storico_movimenti(
        st.session_state.user_id,
        None if filtro_tipo == "Tutti" else filtro_tipo,
        None if filtro_anno == "Tutti" else filtro_anno
    )
    
    if not mostra_cancellati:
        movimenti = [m for m in movimenti if not m.get("cancellato", False)]
    
    if movimenti:
        df = pd.DataFrame(movimenti)
        df["giorni"] = df["ore"].apply(ore_a_giorni)
        df_display = df[["data_movimento", "tipo_permesso", "tipo_movimento", "ore", "giorni", "anno_maturazione", "note", "cancellato"]].copy()
        df_display.columns = ["Data", "Tipo", "Movimento", "Ore", "Giorni", "Anno Matur.", "Note", "Cancellato"]
        
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        # Download CSV
        csv = df_display.to_csv(index=False)
        st.download_button(
            "📥 Scarica CSV",
            csv,
            "storico_permessi.csv",
            "text/csv"
        )
    else:
        st.info("Nessun movimento trovato")

def show_gestione():
    st.subheader("🔧 Gestione Movimenti")
    
    st.write("**Cancella movimenti inseriti per errore**")
    
    # Mostra movimenti recenti non cancellati (tutti i tipi)
    movimenti = supabase.table("movimenti").select("*").eq("user_id", st.session_state.user_id).eq("cancellato", False).order("data_movimento", desc=True).limit(30).execute()
    
    if movimenti.data:
        for mov in movimenti.data:
            col1, col2, col3, col4, col5 = st.columns([2, 1.5, 1.5, 3, 1])
            
            with col1:
                st.write(f"📅 {mov['data_movimento']}")
            
            with col2:
                st.write(f"**{mov['tipo_permesso']}**")
            
            with col3:
                tipo_mov = mov['tipo_movimento']
                if tipo_mov == "UTILIZZO":
                    emoji = "❌"
                elif tipo_mov == "MATURAZIONE":
                    emoji = "➕"
                elif tipo_mov == "SALDO_INIZIALE":
                    emoji = "🔢"
                elif tipo_mov == "RETRIBUZIONE":
                    emoji = "💰"
                else:
                    emoji = "🔄"
                st.write(f"{emoji} {tipo_mov}")
            
            with col4:
                st.write(f"{mov['ore']}h ({ore_a_giorni(mov['ore']):.1f} gg) - {mov['note']}")
            
            with col5:
                if st.button("🗑️", key=f"del_{mov['id']}"):
                    cancella_movimento(mov['id'], st.session_state.user_id)
                    st.success("Movimento cancellato")
                    st.rerun()
    else:
        st.info("Nessun movimento da gestire")

def show_maturazioni():
    st.subheader("📈 Gestisci Maturazioni")
    
    # Recupera maturazioni personalizzate
    maturazioni = get_maturazioni_utente(st.session_state.user_id)
    
    tab1, tab2 = st.tabs(["➕ Aggiungi Maturazione", "🔢 Saldo Iniziale"])
    
    with tab1:
        st.write("**Aggiungi maturazione mensile**")
        
        col1, col2 = st.columns(2)
        
        with col1:
            mese = st.selectbox("Mese", range(1, 13), format_func=lambda x: calendar.month_name[x])
            anno = st.number_input("Anno", min_value=2020, max_value=2030, value=date.today().year)
        
        with col2:
            st.info(f"🔢 **Maturazione per {calendar.month_name[mese]} {anno}:**\n- FERIE: {maturazioni['FERIE']}h\n- ROL: {maturazioni['ROL']}h\n- EX FEST: {maturazioni['EX FEST']}h")
        
        if st.button("Aggiungi Maturazione"):
            aggiungi_maturazione_mensile(st.session_state.user_id, mese, anno, maturazioni)
            st.success(f"✅ Maturazione {calendar.month_name[mese]} {anno} aggiunta!")
            st.rerun()
    
    with tab2:
        st.write("**Inserisci saldo per un mese specifico**")
        st.caption("Utile per correzioni o per aggiungere saldi che avevi prima dell'app")
        
        col1, col2 = st.columns(2)
        
        with col1:
            tipo_saldo = st.selectbox("Tipo permesso", ["FERIE", "ROL", "EX FEST"], key="saldo_tipo")
            ore_saldo = st.number_input("Ore", min_value=0.0, max_value=500.0, value=0.0, step=0.5, key="saldo_ore")
        
        with col2:
            mese_saldo = st.selectbox("Mese", range(1, 13), format_func=lambda x: calendar.month_name[x], key="saldo_mese")
            anno_saldo = st.number_input("Anno", min_value=2020, max_value=2030, value=date.today().year, key="saldo_anno")
        
        st.caption(f"≈ {ore_a_giorni(ore_saldo):.1f} giorni")
        
        if st.button("Inserisci Saldo"):
            if ore_saldo > 0:
                inserisci_saldo_iniziale(st.session_state.user_id, tipo_saldo, ore_saldo, mese_saldo, anno_saldo)
                st.success(f"✅ Saldo {tipo_saldo} inserito: {ore_saldo}h per {calendar.month_name[mese_saldo]} {anno_saldo}!")
                st.rerun()
            else:
                st.error("Inserisci un valore maggiore di 0")

def show_configurazione():
    st.subheader("⚙️ Configurazione")
    
    # Recupera maturazioni attuali
    maturazioni = get_maturazioni_utente(st.session_state.user_id)
    
    st.write("**🔧 Maturazioni Mensili Personalizzate**")
    st.caption("Modifica i valori se cambia il tuo contratto")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.write("**🏖️ FERIE (ore/mese)**")
        ferie_val = st.number_input("FERIE", min_value=0.0, max_value=50.0, 
                                   value=maturazioni["FERIE"], step=0.01, key="conf_ferie")
        st.caption(f"≈ {ore_a_giorni(ferie_val):.2f} gg/mese")
    
    with col2:
        st.write("**⏰ ROL (ore/mese)**")
        rol_val = st.number_input("ROL", min_value=0.0, max_value=20.0, 
                                 value=maturazioni["ROL"], step=0.01, key="conf_rol")
        st.caption(f"≈ {ore_a_giorni(rol_val):.2f} gg/mese")
    
    with col3:
        st.write("**🎉 EX FEST (ore/mese)**")
        ex_val = st.number_input("EX FEST", min_value=0.0, max_value=20.0, 
                                value=maturazioni["EX FEST"], step=0.01, key="conf_ex")
        st.caption(f"≈ {ore_a_giorni(ex_val):.2f} gg/mese")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        if st.button("💾 Salva Configurazione", type="primary"):
            try:
                aggiorna_maturazione_utente(st.session_state.user_id, "FERIE", ferie_val)
                aggiorna_maturazione_utente(st.session_state.user_id, "ROL", rol_val)
                aggiorna_maturazione_utente(st.session_state.user_id, "EX FEST", ex_val)
                
                st.success("✅ Configurazione salvata!")
                st.rerun()
                
            except Exception as e:
                st.error(f"Errore: {str(e)}")
    
    with col2:
        if st.button("🔄 Ripristina Valori Default"):
            aggiorna_maturazione_utente(st.session_state.user_id, "FERIE", MATURAZIONE_DEFAULT["FERIE"])
            aggiorna_maturazione_utente(st.session_state.user_id, "ROL", MATURAZIONE_DEFAULT["ROL"])
            aggiorna_maturazione_utente(st.session_state.user_id, "EX FEST", MATURAZIONE_DEFAULT["EX FEST"])
            
            st.success("✅ Valori default ripristinati!")
            st.rerun()
    
    st.divider()
    
    st.write("**📊 Valori Default**")
    st.caption(f"FERIE: {MATURAZIONE_DEFAULT['FERIE']}h/mese | ROL: {MATURAZIONE_DEFAULT['ROL']}h/mese | EX FEST: {MATURAZIONE_DEFAULT['EX FEST']}h/mese")

# Main
def main():
    if "user_id" not in st.session_state:
        show_login()
    elif "setup_completato" not in st.session_state:
        show_setup_iniziale()
    else:
        show_dashboard()

if __name__ == "__main__":
    main()
