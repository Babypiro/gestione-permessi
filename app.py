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


# Funzioni previsione
def calcola_saldo_mese(user_id, mese, anno):
    """Calcola saldo a fine mese specifico (storico o previsione)"""
    # Recupera tutti i movimenti fino alla fine del mese specificato
    data_fine_mese = date(anno, mese, calendar.monthrange(anno, mese)[1])
    
    movimenti = supabase.table("movimenti").select("*").eq("user_id", user_id).eq("cancellato", False).lte("data_movimento", data_fine_mese.isoformat()).execute()
    
    saldo = {}
    
    for mov in movimenti.data:
        tipo = mov["tipo_permesso"]
        anno_mat = mov["anno_maturazione"]
        ore = mov["ore"]
        
        key = f"{tipo}_{anno_mat}"
        if key not in saldo:
            saldo[key] = {"tipo": tipo, "anno": anno_mat, "ore": 0}
        
        if mov["tipo_movimento"] in ["MATURAZIONE", "RETTIFICA_POSITIVA", "SALDO_INIZIALE"]:
            saldo[key]["ore"] += ore
        else:
            saldo[key]["ore"] -= ore
    
    return saldo

def genera_previsione_mese(user_id, mese_target, anno_target):
    """
    SALDO EFFETTIVO: considera TUTTI i permessi futuri (anche dopo il mese target)
    Mostra: "A fine mese X, considerando TUTTO quello che ho prenotato, avrò Y ore"
    """
    data_fine_mese = date(anno_target, mese_target, calendar.monthrange(anno_target, mese_target)[1])
    oggi = date.today()
    
    # Recupera TUTTI i movimenti (anche futuri)
    movimenti = supabase.table("movimenti").select("*").eq("user_id", user_id).eq("cancellato", False).execute()
    
    saldo = {}
    
    # Calcola saldo considerando TUTTI i movimenti
    for mov in movimenti.data:
        tipo = mov["tipo_permesso"]
        anno_mat = mov["anno_maturazione"]
        ore = mov["ore"]
        
        key = f"{tipo}_{anno_mat}"
        if key not in saldo:
            saldo[key] = {"tipo": tipo, "anno": anno_mat, "ore": 0}
        
        if mov["tipo_movimento"] in ["MATURAZIONE", "RETTIFICA_POSITIVA", "SALDO_INIZIALE"]:
            saldo[key]["ore"] += ore
        else:
            saldo[key]["ore"] -= ore  # Include TUTTI gli utilizzi futuri
    
    # Se è mese futuro, aggiungi maturazioni mancanti fino al mese target
    if data_fine_mese > oggi:
        maturazioni = get_maturazioni_utente(user_id)
        
        # Trova ultimo mese con maturazioni nel database
        ultimo_movimento = supabase.table("movimenti").select("data_movimento").eq("user_id", user_id).eq("tipo_movimento", "MATURAZIONE").eq("cancellato", False).order("data_movimento", desc=True).limit(1).execute()
        
        if ultimo_movimento.data:
            ultima_data = datetime.fromisoformat(ultimo_movimento.data[0]["data_movimento"]).date()
            current = date(ultima_data.year, ultima_data.month, 1)
        else:
            current = date(oggi.year, oggi.month, 1)
        
        # Aggiungi maturazioni fino al mese target
        while current <= data_fine_mese:
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
            
            if current <= data_fine_mese:
                # Verifica se esiste già
                exists = any(
                    mov for mov in movimenti.data 
                    if mov["tipo_movimento"] == "MATURAZIONE" 
                    and datetime.fromisoformat(mov["data_movimento"]).date().year == current.year 
                    and datetime.fromisoformat(mov["data_movimento"]).date().month == current.month
                )
                
                if not exists:
                    for tipo, ore_mensili in maturazioni.items():
                        key = f"{tipo}_{current.year}"
                        if key not in saldo:
                            saldo[key] = {"tipo": tipo, "anno": current.year, "ore": 0}
                        saldo[key]["ore"] += ore_mensili
    
    return saldo

def genera_previsione_solo_maturazioni(user_id, mese_target, anno_target):
    """
    SALDO PREVISTO: considera SOLO permessi PRIMA del mese target (incluso)
    Mostra: "A fine mese X, se non prenoto altro da oggi, avrò Y ore"
    """
    data_fine_mese = date(anno_target, mese_target, calendar.monthrange(anno_target, mese_target)[1])
    oggi = date.today()
    
    # Recupera TUTTI i movimenti (non filtro nel database)
    movimenti_all = supabase.table("movimenti").select("*").eq("user_id", user_id).eq("cancellato", False).execute()
    
    saldo = {}
    
    # Filtra e calcola saldo: SOLO movimenti fino alla fine del mese target
    for mov in movimenti_all.data:
        data_mov = datetime.fromisoformat(mov["data_movimento"]).date()
        
        # IMPORTANTE: Considera solo movimenti FINO alla fine del mese target
        if data_mov <= data_fine_mese:
            tipo = mov["tipo_permesso"]
            anno_mat = mov["anno_maturazione"]
            ore = mov["ore"]
            
            key = f"{tipo}_{anno_mat}"
            if key not in saldo:
                saldo[key] = {"tipo": tipo, "anno": anno_mat, "ore": 0}
            
            if mov["tipo_movimento"] in ["MATURAZIONE", "RETTIFICA_POSITIVA", "SALDO_INIZIALE"]:
                saldo[key]["ore"] += ore
            else:
                saldo[key]["ore"] -= ore  # Scala utilizzi fino al mese target (incluso)
    
    # Se è mese futuro, aggiungi maturazioni mancanti
    if data_fine_mese > oggi:
        maturazioni = get_maturazioni_utente(user_id)
        
        # Trova ultimo mese con maturazioni
        maturazioni_esistenti = [
            datetime.fromisoformat(mov["data_movimento"]).date()
            for mov in movimenti_all.data
            if mov["tipo_movimento"] == "MATURAZIONE"
        ]
        
        if maturazioni_esistenti:
            ultima_data = max(maturazioni_esistenti)
            current = date(ultima_data.year, ultima_data.month, 1)
        else:
            current = date(oggi.year, oggi.month, 1)
        
        # Aggiungi maturazioni fino al mese target
        while current <= data_fine_mese:
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
            
            if current <= data_fine_mese:
                # Verifica se esiste già maturazione per questo mese
                exists = any(
                    datetime.fromisoformat(mov["data_movimento"]).date().year == current.year 
                    and datetime.fromisoformat(mov["data_movimento"]).date().month == current.month
                    and mov["tipo_movimento"] == "MATURAZIONE"
                    for mov in movimenti_all.data
                )
                
                if not exists:
                    for tipo, ore_mensili in maturazioni.items():
                        key = f"{tipo}_{current.year}"
                        if key not in saldo:
                            saldo[key] = {"tipo": tipo, "anno": current.year, "ore": 0}
                        saldo[key]["ore"] += ore_mensili
    
    return saldo

def get_storico_mensile(user_id, num_mesi=12):
    """Recupera storico saldi mensili per grafico"""
    oggi = date.today()
    storico = []
    
    for i in range(num_mesi, 0, -1):
        # Calcola mese
        mese_diff = oggi.month - i
        anno_calc = oggi.year
        mese_calc = mese_diff
        
        if mese_calc <= 0:
            mese_calc += 12
            anno_calc -= 1
        
        saldo = calcola_saldo_mese(user_id, mese_calc, anno_calc)
        
        totali = {"FERIE": 0, "ROL": 0, "EX FEST": 0}
        for key, value in saldo.items():
            if value["ore"] > 0:
                totali[value["tipo"]] += value["ore"]
        
        storico.append({
            "mese": mese_calc,
            "anno": anno_calc,
            "data": date(anno_calc, mese_calc, 1),
            "ferie": totali["FERIE"],
            "rol": totali["ROL"],
            "ex_fest": totali["EX FEST"]
        })
    
    # Aggiungi mese corrente
    saldo_corrente = get_saldo_utente(user_id)
    totali_corrente = {"FERIE": 0, "ROL": 0, "EX FEST": 0}
    for key, value in saldo_corrente.items():
        if value["ore"] > 0:
            totali_corrente[value["tipo"]] += value["ore"]
    
    storico.append({
        "mese": oggi.month,
        "anno": oggi.year,
        "data": date(oggi.year, oggi.month, 1),
        "ferie": totali_corrente["FERIE"],
        "rol": totali_corrente["ROL"],
        "ex_fest": totali_corrente["EX FEST"]
    })
    
    return storico

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
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["➕ Inserisci Permesso", "📊 Storico", "🔧 Gestione", "📈 Maturazioni", "📈 Previsione", "⚙️ Configurazione"])
    
    with tab1:
        show_inserisci_permesso()
    
    with tab2:
        show_storico()
    
    with tab3:
        show_gestione()
    
    with tab4:
        show_maturazioni()
    
    with tab6:
        show_configurazione()
    
    with tab5:
        show_previsione()


def show_inserisci_permesso():
    st.subheader("➕ Inserisci Permesso")
    
    if "giorni_permessi" not in st.session_state:
        st.session_state.giorni_permessi = {}
    
    st.write("**1️⃣ Seleziona periodo**")
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        data_inizio = st.date_input("Data inizio", value=date.today(), key="data_inizio_perm")
    with col2:
        data_fine = st.date_input("Data fine", value=date.today(), key="data_fine_perm")
    with col3:
        if st.button("📅 Carica giorni", type="primary"):
            if data_inizio > data_fine:
                st.error("Data inizio deve essere prima della data fine!")
            else:
                current = data_inizio
                aggiunti = 0
                while current <= data_fine:
                    if current.weekday() < 5:
                        if current not in st.session_state.giorni_permessi:
                            ore_default = 7 if current.weekday() == 4 else 8
                            st.session_state.giorni_permessi[current] = {
                                "tipo": "FERIE",
                                "ore": ore_default,
                                "note": ""
                            }
                            aggiunti += 1
                    current += timedelta(days=1)
                if aggiunti > 0:
                    st.success(f"✅ {aggiunti} giorni lavorativi aggiunti!")
                    st.rerun()
                else:
                    st.info("Tutti i giorni sono già stati aggiunti o sono weekend")
    
    st.divider()
    
    if st.session_state.giorni_permessi:
        st.write("**2️⃣ Modifica permessi per ogni giorno**")
        
        giorni_ordinati = sorted(st.session_state.giorni_permessi.items())
        
        for data_g, info in giorni_ordinati:
            col_data, col_tipo, col_ore, col_note, col_del = st.columns([2, 1.5, 1, 2, 0.5])
            
            with col_data:
                giorno_nome = calendar.day_name[data_g.weekday()]
                st.write(f"📅 **{data_g.strftime('%d/%m/%Y')}** ({giorno_nome[:3]})")
            
            with col_tipo:
                tipo_nuovo = st.selectbox(
                    "Tipo",
                    ["FERIE", "ROL", "EX FEST"],
                    index=["FERIE", "ROL", "EX FEST"].index(info["tipo"]),
                    key=f"tipo_{data_g}",
                    label_visibility="collapsed"
                )
                if tipo_nuovo != info["tipo"]:
                    st.session_state.giorni_permessi[data_g]["tipo"] = tipo_nuovo
                    st.rerun()
            
            with col_ore:
                ore_nuove = st.number_input(
                    "Ore",
                    min_value=0.5,
                    max_value=8.0,
                    value=float(info["ore"]),
                    step=0.5,
                    key=f"ore_{data_g}",
                    label_visibility="collapsed"
                )
                if ore_nuove != info["ore"]:
                    st.session_state.giorni_permessi[data_g]["ore"] = ore_nuove
                    st.rerun()
            
            with col_note:
                note_nuove = st.text_input(
                    "Note",
                    value=info["note"],
                    key=f"note_{data_g}",
                    placeholder="Note opzionali",
                    label_visibility="collapsed"
                )
                if note_nuove != info["note"]:
                    st.session_state.giorni_permessi[data_g]["note"] = note_nuove
            
            with col_del:
                if st.button("🗑️", key=f"del_{data_g}"):
                    del st.session_state.giorni_permessi[data_g]
                    st.rerun()
        
        st.divider()
        
        totali = {"FERIE": 0, "ROL": 0, "EX FEST": 0}
        for info in st.session_state.giorni_permessi.values():
            totali[info["tipo"]] += info["ore"]
        
        st.write("**📊 Riepilogo totali:**")
        col1, col2, col3 = st.columns(3)
        with col1:
            if totali["FERIE"] > 0:
                st.metric("🏖️ FERIE", f"{totali['FERIE']}h", f"{ore_a_giorni(totali['FERIE']):.1f} gg")
        with col2:
            if totali["ROL"] > 0:
                st.metric("⏰ ROL", f"{totali['ROL']}h", f"{ore_a_giorni(totali['ROL']):.1f} gg")
        with col3:
            if totali["EX FEST"] > 0:
                st.metric("🎉 EX FEST", f"{totali['EX FEST']}h", f"{ore_a_giorni(totali['EX FEST']):.1f} gg")
        
        st.divider()
        
        col_conferma, col_cancella = st.columns(2)
        with col_conferma:
            if st.button("✅ Conferma e Inserisci Tutti", type="primary", use_container_width=True):
                errori = []
                successi = 0
                for data_g, info in st.session_state.giorni_permessi.items():
                    note_finale = info["note"] if info["note"] else f"Permesso {info['tipo']}"
                    success, message = inserisci_permesso(
                        st.session_state.user_id,
                        info["tipo"],
                        data_g,
                        data_g,
                        info["ore"],
                        note_finale
                    )
                    if success:
                        successi += 1
                    else:
                        errori.append(f"{data_g.strftime('%d/%m')}: {message}")
                if errori:
                    st.error("⚠️ Alcuni permessi non sono stati inseriti:\n" + "\n".join(errori))
                if successi > 0:
                    st.success(f"✅ {successi} permessi inseriti con successo!")
                    st.session_state.giorni_permessi = {}
                    st.rerun()
        with col_cancella:
            if st.button("🗑️ Svuota tutto", use_container_width=True):
                st.session_state.giorni_permessi = {}
                st.rerun()
    else:
        st.info("👆 Seleziona un periodo e clicca 'Carica giorni' per iniziare")

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



def genera_previsione_solo_maturazioni(user_id, mese_target, anno_target):
    """Genera previsione considerando SOLO maturazioni (ignora utilizzi futuri)"""
    data_fine_mese = date(anno_target, mese_target, calendar.monthrange(anno_target, mese_target)[1])
    oggi = date.today()
    
    # Recupera movimenti FINO AD OGGI (non futuri)
    movimenti = supabase.table("movimenti").select("*").eq("user_id", user_id).eq("cancellato", False).lte("data_movimento", oggi.isoformat()).execute()
    
    saldo = {}
    
    # Calcola saldo attuale
    for mov in movimenti.data:
        tipo = mov["tipo_permesso"]
        anno_mat = mov["anno_maturazione"]
        ore = mov["ore"]
        
        key = f"{tipo}_{anno_mat}"
        if key not in saldo:
            saldo[key] = {"tipo": tipo, "anno": anno_mat, "ore": 0}
        
        if mov["tipo_movimento"] in ["MATURAZIONE", "RETTIFICA_POSITIVA", "SALDO_INIZIALE"]:
            saldo[key]["ore"] += ore
        else:
            saldo[key]["ore"] -= ore
    
    # Se è mese futuro, aggiungi maturazioni
    if data_fine_mese > oggi:
        maturazioni = get_maturazioni_utente(user_id)
        current = date(oggi.year, oggi.month, 1)
        
        while current <= data_fine_mese:
            # Vai al mese successivo
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
            
            if current <= data_fine_mese:
                # Aggiungi maturazioni per questo mese
                for tipo, ore_mensili in maturazioni.items():
                    key = f"{tipo}_{current.year}"
                    if key not in saldo:
                        saldo[key] = {"tipo": tipo, "anno": current.year, "ore": 0}
                    saldo[key]["ore"] += ore_mensili
    
    return saldo

def show_previsione():
    st.subheader("📈 Previsione Saldi Futuri")
    oggi = date.today()
    if "prev_mese" not in st.session_state:
        st.session_state.prev_mese = oggi.month
    if "prev_anno" not in st.session_state:
        st.session_state.prev_anno = oggi.year
    if "layout_previsione" not in st.session_state:
        st.session_state.layout_previsione = "affiancato"
    
    st.write("**Seleziona mese e anno**")
    col1, col2, col3, col4 = st.columns([1.5, 1, 1, 1.5])
    with col1:
        mese_sel = st.selectbox("📅 Mese", range(1, 13), format_func=lambda x: calendar.month_name[x], index=st.session_state.prev_mese - 1)
    with col2:
        anno_sel = st.number_input("📆 Anno", min_value=2020, max_value=2035, value=st.session_state.prev_anno, step=1)
    with col3:
        if st.button("🔍 Visualizza", type="primary"):
            st.session_state.prev_mese = mese_sel
            st.session_state.prev_anno = anno_sel
            st.rerun()
    with col4:
        col_oggi, col_layout = st.columns(2)
        with col_oggi:
            if st.button("📍 Oggi"):
                st.session_state.prev_mese = oggi.month
                st.session_state.prev_anno = oggi.year
                st.rerun()
        with col_layout:
            if st.button("🔄 Layout"):
                st.session_state.layout_previsione = "riga" if st.session_state.layout_previsione == "affiancato" else "affiancato"
                st.rerun()
    
    st.divider()
    data_selezionata = date(st.session_state.prev_anno, st.session_state.prev_mese, 1)
    data_oggi = date(oggi.year, oggi.month, 1)
    is_futuro = data_selezionata > data_oggi
    is_presente = data_selezionata == data_oggi
    
    if data_selezionata < data_oggi:
        st.info(f"**📊 Saldo Storico** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    elif is_presente:
        st.info(f"**📍 Saldo Attuale** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    else:
        st.warning(f"**🔮 Previsione Futuro** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    
    saldo_effettivo = genera_previsione_mese(st.session_state.user_id, st.session_state.prev_mese, st.session_state.prev_anno)
    if is_futuro:
        saldo_previsto = genera_previsione_solo_maturazioni(st.session_state.user_id, st.session_state.prev_mese, st.session_state.prev_anno)
    
    def organizza_saldo(saldo):
        saldo_per_tipo = {"FERIE": {}, "ROL": {}, "EX FEST": {}}
        for key, value in saldo.items():
            if value["ore"] > 0:
                saldo_per_tipo[value["tipo"]][value["anno"]] = value["ore"]
        return saldo_per_tipo
    
    saldo_eff_per_tipo = organizza_saldo(saldo_effettivo)
    if is_futuro:
        saldo_prev_per_tipo = organizza_saldo(saldo_previsto)
    
    if is_futuro:
        if st.session_state.layout_previsione == "affiancato":
            col_eff, col_prev = st.columns(2)
            with col_eff:
                st.markdown("### 📊 Saldo Effettivo")
                st.caption("(include ferie già prenotate)")
                st.divider()
                for tipo, emoji in [("FERIE", "🏖️"), ("ROL", "⏰"), ("EX FEST", "🎉")]:
                    totale = sum(saldo_eff_per_tipo[tipo].values())
                    st.metric(f"{emoji} {tipo}", f"{totale:.2f}h", f"{ore_a_giorni(totale):.1f} gg")
                    for anno, ore in sorted(saldo_eff_per_tipo[tipo].items()):
                        st.caption(f"  {anno}: {ore:.2f}h")
            with col_prev:
                st.markdown("### 🔮 Saldo Previsto")
                st.caption("(solo maturazioni)")
                st.divider()
                for tipo, emoji in [("FERIE", "🏖️"), ("ROL", "⏰"), ("EX FEST", "🎉")]:
                    totale_prev = sum(saldo_prev_per_tipo[tipo].values())
                    totale_eff = sum(saldo_eff_per_tipo[tipo].values())
                    diff = totale_prev - totale_eff
                    st.metric(f"{emoji} {tipo}", f"{totale_prev:.2f}h", f"{diff:+.2f}h")
                    for anno, ore in sorted(saldo_prev_per_tipo[tipo].items()):
                        st.caption(f"  {anno}: {ore:.2f}h")
        else:
            st.markdown("### 📊 Saldo Effettivo")
            st.caption("(include ferie già prenotate)")
            col1, col2, col3 = st.columns(3)
            for idx, (tipo, emoji) in enumerate([("FERIE", "🏖️"), ("ROL", "⏰"), ("EX FEST", "🎉")]):
                with [col1, col2, col3][idx]:
                    totale = sum(saldo_eff_per_tipo[tipo].values())
                    st.metric(f"{emoji} {tipo}", f"{totale:.2f}h", f"{ore_a_giorni(totale):.1f} gg")
                    for anno, ore in sorted(saldo_eff_per_tipo[tipo].items()):
                        st.caption(f"{anno}: {ore:.2f}h")
            st.divider()
            st.markdown("### 🔮 Saldo Previsto")
            st.caption("(solo maturazioni)")
            col1, col2, col3 = st.columns(3)
            for idx, (tipo, emoji) in enumerate([("FERIE", "🏖️"), ("ROL", "⏰"), ("EX FEST", "🎉")]):
                with [col1, col2, col3][idx]:
                    totale_prev = sum(saldo_prev_per_tipo[tipo].values())
                    totale_eff = sum(saldo_eff_per_tipo[tipo].values())
                    st.metric(f"{emoji} {tipo}", f"{totale_prev:.2f}h", f"{(totale_prev - totale_eff):+.2f}h")
                    for anno, ore in sorted(saldo_prev_per_tipo[tipo].items()):
                        st.caption(f"{anno}: {ore:.2f}h")
    else:
        col1, col2, col3 = st.columns(3)
        for idx, (tipo, emoji) in enumerate([("FERIE", "🏖️"), ("ROL", "⏰"), ("EX FEST", "🎉")]):
            with [col1, col2, col3][idx]:
                st.subheader(f"{emoji} {tipo}")
                totale = sum(saldo_eff_per_tipo[tipo].values())
                st.metric("Totale", f"{totale:.2f}h", f"{ore_a_giorni(totale):.1f} gg")
                for anno, ore in sorted(saldo_eff_per_tipo[tipo].items()):
                    st.caption(f"{anno}: {ore:.2f}h")

    def organizza_saldo(saldo):
        saldo_per_tipo = {"FERIE": {}, "ROL": {}, "EX FEST": {}}
        for key, value in saldo.items():
            tipo = value["tipo"]
            anno = value["anno"]
            ore = value["ore"]
            if ore > 0:
                saldo_per_tipo[tipo][anno] = ore
        return saldo_per_tipo
    
    saldo_eff_per_tipo = organizza_saldo(saldo_effettivo)
    
    if is_futuro:
        saldo_prev_per_tipo = organizza_saldo(saldo_previsto)
    
    # Mostra saldi
    if is_futuro:
        # Doppia visualizzazione per mesi futuri
        st.subheader("📊 Saldo Effettivo (con ferie già prenotate)")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.write("**🏖️ FERIE**")
            totale_ferie_eff = sum(saldo_eff_per_tipo["FERIE"].values())
            st.metric("Effettivo", f"{totale_ferie_eff:.2f}h", f"{ore_a_giorni(totale_ferie_eff):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["FERIE"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col2:
            st.write("**⏰ ROL**")
            totale_rol_eff = sum(saldo_eff_per_tipo["ROL"].values())
            st.metric("Effettivo", f"{totale_rol_eff:.2f}h", f"{ore_a_giorni(totale_rol_eff):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["ROL"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col3:
            st.write("**🎉 EX FEST**")
            totale_ex_eff = sum(saldo_eff_per_tipo["EX FEST"].values())
            st.metric("Effettivo", f"{totale_ex_eff:.2f}h", f"{ore_a_giorni(totale_ex_eff):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["EX FEST"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        st.divider()
        
        st.subheader("🔮 Saldo Previsto (solo maturazioni, senza utilizzi futuri)")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.write("**🏖️ FERIE**")
            totale_ferie_prev = sum(saldo_prev_per_tipo["FERIE"].values())
            diff_ferie = totale_ferie_prev - totale_ferie_eff
            st.metric("Previsto", f"{totale_ferie_prev:.2f}h", f"{diff_ferie:+.2f}h rispetto effettivo")
            st.caption(f"≈ {ore_a_giorni(totale_ferie_prev):.1f} giorni")
            for anno, ore in sorted(saldo_prev_per_tipo["FERIE"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col2:
            st.write("**⏰ ROL**")
            totale_rol_prev = sum(saldo_prev_per_tipo["ROL"].values())
            diff_rol = totale_rol_prev - totale_rol_eff
            st.metric("Previsto", f"{totale_rol_prev:.2f}h", f"{diff_rol:+.2f}h rispetto effettivo")
            st.caption(f"≈ {ore_a_giorni(totale_rol_prev):.1f} giorni")
            for anno, ore in sorted(saldo_prev_per_tipo["ROL"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col3:
            st.write("**🎉 EX FEST**")
            totale_ex_prev = sum(saldo_prev_per_tipo["EX FEST"].values())
            diff_ex = totale_ex_prev - totale_ex_eff
            st.metric("Previsto", f"{totale_ex_prev:.2f}h", f"{diff_ex:+.2f}h rispetto effettivo")
            st.caption(f"≈ {ore_a_giorni(totale_ex_prev):.1f} giorni")
            for anno, ore in sorted(saldo_prev_per_tipo["EX FEST"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        # Mostra differenza
        if diff_ferie < 0 or diff_rol < 0 or diff_ex < 0:
            st.info(f"💡 **Hai già prenotato permessi per questo periodo!** La differenza mostra quanto hai già pianificato di utilizzare.")
    
    else:
        # Visualizzazione singola per mesi passati/presente
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.subheader("🏖️ FERIE")
            totale_ferie = sum(saldo_eff_per_tipo["FERIE"].values())
            st.metric("Totale", f"{totale_ferie:.2f}h", f"{ore_a_giorni(totale_ferie):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["FERIE"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col2:
            st.subheader("⏰ ROL")
            totale_rol = sum(saldo_eff_per_tipo["ROL"].values())
            st.metric("Totale", f"{totale_rol:.2f}h", f"{ore_a_giorni(totale_rol):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["ROL"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
        
        with col3:
            st.subheader("🎉 EX FEST")
            totale_ex = sum(saldo_eff_per_tipo["EX FEST"].values())
            st.metric("Totale", f"{totale_ex:.2f}h", f"{ore_a_giorni(totale_ex):.1f} gg")
            for anno, ore in sorted(saldo_eff_per_tipo["EX FEST"].items()):
                st.caption(f"{anno}: {ore:.2f}h ({ore_a_giorni(ore):.1f} gg)")
    
    st.divider()
    
        show_login()
    elif "setup_completato" not in st.session_state:
        show_setup_iniziale()
    else:
        show_dashboard()

if __name__ == "__main__":
    main()
