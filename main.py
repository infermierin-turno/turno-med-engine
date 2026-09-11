from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from supabase import create_client, Client

app = FastAPI(title="TurnoMed Engine", version="1.0")

# Inizializzazione client Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

class GenerazioneRequest(BaseModel):
    organizzazione_id: str
    reparto_id: str
    anno: int
    mese: int

@app.get("/")
def read_root():
    return {"status": "online", "message": "TurnoMed Python Engine attivo!"}

@app.post("/genera-turni")
def genera_turni(data: GenerazioneRequest):
    try:
        # 1. Qui recupereremo gli utenti del reparto da staging_utenti
        # 2. Qui recupereremo le assenze dal mese specificato
        # 3. Qui applicheremo l'algoritmo OR-Tools
        # 4. Qui faremo l'upsert nella tabella pianificazione
        
        return {
            "success": True, 
            "message": f"Turni generati con successo per il reparto {data.reparto_id} ({data.mese}/{data.anno})"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
