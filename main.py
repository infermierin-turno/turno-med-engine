import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
calendar_module = None # placeholder to ensure standard library imports if needed

app = FastAPI(title="TurnoMed Python Engine", version="1.0.0")

# Configurazioni Supabase (assicurati di impostare queste variabili d'ambiente su Render)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://TUA_SUPABASE_URL.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "TUA_SUPABASE_KEY")

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
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    async with httpx.AsyncClient() as client:
        try:
            # 1. Recupero degli operatori del reparto da staging_utenti (o utenti)
            url_utenti = f"{SUPABASE_URL}/rest/v1/utenti?organizzazione_id=eq.{data.organizzazione_id}&reparto_id=eq.{data.reparto_id}&select=id,nome,cognome"
            resp_utenti = await client.get(url_utenti, headers=headers)
            
            if resp_utenti.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Errore recupero utenti da Supabase: {resp_utenti.text}")
            
            operatori = resp_utenti.json()
            if not operatori:
                raise HTTPException(status_code=400, detail="Nessun operatore trovato per questo reparto.")

            # Calcolo dei giorni del mese
            if data.mese == 12:
                primo_giorno_next = datetime(data.anno + 1, 1, 1)
            else:
                primo_giorno_next = datetime(data.anno, data.mese + 1, 1)
            
            ultimo_giorno = primo_giorno_next - timedelta(days=1)
            tot_giorni = ultimo_giorno.day

            # Pattern ciclico di rotazione di base per popolare il tabellone (es: M, P, N, S, R)
            tipi_turno = ["M", "P", "N", "S", "R"]
            payload_inserimento = []

            # Generazione turni giorno per giorno per ciascun operatore
            for index_op, op in enumerate(operatori):
                utente_id = op["id"]
                for giorno in range(1, tot_giorni + 1):
                    data_corrente = datetime(data.anno, data.mese, giorno)
                    data_str = data_corrente.strftime("%Y-%m-%d")
                    
                    # Assegnazione di rotazione basata sull'indice dell'operatore e del giorno
                    turno_assegnato = tipi_turno[(giorno + index_op) % len(tipi_turno)]
                    
                    # Formato timestamp con offset UTC richiesto da Supabase timestamptz
                    data_inizio_ts = f"{data_str} 00:00:00+00"
                    data_fine_ts = f"{data_str} 23:59:59+00"

                    payload_inserimento.append({
                        "organizzazione_id": data.organizzazione_id,
                        "reparto_id": data.reparto_id,
                        "utente_id": utente_id,
                        "data_inizio": data_inizio_ts,
                        "data_fine": data_fine_ts,
                        "tipo_evento": turno_assegnato,
                        "stato": "Generato da AI"
                    })

            # 4. Esecuzione dell'upsert massivo nella tabella pianificazione
            url_pianificazione = f"{SUPABASE_URL}/rest/v1/pianificazione"
            resp_upsert = await client.post(url_pianificazione, headers=headers, json=payload_inserimento)

            if resp_upsert.status_code not in [200, 201, 204]:
                raise HTTPException(status_code=500, detail=f"Errore salvataggio pianificazione su Supabase: {resp_upsert.text}")

            return {
                "success": True,
                "message": f"Turni generati e salvati con successo per il reparto {data.reparto_id} ({data.mese}/{data.anno})"
            }

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=str(e))
