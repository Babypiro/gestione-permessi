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

def show_previsione():
    st.subheader("📈 Previsione Saldi Futuri")
    
    oggi = date.today()
    
    # Inizializza stato se non esiste
    if "prev_mese" not in st.session_state:
        st.session_state.prev_mese = oggi.month
        st.session_state.prev_anno = oggi.year
    
    # Navigazione mese
    col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
    
    with col1:
        if st.button("◀◀ -1 Anno"):
            st.session_state.prev_anno -= 1
            st.rerun()
    
    with col2:
        if st.button("◀ -1 Mese"):
            if st.session_state.prev_mese == 1:
                st.session_state.prev_mese = 12
                st.session_state.prev_anno -= 1
            else:
                st.session_state.prev_mese -= 1
            st.rerun()
    
    with col3:
        st.markdown(f"### 📅 {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    
    with col4:
        if st.button("+1 Mese ▶"):
            if st.session_state.prev_mese == 12:
                st.session_state.prev_mese = 1
                st.session_state.prev_anno += 1
            else:
                st.session_state.prev_mese += 1
            st.rerun()
    
    with col5:
        if st.button("+1 Anno ▶▶"):
            st.session_state.prev_anno += 1
            st.rerun()
    
    # Reset a mese corrente
    col_reset1, col_reset2, col_reset3 = st.columns([1, 1, 2])
    with col_reset1:
        if st.button("🔄 Torna a Oggi"):
            st.session_state.prev_mese = oggi.month
            st.session_state.prev_anno = oggi.year
            st.rerun()
    
    st.divider()
    
    # Determina se è passato, presente o futuro
    data_selezionata = date(st.session_state.prev_anno, st.session_state.prev_mese, 1)
    data_oggi = date(oggi.year, oggi.month, 1)
    
    is_futuro = data_selezionata > data_oggi
    is_presente = data_selezionata == data_oggi
    is_passato = data_selezionata < data_oggi
    
    if is_passato:
        tipo_visualizzazione = "📊 Saldo Storico"
        st.info(f"**{tipo_visualizzazione}** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    elif is_presente:
        tipo_visualizzazione = "📍 Saldo Attuale"
        st.info(f"**{tipo_visualizzazione}** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    else:
        st.warning(f"**🔮 Previsione Futuro** - {calendar.month_name[st.session_state.prev_mese]} {st.session_state.prev_anno}")
    
    # Calcola saldi
    saldo_effettivo = genera_previsione_mese(st.session_state.user_id, st.session_state.prev_mese, st.session_state.prev_anno)
    
    # Per mesi futuri, calcola anche saldo previsto (senza utilizzi futuri)
    if is_futuro:
        saldo_previsto = genera_previsione_solo_maturazioni(st.session_state.user_id, st.session_state.prev_mese, st.session_state.prev_anno)
    
    # Organizza per tipo
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
    
    # Mostra andamento storico
    st.subheader("📊 Andamento Ultimi 12 Mesi")
    
    try:
        storico = get_storico_mensile(st.session_state.user_id, 11)
        
        if storico:
            df_storico = pd.DataFrame(storico)
            df_storico["mese_label"] = df_storico.apply(
                lambda x: f"{calendar.month_abbr[x['mese']]} {x['anno']}", axis=1
            )
            
            # Crea tre grafici separati
            import altair as alt
            
            # Grafico FERIE
            chart_ferie = alt.Chart(df_storico).mark_line(point=True, color="#FF6B6B").encode(
                x=alt.X("mese_label:N", title="Mese", sort=None),
                y=alt.Y("ferie:Q", title="Ore FERIE"),
                tooltip=["mese_label", alt.Tooltip("ferie:Q", format=".2f", title="Ore")]
            ).properties(
                title="FERIE",
                height=200
            )
            
            # Grafico ROL
            chart_rol = alt.Chart(df_storico).mark_line(point=True, color="#4ECDC4").encode(
                x=alt.X("mese_label:N", title="Mese", sort=None),
                y=alt.Y("rol:Q", title="Ore ROL"),
                tooltip=["mese_label", alt.Tooltip("rol:Q", format=".2f", title="Ore")]
            ).properties(
                title="ROL",
                height=200
            )
            
            # Grafico EX FEST
            chart_ex = alt.Chart(df_storico).mark_line(point=True, color="#95E1D3").encode(
                x=alt.X("mese_label:N", title="Mese", sort=None),
                y=alt.Y("ex_fest:Q", title="Ore EX FEST"),
                tooltip=["mese_label", alt.Tooltip("ex_fest:Q", format=".2f", title="Ore")]
            ).properties(
                title="EX FEST",
                height=200
            )
            
            st.altair_chart(chart_ferie, use_container_width=True)
            st.altair_chart(chart_rol, use_container_width=True)
            st.altair_chart(chart_ex, use_container_width=True)
            
    except Exception as e:
        st.warning(f"Impossibile generare grafico: {str(e)}")
    
    st.divider()
    
    # Legenda
    st.caption("💡 **Come funziona:**")
    st.caption("- **📊 Saldo Effettivo**: include tutti i movimenti fino al mese selezionato (anche ferie già prenotate)")
    st.caption("- **🔮 Saldo Previsto**: include solo maturazioni, ignora utilizzi futuri (utile per pianificare)")
    st.caption("- **Differenza**: mostra quanto hai già pianificato di utilizzare in futuro")


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
