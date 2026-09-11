import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="TurnoMed Python Engine", version="2.3.0")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

class GenerazioneRequest(BaseModel):
    organizzazione_id: str
    reparto_id: str
    anno: int
    mese: int

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
        "Prefer": "resolution=merge-duplicates"
    }

    async with httpx.AsyncClient() as client:
        try:
            # 1. Recupero degli operatori dalla tabella corretta staging_utenti
            url_utenti = f"{SUPABASE_URL}/rest/v1/staging_utenti?organizzazione_id=eq.{data.organizzazione_id}&reparto_id=eq.{data.reparto_id}&select=id,nome,cognome"
            resp_utenti = await client.get(url_utenti, headers=headers)
            
            if resp_utenti.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Errore Supabase staging_utenti: {resp_utenti.text}")
            
            operatori = resp_utenti.json()
            if not operatori:
                raise HTTPException(status_code=400, detail=f"Nessun operatore trovato in staging_utenti per organizzazione {data.organizzazione_id} e reparto {data.reparto_id}.")

            # Calcolo date del mese
            data_inizio_mese_str = f"{data.anno:04d}-{data.mese:02d}-01"
            if data.mese == 12:
                primo_giorno_next = datetime(data.anno + 1, 1, 1)
            else:
                primo_giorno_next = datetime(data.anno, data.mese + 1, 1)
            
            ultimo_giorno = primo_giorno_next - timedelta(days=1)
            tot_giorni = ultimo_giorno.day
            data_fine_mese_str = ultimo_giorno.strftime("%Y-%m-%d")

            # 2. Recupero ferie/assenze esistenti nella tabella pianificazione
            url_assenze = f"{SUPABASE_URL}/rest/v1/pianificazione?organizzazione_id=eq.{data.organizzazione_id}&reparto_id=eq.{data.reparto_id}&data_inizio=gte.{data_inizio_mese_str} 00:00:00&data_inizio=lte.{data_fine_mese_str} 23:59:59&select=utente_id,data_inizio,tipo_evento"
            resp_assenze = await client.get(url_assenze, headers=headers)
            
            assenze_map = {}
            if resp_assenze.status_code == 200:
                for row in resp_assenze.json():
                    u_id = row.get("utente_id")
                    d_str = row.get("data_inizio", "")[:10]
                    t_ev = row.get("tipo_evento")
                    if t_ev in ["F", "Per", "104"]:
                        if u_id not in assenze_map:
                            assenze_map[u_id] = {}
                        assenze_map[u_id][d_str] = t_ev

            # Sequenza turni con supporto doppia notte: M -> P -> N -> N -> S -> R
            sequenza_turni = ["M", "P", "N", "N", "S", "R"]
            payload_inserimento = []

            # 3. Generazione turni per operatore
            for index_op, op in enumerate(operatori):
                utente_id = op["id"]
                indice_seq = index_op % len(sequenza_turni)

                for giorno in range(1, tot_giorni + 1):
                    data_corrente = datetime(data.anno, data.mese, giorno)
                    data_str = data_corrente.strftime("%Y-%m-%d")
                    
                    data_inizio_ts = f"{data_str} 00:00:00+00"
                    data_fine_ts = f"{data_str} 23:59:59+00"

                    # Se c'è un'assenza (ferie/permesso), saltiamo l'inserimento ma avanziamo il ciclo
                    if utente_id in assenze_map and data_str in assenze_map[utente_id]:
                        indice_seq = (indice_seq + 1) % len(sequenza_turni)
                        continue

                    turno_assegnato = sequenza_turni[indice_seq]

                    payload_inserimento.append({
                        "organizzazione_id": data.organizzazione_id,
                        "reparto_id": data.reparto_id,
                        "utente_id": utente_id,
                        "data_inizio": data_inizio_ts,
                        "data_fine": data_fine_ts,
                        "tipo_evento": turno_assegnato,
                        "stato": "Generato da AI"
                    })

                    indice_seq = (indice_seq + 1) % len(sequenza_turni)

            # 4. Scrittura massiva su Supabase nella tabella pianificazione
            url_pianificazione = f"{SUPABASE_URL}/rest/v1/pianificazione"
            resp_upsert = await client.post(url_pianificazione, headers=headers, json=payload_inserimento)

            if resp_upsert.status_code not in [200, 201, 204]:
                raise HTTPException(status_code=500, detail=f"Errore scrittura pianificazione Supabase: {resp_upsert.text}")

            return {
                "success": True,
                "message": f"Turni generati con successo per il reparto {data.reparto_id} ({data.mese}/{data.anno})"
            }

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Errore interno Python: {str(e)}")
