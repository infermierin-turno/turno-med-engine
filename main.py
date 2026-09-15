import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
import httpx
from typing import Optional, List, Dict, Any

app = FastAPI(title="TurnoMed Python Engine", version="2.8.2")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

class GenerazioneRequest(BaseModel):
    model_config = ConfigDict(extra='allow') # Evita errori 422 se arrivano campi extra dal PHP
    
    organizzazione_id: str
    reparto_id: str
    anno: int
    mese: int
    operatore_id: Optional[str] = None
    turno_iniziale: Optional[str] = None
    modalita_mattinieri: Optional[bool] = False
    riposo_domenicale: Optional[bool] = False  # <--- Aggiunto per allinearsi perfettamente al PHP
    rispetta_ferie_approvate: Optional[bool] = True
    ferie_approvate: Optional[List[Dict[str, Any]]] = []

@app.get("/")
def read_root():
    return {"status": "online", "message": "TurnoMed Python Engine attivo!"}

@app.post("/genera-turni")
async def genera_turni(data: GenerazioneRequest):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Variabili d'ambiente SUPABASE_URL o SUPABASE_KEY non configurate su Render.")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,on_conflict=utente_id,data_inizio"
    }

    # Sequenza standard aggiornata: una sola notte, seguita da Smonto e Riposo
    sequenza_turni = ["M", "P", "N", "S", "R"]

    async with httpx.AsyncClient() as client:
        try:
            # 1. Costruzione query operatori
            url_utenti = f"{SUPABASE_URL}/rest/v1/staging_utenti?organizzazione_id=eq.{data.organizzazione_id}&reparto_id=eq.{data.reparto_id}"
            if data.operatore_id:
                url_utenti += f"&id=eq.{data.operatore_id}"
            url_utenti += "&select=*"

            resp_utenti = await client.get(url_utenti, headers=headers)
            if resp_utenti.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Errore Supabase staging_utenti: {resp_utenti.text}")
            
            operatori = resp_utenti.json()
            if not operatori:
                raise HTTPException(status_code=400, detail="Nessun operatore trovato con i filtri selezionati.")

            # Calcolo date del mese
            data_inizio_mese_str = f"{data.anno:04d}-{data.mese:02d}-01"
            if data.mese == 12:
                primo_giorno_next = datetime(data.anno + 1, 1, 1)
            else:
                primo_giorno_next = datetime(data.anno, data.mese + 1, 1)
            
            ultimo_giorno = primo_giorno_next - timedelta(days=1)
            tot_giorni = ultimo_giorno.day
            data_fine_mese_str = ultimo_giorno.strftime("%Y-%m-%d")

            # 2. Recupero e mappatura ferie/assenze approvate (da payload PHP o direttamente da Supabase)
            assenze_map = {}
            if data.rispetta_ferie_approvate:
                if data.ferie_approvate:
                    for ev in data.ferie_approvate:
                        u_id = ev.get("utente_id")
                        d_str = str(ev.get("data_inizio", ""))[:10]
                        t_ev = ev.get("tipo_evento")
                        if u_id and d_str and t_ev:
                            if u_id not in assenze_map:
                                assenze_map[u_id] = {}
                            assenze_map[u_id][d_str] = t_ev
                else:
                    # Fallback di sicurezza: interroga Supabase se non passate dal PHP
                    url_assenze = f"{SUPABASE_URL}/rest/v1/pianificazione?organizzazione_id=eq.{data.organizzazione_id}&reparto_id=eq.{data.reparto_id}&data_inizio=gte.{data_inizio_mese_str} 00:00:00&data_inizio=lte.{data_fine_mese_str} 23:59:59&stato=eq.Approvato&select=utente_id,data_inizio,tipo_evento"
                    resp_assenze = await client.get(url_assenze, headers=headers)
                    if resp_assenze.status_code == 200:
                        for row in resp_assenze.json():
                            u_id = row.get("utente_id")
                            d_str = str(row.get("data_inizio", ""))[:10]
                            t_ev = row.get("tipo_evento")
                            if u_id and d_str and t_ev:
                                if u_id not in assenze_map:
                                    assenze_map[u_id] = {}
                                assenze_map[u_id][d_str] = t_ev

            payload_inserimento = []

            # 3. Generazione turni per ogni operatore rispettando rigorosamente ferie e assenze
            for index_op, op in enumerate(operatori):
                utente_id = op.get("id")
                if not utente_id:
                    continue
                
                # Determiniamo il punto di partenza nella sequenza standard
                if data.turno_iniziale and data.turno_iniziale in sequenza_turni and data.operatore_id:
                    indice_seq = sequenza_turni.index(data.turno_iniziale)
                else:
                    indice_seq = (index_op * 2) % len(sequenza_turni)

                for giorno in range(1, tot_giorni + 1):
                    data_corrente = datetime(data.anno, data.mese, giorno)
                    data_str = data_corrente.strftime("%Y-%m-%d")
                    
                    data_inizio_ts = f"{data_str} 00:00:00+00"
                    data_fine_ts = f"{data_str} 23:59:59+00"

                    # CONTROLLO BLINDATO: Se esiste un'assenza o ferie approvata, NON generare e NON inserire nulla per questa data
                    if utente_id in assenze_map and data_str in assenze_map[utente_id]:
                        continue

                    # REGOLA: Gestione Modalità Mattinieri o Riposo Domenicale
                    is_mattiniero_attivo = data.modalita_mattinieri or data.riposo_domenicale

                    if is_mattiniero_attivo:
                        if data_corrente.weekday() == 6:
                            turno_assegnato = "R" # Domenica libera / Riposo
                        else:
                            turno_assegnato = "M" # Mattina nei giorni lavorativi
                    else:
                        turno_assegnato = sequenza_turni[indice_seq]
                        indice_seq = (indice_seq + 1) % len(sequenza_turni)

                    payload_inserimento.append({
                        "organizzazione_id": data.organizzazione_id,
                        "reparto_id": data.reparto_id,
                        "utente_id": utente_id,
                        "data_inizio": data_inizio_ts,
                        "data_fine": data_fine_ts,
                        "tipo_evento": turno_assegnato,
                        "stato": "Generato da AI"
                    })

            # 4. Scrittura massiva su Supabase
            url_pianificazione = f"{SUPABASE_URL}/rest/v1/pianificazione"
            resp_upsert = await client.post(url_pianificazione, headers=headers, json=payload_inserimento)

            if resp_upsert.status_code not in [200, 201, 204]:
                raise HTTPException(status_code=500, detail=f"Errore scrittura pianificazione Supabase: {resp_upsert.text}")

            modo_str = " (Profilo Mattinieri con domeniche libere)" if (data.modalita_mattinieri or data.riposo_domenicale) else ""
            return {
                "success": True,
                "message": f"Turni generati con successo per {len(operatori)} operatore/i ({data.mese}/{data.anno}){modo_str}"
            }

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Errore interno Python: {str(e)}")
