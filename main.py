<?php
session_start();
if (!isset($_SESSION['utente'])) {
    header("Location: index.php");
    exit;
}
require_once 'config.php';

// Funzione di supporto personalizzata per le chiamate a Supabase con forzatura dello schema public
function supabase_turni_request($endpoint, $method = 'GET', $data = null) {
    $url = SUPABASE_URL . '/rest/v1/' . $endpoint;
    $ch = curl_init($url);
    
    $headers = [
        'apikey: ' . SUPABASE_KEY,
        'Authorization: Bearer ' . SUPABASE_KEY,
        'Content-Type: application/json',
        'Accept-Profile: public',
        'Content-Profile: public'
    ];

    if ($method === 'POST') {
        curl_setopt($ch, CURLOPT_POST, true);
        if ($data) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
        }
        $headers[] = 'Prefer: return=representation';
    } elseif ($method === 'PATCH') {
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'PATCH');
        if ($data) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
        }
        $headers[] = 'Prefer: return=representation';
    } elseif ($method === 'DELETE') {
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'DELETE');
    }

    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($httpCode >= 200 && $httpCode < 300) {
        return json_decode($response, true);
    }
    return $method === 'GET' ? [] : false;
}

// Funzione per comunicare con il microservizio Python su Render puntando all'endpoint corretto /genera-turni
function call_python_engine_genera($payload = []) {
    $url = 'https://turno-med-engine.onrender.com/genera-turni';

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'Accept: application/json'
    ]);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_TIMEOUT, 60);
    
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($error) {
        return ['success' => false, 'error' => "Errore cURL: " . $error];
    }

    $decoded = json_decode($response, true);

    if ($httpCode >= 200 && $httpCode < 300) {
        return is_array($decoded) ? $decoded : ['success' => true, 'response' => $response];
    }

    return [
        'success' => false, 
        'error' => "HTTP Code: $httpCode", 
        'response' => $response
    ];
}

// Estrazione sicura dell'ID utente, dell'organizzazione e del reparto dalla sessione
$utente_id = $_SESSION['utente_id'] ?? $_SESSION['utente']['id'] ?? $_SESSION['utente']['ID'] ?? null;
$org_id_utente = $_SESSION['organizzazione_id'] ?? $_SESSION['utente']['organizzazione_id'] ?? $_SESSION['utente']['ORGANIZZAZIONE_ID'] ?? null;
$reparto_id_utente = $_SESSION['reparto_id'] ?? $_SESSION['utente']['reparto_id'] ?? $_SESSION['utente']['REPARTO_ID'] ?? null;

// Normalizzazione del ruolo e dei permessi
$ruolo_raw = trim($_SESSION['ruolo'] ?? $_SESSION['utente']['ruolo'] ?? $_SESSION['utente']['RUOLO'] ?? '');
$ruolo_lower = strtolower(str_replace([' ', '-'], '_', $ruolo_raw));

$is_super_admin = $_SESSION['is_super_admin'] ?? false;
if (!$is_super_admin && ($ruolo_lower === 'super_admin' || $ruolo_lower === 'admin' || $ruolo_lower === 'superadmin')) {
    $is_super_admin = true;
}

$is_capo_personale = ($ruolo_lower === 'capo_personale' || $ruolo_lower === 'capopersonale');
$is_coordinatore = ($ruolo_lower === 'coordinatore');

if (empty($utente_id)) {
    header("Location: index.php");
    exit;
}

// Se l'utente corrente è un coordinatore ma non ha il reparto in sessione, proviamo a recuperarlo da staging_utenti
if ($is_coordinatore && empty($reparto_id_utente)) {
    $datiUserCurr = supabase_turni_request("staging_utenti?id=eq.$utente_id&select=reparto_id,organizzazione_id");
    if (!empty($datiUserCurr) && is_array($datiUserCurr)) {
        $reparto_id_utente = $datiUserCurr[0]['reparto_id'] ?? null;
        if (empty($org_id_utente)) {
            $org_id_utente = $datiUserCurr[0]['organizzazione_id'] ?? null;
        }
    }
}

$messaggio = '';
$tipo_alert = '';

// Recupero tipologie di turno dalla tabella tipologie_turno su Supabase
$queryTipologie = "tipologie_turno?select=*";
$tipologieTurni = supabase_turni_request($queryTipologie);

if (!is_array($tipologieTurni) || empty($tipologieTurni)) {
    $tipologieTurni = [
        ['id' => '1', 'nome_turno' => 'Mattina', 'codice_breve' => 'M'],
        ['id' => '2', 'nome_turno' => 'Pomeriggio', 'codice_breve' => 'P'],
        ['id' => '3', 'nome_turno' => 'Notte', 'codice_breve' => 'N'],
        ['id' => '4', 'nome_turno' => 'Smonto', 'codice_breve' => 'S'],
        ['id' => '5', 'nome_turno' => 'Riposo', 'codice_breve' => 'R']
    ];
}

// Recupero elenco collaboratori dalla tabella staging_utenti
$mappaUtenti = [];
if (!$is_super_admin && !$is_capo_personale && !empty($reparto_id_utente)) {
    $resCollabReparto = supabase_turni_request("staging_utenti?reparto_id=eq." . urlencode($reparto_id_utente) . "&select=id,nome,ruolo,reparto_id,squadra&order=nome.asc");
    if (is_array($resCollabReparto)) {
        foreach ($resCollabReparto as $c) {
            $rC_low = strtolower(str_replace([' ', '-'], '_', $c['ruolo'] ?? ''));
            if ($rC_low === 'super_admin' || $rC_low === 'admin' || $rC_low === 'superadmin' || $rC_low === 'capo_personale' || $rC_low === 'capopersonale') {
                continue;
            }
            $mappaUtenti[$c['id']] = $c;
        }
    }
}

if (empty($mappaUtenti)) {
    $resCollabAll = supabase_turni_request("staging_utenti?select=id,nome,ruolo,reparto_id,squadra&order=nome.asc");
    if (is_array($resCollabAll)) {
        foreach ($resCollabAll as $c) {
            $rC_low = strtolower(str_replace([' ', '-'], '_', $c['ruolo'] ?? ''));
            if ($rC_low === 'super_admin' || $rC_low === 'admin' || $rC_low === 'superadmin' || $rC_low === 'capo_personale' || $rC_low === 'capopersonale') {
                continue;
            }
            if (!$is_super_admin && !$is_capo_personale && $is_coordinatore && !empty($reparto_id_utente)) {
                if (isset($c['reparto_id']) && (string)$c['reparto_id'] !== (string)$reparto_id_utente) {
                    continue;
                }
            }
            $mappaUtenti[$c['id']] = $c;
        }
    }
}

$listaCollaboratori = array_values($mappaUtenti);
$idsUtentiReparto = array_keys($mappaUtenti);

// Gestione POST: Azioni ed interazioni
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $azione = $_POST['azione'] ?? '';

    // A. MODIFICA RAPIDA CELLA (Al click)
    if ($azione === 'modifica_cella') {
        $id_turno = trim($_POST['id_turno'] ?? '');
        $utente_cell = trim($_POST['utente_id'] ?? '');
        $data_cell = trim($_POST['data'] ?? '');
        $nuovo_valore = trim($_POST['valore'] ?? '');

        if (!empty($id_turno)) {
            $datiPatch = ['tipo_evento' => $nuovo_valore];
            $res = supabase_turni_request("pianificazione?id=eq." . urlencode($id_turno), 'PATCH', $datiPatch);
            if ($res !== false) {
                $messaggio = "Turno aggiornato con successo!";
                $tipo_alert = "success";
            } else {
                $messaggio = "Errore durante l'aggiornamento del turno.";
                $tipo_alert = "danger";
            }
        } elseif (!empty($utente_cell) && !empty($data_cell)) {
            $datiPost = [
                'organizzazione_id' => !empty($org_id_utente) ? $org_id_utente : null,
                'reparto_id' => !empty($reparto_id_utente) ? $reparto_id_utente : null,
                'utente_id' => $utente_cell,
                'data_inizio' => $data_cell . ' 00:00:00+00',
                'data_fine' => $data_cell . ' 23:59:59+00',
                'tipo_evento' => $nuovo_valore,
                'stato' => 'Approvato',
                'note' => 'Modifica rapida da tabellone'
            ];
            $res = supabase_turni_request("pianificazione", 'POST', $datiPost);
            if ($res !== false) {
                $messaggio = "Turno inserito con successo!";
                $tipo_alert = "success";
            } else {
                $messaggio = "Errore durante l'inserimento del turno.";
                $tipo_alert = "danger";
            }
        }
    }

    // B. GENERAZIONE TRAMITE MOTORE PYTHON SU RENDER (/genera-turni)
    if ($azione === 'genera_turni_python') {
        $mese_selezionato_form = trim($_POST['mese_python'] ?? date('Y-m'));
        list($anno_str, $mese_str) = explode('-', $mese_sel = $mese_selezionato_form);
        $id_operatore_singolo = trim($_POST['id_operatore_singolo'] ?? '');
        $turno_iniziale = trim($_POST['turno_iniziale'] ?? '');

        if (!empty($org_id_utente) && !empty($reparto_id_utente) && !empty($anno_str) && !empty($mese_str)) {
            $payloadPython = [
                'organizzazione_id' => (string)$org_id_utente,
                'reparto_id' => (string)$reparto_id_utente,
                'anno' => (int)$anno_str,
                'mese' => (int)$mese_str
            ];

            if (!empty($id_operatore_singolo)) {
                $payloadPython['operatore_id'] = (string)$id_operatore_singolo;
            }
            if (!empty($turno_iniziale)) {
                $payloadPython['turno_iniziale'] = $turno_iniziale;
            }

            $risultatoPython = call_python_engine_genera($payloadPython);

            if (is_array($risultatoPython) && (
                (isset($risultatoPython['success']) && $risultatoPython['success'] === true) || 
                isset($risultatoPython['message'])
            )) {
                $msgPython = $risultatoPython['message'] ?? 'Generazione completata con successo.';
                $messaggio = "Motore Python su Render: " . htmlspecialchars($msgPython);
                $tipo_alert = "success";
            } else {
                $errDetails = $risultatoPython['error'] ?? ($risultatoPython['detail'] ?? 'Risposta non valida dal server Python.');
                if (is_array($errDetails)) {
                    $errDetails = json_encode($errDetails);
                }
                $messaggio = "Impossibile completare la chiamata al motore Python su Render. Dettaglio: " . htmlspecialchars($errDetails);
                $tipo_alert = "danger";
            }
        } else {
            $messaggio = "Mancano parametri fondamentali (Organizzazione o Reparto) per avviare la generazione automatica.";
            $tipo_alert = "danger";
        }
    }

    // C. ASSEGNAZIONE MANUALE
    if ($azione === 'assegna_turno') {
        $collaboratore_selezionato = trim($_POST['collaboratore_id'] ?? '');
        $tipologia_turno_id = trim($_POST['tipologia_turno_id'] ?? '');
        $data_inizio = trim($_POST['data_inizio'] ?? '');
        $data_fine = trim($_POST['data_fine'] ?? $data_inizio);
        $note = trim($_POST['note'] ?? '');
        $stato_assegnazione = trim($_POST['stato_assegnazione'] ?? 'Approvato');

        if (!empty($collaboratore_selezionato) && !empty($tipologia_turno_id) && !empty($data_inizio)) {
            $datiPianificazione = [
                'organizzazione_id' => !empty($org_id_utente) ? $org_id_utente : null,
                'reparto_id' => !empty($reparto_id_utente) ? $reparto_id_utente : null,
                'utente_id' => $collaboratore_selezionato,
                'data_inizio' => $data_inizio . ' 00:00:00+00',
                'data_fine' => (!empty($data_fine) ? $data_fine : $data_inizio) . ' 23:59:59+00',
                'tipo_evento' => $tipologia_turno_id,
                'stato' => $stato_assegnazione,
                'note' => !empty($note) ? $note : "Assegnazione manuale"
            ];

            $resp = supabase_turni_request("pianificazione", 'POST', $datiPianificazione);
            if ($resp !== false) {
                $messaggio = "Turno assegnato con successo!";
                $tipo_alert = "success";
            } else {
                $messaggio = "Errore durante l'assegnazione del turno.";
                $tipo_alert = "danger";
            }
        } else {
            $messaggio = "Tutti i campi obbligatori devono essere compilati.";
            $tipo_alert = "danger";
        }
    }
}

// Eliminazione turno
if (isset($_GET['azione_stato']) && $_GET['azione_stato'] === 'elimina' && isset($_GET['id'])) {
    $id_pianificazione = trim($_GET['id']);
    supabase_turni_request("pianificazione?id=eq." . urlencode($id_pianificazione), 'DELETE');
    header("Location: turni.php?msg=eliminato");
    exit;
}

// Gestione visualizzazione Tabellone Mensile rapido
$mese_selezionato = trim($_GET['mese_tabellone'] ?? date('Y-m'));
$primo_giorno_tab = $mese_selezionato . '-01';
$giorni_nel_mese = intval(date('t', strtotime($primo_giorno_tab)));

// Recuperiamo tutti i turni del mese selezionato per popolare la griglia interattiva
$queryTurniMese = "pianificazione?select=*&data_inizio=gte." . $primo_giorno_tab . "&data_inizio=lte." . date('Y-m-t', strtotime($primo_giorno_tab));
if (!empty($idsUtentiReparto) && !$is_super_admin && !$is_capo_personale) {
    $queryTurniMese .= "&utente_id=in.(" . implode(',', $idsUtentiReparto) . ")";
}
$turniMeseData = supabase_turni_request($queryTurniMese);

// Mappa dei turni per [utente_id][giorno] = ['id' => ..., 'tipo_evento' => ...]
$mappaTurniGriglia = [];
if (is_array($turniMeseData)) {
    foreach ($turniMeseData as $tm) {
        $uId = $tm['utente_id'];
        $giornoNum = intval(date('d', strtotime($tm['data_inizio'])));
        $mappaTurniGriglia[$uId][$giornoNum] = [
            'id' => $tm['id'],
            'tipo_evento' => $tm['tipo_evento']
        ];
    }
}
?>
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Gestione Turni - PRO-TUR</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        body { background-color: #f4f7f6; padding-bottom: 70px; }
        .card { border: none; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        .table-turni th, .table-turni td { text-align: center; vertical-align: middle; font-size: 0.85rem; padding: 6px 4px; }
        .cella-interattiva { cursor: pointer; transition: background-color 0.2s; }
        .cella-interattiva:hover { background-color: #e2e6ea !important; font-weight: bold; }
        .col-operatore-sticky { position: sticky; left: 0; background-color: #ffffff; z-index: 2; text-align: left !important; min-width: 170px; font-weight: 600; }
    </style>
</head>
<body class="bg-light">

    <nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4">
        <div class="container-fluid">
            <a class="navbar-brand fw-bold" href="dashboard.php">PRO-TUR | Gestione Turni Ospedalieri</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item"><a class="nav-link" href="dashboard.php">Dashboard</a></li>
                    <li class="nav-item"><a class="nav-link active" href="turni.php">Turni</a></li>
                    <li class="nav-item"><a class="nav-link" href="planner.php">Planner Mensile</a></li>
                    <li class="nav-item"><a class="nav-link" href="ferie.php">Ferie e Assenze</a></li>
                    <li class="nav-item"><a class="nav-link text-danger" href="logout.php">Logout</a></li>
                </ul>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <h2 class="mb-1 fw-bold">Assegnazione e Gestione Turni</h2>
        <h4 class="text-muted mb-4 fs-6">Gestisci i turni di servizio, pianifica le coperture e modifica rapidamente la griglia con un clic.</h4>

        <?php if (isset($_GET['msg']) && $_GET['msg'] === 'eliminato'): ?>
            <div class="alert alert-success py-2 small" role="alert">Turno eliminato con successo.</div>
        <?php endif; ?>

        <?php if (!empty($messaggio)) { ?>
            <div class="alert alert-<?php echo $tipo_alert; ?> py-2 small" role="alert">
                <?php echo htmlspecialchars($messaggio); ?>
            </div>
        <?php } ?>

        <!-- Sezione: Generazione Turni tramite Motore Python (Render Engine /genera-turni) -->
        <?php if ($is_coordinatore || $is_super_admin || $is_capo_personale): ?>
        <div class="card shadow-sm mb-4 bg-white border-primary">
            <div class="card-header bg-primary text-white">
                <h5 class="mb-0 fs-6 fw-bold">🐍 Generazione Turni Python (Render Engine - /genera-turni)</h5>
            </div>
            <div class="card-body">
                <form method="POST" action="turni.php" class="row g-3 align-items-end">
                    <input type="hidden" name="azione" value="genera_turni_python">
                    <div class="col-md-3">
                        <label for="mese_python" class="form-label small fw-bold">Mese di Riferimento *</label>
                        <input type="month" class="form-control" id="mese_python" name="mese_python" value="<?php echo date('Y-m'); ?>" required>
                    </div>
                    <div class="col-md-3">
                        <label for="id_operatore_singolo" class="form-label small fw-bold">Singolo Operatore (Opzionale)</label>
                        <select class="form-select" id="id_operatore_singolo" name="id_operatore_singolo">
                            <option value="">-- Tutti gli operatori del reparto --</option>
                            <?php foreach ($listaCollaboratori as $c) { ?>
                                <option value="<?php echo $c['id']; ?>">
                                    <?php echo htmlspecialchars($c['nome']); ?> (<?php echo htmlspecialchars($c['ruolo'] ?? 'N/D'); ?>)
                                </option>
                            <?php } ?>
                        </select>
                    </div>
                    <div class="col-md-3">
                        <label for="turno_iniziale" class="form-label small fw-bold">Turno Iniziale (Opzionale)</label>
                        <select class="form-select" id="turno_iniziale" name="turno_iniziale">
                            <option value="">-- Automatico / Default --</option>
                            <option value="M">Mattina (M)</option>
                            <option value="P">Pomeriggio (P)</option>
                            <option value="N">Notte (N)</option>
                            <option value="S">Smonto (S)</option>
                            <option value="R">Riposo (R)</option>
                        </select>
                    </div>
                    <div class="col-md-3">
                        <button type="submit" class="btn btn-dark w-100 fw-bold" onclick="return confirm('Avviare la generazione dei turni tramite il motore Python su Render?');"><i class="bi bi-cpu"></i> Genera con Python</button>
                    </div>
                </form>
            </div>
        </div>
        <?php endif; ?>

        <!-- Sezione: Tabellone Turni Mensili con Modifica Rapida al Click -->
        <div class="card shadow-sm mb-5 bg-white">
            <div class="card-header bg-secondary text-white d-flex justify-content-between align-items-center">
                <h5 class="mb-0 fs-6 fw-bold">📅 Tabellone Turni Mensili (Clicca su una casella per modificare)</h5>
                <form method="GET" action="turni.php" class="d-flex align-items-center gap-2 m-0">
                    <input type="month" name="mese_tabellone" value="<?php echo htmlspecialchars($mese_selezionato); ?>" class="form-control form-control-sm" onchange="this.form.submit()">
                </form>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive" style="max-height: 65vh;">
                    <table class="table table-bordered table-striped table-turni m-0">
                        <thead class="table-dark sticky-top">
                            <tr>
                                <th class="col-operatore-sticky">Operatore</th>
                                <?php for ($g = 1; $g <= $giorni_nel_mese; $g++): ?>
                                    <th><?php echo $g; ?></th>
                                <?php endfor; ?>
                            </tr>
                        </thead>
                        <tbody>
                            <?php if (empty($listaCollaboratori)): ?>
                                <tr>
                                    <td colspan="<?php echo $giorni_nel_mese + 1; ?>" class="text-center text-muted py-4">Nessun operatore trovato.</td>
                                </tr>
                            <?php else: ?>
                                <?php foreach ($listaCollaboratori as $c): ?>
                                    <?php 
                                        $cId = $c['id'];
                                        $cNome = $c['nome'];
                                    ?>
                                    <tr>
                                        <td class="col-operatore-sticky"><?php echo htmlspecialchars($cNome); ?></td>
                                        <?php for ($g = 1; $g <= $giorni_nel_mese; $g++): ?>
                                            <?php 
                                                $giornoStr = sprintf('%s-%02d', $mese_selezionato, $g);
                                                $turnoInfo = $mappaTurniGriglia[$cId][$g] ?? ['id' => '', 'tipo_evento' => ''];
                                                $idTurnoRecord = $turnoInfo['id'];
                                                $codiceTurno = $turnoInfo['tipo_evento'];
                                            ?>
                                            <td class="cella-interattiva" 
                                                onclick="modificaTurnoRapido('<?php echo htmlspecialchars($idTurnoRecord); ?>', '<?php echo htmlspecialchars($cId); ?>', '<?php echo htmlspecialchars($giornoStr); ?>', '<?php echo htmlspecialchars($codiceTurno); ?>')">
                                                <?php echo htmlspecialchars($codiceTurno !== '' ? $codiceTurno : '-'); ?>
                                            </td>
                                        <?php endfor; ?>
                                    </tr>
                                <?php endforeach; ?>
                            <?php endif; ?>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Form Assegnazione Manuale (attingendo da tipologie_turno) -->
        <div class="card shadow-sm mb-5 bg-white">
            <div class="card-header bg-secondary text-white">
                <h5 class="mb-0 fs-6 fw-bold">Nuova Assegnazione Turno di Servizio (Manuale)</h5>
            </div>
            <div class="card-body">
                <form method="POST" action="turni.php">
                    <input type="hidden" name="azione" value="assegna_turno">
                    <div class="row g-3">
                        <div class="col-md-3">
                            <label for="collaboratore_id" class="form-label small fw-bold">Collaboratore / Operatore *</label>
                            <select class="form-select" id="collaboratore_id" name="collaboratore_id" required>
                                <option value="">Seleziona operatore...</option>
                                <?php foreach ($listaCollaboratori as $c) { ?>
                                    <option value="<?php echo $c['id']; ?>">
                                        <?php echo htmlspecialchars($c['nome']); ?> (<?php echo htmlspecialchars($c['ruolo'] ?? 'N/D'); ?>)
                                    </option>
                                <?php } ?>
                            </select>
                        </div>
                        <div class="col-md-3">
                            <label for="tipologia_turno_id" class="form-label small fw-bold">Tipologia Turno / Codice *</label>
                            <select class="form-select" id="tipologia_turno_id" name="tipologia_turno_id" required>
                                <option value="">Seleziona tipologia...</option>
                                <?php foreach ($tipologieTurni as $t) { ?>
                                    <option value="<?php echo htmlspecialchars($t['codice_breve']); ?>">
                                        <?php echo htmlspecialchars($t['nome_turno']); ?> (<?php echo htmlspecialchars($t['codice_breve']); ?>)
                                    </option>
                                <?php } ?>
                            </select>
                        </div>
                        <div class="col-md-2">
                            <label for="data_inizio" class="form-label small fw-bold">Data Inizio *</label>
                            <input type="date" class="form-control" id="data_inizio" name="data_inizio" required>
                        </div>
                        <div class="col-md-2">
                            <label for="data_fine" class="form-label small fw-bold">Data Fine (Opzionale)</label>
                            <input type="date" class="form-control" id="data_fine" name="data_fine">
                        </div>
                        <div class="col-md-2">
                            <label for="stato_assegnazione" class="form-label small fw-bold">Stato</label>
                            <select class="form-select" id="stato_assegnazione" name="stato_assegnazione">
                                <option value="Approvato">Approvato</option>
                                <option value="In attesa">In attesa</option>
                            </select>
                        </div>
                        <div class="col-md-10">
                            <label for="note" class="form-label small fw-bold">Note Operative</label>
                            <input type="text" class="form-control" id="note" name="note" placeholder="Eventuali annotazioni">
                        </div>
                        <div class="col-md-2 text-end d-flex align-items-end">
                            <button type="submit" class="btn btn-success w-100 fw-bold">Assegna</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>

    </div>

    <!-- Form nascosto per la modifica rapida della cella -->
    <form id="formModificaCella" method="POST" action="turni.php" style="display: none;">
        <input type="hidden" name="azione" value="modifica_cella">
        <input type="hidden" name="id_turno" id="input_id_turno">
        <input type="hidden" name="utente_id" id="input_utente_id">
        <input type="hidden" name="data" id="input_data">
        <input type="hidden" name="valore" id="input_valore">
    </form>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
    function modificaTurnoRapido(idTurno, idUtente, dataGiorno, valoreAttuale) {
        let nuovoValore = prompt("Inserisci il codice turno (es. M, P, N, S, R o altre sigle):", valoreAttuale);
        if (nuovoValore !== null) {
            document.getElementById('input_id_turno').value = idTurno;
            document.getElementById('input_utente_id').value = idUtente;
            document.getElementById('input_data').value = dataGiorno;
            document.getElementById('input_valore').value = nuovoValore.toUpperCase();
            document.getElementById('formModificaCella').submit();
        }
    }
    </script>
</body>
</html>
