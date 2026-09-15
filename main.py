import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
import httpx
from typing import Optional, List, Dict, Any

app = FastAPI(title="TurnoMed Python Engine", version="2.8.6")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

class GenerazioneRequest(BaseModel):
    model_config = ConfigDict(extra='allow') # Accetta campi extra ed evita errori 422
    
    organizzazione_id: Optional[str] = None
    reparto_id: Optional[str] = None
    data_inizio: Optional[str] = None
    data_fine: Optional[str] = None
    anno: Optional[int] = None
    mese: Optional[int] = None
    operatore_id: Optional[str] = None
    utente_id: Optional[str] = None
    turno_iniziale: Optional[str] = None
    turno_partenza: Optional[str] = None
    rispetta_ferie: Optional[bool] = True
    rispetta_ferie_approvate: Optional[bool] = True
    riposo_domenicale: Optional[bool] = False
    modalita_mattinieri: Optional[bool] = False
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

    # Allineamento flessibile dei campi ricevuti da PHP
    org_id = data.organizzazione_id
    rep_id = data.reparto_id
    op_id = data.operatore_id or data.utente_id
    t_iniziale = data.turno_iniziale or data.turno_partenza
    riposo_dom = data.riposo_domenicale or data.modalita_mattinieri

    # Identificativo autore valido per campi UUID (evita stringhe descrittive su colonne UUID)
    autore_uuid = op_id if op_id else None

    # Gestione anno e mese (ricavati da data_inizio se non espliciti)
    if not data.anno or not data.mese:
        if data.data_inizio:
            try:
                dt_parsed = datetime.strptime(str(data.data_inizio)[:10], "%Y-%m-%d")
                anno = dt_parsed.year
                mese = dt_parsed.month
            except:
                anno = datetime.now().year
                mese = datetime.now().month
        else:
            anno = datetime.now().year
            mese = datetime.now().month
    else:
        anno = data.anno
        mese = data.mese

    sequenza_turni = ["M", "P", "N", "S", "R"]

    async with httpx.AsyncClient() as client:
        try:
            # 1. Costruzione query operatori
            url_utenti = f"{SUPABASE_URL}/rest/v1/staging_utenti?organizzazione_id=eq.{org_id}&reparto_id=eq.{rep_id}"
            if op_id:
                url_utenti += f"&id=eq.{op_id}"
            url_utenti += "&select=*"

            resp_utenti = await client.get(url_utenti, headers=headers)
            if resp_utenti.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Errore Supabase staging_utenti: {resp_utenti.text}")
            
            operatori = resp_utenti.json()
            if not operatori:
                raise HTTPException(status_code=400, detail="Nessun operatore trovato con i filtri selezionati.")

            # Calcolo date del mese
            data_inizio_mese_str = f"{anno:04d}-{mese:02d}-01"
            if mese == 12:
                primo_giorno_next = datetime(anno + 1, 1, 1)
            else:
                primo_giorno_next = datetime(anno, mese + 1, 1)
            
            ultimo_giorno = primo_giorno_next - timedelta(days=1)
            tot_giorni = ultimo_giorno.day
            data_fine_mese_str = ultimo_giorno.strftime("%Y-%m-%d")

            # Verifica se è stato passato un range personalizzato dal PHP (es. data_inizio / data_fine)
            is_custom_range = False
            if data.data_inizio and data.data_fine:
                try:
                    d_start_dt = datetime.strptime(str(data.data_inizio)[:10], "%Y-%m-%d")
                    d_end_dt = datetime.strptime(str(data.data_fine)[:10], "%Y-%m-%d")
                    if (d_end_dt - d_start_dt).days >= 0:
                        is_custom_range = True
                except:
                    is_custom_range = False

            # 2. Recupero ferie/assenze approvate
            assenze_map = {}
            url_assenze = f"{SUPABASE_URL}/rest/v1/pianificazione?organizzazione_id=eq.{org_id}&reparto_id=eq.{rep_id}&data_inizio=gte.{data_inizio_mese_str} 00:00:00&data_inizio=lte.{data_fine_mese_str} 23:59:59&stato=eq.Approvato&select=utente_id,data_inizio,tipo_evento"
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

            # 3. Generazione turni
            for index_op, op in enumerate(operatori):
                utente_id = op.get("id")
                if not utente_id:
                    continue
                
                if t_iniziale and t_iniziale in sequenza_turni and op_id:
                    indice_seq = sequenza_turni.index(t_iniziale)
                else:
                    indice_seq = (index_op * 2) % len(sequenza_turni)

                if is_custom_range:
                    current_date_iter = d_start_dt
                    while current_date_iter <= d_end_dt:
                        data_str = current_date_iter.strftime("%Y-%m-%d")
                        data_inizio_ts = f"{data_str} 00:00:00+00"
                        data_fine_ts = f"{data_str} 23:59:59+00"

                        if utente_id in assenze_map and data_str in assenze_map[utente_id]:
                            current_date_iter += timedelta(days=1)
                            continue

                        # Regola riposo domenicale / mattiniero
                        if riposo_dom and current_date_iter.weekday() == 6:
                            turno_assegnato = "R"
                        elif riposo_dom:
                            turno_assegnato = "M"
                        else:
                            turno_assegnato = sequenza_turni[indice_seq]
                            indice_seq = (indice_seq + 1) % len(sequenza_turni)

                        payload_inserimento.append({
                            "organizzazione_id": org_id,
                            "reparto_id": rep_id,
                            "utente_id": utente_id,
                            "data_inizio": data_inizio_ts,
                            "data_fine": data_fine_ts,
                            "tipo_evento": turno_assegnato,
                            "stato": "Generato da AI",
                            "creato_da": autore_uuid,
                            "modificato_da": autore_uuid,
                            "note": "Generato automaticamente da Motore AI"
                        })
                        current_date_iter += timedelta(days=1)
                else:
                    for giorno in range(1, tot_giorni + 1):
                        current_date_iter = datetime(anno, mese, giorno)
                        data_str = current_date_iter.strftime("%Y-%m-%d")
                        data_inizio_ts = f"{data_str} 00:00:00+00"
                        data_fine_ts = f"{data_str} 23:59:59+00"

                        if utente_id in assenze_map and data_str in assenze_map[utente_id]:
                            continue

                        if riposo_dom and current_date_iter.weekday() == 6:
                            turno_assegnato = "R"
                        elif riposo_dom:
                            turno_assegnato = "M"
                        else:
                            turno_assegnato = sequenza_turni[indice_seq]
                            indice_seq = (indice_seq + 1) % len(sequenza_turni)

                        payload_inserimento.append({
                            "organizzazione_id": org_id,
                            "reparto_id": rep_id,
                            "utente_id": utente_id,
                            "data_inizio": data_inizio_ts,
                            "data_fine": data_fine_ts,
                            "tipo_evento": turno_assegnato,
                            "stato": "Generato da AI",
                            "creato_da": autore_uuid,
                            "modificato_da": autore_uuid,
                            "note": "Generato automaticamente da Motore AI"
                        })

            # 4. Scrittura massiva su Supabase
            url_pianificazione = f"{SUPABASE_URL}/rest/v1/pianificazione"
            resp_upsert = await client.post(url_pianificazione, headers=headers, json=payload_inserimento)

            if resp_upsert.status_code not in [200, 201, 204]:
                raise HTTPException(status_code=500, detail=f"Errore scrittura pianificazione Supabase: {resp_upsert.text}")

            modo_str = " (con domeniche di riposo garantite)" if riposo_dom else ""
            return {
                "success": True,
                "message": f"Turni generati con successo per {len(operatori)} operatore/i{modo_str}"
            }

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Errore interno Python: {str(e)}")
